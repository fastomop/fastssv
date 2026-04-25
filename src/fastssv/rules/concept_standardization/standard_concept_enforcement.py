"""Standard Concept Enforcement Rule.

OMOP semantic rule:
If query uses a STANDARD OMOP concept field, it must either:
  - enforce concept.standard_concept = 'S'
  OR
  - use mapping via concept_relationship relationship_id = 'Maps to'
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    has_condition,
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register
from fastssv.schemas import STANDARD_CONCEPT_FIELDS

# relationship_id values commonly used for standard mapping in OMOP
MAPS_TO_RELATIONSHIP = "Maps to"


def _extract_concept_references(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """Extract all resolved (table, column) references for concept fields.

    For unqualified columns (e.g. ``condition_concept_id`` rather than
    ``co.condition_concept_id``), attribute to the unique table in scope
    whose schema lists the column as a standard concept field. Without
    this fallback, single-table queries that omit aliases miss the rule
    entirely.
    """
    refs: List[Tuple[str, str]] = []
    tables_in_scope = {normalize_name(t) for t in aliases.values() if t}

    for col in tree.find_all(exp.Column):
        table, col_name = resolve_table_col(col, aliases)

        if not col_name:
            continue
        if col_name != "concept_id" and not col_name.endswith("_concept_id"):
            continue

        if not table:
            # Unqualified — try to attribute to a unique standard-field-owning
            # table in scope. Skip if zero or multiple candidates (ambiguous).
            col_norm = normalize_name(col_name)
            candidates = [
                t for t in tables_in_scope if (t, col_norm) in standard_fields
            ]
            if len(candidates) != 1:
                continue
            table = candidates[0]

        refs.append((table, col_name))

    return refs


def _has_specific_concept_id_filter(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> bool:
    """Check if query filters specific STANDARD concept fields with literal IDs.

    Only literal filters on columns that are actually in ``standard_fields``
    (e.g. ``condition_occurrence.condition_concept_id``) count as "user already
    chose specific standard concepts" intent. Literal filters on vocabulary
    table columns such as ``concept_ancestor.ancestor_concept_id`` don't —
    those are hierarchy-rollup inputs, not standard-concept enforcement.
    """
    from fastssv.core.helpers import is_numeric_literal

    tables_in_scope = {normalize_name(t) for t in aliases.values() if t}

    for node in tree.find_all((exp.EQ, exp.In)):
        if not isinstance(node.this, exp.Column):
            continue

        table_resolved, col_name = resolve_table_col(node.this, aliases)
        if not col_name:
            continue

        # Only literals on actual standard-concept fields count as intent.
        col_norm = normalize_name(col_name)
        if table_resolved:
            table_norm = normalize_name(table_resolved)
        else:
            # Unqualified — attribute to the unique standard-field-owning
            # table in scope, mirroring _extract_concept_references.
            candidates = [
                t for t in tables_in_scope if (t, col_norm) in standard_fields
            ]
            if len(candidates) != 1:
                continue
            table_norm = candidates[0]
        if (table_norm, col_norm) not in standard_fields:
            continue

        # Check for EQ with numeric literal
        if isinstance(node, exp.EQ):
            right = node.expression
            if is_numeric_literal(right) and not is_numeric_literal(right, 0):
                return True

        # Check for IN with numeric literals
        if isinstance(node, exp.In):
            for val in node.expressions or []:
                if is_numeric_literal(val) and not is_numeric_literal(val, 0):
                    return True

    return False


def _enforces_standard_concept(tree: exp.Expression) -> bool:
    """Detect if query enforces standard concepts via standard_concept = 'S'."""
    if not has_table_reference(tree, "concept"):
        return False

    return has_condition(tree, "standard_concept", {"s"}, require_where_clause=True)


def _uses_maps_to_relationship(tree: exp.Expression) -> bool:
    """Detect if query uses concept_relationship relationship_id = 'Maps to'."""
    if not has_table_reference(tree, "concept_relationship"):
        return False

    return has_condition(
        tree,
        "relationship_id",
        {normalize_name(MAPS_TO_RELATIONSHIP)},
        require_where_clause=True
    )


@register
class StandardConceptEnforcementRule(Rule):
    """Ensures queries using STANDARD concept fields enforce standard concepts."""

    rule_id = "concept_standardization.standard_concept_enforcement"
    name = "Standard Concept Enforcement"
    description = (
        "Ensures queries using STANDARD concept fields enforce standard concepts "
        "via concept.standard_concept = 'S' or concept_relationship 'Maps to'"
    )
    severity = Severity.WARNING
    suggested_fix = "ADD: `AND c.standard_concept = 'S'` to clinical-concept filters, OR resolve source concepts via `JOIN concept_relationship cr ON co.<x>_concept_id = cr.concept_id_1 AND cr.relationship_id = 'Maps to'`."
    long_description = (
        "Standard OMOP *_concept_id columns can point to non-standard or "
        "deprecated concepts unless the query explicitly enforces "
        "standard_concept = 'S'. Without that filter, cohort queries "
        "silently mix in classification-only concepts ('C'), invalid "
        "entries, or legacy mappings that never should have persisted, "
        "producing over-counts or non-reproducible results across sites. "
        "Era tables (condition_era, drug_era) and a handful of other "
        "columns are already guaranteed-standard by spec and are "
        "excluded from this rule."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'SNOMED';"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'SNOMED'\n"
        "  AND c.standard_concept = 'S';"
    )

    # Fields that are already guaranteed to be standard by OMOP CDM design
    # These do NOT require explicit standard_concept = 'S' enforcement
    ALREADY_STANDARD_FIELDS = {
        # ERA tables - derived from occurrence tables, only contain standard concepts
        ("condition_era", "condition_concept_id"),
        ("drug_era", "drug_concept_id"),
        ("dose_era", "drug_concept_id"),

        # Person demographic attributes - always standard
        ("person", "gender_concept_id"),
        ("person", "race_concept_id"),
        ("person", "ethnicity_concept_id"),
    }

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            # Parse errors handled elsewhere
            return []

        # Known standard fields from schema lists
        standard_fields: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in STANDARD_CONCEPT_FIELDS
        }

        already_standard: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in self.ALREADY_STANDARD_FIELDS
        }

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)
            refs = _extract_concept_references(tree, aliases, standard_fields)

            # Check if any STANDARD concept fields are used
            uses_standard_fields = False
            for table, col in refs:
                col_norm = normalize_name(col)
                # *_type_concept_id columns hold data-provenance tokens
                # (EHR / Claim / etc.), not clinical concepts. Filtering them
                # by standard_concept = 'S' is a category error — skip.
                if col_norm.endswith("_type_concept_id"):
                    continue
                key = (normalize_name(table), col_norm)
                if key in standard_fields and key not in already_standard:
                    uses_standard_fields = True
                    break

            if not uses_standard_fields:
                continue

            # Check if there's proper enforcement
            has_standard_enforcement = _enforces_standard_concept(tree)
            has_maps_to = _uses_maps_to_relationship(tree)
            has_specific_filter = _has_specific_concept_id_filter(tree, aliases, standard_fields)

            # If no enforcement mechanism is present, warn
            if not has_standard_enforcement and not has_maps_to and not has_specific_filter:
                # Check strict mode for severity escalation
                from fastssv.core.validation_context import get_validation_context
                ctx = get_validation_context()
                severity = Severity.ERROR if ctx.should_escalate_rule(self.rule_id) else Severity.WARNING

                message = "Query uses STANDARD concept fields without ensuring concepts are standard."
                if severity == Severity.ERROR:
                    message += " (Strict mode: cohort definitions must use standard concepts)"

                violations.append(self.create_violation(
                    message=message,
                    severity=severity,
                    suggested_fix="ADD: `JOIN concept c ON c.concept_id = <table>.<concept_id_col>` AND `WHERE c.standard_concept = 'S'` to filter to standard concepts.",
                    details={"strict_mode_escalated": severity == Severity.ERROR}
                ))

        return violations


__all__ = ["StandardConceptEnforcementRule"]

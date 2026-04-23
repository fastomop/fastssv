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
from fastssv.schemas import SOURCE_CONCEPT_FIELDS, STANDARD_CONCEPT_FIELDS

# relationship_id values commonly used for standard mapping in OMOP
MAPS_TO_RELATIONSHIP = "Maps to"


def _extract_concept_references(
    tree: exp.Expression, aliases: Dict[str, str]
) -> List[Tuple[str, str]]:
    """Extract all resolved (table, column) references for concept fields."""
    refs: List[Tuple[str, str]] = []

    for col in tree.find_all(exp.Column):
        table, col_name = resolve_table_col(col, aliases)

        if not table:
            continue

        if col_name == "concept_id" or col_name.endswith("_concept_id"):
            refs.append((table, col_name))

    return refs


def _has_specific_concept_id_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if query filters on specific concept_id values using IN or = operators."""
    from fastssv.core.helpers import is_numeric_literal

    for node in tree.find_all((exp.EQ, exp.In)):
        if not isinstance(node.this, exp.Column):
            continue

        _, col_name = resolve_table_col(node.this, aliases)
        if not col_name or not (col_name == "concept_id" or col_name.endswith("_concept_id")):
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
    suggested_fix = "JOIN concept table and add: WHERE concept.standard_concept = 'S'"

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

        # Known standard/source fields from schema lists
        standard_fields: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in STANDARD_CONCEPT_FIELDS
        }
        source_fields: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in SOURCE_CONCEPT_FIELDS
        }

        already_standard: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in self.ALREADY_STANDARD_FIELDS
        }

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)
            refs = _extract_concept_references(tree, aliases)

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
            has_specific_filter = _has_specific_concept_id_filter(tree, aliases)

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
                    suggested_fix="JOIN concept table and add: WHERE concept.standard_concept = 'S'",
                    details={"strict_mode_escalated": severity == Severity.ERROR}
                ))

        return violations


__all__ = ["StandardConceptEnforcementRule"]

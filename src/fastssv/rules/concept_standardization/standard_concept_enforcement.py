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
    collect_cte_names,
    has_condition,
    extract_aliases,
    extract_join_conditions,
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
            candidates = [t for t in tables_in_scope if (t, col_norm) in standard_fields]
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
            candidates = [t for t in tables_in_scope if (t, col_norm) in standard_fields]
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


def _filters_via_concept_ancestor(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> bool:
    """True if the query restricts a STANDARD concept_id column via
    concept_ancestor's hierarchy.

    Pattern:
        ``<standard_concept_id_col> IN (SELECT descendant_concept_id
                                        FROM concept_ancestor [WHERE ...])``
        ``<standard_concept_id_col> IN (SELECT ancestor_concept_id
                                        FROM concept_ancestor [WHERE ...])``

    By OMOP CDM definition, concept_ancestor is a hierarchy over Standard
    Concepts only — both ancestor_concept_id and descendant_concept_id are
    guaranteed-standard. Feeding rows from concept_ancestor into a
    *_concept_id slot transitively guarantees the standard-concept property,
    so an additional ``standard_concept = 'S'`` filter would be redundant.

    Scope-limited to the *direct* subquery form. CTE-indirected patterns
    (``WITH cte AS (SELECT descendant_concept_id FROM concept_ancestor ...)
    SELECT ... WHERE col IN (SELECT concept_id FROM cte)``) are not traced
    here — they're handled by existing rule behavior, where the literal-vs-
    standard distinction is harder to verify safely without inlining the CTE.
    """
    tables_in_scope = {normalize_name(t) for t in aliases.values() if t}

    for node in tree.find_all(exp.In):
        # Subquery form only — IN (1, 2, 3) has node.expressions populated.
        if node.expressions:
            continue

        if not isinstance(node.this, exp.Column):
            continue

        table_resolved, col_name = resolve_table_col(node.this, aliases)
        if not col_name:
            continue
        col_norm = normalize_name(col_name)

        if table_resolved:
            table_norm = normalize_name(table_resolved)
        else:
            candidates = [t for t in tables_in_scope if (t, col_norm) in standard_fields]
            if len(candidates) != 1:
                continue
            table_norm = candidates[0]

        if (table_norm, col_norm) not in standard_fields:
            continue

        if _in_subquery_selects_concept_ancestor_id(node):
            return True

    return False


def _has_chained_join_to_concept_ancestor_via_concept(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> bool:
    """True if a clinical *_concept_id is constrained to standard concepts via
    a two-hop JOIN chain through `concept.concept_id` to
    `concept_ancestor.descendant_concept_id` (or `ancestor_concept_id`).

    Pattern (semantically identical to the direct-JOIN and IN-subquery forms
    handled elsewhere; users adopt this shape when they also want to project
    columns from the concept table, e.g. `concept_name`):

        FROM <clinical>
        JOIN concept c ON <clinical>.<concept_id_col> = c.concept_id
        JOIN concept_ancestor ca ON c.concept_id = ca.descendant_concept_id
        WHERE ca.ancestor_concept_id = <id>

    The chain transitively constrains `<clinical>.<concept_id_col>` to
    `concept_ancestor.descendant_concept_id`, which is guaranteed-standard
    by OMOP CDM definition. The intermediate `concept` join is just a
    relay — it doesn't restrict standardness further or undo it. An
    additional `concept.standard_concept = 'S'` filter would be redundant.

    The two hops can appear in either order in the query, and either side
    of each EQ; this helper checks both orientations.
    """
    target_cols = {"descendant_concept_id", "ancestor_concept_id"}
    join_conditions = extract_join_conditions(tree, aliases)

    has_clinical_to_concept = False
    has_concept_to_concept_ancestor = False

    for lt, lc, rt, rc in join_conditions:
        for s1_t, s1_c, s2_t, s2_c in ((lt, lc, rt, rc), (rt, rc, lt, lc)):
            # Hop 1: clinical fact table . *_concept_id = concept.concept_id
            if normalize_name(s2_t) == "concept" and normalize_name(s2_c) == "concept_id":
                key = (normalize_name(s1_t), normalize_name(s1_c))
                if key in standard_fields:
                    has_clinical_to_concept = True
            # Hop 2: concept.concept_id = concept_ancestor.{ancestor,descendant}_concept_id
            if (
                normalize_name(s1_t) == "concept"
                and normalize_name(s1_c) == "concept_id"
                and normalize_name(s2_t) == "concept_ancestor"
                and normalize_name(s2_c) in target_cols
            ):
                has_concept_to_concept_ancestor = True

    return has_clinical_to_concept and has_concept_to_concept_ancestor


def _has_clinical_join_to_concept_ancestor(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> bool:
    """True if the query joins a clinical fact table directly to
    concept_ancestor on its descendant_concept_id or ancestor_concept_id,
    constraining the clinical *_concept_id slot to standard concepts.

    Pattern (semantically identical to the IN-subquery form handled by
    `_filters_via_concept_ancestor`):

        FROM drug_exposure de
        JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
        WHERE ca.ancestor_concept_id = <id>

    Every `de.drug_concept_id` that survives the join is by construction a
    `concept_ancestor.descendant_concept_id`, which is guaranteed-standard
    by OMOP CDM definition (concept_ancestor is a hierarchy over Standard
    Concepts only). An additional `standard_concept = 'S'` filter would be
    redundant. The JOIN form is the more common idiom in OHDSI cohort SQL
    because it avoids a correlated subquery.
    """
    target_cols = {"descendant_concept_id", "ancestor_concept_id"}

    for lt, lc, rt, rc in extract_join_conditions(tree, aliases):
        # Check both join directions: clinical=ca, ca=clinical.
        for side1_table, side1_col, side2_table, side2_col in (
            (lt, lc, rt, rc),
            (rt, rc, lt, lc),
        ):
            if side2_table != "concept_ancestor":
                continue
            if normalize_name(side2_col) not in target_cols:
                continue
            key = (normalize_name(side1_table), normalize_name(side1_col))
            if key in standard_fields:
                return True
    return False


def _in_subquery_selects_concept_ancestor_id(in_node: exp.In) -> bool:
    """True if the IN's subquery selects descendant_concept_id or
    ancestor_concept_id directly from concept_ancestor."""
    selects = list(in_node.find_all(exp.Select))
    if not selects:
        return False

    select = selects[0]

    if not has_table_reference(select, "concept_ancestor"):
        return False

    target_cols = {"descendant_concept_id", "ancestor_concept_id"}
    for proj in select.expressions or []:
        underlying = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(underlying, exp.Column):
            if normalize_name(underlying.name) in target_cols:
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

    return has_condition(tree, "relationship_id", {normalize_name(MAPS_TO_RELATIONSHIP)}, require_where_clause=True)


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
            has_concept_ancestor_filter = _filters_via_concept_ancestor(tree, aliases, standard_fields)
            has_concept_ancestor_join = _has_clinical_join_to_concept_ancestor(tree, aliases, standard_fields)
            has_concept_ancestor_chain = _has_chained_join_to_concept_ancestor_via_concept(
                tree, aliases, standard_fields
            )

            # If no enforcement mechanism is present, warn
            if (
                not has_standard_enforcement
                and not has_maps_to
                and not has_specific_filter
                and not has_concept_ancestor_filter
                and not has_concept_ancestor_join
                and not has_concept_ancestor_chain
            ):
                # Check strict mode for severity escalation
                from fastssv.core.validation_context import get_validation_context

                ctx = get_validation_context()
                severity = Severity.ERROR if ctx.should_escalate_rule(self.rule_id) else Severity.WARNING

                message = "Query uses STANDARD concept fields without ensuring concepts are standard."
                if severity == Severity.ERROR:
                    message += " (Strict mode: cohort definitions must use standard concepts)"

                # CTE-shadow aware suggested fix: if the user has a CTE named
                # `concept` (or `concept_relationship`) in scope, the default
                # `JOIN concept c ...` suggestion would resolve to that CTE
                # — which has no `standard_concept` column — and break at
                # execution time. Switch to the schema-qualified form and
                # flag the shadow so the user sees the actual root cause.
                cte_names = collect_cte_names(tree)
                shadow = cte_names & {"concept", "concept_relationship"}
                if shadow:
                    shadow_list = ", ".join(sorted(shadow))
                    suggested_fix = (
                        "ADD: `JOIN omop.concept c ON c.concept_id = <table>.<concept_id_col>` "
                        "AND `WHERE c.standard_concept = 'S'` to filter to standard concepts. "
                        f"NOTE: this query has a CTE named `{shadow_list}` which shadows the OMOP "
                        "vocabulary table — the JOIN must be schema-qualified (`omop.concept`) "
                        "or the CTE renamed, otherwise the JOIN would bind to the CTE and the "
                        "`standard_concept` column would not exist."
                    )
                else:
                    suggested_fix = (
                        "ADD: `JOIN concept c ON c.concept_id = <table>.<concept_id_col>` "
                        "AND `WHERE c.standard_concept = 'S'` to filter to standard concepts."
                    )

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=severity,
                        suggested_fix=suggested_fix,
                        details={"strict_mode_escalated": severity == Severity.ERROR},
                    )
                )

        return violations


__all__ = ["StandardConceptEnforcementRule"]

"""Join Path Validation Rule.

OMOP semantic rule:
Verify that concept or concept_relationship tables are properly joined to the clinical tables (condition_ocurrence, condition_era, drug_exposure, drug_era, measurement, 
observation, visit_occurrence, person, death, observation_period) using the standard concept fields.
In a nutshell: This rule checks "Did you forget to write the JOIN condition?"
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    extract_join_conditions,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register
from fastssv.schemas import STANDARD_CONCEPT_FIELDS


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


def _verify_concept_join_path(
    tree: exp.Expression,
    aliases: Dict[str, str],
    used_standard_fields: Set[Tuple[str, str]]
) -> List[str]:
    """Verify that vocabulary tables are properly joined to clinical tables."""
    warnings: List[str] = []

    uses_concept = uses_table(tree, "concept")
    uses_concept_rel = uses_table(tree, "concept_relationship")

    if not uses_concept and not uses_concept_rel:
        return []  # No vocabulary tables used, nothing to verify

    # Check if CTEs are used - if vocabulary tables are accessed via CTEs,
    # the join path is likely valid but indirect
    cte_names: Set[str] = set()
    for cte in tree.find_all(exp.CTE):
        if cte.alias:
            cte_names.add(normalize_name(cte.alias))

    # Check if any CTE references vocabulary tables and outputs concept_id
    ctes_with_vocab_and_concept_id: Set[str] = set()
    for cte in tree.find_all(exp.CTE):
        cte_name = normalize_name(cte.alias) if cte.alias else ""
        if not cte_name:
            continue

        # Check if this CTE uses vocabulary tables
        cte_uses_vocab = False
        for t in cte.find_all(exp.Table):
            table_name = normalize_name(t.name)
            if table_name in {"concept", "concept_relationship", "concept_ancestor",
                              "concept_synonym", "vocabulary", "domain", "concept_class"}:
                cte_uses_vocab = True
                break

        if not cte_uses_vocab:
            continue

        # Check if this CTE outputs a concept_id column
        cte_select = cte.find(exp.Select)
        if cte_select:
            for proj in cte_select.expressions or []:
                if isinstance(proj, exp.Star):
                    ctes_with_vocab_and_concept_id.add(cte_name)
                    break
                col_name = ""
                alias_name = ""
                if isinstance(proj, exp.Alias):
                    alias_name = normalize_name(proj.alias) if proj.alias else ""
                    if isinstance(proj.this, exp.Column):
                        col_name = normalize_name(proj.this.name)
                elif isinstance(proj, exp.Column):
                    col_name = normalize_name(proj.name)

                if (col_name == "concept_id" or col_name.endswith("_concept_id") or
                    alias_name == "concept_id" or alias_name.endswith("_concept_id")):
                    ctes_with_vocab_and_concept_id.add(cte_name)
                    break

    # If there are CTEs that bridge vocabulary tables to concept_ids,
    # check if those CTEs are joined to clinical tables
    if ctes_with_vocab_and_concept_id:
        join_conditions = extract_join_conditions(tree, aliases)

        for lt, lc, rt, rc in join_conditions:
            if lt in ctes_with_vocab_and_concept_id or rt in ctes_with_vocab_and_concept_id:
                if lc.endswith("_concept_id") or lc == "concept_id":
                    return []  # Valid indirect join path
                if rc.endswith("_concept_id") or rc == "concept_id":
                    return []  # Valid indirect join path

    # Direct join path check
    join_conditions = extract_join_conditions(tree, aliases)

    # Build a set of all join connections (bidirectional)
    join_pairs: Set[Tuple[str, str, str, str]] = set(join_conditions)
    for lt, lc, rt, rc in join_conditions:
        join_pairs.add((rt, rc, lt, lc))

    linked_to_concept = False
    linked_to_concept_rel = False

    for table, col in used_standard_fields:
        for lt, lc, rt, rc in join_pairs:
            if lt == table and lc == col:
                if rt == "concept" and rc == "concept_id":
                    linked_to_concept = True
                if rt == "concept_relationship":
                    linked_to_concept_rel = True

    # Also check if concept/concept_relationship are joined to concept_ancestor
    for lt, lc, rt, rc in join_pairs:
        if lt == "concept" and lc == "concept_id":
            if rt == "concept_ancestor" and rc in {"ancestor_concept_id", "descendant_concept_id"}:
                linked_to_concept = True
        if lt == "concept_ancestor":
            if rt == "concept" and rc == "concept_id":
                linked_to_concept = True

    if uses_concept and not linked_to_concept:
        warnings.append(
            "Query uses 'concept' table but it may not be properly joined "
            "to the clinical tables via standard concept fields."
        )

    if uses_concept_rel and not linked_to_concept_rel:
        warnings.append(
            "Query uses 'concept_relationship' table but it may not be properly joined "
            "to the clinical tables."
        )

    return warnings


@register
class JoinPathValidationRule(Rule):
    """Validates proper JOIN paths between clinical and vocabulary tables."""

    rule_id = "semantic.join_path_validation"
    name = "Join Path Validation"
    description = (
        "Verifies that concept or concept_relationship tables are properly joined "
        "to clinical tables using standard concept fields"
    )
    severity = Severity.WARNING
    suggested_fix = "JOIN concept ON clinical_table.*_concept_id = concept.concept_id"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        # Known standard fields from schema
        standard_fields: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in STANDARD_CONCEPT_FIELDS
        }

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)
            refs = _extract_concept_references(tree, aliases)

            used_standard = {(t, c) for (t, c) in refs if (t, c) in standard_fields}

            # Only validate if standard fields are being used
            if not used_standard:
                continue

            warnings = _verify_concept_join_path(tree, aliases, used_standard)

            for warning in warnings:
                violations.append(self.create_violation(
                    message=warning,
                ))

        return violations


__all__ = ["JoinPathValidationRule"]

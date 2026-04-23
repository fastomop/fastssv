from typing import Dict, List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


ERA_TABLES = {
    "condition_era",
    "drug_era",
    "dose_era",
}

ERA_TABLE_CONCEPT_COLUMNS = {
    "condition_era": "condition_concept_id",
    "drug_era": "drug_concept_id",
    "dose_era": "drug_concept_id",
}


def _find_valid_era_concept_joins(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> Set[str]:
    """
    Find concept table aliases that are VALIDLY joined to era tables
    using the correct concept_id column.
    """
    valid_concept_aliases: Set[str] = set()

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left = eq.this
            right = eq.expression

            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue

            l_table, l_col = resolve_table_col(left, aliases)
            r_table, r_col = resolve_table_col(right, aliases)

            l_table = normalize_name(l_table) if l_table else ""
            r_table = normalize_name(r_table) if r_table else ""
            l_col = normalize_name(l_col) if l_col else ""
            r_col = normalize_name(r_col) if r_col else ""

            # Case 1: era -> concept
            if l_table in ERA_TABLES and r_table == "concept":
                expected = ERA_TABLE_CONCEPT_COLUMNS[l_table]
                if l_col == expected and r_col == "concept_id":
                    valid_concept_aliases.add(right.table)

            # Case 2: concept -> era
            elif r_table in ERA_TABLES and l_table == "concept":
                expected = ERA_TABLE_CONCEPT_COLUMNS[r_table]
                if r_col == expected and l_col == "concept_id":
                    valid_concept_aliases.add(left.table)

    return valid_concept_aliases


def _extract_literal(node: exp.Expression) -> str:
    """Extract normalized literal value."""
    if isinstance(node, exp.Literal):
        return normalize_name(str(node.this))
    if isinstance(node, exp.Null):
        return "null"
    return ""


def _is_standard_concept_column(col: exp.Expression, aliases: Dict[str, str], valid_aliases: Set[str]) -> bool:
    """Check if column is concept.standard_concept from a valid join."""
    if not isinstance(col, exp.Column):
        return False

    _, column = resolve_table_col(col, aliases)

    return (
        normalize_name(column) == "standard_concept"
        and col.table in valid_aliases
    )


def _check_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
    valid_aliases: Set[str],
) -> List[str]:
    """Detect invalid filters for non-standard concepts."""
    issues: List[str] = []

    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.NEQ, exp.Is, exp.In)):
            continue

        left = node.this
        right = node.expression

        # Check both sides (handle reversed conditions)
        sides = [(left, right), (right, left)]

        for col_node, val_node in sides:
            if not _is_standard_concept_column(col_node, aliases, valid_aliases):
                continue

            value = _extract_literal(val_node)

            # --- Invalid cases ---
            if isinstance(node, exp.Is) and value == "null":
                issues.append(
                    "Filtering for non-standard concepts (standard_concept IS NULL) "
                    "on an era table. Era tables only contain standard concepts. "
                    "This query will return 0 rows."
                )

            elif isinstance(node, exp.NEQ) and value == "s":
                issues.append(
                    "Filtering for non-standard concepts (standard_concept != 'S') "
                    "on an era table. This will return 0 rows."
                )

            elif isinstance(node, exp.EQ) and value not in ("s", ""):
                issues.append(
                    f"Filtering for standard_concept = '{value.upper()}', which is not 'S'. "
                    "Era tables only contain standard concepts. This will return 0 rows."
                )

            elif isinstance(node, exp.In):
                # For IN clauses, values are in node.args['expressions']
                in_values = node.args.get('expressions', [])
                if in_values:
                    values = {_extract_literal(v) for v in in_values}
                    # Flag if any value is not 'S'
                    if values and any(v != "s" for v in values):
                        issues.append(
                            "Filtering standard_concept IN (...) with non-'S' values "
                            "on an era table. This will return 0 rows."
                        )

    return issues


@register
class EraTableStandardConceptsRule(Rule):
    """Robust validation for era table standard concept misuse."""

    rule_id = "concept_standardization.era_table_standard_concepts"
    name = "Era Tables Use Standard Concepts Only"
    description = (
        "Era tables contain only standard concepts. Filtering for non-standard "
        "concepts will always return 0 rows."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Remove filters for non-standard concepts. Era tables only contain "
        "standard concepts (standard_concept = 'S')."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            valid_aliases = _find_valid_era_concept_joins(tree, aliases)

            if not valid_aliases:
                continue

            issues = _check_filters(tree, aliases, valid_aliases)

            for issue in issues:
                violations.append(self.create_violation(message=issue))

        return violations


__all__ = ["EraTableStandardConceptsRule"]

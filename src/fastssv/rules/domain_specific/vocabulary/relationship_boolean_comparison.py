"""Relationship Boolean Comparison Rule.

OMOP semantic rule OMOP_150:
The relationship table has is_hierarchical and defines_ancestry columns.
These are boolean flags that should be compared with proper boolean values
(1, 0, TRUE, FALSE), not strings or other invalid types.

The Problem:
    The relationship vocabulary table uses boolean flags to indicate:
    - is_hierarchical: Whether the relationship represents a hierarchy
    - defines_ancestry: Whether the relationship defines ancestry paths

    When filtering these columns, developers sometimes use incorrect value types:
    - String literals: 'true', 'false', '1', '0'
    - Invalid integers: 2, -1, or any value other than 0 or 1

    This causes issues:
    1. Type mismatch errors in strongly-typed databases
    2. Incorrect comparisons (boolean vs string comparison semantics differ)
    3. Performance problems (prevents index usage)
    4. Silent failures or unexpected results

Why this is wrong:
    Boolean columns should be compared with boolean-compatible values:
    - In most SQL dialects, booleans are represented as 1 (TRUE) or 0 (FALSE)
    - Comparing with strings requires implicit conversion that may fail
    - Using invalid integers (2, -1, etc.) is semantically meaningless
    - String comparisons have different semantics than boolean comparisons

Violation patterns:
    SELECT * FROM relationship WHERE is_hierarchical = 'true'
    -- ERROR: String comparison on boolean column

    SELECT * FROM relationship WHERE defines_ancestry = '1'
    -- ERROR: String '1' is not the same as integer 1

    SELECT * FROM relationship WHERE is_hierarchical = 2
    -- ERROR: Invalid boolean value (only 0 or 1 allowed)

    SELECT * FROM relationship WHERE defines_ancestry IN ('true', 'false')
    -- ERROR: String values in IN clause

    SELECT r.* FROM concept_relationship cr
    JOIN relationship r ON cr.relationship_id = r.relationship_id
    WHERE r.is_hierarchical = 'Y'
    -- ERROR: 'Y'/'N' pattern is wrong for boolean

Correct patterns:
    SELECT * FROM relationship WHERE is_hierarchical = 1
    -- OK: Integer boolean comparison

    SELECT * FROM relationship WHERE is_hierarchical = TRUE
    -- OK: Boolean literal

    SELECT * FROM relationship WHERE is_hierarchical
    -- OK: Direct boolean usage

    SELECT * FROM relationship WHERE is_hierarchical = 0
    -- OK: Integer boolean comparison (FALSE)

    SELECT * FROM relationship WHERE is_hierarchical IS NOT NULL
    -- OK: NULL check is valid

    SELECT * FROM relationship WHERE is_hierarchical IN (0, 1)
    -- OK: Integer boolean values

Note:
    This is an ERROR, not a warning. Using incorrect types for boolean comparisons
    can cause query failures or incorrect results. Parameterized queries are allowed
    since we cannot validate the parameter type statically.
"""

import logging
from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import extract_aliases, normalize_name, parse_sql
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

RELATIONSHIP_TABLE = "relationship"
BOOLEAN_COLUMNS = {"is_hierarchical", "defines_ancestry"}


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    return {
        _norm(cte.alias_or_name)
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _is_relationship_table(name: Optional[str]) -> bool:
    return name == RELATIONSHIP_TABLE


def _is_boolean_column(name: Optional[str]) -> bool:
    return name in BOOLEAN_COLUMNS


def _is_valid_boolean_value(node: exp.Expression) -> bool:
    if isinstance(node, (exp.Boolean, exp.Null)):
        return True

    if isinstance(node, (exp.Column, exp.Placeholder, exp.Parameter)):
        return True

    if isinstance(node, exp.Literal):
        if node.is_string:
            return False

        try:
            value = int(node.this)
            return value in (0, 1)
        except (ValueError, TypeError):
            return False

    if isinstance(node, (exp.Cast, exp.TryCast)):
        return True

    return False


def _resolve_table_name(
    table_name: Optional[str],
    aliases: Dict[str, str],
) -> Optional[str]:
    if not table_name:
        return None

    table_name = _norm(table_name)
    resolved = aliases.get(table_name)

    if resolved:
        return _norm(resolved)

    return table_name


def _collect_tables(tree: exp.Expression, cte_names: Set[str]) -> Set[str]:
    """Collect all table names in the query (excluding CTEs)."""
    tables = set()
    for tbl in tree.find_all(exp.Table):
        name = _norm(tbl.name)
        if name and name not in cte_names:
            tables.add(name)
    return tables


def _check_comparison(
    comparison: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    tables_in_query: Set[str],
) -> Optional[str]:
    left = comparison.this
    right = comparison.expression

    if not left or not right:
        return None

    # Handle BOTH directions (critical fix)
    for col_side, value_side in [(left, right), (right, left)]:
        if not isinstance(col_side, exp.Column):
            continue

        col_name = _norm(col_side.name)
        table_name = _resolve_table_name(col_side.table, aliases)

        # Handle unqualified columns
        if not table_name:
            # Only flag if relationship is the sole table
            if tables_in_query == {RELATIONSHIP_TABLE} and _is_boolean_column(col_name):
                table_name = RELATIONSHIP_TABLE
            else:
                continue

        if table_name in cte_names:
            continue

        if not (_is_boolean_column(col_name) and _is_relationship_table(table_name)):
            continue

        if not _is_valid_boolean_value(value_side):
            if isinstance(value_side, exp.Literal):
                if value_side.is_string:
                    return (
                        f"Column '{col_name}' is boolean but compared with string "
                        f"'{value_side.this}'. Use 0/1 or TRUE/FALSE."
                    )
                else:
                    return (
                        f"Column '{col_name}' is boolean but compared with invalid "
                        f"value '{value_side.this}'. Only 0, 1, TRUE, FALSE allowed."
                    )
            else:
                return (
                    f"Column '{col_name}' is boolean but compared with "
                    f"invalid expression. Use 0/1 or TRUE/FALSE."
                )

    return None


def _check_in_clause(
    in_expr: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    tables_in_query: Set[str],
) -> Optional[str]:
    left = in_expr.this

    if not isinstance(left, exp.Column):
        return None

    col_name = _norm(left.name)
    table_name = _resolve_table_name(left.table, aliases)

    # Handle unqualified columns
    if not table_name:
        # Only flag if relationship is the sole table
        if tables_in_query == {RELATIONSHIP_TABLE} and _is_boolean_column(col_name):
            table_name = RELATIONSHIP_TABLE
        else:
            return None

    if table_name in cte_names:
        return None

    if not (_is_boolean_column(col_name) and _is_relationship_table(table_name)):
        return None

    invalid_values = []

    for value in in_expr.expressions:
        if not _is_valid_boolean_value(value):
            invalid_values.append(value.sql())

    if invalid_values:
        return (
            f"Column '{col_name}' is boolean but IN/NOT IN contains invalid values: "
            f"{', '.join(invalid_values)}. Use 0/1 or TRUE/FALSE."
        )

    return None


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> List[str]:
    issues: List[str] = []

    # Collect tables for unqualified column handling
    tables_in_query = _collect_tables(tree, cte_names)

    # Comparisons
    for comp in tree.find_all(exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE):
        msg = _check_comparison(comp, aliases, cte_names, tables_in_query)
        if msg:
            issues.append(msg)

    # IN clauses (NOT IN is exp.Not wrapping exp.In)
    for in_expr in tree.find_all(exp.In):
        msg = _check_in_clause(in_expr, aliases, cte_names, tables_in_query)
        if msg:
            issues.append(msg)

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class RelationshipBooleanComparisonRule(Rule):
    """
    OMOP_150: Validate boolean column comparisons in relationship table.
    """

    rule_id = "domain_specific.vocabulary.relationship_boolean_comparison"
    name = "Relationship Boolean Comparison"

    description = (
        "relationship.is_hierarchical and defines_ancestry must be compared "
        "with valid boolean values (0,1,TRUE,FALSE), not strings."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use 0/1 or TRUE/FALSE instead of strings."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if RELATIONSHIP_TABLE not in sql_lower:
            return []

        if not any(col in sql_lower for col in BOOLEAN_COLUMNS):
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_150",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            cte_names = _extract_cte_names(tree)

            issues = _find_violations(tree, aliases, cte_names)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["RelationshipBooleanComparisonRule"]

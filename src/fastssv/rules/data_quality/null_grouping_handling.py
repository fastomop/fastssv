"""NULL Grouping Handling Rule.

OMOP semantic rule:
When grouping by columns from LEFT JOIN, warn about potential NULL buckets.
LEFT JOINs can produce NULL values for columns from the right table when there's
no match, and GROUP BY will create a NULL bucket that users may not intend.
"""

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


def _extract_left_join_right_tables(tree: exp.Expression, aliases: Dict[str, str]) -> Set[str]:
    """Extract table names that appear on the right side of LEFT JOINs.

    These tables can produce NULL values for their columns when there's no match.
    """
    right_tables = set()

    for join in tree.find_all(exp.Join):
        # Check if it's a LEFT JOIN
        if join.side == "LEFT" or (hasattr(join, 'kind') and join.kind == "LEFT"):
            # Get the table being joined (right side)
            table_expr = join.this
            if isinstance(table_expr, exp.Table):
                table_name = normalize_name(table_expr.name)
                right_tables.add(table_name)

                # Also track the alias if present
                alias = table_expr.alias
                if alias:
                    alias_name = normalize_name(alias)
                    # Map alias to real table
                    for k, v in aliases.items():
                        if k == alias_name:
                            right_tables.add(v)

    return right_tables


def _get_all_table_columns(tree: exp.Expression) -> Dict[str, Set[str]]:
    """Build a map of table name to columns used from that table.

    This helps identify which table unqualified columns might belong to.
    """
    table_columns = {}

    # Scan all columns in the query
    for col in tree.find_all(exp.Column):
        # col.table can be either an Identifier or a string
        table = ""
        if isinstance(col.table, exp.Identifier):
            table = normalize_name(col.table.name)
        elif isinstance(col.table, str) and col.table:
            table = normalize_name(col.table)

        if table:
            col_name = normalize_name(col.name)

            if table not in table_columns:
                table_columns[table] = set()
            table_columns[table].add(col_name)

    return table_columns


def _infer_table_for_unqualified_column(
    col_name: str,
    table_columns: Dict[str, Set[str]],
    right_tables: Set[str]
) -> str:
    """Try to infer which table an unqualified column belongs to.

    Prioritize right tables (from LEFT JOIN) since those are what we care about.
    """
    # First check if the column appears in any right table
    for table in right_tables:
        if table in table_columns and col_name in table_columns[table]:
            return table

    # Otherwise, check all tables
    for table, cols in table_columns.items():
        if col_name in cols:
            return table

    return ""


def _extract_group_by_columns(
    tree: exp.Expression,
    aliases: Dict[str, str],
    right_tables: Set[str]
) -> List[tuple]:
    """Extract columns used in GROUP BY clause.

    Returns list of (table, column) tuples.
    """
    group_by_cols = []
    table_columns = _get_all_table_columns(tree)

    for group in tree.find_all(exp.Group):
        for expr in group.expressions:
            if isinstance(expr, exp.Column):
                table, col = resolve_table_col(expr, aliases)

                # If table is not resolved, try to infer it
                if not table and col:
                    table = _infer_table_for_unqualified_column(col, table_columns, right_tables)

                if col:
                    group_by_cols.append((table, col))

    return group_by_cols


@register
class NullGroupingHandlingRule(Rule):
    """Warns when grouping by columns from LEFT JOIN that may produce NULL buckets."""

    rule_id = "data_quality.null_grouping_handling"
    name = "NULL Grouping Handling"
    description = (
        "Warns when GROUP BY uses columns from LEFT JOIN that may introduce "
        "NULL buckets when there's no match"
    )
    severity = Severity.WARNING
    suggested_fix = "Add WHERE clause to filter out NULLs if not desired"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)

            # Find tables on the right side of LEFT JOINs
            right_tables = _extract_left_join_right_tables(tree, aliases)

            if not right_tables:
                continue

            # Find columns in GROUP BY
            group_by_cols = _extract_group_by_columns(tree, aliases, right_tables)

            # Check if any GROUP BY columns come from LEFT JOIN right tables
            # or are unqualified (which might be from the right table)
            nullable_cols = []
            for table, col in group_by_cols:
                # If the column is from a known right table, it's nullable
                if table and table in right_tables:
                    nullable_cols.append(col)
                # If the column is unqualified and we have LEFT JOINs, it might be nullable
                # (we can't determine for sure without schema knowledge, but it's worth warning about)
                elif not table and right_tables:
                    nullable_cols.append(col)

            if nullable_cols:
                if len(nullable_cols) == 1:
                    col_str = f"'{nullable_cols[0]}'"
                    message = f"Grouping by nullable column {col_str} may introduce NULL bucket."
                    suggested_fix = f"Add: WHERE {nullable_cols[0]} IS NOT NULL (if NULLs are not desired)"
                else:
                    col_str = " and ".join([f"'{c}'" for c in nullable_cols])
                    message = f"Grouping by nullable columns {col_str} may introduce NULL buckets."
                    where_conditions = " AND ".join([f"{c} IS NOT NULL" for c in nullable_cols])
                    suggested_fix = f"Add: WHERE {where_conditions} (if NULLs are not desired)"

                violations.append(self.create_violation(
                    message=message,
                    suggested_fix=suggested_fix,
                    details={
                        "nullable_columns": nullable_cols,
                    }
                ))

        return violations


__all__ = ["NullGroupingHandlingRule"]

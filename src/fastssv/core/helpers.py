"""Shared SQL parsing helpers for FastSSV validation rules."""

from typing import Dict, List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


def normalize_name(s: str) -> str:
    """Normalize identifier names to lowercase."""
    return s.lower().strip()


def parse_sql(sql: str, dialect: str = "postgres") -> Tuple[Optional[List[exp.Expression]], Optional[str]]:
    """Parse SQL and return list of statement trees.

    Handles multiple statements (UNION, etc.) and returns parse errors gracefully.

    Args:
        sql: The SQL string to parse
        dialect: SQL dialect for parsing

    Returns:
        Tuple of (list_of_trees, error_message). If parsing succeeds,
        error_message is None. If it fails, list_of_trees is None.
    """
    try:
        trees = sqlglot.parse(sql, read=dialect)
        if not trees:
            return None, "Failed to parse SQL: empty result"
        return trees, None
    except ParseError as e:
        return None, f"SQL parse error: {str(e)}"
    except Exception as e:
        return None, f"Unexpected error parsing SQL: {str(e)}"


def extract_aliases(tree: exp.Expression) -> Dict[str, str]:
    """Build a mapping of alias -> real_table_name.

    Example:
        FROM condition_occurrence c
    gives:
        {"c": "condition_occurrence", "condition_occurrence": "condition_occurrence"}

    Also handles CTEs by extracting their names.

    Args:
        tree: The SQL AST to extract aliases from

    Returns:
        Dictionary mapping aliases to real table names
    """
    aliases: Dict[str, str] = {}

    # Handle CTEs - extract CTE names as self-referencing aliases
    for cte in tree.find_all(exp.CTE):
        cte_alias = cte.alias
        if cte_alias:
            cte_name = normalize_name(cte_alias)
            aliases[cte_name] = cte_name

    for t in tree.find_all(exp.Table):
        real = normalize_name(t.name)

        # SQLGlot aliases are sometimes objects; alias_or_name is safe
        alias = t.alias_or_name
        if alias:
            alias_norm = normalize_name(alias)
            aliases[alias_norm] = real

        aliases[real] = real

    return aliases


def resolve_table_col(col: exp.Column, aliases: Dict[str, str]) -> Tuple[str, str]:
    """Resolve exp.Column into (real_table_name, column_name).

    Example:
        c.condition_concept_id -> ("condition_occurrence", "condition_concept_id")

    Args:
        col: The Column expression to resolve
        aliases: Dictionary mapping aliases to real table names

    Returns:
        Tuple of (table_name, column_name). Table may be empty string if unqualified.
    """
    col_name = normalize_name(col.name)
    table_name = ""
    if col.table:
        table_alias = normalize_name(col.table)
        table_name = aliases.get(table_alias, table_alias)
    return table_name, col_name


def is_string_literal(e: exp.Expression) -> bool:
    """Check if expression is a string literal."""
    return isinstance(e, exp.Literal) and e.is_string


def is_numeric_literal(e: exp.Expression, value: Optional[int] = None) -> bool:
    """Check if expression is a numeric literal, optionally with specific value.

    Args:
        e: The expression to check
        value: If provided, check if the literal equals this value

    Returns:
        True if it's a numeric literal (optionally matching value)
    """
    if not isinstance(e, exp.Literal) or e.is_string:
        return False
    try:
        num_val = int(e.this)
        if value is not None:
            return num_val == value
        return True
    except (ValueError, TypeError):
        return False


def has_table_reference(tree: exp.Expression, table_name: str) -> bool:
    """Check if query references a table by name anywhere.

    Args:
        tree: The SQL AST to search
        table_name: The table name to look for

    Returns:
        True if the table is referenced
    """
    target = normalize_name(table_name)
    return any(normalize_name(t.name) == target for t in tree.find_all(exp.Table))


def is_in_where_or_join_clause(node: exp.Expression) -> bool:
    """Check if an expression node is within a WHERE clause or JOIN ON condition.

    Args:
        node: The expression node to check

    Returns:
        True if the node is in a WHERE or JOIN ON clause
    """
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Where):
            return True
        if isinstance(parent, exp.Join):
            return True
        parent = parent.parent
    return False


def has_equality_condition(
    tree: exp.Expression,
    column_name: str,
    expected_values: Set[str],
    require_where_clause: bool = True
) -> bool:
    """Check if there's an equality condition for the given column.

    Looks for patterns like: col = 'value' or 'value' = col

    Args:
        tree: The SQL AST to search
        column_name: The column to check (normalized)
        expected_values: Set of acceptable values (normalized)
        require_where_clause: If True, condition must be in WHERE/JOIN ON clause

    Returns:
        True if a matching condition is found
    """
    for eq in tree.find_all(exp.EQ):
        if require_where_clause and not is_in_where_or_join_clause(eq):
            continue

        left, right = eq.left, eq.right

        # column = 'value'
        if isinstance(left, exp.Column) and normalize_name(left.name) == column_name:
            if is_string_literal(right) and normalize_name(right.this) in expected_values:
                return True

        # 'value' = column
        if isinstance(right, exp.Column) and normalize_name(right.name) == column_name:
            if is_string_literal(left) and normalize_name(left.this) in expected_values:
                return True

    return False


def has_in_condition(
    tree: exp.Expression,
    column_name: str,
    expected_values: Set[str],
    require_where_clause: bool = True
) -> bool:
    """Check if there's an IN condition for the given column.

    Looks for patterns like: col IN ('value1', 'value2', ...)

    Args:
        tree: The SQL AST to search
        column_name: The column to check (normalized)
        expected_values: Set of acceptable values (normalized)
        require_where_clause: If True, condition must be in WHERE/JOIN ON clause

    Returns:
        True if a matching condition is found
    """
    for in_expr in tree.find_all(exp.In):
        if require_where_clause and not is_in_where_or_join_clause(in_expr):
            continue

        if not isinstance(in_expr.this, exp.Column):
            continue
        if normalize_name(in_expr.this.name) != column_name:
            continue

        expressions = in_expr.expressions
        if expressions:
            for val_expr in expressions:
                if is_string_literal(val_expr):
                    if normalize_name(val_expr.this) in expected_values:
                        return True

    return False


def has_condition(
    tree: exp.Expression,
    column_name: str,
    expected_values: Set[str],
    require_where_clause: bool = True
) -> bool:
    """Check if there's a condition (equality or IN) for the given column.

    Args:
        tree: The SQL AST to search
        column_name: The column to check (normalized)
        expected_values: Set of acceptable values (normalized)
        require_where_clause: If True, condition must be in WHERE/JOIN ON clause

    Returns:
        True if a matching condition is found
    """
    return (
        has_equality_condition(tree, column_name, expected_values, require_where_clause) or
        has_in_condition(tree, column_name, expected_values, require_where_clause)
    )


def extract_join_conditions(tree: exp.Expression, aliases: Dict[str, str]) -> List[Tuple[str, str, str, str]]:
    """Extract JOIN conditions to verify proper table linking.

    Args:
        tree: The SQL AST to search
        aliases: Dictionary mapping aliases to real table names

    Returns:
        List of tuples: (left_table, left_col, right_table, right_col)
    """
    join_conditions: List[Tuple[str, str, str, str]] = []

    for eq in tree.find_all(exp.EQ):
        parent = eq.parent
        in_join = False
        while parent:
            if isinstance(parent, exp.Join):
                in_join = True
                break
            parent = parent.parent

        if not in_join:
            continue

        left, right = eq.left, eq.right

        if isinstance(left, exp.Column) and isinstance(right, exp.Column):
            left_table, left_col = resolve_table_col(left, aliases)
            right_table, right_col = resolve_table_col(right, aliases)

            if left_table and right_table:
                join_conditions.append((left_table, left_col, right_table, right_col))

    return join_conditions


__all__ = [
    "normalize_name",
    "parse_sql",
    "extract_aliases",
    "resolve_table_col",
    "is_string_literal",
    "is_numeric_literal",
    "has_table_reference",
    "is_in_where_or_join_clause",
    "has_equality_condition",
    "has_in_condition",
    "has_condition",
    "extract_join_conditions",
]

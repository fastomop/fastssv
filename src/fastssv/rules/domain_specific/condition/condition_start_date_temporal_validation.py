"""Condition Temporal Column Validation Rule.

OMOP semantic rule CLIN_010:
Temporal queries on condition_occurrence should use condition_start_date (NOT NULL)
rather than condition_start_datetime or condition_end_date (both nullable).

The Problem:
    condition_occurrence has three temporal columns:
    - condition_start_date: Required, NOT NULL (always populated)
    - condition_start_datetime: Optional, may be NULL
    - condition_end_date: Optional, may be NULL (ongoing conditions)

    Using nullable columns for temporal filtering can silently exclude records
    where those columns are NULL, leading to incomplete result sets.

Example impact:
    -- BAD: Uses nullable column
    SELECT COUNT(*) FROM condition_occurrence
    WHERE condition_start_datetime BETWEEN '2023-01-01' AND '2023-12-31'
    -- May exclude records where datetime is NULL but date is populated

    -- GOOD: Uses required column
    SELECT COUNT(*) FROM condition_occurrence
    WHERE condition_start_date BETWEEN '2023-01-01' AND '2023-12-31'
    -- Includes all records (start_date is always populated)

Violation patterns:
    -- Using datetime for temporal filter
    WHERE condition_start_datetime > '2023-01-01'

    -- Using end_date for temporal filter (especially problematic)
    WHERE condition_end_date BETWEEN '2023-01-01' AND '2023-12-31'

Correct patterns:
    -- Use condition_start_date
    WHERE condition_start_date BETWEEN '2023-01-01' AND '2023-12-31'

    -- Or use COALESCE if datetime precision is needed
    WHERE COALESCE(condition_start_datetime, condition_start_date) > '2023-01-01'
"""

from typing import Dict, List, Optional, Set, Tuple
import re

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

TABLE_NAME = "condition_occurrence"

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")

TEMPORAL_COLUMNS = {
    "condition_start_datetime": {
        "nullable": True,
        "preferred": "condition_start_date",
        "description": "nullable datetime column",
    },
    "condition_end_date": {
        "nullable": True,
        "preferred": "condition_start_date",
        "description": "nullable end date (often NULL for ongoing conditions)",
    },
    "condition_start_date": {
        "nullable": False,
        "preferred": None,
        "description": "required NOT NULL column",
    },
}

TEMPORAL_OPERATORS = {
    exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ, exp.Between
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_date_literal(node: exp.Expression) -> bool:
    """Robust detection of date-like expressions."""
    if node is None:
        return False

    # String literal date
    if isinstance(node, exp.Literal):
        val = str(node.this).strip("'\"")
        return bool(DATE_PATTERN.match(val))

    # CURRENT_DATE
    if isinstance(node, exp.CurrentDate):
        return True

    # DATE '2020-01-01'
    if isinstance(node, exp.Cast):
        return _is_date_literal(node.this)

    return False


def _is_temporal_comparison(node: exp.Expression) -> bool:
    if isinstance(node, exp.Between):
        return _is_date_literal(node.args.get("low")) or _is_date_literal(node.args.get("high"))

    if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
        return _is_date_literal(node.left) or _is_date_literal(node.right)

    return False


def _is_in_coalesce(col: exp.Column) -> bool:
    parent = col.parent
    while parent:
        if isinstance(parent, exp.Coalesce):
            return True
        parent = parent.parent
    return False


def _has_null_check(
    tree: exp.Expression,
    table: str,
    col_name: str,
    aliases: Dict[str, str],
) -> bool:
    """Detect IS NOT NULL or equivalent patterns."""
    table_norm = _norm(table)
    col_norm = _norm(col_name)

    for node in tree.walk():

        # Handle: col IS NOT NULL
        if isinstance(node, exp.Is):
            if isinstance(node.expression, exp.Null) and node.args.get("negated"):
                col_node = node.this
            else:
                continue

        # Handle: NOT (col IS NULL)
        elif isinstance(node, exp.Not):
            inner = node.this
            if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
                col_node = inner.this
            else:
                continue
        else:
            continue

        if isinstance(col_node, exp.Column):
            t, c = resolve_table_col(col_node, aliases)
            t_norm = _norm(t) if t else None
            c_norm = _norm(c)

            if c_norm == col_norm:
                if not t_norm or not table_norm or t_norm == table_norm:
                    return True

    return False


def _is_target_table(
    table_norm: Optional[str],
    aliases: Dict[str, str],
) -> bool:
    """Ensure we are safely operating on condition_occurrence."""
    if table_norm:
        return table_norm == TABLE_NAME

    # No table specified → only allow if single-table query
    tables = {_norm(t) for t in aliases.values()}
    return len(tables) == 1 and TABLE_NAME in tables


# --- Detection -------------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str]]:
    issues = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not isinstance(node, tuple(TEMPORAL_OPERATORS)):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        if not _is_temporal_comparison(node):
            continue

        # Extract column
        col_node = None
        if isinstance(node, exp.Between):
            col_node = node.this
        else:
            if isinstance(node.left, exp.Column):
                col_node = node.left
            elif isinstance(node.right, exp.Column):
                col_node = node.right

        if not isinstance(col_node, exp.Column):
            continue

        table, col_name = resolve_table_col(col_node, aliases)
        table_norm = _norm(table)
        col_norm = _norm(col_name)

        if not _is_target_table(table_norm, aliases):
            continue

        if col_norm not in TEMPORAL_COLUMNS:
            continue

        config = TEMPORAL_COLUMNS[col_norm]

        if not config["nullable"]:
            continue

        if _is_in_coalesce(col_node):
            continue

        check_table = table_norm if table_norm else TABLE_NAME
        if _has_null_check(tree, check_table, col_norm, aliases):
            continue

        key = f"{col_norm}|{node.sql()}"
        if key in seen:
            continue
        seen.add(key)

        preferred = config["preferred"]
        description = config["description"]

        message = (
            f"Temporal filter uses {col_norm} ({description}). "
            f"Consider using {preferred} instead, which is always populated."
        )

        issues.append((col_norm, message))

    return issues


# --- Rule ------------------------------------------------------------------

@register
class ConditionStartDateTemporalValidationRule(Rule):
    """Validate safe temporal filtering in condition_occurrence."""

    rule_id = "semantic.condition_start_date_temporal_validation"
    name = "Condition Temporal Column Validation"
    description = (
        "Temporal queries on condition_occurrence should use condition_start_date "
        "(NOT NULL) instead of nullable datetime columns to avoid missing records."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Use condition_start_date or COALESCE(nullable_column, condition_start_date)"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for col_name, message in issues:
                config = TEMPORAL_COLUMNS[col_name]

                violations.append(
                    self.create_violation(
                        severity=Severity.WARNING,
                        message=message,
                        suggested_fix=(
                            f"Replace {col_name} with {config['preferred']} "
                            f"or use COALESCE({col_name}, {config['preferred']})"
                        ),
                        details={
                            "column": col_name,
                            "preferred_column": config["preferred"],
                            "table": TABLE_NAME,
                        },
                    )
                )

        return violations


__all__ = ["ConditionStartDateTemporalValidationRule"]

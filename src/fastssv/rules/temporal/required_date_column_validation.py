"""Required Date Column Validation Rule.

OMOP semantic rules CLIN_010, CLIN_015, CLIN_030, CLIN_035:
Temporal queries on clinical tables should use required (NOT NULL) date columns
rather than optional nullable columns (datetime variants, end dates, etc.).

The Problem:
    Many clinical tables have multiple temporal columns with different nullability:
    - A required date column (NOT NULL, always populated)
    - Optional datetime columns (may be NULL)
    - Optional end date columns (may be NULL for ongoing events)

    Using nullable columns for temporal filtering can silently exclude records
    where those columns are NULL, leading to incomplete result sets.

Covered Tables and Columns:
    condition_occurrence:
        - condition_start_date: Required (NOT NULL)
        - condition_start_datetime: Optional (nullable)
        - condition_end_date: Optional (nullable, often NULL for ongoing conditions)

    drug_exposure:
        - drug_exposure_start_date: Required (NOT NULL)
        - drug_exposure_start_datetime: Optional (nullable)
        - drug_exposure_end_date: Optional (nullable)

    measurement:
        - measurement_date: Required (NOT NULL)
        - measurement_datetime: Optional (nullable)
        - measurement_time: Optional (nullable)

    observation:
        - observation_date: Required (NOT NULL)
        - observation_datetime: Optional (nullable)

Example impact:
    -- BAD: Uses nullable column
    SELECT COUNT(*) FROM drug_exposure
    WHERE drug_exposure_end_date BETWEEN '2023-01-01' AND '2023-12-31'
    -- May exclude records where end_date is NULL

    -- GOOD: Uses required column
    SELECT COUNT(*) FROM drug_exposure
    WHERE drug_exposure_start_date BETWEEN '2023-01-01' AND '2023-12-31'
    -- Includes all records (start_date is always populated)

Correct patterns (no violation):
    -- Use required date column
    WHERE condition_start_date BETWEEN '2023-01-01' AND '2023-12-31'

    -- Or use COALESCE if datetime precision is needed
    WHERE COALESCE(condition_start_datetime, condition_start_date) > '2023-01-01'

    -- Or explicitly handle NULLs
    WHERE condition_start_datetime > '2023-01-01'
      AND condition_start_datetime IS NOT NULL
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

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}:\d{2}")

TABLE_CONFIGS = {
    "condition_occurrence": {
        "required": "condition_start_date",
        "nullable": ["condition_start_datetime", "condition_end_date"],
        "description": "condition records",
    },
    "drug_exposure": {
        "required": "drug_exposure_start_date",
        "nullable": ["drug_exposure_start_datetime", "drug_exposure_end_date"],
        "description": "drug exposure records",
    },
    "measurement": {
        "required": "measurement_date",
        "nullable": ["measurement_datetime", "measurement_time"],
        "description": "measurement records",
    },
    "observation": {
        "required": "observation_date",
        "nullable": ["observation_datetime"],
        "description": "observation records",
    },
}

TEMPORAL_OPERATORS = {
    exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ, exp.Between
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_date_literal(node: exp.Expression) -> bool:
    """Robust detection of date/time expressions."""
    if node is None:
        return False

    if isinstance(node, exp.Literal):
        val = str(node.this).strip("'\"")
        return bool(DATE_PATTERN.match(val) or TIME_PATTERN.match(val))

    if isinstance(node, exp.CurrentDate):
        return True

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
    """Detect IS NOT NULL and equivalent patterns."""
    table_norm = _norm(table)
    col_norm = _norm(col_name)

    for node in tree.walk():

        # col IS NOT NULL
        if isinstance(node, exp.Is):
            if isinstance(node.expression, exp.Null) and node.args.get("negated"):
                col_node = node.this
            else:
                continue

        # NOT (col IS NULL)
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


def _resolve_target_table(
    table_norm: Optional[str],
    aliases: Dict[str, str],
) -> Optional[str]:
    """Safely resolve which table config applies."""
    if table_norm:
        return table_norm if table_norm in TABLE_CONFIGS else None

    # No table specified → only allow if single-table query
    tables = {_norm(t) for t in aliases.values()}

    if len(tables) != 1:
        return None

    only_table = list(tables)[0]
    return only_table if only_table in TABLE_CONFIGS else None


# --- Detection -------------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str]]:
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

        target_table = _resolve_target_table(table_norm, aliases)
        if not target_table:
            continue

        config = TABLE_CONFIGS[target_table]

        if col_norm not in config["nullable"]:
            continue

        if _is_in_coalesce(col_node):
            continue

        check_table = table_norm if table_norm else target_table
        if _has_null_check(tree, check_table, col_norm, aliases):
            continue

        key = f"{target_table}|{col_norm}|{node.sql()}"
        if key in seen:
            continue
        seen.add(key)

        required_col = config["required"]
        table_desc = config["description"]

        message = (
            f"Temporal filter uses {col_norm} (nullable) on {target_table}. "
            f"Consider using {required_col} instead, which is always populated "
            f"and won't silently exclude {table_desc}."
        )

        issues.append((target_table, col_norm, message))

    return issues


# --- Rule ------------------------------------------------------------------

@register
class RequiredDateColumnValidationRule(Rule):
    """Validate safe temporal filtering across OMOP clinical tables."""

    rule_id = "temporal.required_date_column_validation"
    name = "Required Date Column Validation"
    description = (
        "Temporal queries on clinical tables should use required (NOT NULL) date "
        "columns instead of nullable columns to avoid silently excluding records."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Use required date columns, COALESCE, or explicit IS NOT NULL checks"
    )
    long_description = (
        "Each OMOP clinical table has at least one required (NOT NULL) date "
        "column and one or more optional end-date columns. Filtering on a "
        "nullable column (e.g. drug_exposure_end_date instead of "
        "drug_exposure_start_date) silently excludes every row where that "
        "column happens to be null, often a sizeable fraction of the data. "
        "Prefer the required column for the primary temporal filter; reach "
        "for the nullable ones only when their meaning is specifically what "
        "you need (duration calculations, end-of-period cohorts)."
    )
    example_bad = (
        "SELECT person_id\n"
        "FROM drug_exposure\n"
        "WHERE drug_exposure_end_date >= DATE '2023-01-01';"
    )
    example_good = (
        "SELECT person_id\n"
        "FROM drug_exposure\n"
        "WHERE drug_exposure_start_date >= DATE '2023-01-01';"
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

            for table_name, col_name, message in issues:
                config = TABLE_CONFIGS[table_name]
                required_col = config["required"]

                violations.append(
                    self.create_violation(
                        severity=Severity.WARNING,
                        message=message,
                        suggested_fix=(
                            f"Replace {col_name} with {required_col}, "
                            f"use COALESCE({col_name}, {required_col}), "
                            f"or add '{col_name} IS NOT NULL'"
                        ),
                        details={
                            "column": col_name,
                            "preferred_column": required_col,
                            "table": table_name,
                        },
                    )
                )

        return violations


__all__ = ["RequiredDateColumnValidationRule"]

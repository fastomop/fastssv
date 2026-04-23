"""Datetime BETWEEN with Date Literal Rule.

GAP_005: between_inclusive_both_ends_with_datetime

SQL BETWEEN is inclusive on both ends. When used with datetime columns and date
literals (without time component), it creates a subtle data loss bug.

The Problem:
    BETWEEN with datetime columns and date literals excludes non-midnight times
    on the end date:

    WHERE measurement_datetime BETWEEN '2023-01-01' AND '2023-01-31'
    -- '2023-01-31' is interpreted as '2023-01-31 00:00:00'
    -- Excludes: '2023-01-31 08:30:00', '2023-01-31 23:59:59', etc.
    -- SILENT DATA LOSS!

    This is a common mistake that's hard to catch because:
    - Query executes without error
    - Returns results (just incomplete)
    - Easy to miss in testing

OMOP Context:
    OMOP CDM has parallel DATE and DATETIME columns:

    Datetime columns (affected):
    - condition_start_datetime, condition_end_datetime
    - drug_exposure_start_datetime, drug_exposure_end_datetime
    - measurement_datetime
    - observation_datetime
    - visit_start_datetime, visit_end_datetime
    - procedure_datetime
    - device_exposure_start_datetime, device_exposure_end_datetime

    Date columns (safe with BETWEEN):
    - condition_start_date, drug_exposure_start_date, etc.

Violation patterns:
    WHERE measurement_datetime BETWEEN '2023-01-01' AND '2023-01-31'
    WHERE condition_start_datetime BETWEEN '2020-01-01' AND '2020-12-31'
    WHERE visit_start_datetime BETWEEN '2022-06-01' AND '2022-06-30'

Correct patterns:
    -- Option 1: Use >= and < (RECOMMENDED)
    WHERE measurement_datetime >= '2023-01-01'
      AND measurement_datetime < '2023-02-01'

    -- Option 2: Include time component in end literal
    WHERE measurement_datetime BETWEEN '2023-01-01' AND '2023-01-31 23:59:59.999'

    -- Option 3: Use corresponding DATE column
    WHERE measurement_date BETWEEN '2023-01-01' AND '2023-01-31'
"""

from typing import Dict, List, Optional, Set
import re

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    parse_sql,
    resolve_table_col,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

DATETIME_COLUMNS: Set[str] = {
    "condition_start_datetime",
    "condition_end_datetime",
    "drug_exposure_start_datetime",
    "drug_exposure_end_datetime",
    "measurement_datetime",
    "observation_datetime",
    "visit_start_datetime",
    "visit_end_datetime",
    "procedure_datetime",
    "device_exposure_start_datetime",
    "device_exposure_end_datetime",
    "specimen_datetime",
    "note_datetime",
}


DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{4}/\d{2}/\d{2}$"),
    re.compile(r"^\d{8}$"),
]


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return x.lower() if x else None


def _is_datetime_column(col_name: Optional[str]) -> bool:
    if not col_name:
        return False
    col = _norm(col_name)
    return col in DATETIME_COLUMNS or col.endswith("_datetime")


def _is_date_only(val: str) -> bool:
    return any(p.match(val) for p in DATE_PATTERNS)


def _extract_date_literal(node: exp.Expression) -> Optional[str]:
    """Extract date literal WITHOUT time component."""

    # Literal: '2023-01-31'
    if isinstance(node, exp.Literal):
        val = str(node.this).strip("'\"")
        return val if _is_date_only(val) else None

    # DATE '2023-01-31'
    if isinstance(node, exp.Date):
        lit = next(node.find_all(exp.Literal), None)
        if lit:
            val = str(lit.this)
            return val if _is_date_only(val) else None

    # TIMESTAMP '...' → reject if contains time
    if isinstance(node, exp.Timestamp):
        lit = next(node.find_all(exp.Literal), None)
        if lit:
            val = str(lit.this)
            return val if _is_date_only(val) else None

    # CAST(...)
    if isinstance(node, exp.Cast):
        return _extract_date_literal(node.this)

    return None


def _extract_column_name(node: exp.Expression, aliases: Dict[str, str]) -> Optional[str]:
    """Extract underlying column name from expression."""
    for col in node.find_all(exp.Column):
        _, col_name = resolve_table_col(col, aliases)
        if col_name:
            return col_name
    return None


def _build_aliases(tree: exp.Expression) -> Dict[str, str]:
    aliases: Dict[str, str] = {}

    for table in tree.find_all(exp.Table):
        alias_expr = table.args.get("alias")
        alias_name = alias_expr.name if alias_expr else table.name
        aliases[alias_name] = table.name

    return aliases


# --- Rule ------------------------------------------------------------------

@register
class DatetimeBetweenDateLiteralRule(Rule):
    """Detect BETWEEN on datetime columns with date-only literals."""

    rule_id = "temporal.datetime_between_date_literal"
    name = "Datetime BETWEEN with Date Literal"

    description = (
        "BETWEEN on datetime columns using date-only literals causes data loss "
        "because the end date excludes non-midnight times."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use >= start AND < next_day, or include time in end literal, "
        "or use *_date column instead."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = _build_aliases(tree)

            # Collect BETWEEN nodes (including root safety)
            betweens = list(tree.find_all(exp.Between))
            if isinstance(tree, exp.Between):
                betweens.append(tree)

            for between in betweens:
                # Optional: restrict to WHERE/JOIN context
                if not is_in_where_or_join_clause(between):
                    continue

                col_name = _extract_column_name(between.this, aliases)

                if not _is_datetime_column(col_name):
                    continue

                low = between.args.get("low")
                high = between.args.get("high")

                if not low or not high:
                    continue

                low_val = _extract_date_literal(low)
                high_val = _extract_date_literal(high)

                if not (low_val and high_val):
                    continue

                key = f"{col_name}|{low_val}|{high_val}"
                if key in seen:
                    continue
                seen.add(key)

                violations.append(
                    self.create_violation(
                        message=(
                            f"BETWEEN on datetime column '{col_name}' uses date-only "
                            f"literals ('{low_val}' AND '{high_val}'), excluding "
                            f"non-midnight times on the end date."
                        ),
                        severity=self.severity,
                        suggested_fix=(
                            f"Use: {col_name} >= '{low_val}' AND {col_name} < 'next_day', "
                            "or include time in end literal."
                        ),
                        details={
                            "column": col_name,
                            "start": low_val,
                            "end": high_val,
                            "issue": "datetime_between_date_literal",
                        },
                    )
                )

        return violations


__all__ = ["DatetimeBetweenDateLiteralRule"]

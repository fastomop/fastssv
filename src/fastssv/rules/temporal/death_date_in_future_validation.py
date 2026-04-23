"""Death Date In Future Validation Rule.

OMOP semantic rule CLIN_051: death_date_in_future

death.death_date should not be in the future relative to the data extraction date.
Queries filtering for death_date > CURRENT_DATE or death_date > far-future date
literals indicate data corruption or query logic errors.

The Problem:
    Death dates in the future are impossible and represent:
    - Data quality issues (incorrect death dates)
    - Data entry errors (wrong year, wrong century)
    - Logic errors in the query (wrong comparison operator)

Violation patterns:
    -- Comparing against CURRENT_DATE
    SELECT * FROM death WHERE death_date > CURRENT_DATE

    -- Hardcoded far-future date (unlikely to be valid)
    SELECT * FROM death WHERE death_date > '2030-01-01'

    -- Near-future dates might be warnings (data extraction lag)
    SELECT * FROM death WHERE death_date > '2025-01-01'

Correct patterns:
    -- Past or present dates
    SELECT * FROM death WHERE death_date <= CURRENT_DATE

    -- Reasonable date range
    SELECT * FROM death WHERE death_date BETWEEN '2020-01-01' AND '2023-12-31'
"""

from typing import List, Dict, Set, Optional
from datetime import datetime

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


DEATH_TABLE = "death"
DEATH_DATE = "death_date"
DEATH_DATETIME = "death_datetime"

CURRENT_YEAR = datetime.now().year
FAR_FUTURE_THRESHOLD_YEAR = CURRENT_YEAR + 10


def _norm(x: str) -> str:
    return normalize_name(x) if x else ""


def _is_in_or(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Or):
            return True
        parent = parent.parent
    return False


def _extract_date_literal_year(node: exp.Expression) -> Optional[int]:
    if isinstance(node, exp.Literal):
        date_str = str(node.this).strip("'\"")
        for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]:
            try:
                return datetime.strptime(date_str, fmt).year
            except ValueError:
                continue

    if isinstance(node, (exp.Date, exp.Timestamp)):
        for lit in node.find_all(exp.Literal):
            year = _extract_date_literal_year(lit)
            if year is not None:
                return year

    return None


def _is_current_date(node: exp.Expression) -> bool:
    if isinstance(node, (exp.CurrentDate, exp.CurrentTimestamp)):
        return True

    if isinstance(node, exp.Anonymous):
        name = _norm(node.name if hasattr(node, "name") else str(node.this))
        if name in {"now", "getdate", "sysdate", "current_date"}:
            return True

    # recursive fallback
    for sub in node.walk():
        if isinstance(sub, (exp.CurrentDate, exp.CurrentTimestamp)):
            return True

    return False


def _is_death_column(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    if not isinstance(node, exp.Column):
        return False

    table, col = resolve_table_col(node, aliases)

    # Handle explicit table references
    table_norm = _norm(table)
    if table_norm == DEATH_TABLE:
        return _norm(col) in {DEATH_DATE, DEATH_DATETIME}

    # Handle implicit table references (no table prefix)
    # If the column name matches and there's only one table in the FROM clause
    if not table_norm:
        col_norm = _norm(col)
        if col_norm in {DEATH_DATE, DEATH_DATETIME}:
            # Check if the only table in use is the death table
            tables = {_norm(t) for t in aliases.values() if t}
            if len(tables) == 1 and tables == {DEATH_TABLE}:
                return True

    return False


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    violations: List[str] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_or(node):
            continue

        # --- Comparisons ---
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            left_is_death = _is_death_column(node.this, aliases)
            right_is_death = _is_death_column(node.expression, aliases)

            # We only care about comparisons involving death_date
            if not left_is_death and not right_is_death:
                continue

            # Determine which side is the death column and which is the comparator
            if left_is_death:
                death_side = "left"
                comparator = node.expression
            else:
                death_side = "right"
                comparator = node.this

            # Skip if both sides are death columns
            if left_is_death and right_is_death:
                continue

            # Check for problematic comparisons
            # VIOLATION: death_date > CURRENT_DATE or death_date >= CURRENT_DATE
            if death_side == "left" and _is_current_date(comparator):
                if isinstance(node, (exp.GT, exp.GTE)):
                    key = "future_vs_current"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            "death_date is compared to CURRENT_DATE or today's date, "
                            "indicating potential future death dates."
                        )

            # VIOLATION: CURRENT_DATE > death_date or CURRENT_DATE >= death_date (inverted)
            elif death_side == "right" and _is_current_date(comparator):
                if isinstance(node, (exp.LT, exp.LTE)):
                    key = "future_vs_current"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            "death_date is compared to CURRENT_DATE or today's date, "
                            "indicating potential future death dates."
                        )

            # Check for future year literals
            year = _extract_date_literal_year(comparator)
            if year is not None and year > FAR_FUTURE_THRESHOLD_YEAR:
                # VIOLATION: death_date > '2050-01-01' or death_date >= '2050-01-01'
                if death_side == "left" and isinstance(node, (exp.GT, exp.GTE)):
                    key = f"future_{year}"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            f"death_date is compared to a far future date ({year}), "
                            "indicating potential data or logic issues."
                        )

                # VIOLATION: '2050-01-01' > death_date or '2050-01-01' >= death_date (inverted)
                elif death_side == "right" and isinstance(node, (exp.LT, exp.LTE)):
                    key = f"future_{year}"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            f"death_date is compared to a far future date ({year}), "
                            "indicating potential data or logic issues."
                        )

        # --- BETWEEN ---
        if isinstance(node, exp.Between):
            if _is_death_column(node.this, aliases):
                for bound in [node.args.get("low"), node.args.get("high")]:
                    year = _extract_date_literal_year(bound)
                    if year and year > FAR_FUTURE_THRESHOLD_YEAR:
                        violations.append(
                            f"death_date BETWEEN includes far future year {year}."
                        )

    return violations


@register
class DeathDateInFutureValidationRule(Rule):
    rule_id = "temporal.death_date_in_future_validation"
    name = "Death Date In Future Validation"

    description = (
        "Detects queries filtering for future death dates, indicating data or logic issues."
    )

    severity = Severity.WARNING
    suggested_fix = "Ensure death_date <= CURRENT_DATE"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, DEATH_TABLE):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["DeathDateInFutureValidationRule"]

"""End Before Start Validation Rule.

OMOP semantic rules CLIN_011, CLIN_045, OMOP_052, OMOP_244, OMOP_401, OMOP_515, OMOP_526, OMOP_529, OMOP_551:
Detects logically impossible date constraints where static filters force
end_date < start_date for the same record.

The Problem:
    Many OMOP tables have start_date and end_date columns representing
    temporal events. A query with static date filters that forces the end
    date to be before the start date is logically impossible and indicates
    a WHERE clause error.

Covered Tables and Column Pairs:
    condition_occurrence:
        - condition_start_date, condition_end_date (CLIN_011, OMOP_551)

    drug_exposure:
        - drug_exposure_start_date, drug_exposure_end_date (OMOP_515, OMOP_551)

    device_exposure:
        - device_exposure_start_date, device_exposure_end_date (OMOP_526)

    visit_occurrence:
        - visit_start_date, visit_end_date (OMOP_052, OMOP_551)

    visit_detail:
        - visit_detail_start_date, visit_detail_end_date (CLIN_045)

    cohort:
        - cohort_start_date, cohort_end_date (OMOP_529)

    episode:
        - episode_start_date, episode_end_date (OMOP_244)

    payer_plan_period:
        - payer_plan_period_start_date, payer_plan_period_end_date (OMOP_401)

Example Violations:
    -- ERROR: Start must be after June, but end must be before January
    WHERE condition_start_date > '2023-06-01'
      AND condition_end_date < '2023-01-01'

    -- ERROR: Start >= June 1, but end < June 1
    WHERE drug_exposure_start_date >= '2023-06-01'
      AND drug_exposure_end_date < '2023-06-01'

    -- ERROR: Start = June 15, but end = May 1
    WHERE visit_start_date = '2023-06-15'
      AND visit_end_date = '2023-05-01'

Valid Patterns (no violation):
    -- OK: Overlapping range is possible
    WHERE condition_start_date > '2023-01-01'
      AND condition_end_date < '2023-12-31'

    -- OK: Could start and end on same day
    WHERE visit_start_date >= '2023-06-01'
      AND visit_end_date >= '2023-06-01'

    -- OK: Dynamic comparison (not static date literals)
    WHERE condition_end_date < condition_start_date + INTERVAL '30 days'
"""

from datetime import datetime
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

TABLE_CONFIGS = {
    "condition_occurrence": {
        "start": "condition_start_date",
        "end": "condition_end_date",
    },
    "drug_exposure": {
        "start": "drug_exposure_start_date",
        "end": "drug_exposure_end_date",
    },
    "device_exposure": {
        "start": "device_exposure_start_date",
        "end": "device_exposure_end_date",
    },
    "visit_occurrence": {
        "start": "visit_start_date",
        "end": "visit_end_date",
    },
    "visit_detail": {
        "start": "visit_detail_start_date",
        "end": "visit_detail_end_date",
    },
    "cohort": {
        "start": "cohort_start_date",
        "end": "cohort_end_date",
    },
    "episode": {
        "start": "episode_start_date",
        "end": "episode_end_date",
    },
    "payer_plan_period": {
        "start": "payer_plan_period_start_date",
        "end": "payer_plan_period_end_date",
    },
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_in_or(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Or):
            return True
        parent = parent.parent
    return False


def _parse_date_literal(node: exp.Expression) -> Optional[datetime]:
    """Extract datetime from SQL expression."""
    if node is None:
        return None

    if isinstance(node, exp.Literal):
        val = str(node.this).strip("'\"")
        if DATE_PATTERN.match(val):
            try:
                return datetime.strptime(val[:10], "%Y-%m-%d")
            except Exception:
                return None

    if isinstance(node, exp.Cast):
        return _parse_date_literal(node.this)

    if isinstance(node, exp.CurrentDate):
        return datetime.today()

    return None


def _resolve_table_safe(
    table: Optional[str],
    aliases: Dict[str, str],
) -> Optional[str]:
    table_norm = _norm(table)

    if table_norm:
        return table_norm if table_norm in TABLE_CONFIGS else None

    tables = {_norm(t) for t in aliases.values()}
    if len(tables) == 1:
        only = list(tables)[0]
        return only if only in TABLE_CONFIGS else None

    return None


# --- Constraint Model ------------------------------------------------------

class DateBounds:
    def __init__(self):
        self.min_date: Optional[datetime] = None
        self.max_date: Optional[datetime] = None
        self.min_inclusive: bool = True
        self.max_inclusive: bool = True
        self.has_constraints = False

    def add_gt(self, d: datetime, inclusive: bool):
        self.has_constraints = True
        if self.min_date is None or d > self.min_date:
            self.min_date = d
            self.min_inclusive = inclusive
        elif d == self.min_date:
            self.min_inclusive = self.min_inclusive and inclusive

    def add_lt(self, d: datetime, inclusive: bool):
        self.has_constraints = True
        if self.max_date is None or d < self.max_date:
            self.max_date = d
            self.max_inclusive = inclusive
        elif d == self.max_date:
            self.max_inclusive = self.max_inclusive and inclusive

    def add_eq(self, d: datetime):
        self.has_constraints = True
        self.min_date = d
        self.max_date = d
        self.min_inclusive = True
        self.max_inclusive = True

    def impossible_with(self, other: "DateBounds") -> bool:
        if not self.has_constraints or not other.has_constraints:
            return False

        if self.min_date and other.max_date:
            if self.min_date > other.max_date:
                return True
            if self.min_date == other.max_date:
                if not self.min_inclusive or not other.max_inclusive:
                    return True
        return False


# --- Detection -------------------------------------------------------------

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]):
    bounds: Dict[Tuple[str, str], DateBounds] = {}
    violations = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_or(node):
            continue

        # --- Column-to-column comparison (critical fix) ---
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            if isinstance(node.left, exp.Column) and isinstance(node.right, exp.Column):
                t1, c1 = resolve_table_col(node.left, aliases)
                t2, c2 = resolve_table_col(node.right, aliases)

                t1 = _resolve_table_safe(t1, aliases)
                t2 = _resolve_table_safe(t2, aliases)

                if not t1 or not t2 or t1 != t2:
                    continue

                config = TABLE_CONFIGS[t1]
                start = _norm(config["start"])
                end = _norm(config["end"])

                c1n, c2n = _norm(c1), _norm(c2)

                if c1n == start and c2n == end and isinstance(node, (exp.GT, exp.GTE)):
                    key = f"{t1}|direct"
                    if key not in seen:
                        seen.add(key)
                        violations.append((
                            t1,
                            f"{c1n} compared greater than {c2n}, forcing start > end"
                        ))

                if c2n == start and c1n == end and isinstance(node, (exp.LT, exp.LTE)):
                    key = f"{t1}|direct"
                    if key not in seen:
                        seen.add(key)
                        violations.append((
                            t1,
                            f"{c1n} compared less than {c2n}, forcing end < start"
                        ))

        # --- Column vs literal constraints ---
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
            pairs = [(node.left, node.right), (node.right, node.left)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                date_val = _parse_date_literal(val_node)
                if not date_val:
                    continue

                table, col = resolve_table_col(col_node, aliases)
                table = _resolve_table_safe(table, aliases)
                col = _norm(col)

                if not table or table not in TABLE_CONFIGS:
                    continue

                key = (table, col)
                if key not in bounds:
                    bounds[key] = DateBounds()

                if isinstance(node, exp.GT):
                    bounds[key].add_gt(date_val, False)
                elif isinstance(node, exp.GTE):
                    bounds[key].add_gt(date_val, True)
                elif isinstance(node, exp.LT):
                    bounds[key].add_lt(date_val, False)
                elif isinstance(node, exp.LTE):
                    bounds[key].add_lt(date_val, True)
                elif isinstance(node, exp.EQ):
                    bounds[key].add_eq(date_val)

        # --- BETWEEN ---
        if isinstance(node, exp.Between):
            if isinstance(node.this, exp.Column):
                low = _parse_date_literal(node.args.get("low"))
                high = _parse_date_literal(node.args.get("high"))

                if not low or not high:
                    continue

                table, col = resolve_table_col(node.this, aliases)
                table = _resolve_table_safe(table, aliases)
                col = _norm(col)

                if not table:
                    continue

                key = (table, col)
                if key not in bounds:
                    bounds[key] = DateBounds()

                bounds[key].add_gt(low, True)
                bounds[key].add_lt(high, True)

    # --- Evaluate contradictions ---
    for table, config in TABLE_CONFIGS.items():
        start = _norm(config["start"])
        end = _norm(config["end"])

        if (table, start) not in bounds or (table, end) not in bounds:
            continue

        if bounds[(table, start)].impossible_with(bounds[(table, end)]):
            key = f"{table}|range"
            if key in seen:
                continue
            seen.add(key)

            violations.append((
                table,
                f"Impossible date constraint on {table}: filters force {start} > {end}"
            ))

    return violations


# --- Rule ------------------------------------------------------------------

@register
class EndBeforeStartValidationRule(Rule):
    rule_id = "temporal.end_before_start_validation"
    name = "End Before Start Validation"
    description = "Detects impossible constraints where start_date > end_date"
    severity = Severity.ERROR
    suggested_fix = "Ensure start_date <= end_date in filters"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for table, msg in issues:
                cfg = TABLE_CONFIGS[table]

                violations.append(
                    self.create_violation(
                        severity=Severity.ERROR,
                        message=msg,
                        suggested_fix=(
                            f"Ensure {cfg['start']} <= {cfg['end']} "
                            f"and review conflicting filters"
                        ),
                        details={
                            "table": table,
                            "start_column": cfg["start"],
                            "end_column": cfg["end"],
                        },
                    )
                )

        return violations


__all__ = ["EndBeforeStartValidationRule"]
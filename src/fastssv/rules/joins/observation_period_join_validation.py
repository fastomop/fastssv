"""Observation Period Join Validation Rule.

JOIN rule JOIN_023:
observation_period joins to clinical tables via person_id AND a date overlap constraint.
A join on person_id alone produces multiple observation_period rows per event (since
patients can have multiple observation periods), inflating results.

The Problem:
    Patients can have multiple observation_periods (gaps in data, enrollment periods).
    Joining clinical tables to observation_period on person_id alone creates a Cartesian
    product where each clinical event appears once per observation period.

    Example:
    - Patient has 3 observation periods
    - Patient has 5 conditions
    - JOIN on person_id alone returns 15 rows (5 × 3) instead of 5

Why this is wrong:
    Joining on person_id alone causes:
    - Result inflation (duplicate rows for each observation period)
    - Incorrect counts and aggregations
    - Wrong analytical results
    - Cartesian product that is almost always unintended

Violation pattern:
    SELECT co.*
    FROM condition_occurrence co
    JOIN observation_period op ON co.person_id = op.person_id

Correct pattern:
    SELECT co.*
    FROM condition_occurrence co
    JOIN observation_period op
      ON co.person_id = op.person_id
      AND co.condition_start_date BETWEEN op.observation_period_start_date
                                     AND op.observation_period_end_date
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

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

logger = logging.getLogger(__name__)


# --- Constants -------------------------------------------------------------

OBSERVATION_PERIOD_TABLE = "observation_period"
PERSON_ID = "person_id"

CLINICAL_TABLES_WITH_DATES = {
    "condition_occurrence": ["condition_start_date", "condition_end_date"],
    "drug_exposure": ["drug_exposure_start_date", "drug_exposure_end_date"],
    "procedure_occurrence": ["procedure_date"],
    "measurement": ["measurement_date"],
    "observation": ["observation_date"],
    "visit_occurrence": ["visit_start_date", "visit_end_date"],
    "visit_detail": ["visit_detail_start_date", "visit_detail_end_date"],
    "device_exposure": ["device_exposure_start_date", "device_exposure_end_date"],
    "specimen": ["specimen_date"],
    "note": ["note_date"],
}


# --- Normalized ------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


OP_NORM = _norm(OBSERVATION_PERIOD_TABLE)
PERSON_ID_NORM = _norm(PERSON_ID)

CLINICAL_TABLES_NORM: Dict[str, Set[str]] = {
    _norm(t): {_norm(c) for c in cols} for t, cols in CLINICAL_TABLES_WITH_DATES.items()
}


# --- Helpers ---------------------------------------------------------------


def _is_op_date(col: str) -> bool:
    return _norm(col) in {
        _norm("observation_period_start_date"),
        _norm("observation_period_end_date"),
    }


def _is_clinical_date(table: str, col: str) -> bool:
    return _norm(table) in CLINICAL_TABLES_NORM and _norm(col) in CLINICAL_TABLES_NORM[_norm(table)]


def _extract_join_aliases(on: exp.Expression) -> Set[str]:
    aliases = set()
    for col in on.find_all(exp.Column):
        if col.table:
            aliases.add(_norm(col.table))
    return aliases


def _has_person_id_join(on: exp.Expression, aliases: Dict[str, str], a1: str, a2: str) -> bool:
    for eq in on.find_all(exp.EQ):
        if not isinstance(eq.left, exp.Column) or not isinstance(eq.right, exp.Column):
            continue

        lt, lc = resolve_table_col(eq.left, aliases)
        rt, rc = resolve_table_col(eq.right, aliases)

        if _norm(lc) == PERSON_ID_NORM and _norm(rc) == PERSON_ID_NORM:
            if {_norm(eq.left.table), _norm(eq.right.table)} == {a1, a2}:
                return True

    return False


def _has_strict_overlap(
    tree: exp.Expression,
    aliases: Dict[str, str],
    op_alias: str,
    clinical_alias: str,
    clinical_table: str,
) -> bool:
    op_alias = _norm(op_alias)
    clinical_alias = _norm(clinical_alias)

    has_lower = False
    has_upper = False

    # BETWEEN
    for between in tree.find_all(exp.Between):
        if not is_in_where_or_join_clause(between):
            continue

        if not isinstance(between.this, exp.Column):
            continue

        # Use direct table reference (alias), not resolved table name
        col_alias = _norm(between.this.table) if between.this.table else None
        _, c = resolve_table_col(between.this, aliases)
        if col_alias != clinical_alias or not _is_clinical_date(clinical_table, c):
            continue

        low = between.args.get("low")
        high = between.args.get("high")

        if isinstance(low, exp.Column) and isinstance(high, exp.Column):
            low_alias = _norm(low.table) if low.table else None
            high_alias = _norm(high.table) if high.table else None
            _, lc = resolve_table_col(low, aliases)
            _, hc = resolve_table_col(high, aliases)

            if low_alias == op_alias and _is_op_date(lc) and high_alias == op_alias and _is_op_date(hc):
                return True

    # Comparisons
    for comp in tree.find_all(exp.GTE, exp.LTE, exp.GT, exp.LT):
        if not is_in_where_or_join_clause(comp):
            continue

        if not isinstance(comp.left, exp.Column) or not isinstance(comp.right, exp.Column):
            continue

        # Use direct table references (aliases), not resolved table names
        left_alias = _norm(comp.left.table) if comp.left.table else None
        right_alias = _norm(comp.right.table) if comp.right.table else None
        _, lc = resolve_table_col(comp.left, aliases)
        _, rc = resolve_table_col(comp.right, aliases)

        lc, rc = _norm(lc), _norm(rc)

        # clinical >= op_start
        if (
            left_alias == clinical_alias
            and _is_clinical_date(clinical_table, lc)
            and right_alias == op_alias
            and _is_op_date(rc)
        ):
            has_lower = True

        # clinical <= op_end
        if (
            right_alias == clinical_alias
            and _is_clinical_date(clinical_table, rc)
            and left_alias == op_alias
            and _is_op_date(lc)
        ):
            has_upper = True

        # reversed cases: op_start <= clinical
        if (
            right_alias == clinical_alias
            and _is_clinical_date(clinical_table, rc)
            and left_alias == op_alias
            and _is_op_date(lc)
        ):
            has_lower = True

        # reversed cases: op_end >= clinical
        if (
            left_alias == clinical_alias
            and _is_clinical_date(clinical_table, lc)
            and right_alias == op_alias
            and _is_op_date(rc)
        ):
            has_upper = True

    return has_lower and has_upper


# --- Rule ------------------------------------------------------------------


@register
class ObservationPeriodJoinValidationRule(Rule):
    rule_id = "joins.observation_period_join_validation"
    name = "Observation Period Join Requires Date Overlap"

    description = "observation_period joins to clinical tables must include date overlap constraints."

    severity = Severity.WARNING

    suggested_fix = "ADD: `AND <clinical>.<event_date> BETWEEN op.observation_period_start_date AND op.observation_period_end_date` (date-overlap) on every observation_period join, in addition to the person_id linkage."
    example_bad = "SELECT * FROM condition_occurrence co\nJOIN observation_period op ON co.person_id = op.person_id;"
    example_good = (
        "SELECT * FROM condition_occurrence co\n"
        "JOIN observation_period op\n"
        "  ON co.person_id = op.person_id\n"
        "  AND co.condition_start_date BETWEEN op.observation_period_start_date\n"
        "                                  AND op.observation_period_end_date;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        if OBSERVATION_PERIOD_TABLE not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            logger.warning(f"[{self.rule_id}] SQL parse error: {err}")
            return []

        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, str]] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            # Find observation_period aliases
            op_aliases = {_norm(a) for a, t in aliases.items() if _norm(t) == OP_NORM}

            if not op_aliases:
                continue

            # Find clinical aliases
            clinical_aliases = {_norm(a): _norm(t) for a, t in aliases.items() if _norm(t) in CLINICAL_TABLES_NORM}

            if not clinical_aliases:
                continue

            for join in tree.find_all(exp.Join):
                on = join.args.get("on")
                if not on:
                    continue

                join_aliases = _extract_join_aliases(on)

                # Check if OP involved
                op_in_join = op_aliases & join_aliases
                if not op_in_join:
                    continue

                op_alias = next(iter(op_in_join))

                # Find clinical partner
                clinical_alias = next((a for a in join_aliases if a in clinical_aliases), None)

                if not clinical_alias:
                    continue

                # Ensure person_id join
                if not _has_person_id_join(on, aliases, op_alias, clinical_alias):
                    continue

                key = (op_alias, clinical_alias)
                if key in seen:
                    continue
                seen.add(key)

                clinical_table = clinical_aliases[clinical_alias]

                if not _has_strict_overlap(tree, aliases, op_alias, clinical_alias, clinical_table):
                    violations.append(
                        self.create_violation(
                            message=(f"observation_period joined to {clinical_table} without date overlap constraint."),
                            severity=self.severity,
                        )
                    )

        return violations


__all__ = ["ObservationPeriodJoinValidationRule"]

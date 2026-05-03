"""Visit Event Temporal Validation Rule.

OMOP semantic rule CLIN_041: visit_occurrence_event_before_visit_start

When clinical events (conditions, drugs, measurements, procedures) are linked
to visits via visit_occurrence_id, the event dates should fall within the visit
date range. An event date before visit_start_date indicates a join error, data
quality issue, or logic error.

The Problem:
    Clinical events are linked to visits via visit_occurrence_id. These events
    should occur during the visit:
    - event_date >= visit_start_date
    - event_date <= visit_end_date (if not NULL)

    If a query filters for event_date < visit_start_date, this suggests:
    1. Wrong visit_occurrence_id (join error)
    2. Data quality issue in the source data
    3. Logic error in the query

Violation pattern:
    SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE co.condition_start_date < vo.visit_start_date
    -- Condition occurred before the visit started!

Correct patterns:
    -- Option 1: Filter for events during visit
    SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE co.condition_start_date >= vo.visit_start_date
      AND co.condition_start_date <= COALESCE(vo.visit_end_date, CURRENT_DATE)

    -- Option 2: Remove temporal filter if analyzing data quality
    SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    -- No temporal filter - will include all linked events
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Configuration ---------------------------------------------------------

VISIT_OCCURRENCE_TABLE = "visit_occurrence"
VISIT_START_DATE = "visit_start_date"
VISIT_OCCURRENCE_ID = "visit_occurrence_id"

CLINICAL_EVENT_TABLES = {
    "condition_occurrence": ["condition_start_date", "condition_end_date"],
    "drug_exposure": ["drug_exposure_start_date", "drug_exposure_end_date"],
    "measurement": ["measurement_date", "measurement_datetime"],
    "procedure_occurrence": ["procedure_date", "procedure_datetime"],
    "observation": ["observation_date", "observation_datetime"],
    "device_exposure": ["device_exposure_start_date", "device_exposure_end_date"],
    "specimen": ["specimen_date", "specimen_datetime"],
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


def _is_visit_start_date(node: exp.Column, aliases: Dict[str, str]) -> bool:
    table, col = resolve_table_col(node, aliases)

    if _norm(col) != _norm(VISIT_START_DATE):
        return False

    if table:
        return _norm(table) == _norm(VISIT_OCCURRENCE_TABLE)

    return VISIT_OCCURRENCE_TABLE in {_norm(t) for t in aliases.values()}


def _is_event_date_column(
    node: exp.Column,
    aliases: Dict[str, str],
) -> Optional[Tuple[str, str]]:
    table, col = resolve_table_col(node, aliases)
    if not col:
        return None

    col_norm = _norm(col)

    for clinical_table, cols in CLINICAL_EVENT_TABLES.items():
        if col_norm not in {_norm(c) for c in cols}:
            continue

        if table:
            if _norm(table) == _norm(clinical_table):
                return (clinical_table, col)
        else:
            if clinical_table in {_norm(t) for t in aliases.values()}:
                return (clinical_table, col)

    return None


def _has_valid_join(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """
    Ensure there is a proper join between clinical tables and visit_occurrence
    via visit_occurrence_id.
    """
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left, right = eq.this, eq.expression

            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue

            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            if not (lt and lc and rt and rc):
                continue

            if _norm(lc) != VISIT_OCCURRENCE_ID or _norm(rc) != VISIT_OCCURRENCE_ID:
                continue

            if (_norm(lt) == VISIT_OCCURRENCE_TABLE and _norm(rt) in CLINICAL_EVENT_TABLES) or (
                _norm(rt) == VISIT_OCCURRENCE_TABLE and _norm(lt) in CLINICAL_EVENT_TABLES
            ):
                return True

    return False


# --- Detection -------------------------------------------------------------


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[str]:
    violations: List[str] = []
    seen: Set[str] = set()

    if not _has_valid_join(tree, aliases):
        return violations

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_or(node):
            continue

        # event_date < visit_start_date
        if isinstance(node, (exp.LT, exp.LTE)):
            left, right = node.this, node.expression

            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                event = _is_event_date_column(left, aliases)
                visit = _is_visit_start_date(right, aliases)

                if event and visit:
                    table, col = event
                    key = f"{table}|{col}|lt"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            f"{table}.{col} is filtered before visit_start_date. "
                            f"This may indicate a join mismatch, temporal inconsistency, "
                            f"or an intentional data quality check."
                        )

        # visit_start_date > event_date
        elif isinstance(node, (exp.GT, exp.GTE)):
            left, right = node.this, node.expression

            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                visit = _is_visit_start_date(left, aliases)
                event = _is_event_date_column(right, aliases)

                if visit and event:
                    table, col = event
                    key = f"{table}|{col}|gt"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            f"{table}.{col} occurs before visit_start_date. "
                            f"This may indicate a join mismatch, temporal inconsistency, "
                            f"or an intentional data quality check."
                        )

    return violations


# --- Rule ------------------------------------------------------------------


@register
class VisitEventTemporalValidationRule(Rule):
    """Validate temporal consistency between clinical events and visit start."""

    rule_id = "domain_specific.visit_event_temporal_validation"
    name = "Visit Event Temporal Validation"

    description = (
        "Detects when clinical events are filtered to occur before visit_start_date, "
        "which may indicate a join mismatch or temporal inconsistency."
    )

    severity = Severity.WARNING
    suggested_fix = "REPLACE: `<event>.<date> < <visit>.visit_start_date` WITH `<event>.<date> >= <visit>.visit_start_date`. Clinical events should occur on or after the visit's start; a violation usually means the wrong visit_occurrence_id was joined."
    example_bad = (
        "SELECT co.person_id FROM condition_occurrence co\n"
        "JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id\n"
        "WHERE co.condition_start_date < vo.visit_start_date;"
    )
    example_good = (
        "SELECT co.person_id FROM condition_occurrence co\n"
        "JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id\n"
        "WHERE co.condition_start_date >= vo.visit_start_date;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, VISIT_OCCURRENCE_TABLE):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        details={
                            "visit_table": VISIT_OCCURRENCE_TABLE,
                            "clinical_tables": list(CLINICAL_EVENT_TABLES.keys()),
                        },
                    )
                )

        return violations


__all__ = ["VisitEventTemporalValidationRule"]

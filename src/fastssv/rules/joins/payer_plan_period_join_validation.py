"""Payer Plan Period Join Validation Rule.

OMOP semantic rule JOIN_030:
payer_plan_period links to clinical events via person_id AND date overlap.
Joining only on person_id without date constraints pairs events with incorrect
payer periods (a patient may have multiple insurance periods).

The Problem:
    A patient can have multiple insurance coverage periods over time:
    - person_id = 12345, coverage from 2020-01-01 to 2020-12-31
    - person_id = 12345, coverage from 2021-01-01 to 2022-06-30
    - person_id = 12345, coverage from 2022-07-01 to 2024-12-31

    If you join only on person_id, a drug exposure on 2021-06-15 will match
    ALL THREE insurance periods, not just the active one!

Violation pattern:
    SELECT * FROM drug_exposure de
    JOIN payer_plan_period pp ON de.person_id = pp.person_id
    -- WRONG: Returns all payer periods for the patient, not just the active one!

Correct pattern:
    SELECT * FROM drug_exposure de
    JOIN payer_plan_period pp
      ON de.person_id = pp.person_id
      AND de.drug_exposure_start_date BETWEEN
          pp.payer_plan_period_start_date AND pp.payer_plan_period_end_date
    -- CORRECT: Only returns the payer period active during the drug exposure
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

PAYER_PLAN_PERIOD = "payer_plan_period"
PERSON_ID = "person_id"

CLINICAL_EVENT_TABLES = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "device_exposure",
    "visit_occurrence",
    "specimen",
    "note",
}

CLINICAL_DATE_COLUMNS: Dict[str, Set[str]] = {
    "condition_occurrence": {
        "condition_start_date",
        "condition_start_datetime",
        "condition_end_date",
        "condition_end_datetime",
    },
    "drug_exposure": {
        "drug_exposure_start_date",
        "drug_exposure_start_datetime",
        "drug_exposure_end_date",
        "drug_exposure_end_datetime",
    },
    "procedure_occurrence": {"procedure_date", "procedure_datetime"},
    "measurement": {"measurement_date", "measurement_datetime"},
    "observation": {"observation_date", "observation_datetime"},
    "device_exposure": {
        "device_exposure_start_date",
        "device_exposure_start_datetime",
        "device_exposure_end_date",
        "device_exposure_end_datetime",
    },
    "visit_occurrence": {
        "visit_start_date",
        "visit_start_datetime",
        "visit_end_date",
        "visit_end_datetime",
    },
    "specimen": {"specimen_date", "specimen_datetime"},
    "note": {"note_date", "note_datetime"},
}

PAYER_START = "payer_plan_period_start_date"
PAYER_END = "payer_plan_period_end_date"


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_payer(table: Optional[str]) -> bool:
    return _norm(table) == PAYER_PLAN_PERIOD


def _is_clinical(table: Optional[str]) -> bool:
    return _norm(table) in CLINICAL_EVENT_TABLES


def _is_person_id(col: Optional[str]) -> bool:
    return _norm(col) == PERSON_ID


def _is_clinical_date(table: Optional[str], col: Optional[str]) -> bool:
    t = _norm(table)
    c = _norm(col)
    if not t or not c:
        return False
    return c in CLINICAL_DATE_COLUMNS.get(t, set())


def _is_payer_start(col: Optional[str]) -> bool:
    return _norm(col) == PAYER_START


def _is_payer_end(col: Optional[str]) -> bool:
    return _norm(col) == PAYER_END


def _extract_conditions(tree: exp.Expression) -> List[exp.Expression]:
    """Extract relevant comparison conditions."""
    conds: List[exp.Expression] = []

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause:
            conds.extend(list(on_clause.find_all(exp.Expression)))

    where = tree.find(exp.Where)
    if where:
        conds.extend(list(where.find_all(exp.Expression)))

    return conds


# --- Temporal Logic --------------------------------------------------------


def _detect_temporal_overlap(
    conditions: List[exp.Expression],
    clinical_table: str,
    aliases: Dict[str, str],
) -> bool:
    """
    Require BOTH:
        clinical_date >= payer_start
        clinical_date <= payer_end
    OR:
        BETWEEN start AND end
    """

    lower_bound = False
    upper_bound = False
    between_valid = False

    for cond in conditions:
        # --- BETWEEN -------------------------------------------------------
        if isinstance(cond, exp.Between):
            this = cond.this
            low = cond.args.get("low")
            high = cond.args.get("high")

            if not isinstance(this, exp.Column):
                continue

            t, c = resolve_table_col(this, aliases)

            if not _is_clinical_date(t, c):
                continue

            # Ensure this date belongs to the clinical table we're checking
            if _norm(t) != clinical_table:
                continue

            if isinstance(low, exp.Column) and isinstance(high, exp.Column):
                _, low_col = resolve_table_col(low, aliases)
                _, high_col = resolve_table_col(high, aliases)

                if _is_payer_start(low_col) and _is_payer_end(high_col):
                    between_valid = True

        # --- Binary comparisons -------------------------------------------
        if isinstance(cond, (exp.GTE, exp.GT, exp.LTE, exp.LT)):
            if not isinstance(cond.this, exp.Column) or not isinstance(cond.expression, exp.Column):
                continue

            lt, lc = resolve_table_col(cond.this, aliases)
            rt, rc = resolve_table_col(cond.expression, aliases)

            # clinical >= payer_start
            if _is_clinical_date(lt, lc) and _norm(lt) == clinical_table and _is_payer_start(rc):
                lower_bound = True

            if _is_clinical_date(rt, rc) and _norm(rt) == clinical_table and _is_payer_start(lc):
                lower_bound = True

            # clinical <= payer_end
            if _is_clinical_date(lt, lc) and _norm(lt) == clinical_table and _is_payer_end(rc):
                upper_bound = True

            if _is_clinical_date(rt, rc) and _norm(rt) == clinical_table and _is_payer_end(lc):
                upper_bound = True

    return between_valid or (lower_bound and upper_bound)


# --- Detection -------------------------------------------------------------


def _detect(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[str]:
    violations: List[str] = []

    tables = {_norm(t.name) for t in tree.find_all(exp.Table) if t.name}

    if PAYER_PLAN_PERIOD not in tables:
        return []

    clinical_tables = [t for t in tables if t in CLINICAL_EVENT_TABLES]
    if not clinical_tables:
        return []

    conditions = _extract_conditions(tree)

    for clinical in clinical_tables:
        has_person_join = False

        for cond in conditions:
            if not isinstance(cond, exp.EQ):
                continue

            if not isinstance(cond.this, exp.Column) or not isinstance(cond.expression, exp.Column):
                continue

            lt, lc = resolve_table_col(cond.this, aliases)
            rt, rc = resolve_table_col(cond.expression, aliases)

            if (_norm(lt) == clinical and _is_person_id(lc) and _is_payer(rt) and _is_person_id(rc)) or (
                _is_payer(lt) and _is_person_id(lc) and _norm(rt) == clinical and _is_person_id(rc)
            ):
                has_person_join = True
                break

        if not has_person_join:
            continue

        has_overlap = _detect_temporal_overlap(conditions, clinical, aliases)

        if not has_overlap:
            violations.append(clinical)

    return violations


# --- Rule ------------------------------------------------------------------


@register
class PayerPlanPeriodJoinValidationRule(Rule):
    """
    Ensure payer_plan_period joins include proper temporal overlap.
    """

    rule_id = "joins.payer_plan_period_join_validation"
    name = "Payer Plan Period Join Validation"

    description = (
        "Ensures payer_plan_period joins to clinical tables include proper date overlap conditions, not just person_id."
    )

    severity = Severity.WARNING

    suggested_fix = "ADD: `AND <clinical>.<event_date> BETWEEN ppp.payer_plan_period_start_date AND ppp.payer_plan_period_end_date` (date-overlap) on every payer_plan_period join, in addition to person_id."
    example_bad = "SELECT * FROM condition_occurrence co\nJOIN payer_plan_period ppp ON co.person_id = ppp.person_id;"
    example_good = (
        "SELECT * FROM condition_occurrence co\n"
        "JOIN payer_plan_period ppp\n"
        "  ON co.person_id = ppp.person_id\n"
        "  AND co.condition_start_date BETWEEN ppp.payer_plan_period_start_date\n"
        "                                  AND ppp.payer_plan_period_end_date;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        if PAYER_PLAN_PERIOD not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            detected = _detect(tree, aliases)

            for clinical in detected:
                message = (
                    f"{clinical} joined to payer_plan_period without proper date overlap. "
                    f"This may incorrectly associate events with all payer periods."
                )

                violations.append(
                    self.create_violation(
                        message=message,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "missing_temporal_overlap",
                            "clinical_table": clinical,
                        },
                    )
                )

        return violations


__all__ = ["PayerPlanPeriodJoinValidationRule"]

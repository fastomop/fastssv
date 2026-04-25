"""Clinical Event Date Before 1900 Validation Rule.

OMOP semantic rule CLIN_054: clinical_event_date_before_1900

Clinical event dates should not be before 1900. This is the conventional minimum
threshold for medical records in OMOP CDM. Dates before 1900 indicate either data
corruption, data entry errors, or incorrect query logic.

This rule generalizes the concept from CLIN_006 (person.year_of_birth >= 1900)
to all clinical event date columns.

The Problem:
    Clinical event dates before 1900 are implausible and represent:
    - Data quality issues (incorrect event dates)
    - Data entry errors (wrong year, wrong century)
    - Logic errors in the query (accidentally filtering for ancient dates)

Clinical event tables covered:
    - condition_occurrence (condition_start_date, condition_end_date, etc.)
    - drug_exposure (drug_exposure_start_date, drug_exposure_end_date, etc.)
    - procedure_occurrence (procedure_date, procedure_datetime)
    - measurement (measurement_date, measurement_datetime)
    - observation (observation_date, observation_datetime)
    - visit_occurrence (visit_start_date, visit_end_date, etc.)
    - visit_detail (visit_detail_start_date, visit_detail_end_date, etc.)
    - device_exposure (device_exposure_start_date, device_exposure_end_date, etc.)
    - specimen (specimen_date, specimen_datetime)
    - note (note_date, note_datetime)
    - episode (episode_start_date, episode_end_date, etc.)

Violation patterns:
    SELECT * FROM condition_occurrence WHERE condition_start_date < '1900-01-01'
    SELECT * FROM drug_exposure WHERE drug_exposure_start_date <= '1899-12-31'
    SELECT * FROM procedure_occurrence WHERE YEAR(procedure_date) < 1900
    SELECT * FROM measurement WHERE measurement_date BETWEEN '1850-01-01' AND '1899-12-31'
    SELECT * FROM observation WHERE observation_date IN ('1880-01-01', '1890-01-01')

Correct patterns:
    SELECT * FROM condition_occurrence WHERE condition_start_date >= '1900-01-01'
    SELECT * FROM drug_exposure WHERE drug_exposure_start_date BETWEEN '1950-01-01' AND '2023-12-31'
    SELECT * FROM procedure_occurrence WHERE YEAR(procedure_date) >= 1900
"""


from typing import Dict, List, Optional, Set, Tuple
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


# --- Configuration ---------------------------------------------------------

CLINICAL_EVENT_TABLES_DATES: Dict[str, Set[str]] = {
    "condition_occurrence": {
        "condition_start_date", "condition_start_datetime",
        "condition_end_date", "condition_end_datetime",
    },
    "drug_exposure": {
        "drug_exposure_start_date", "drug_exposure_start_datetime",
        "drug_exposure_end_date", "drug_exposure_end_datetime",
    },
    "procedure_occurrence": {
        "procedure_date", "procedure_datetime",
    },
    "measurement": {
        "measurement_date", "measurement_datetime",
    },
    "observation": {
        "observation_date", "observation_datetime",
    },
    "visit_occurrence": {
        "visit_start_date", "visit_start_datetime",
        "visit_end_date", "visit_end_datetime",
    },
    "visit_detail": {
        "visit_detail_start_date", "visit_detail_start_datetime",
        "visit_detail_end_date", "visit_detail_end_datetime",
    },
    "device_exposure": {
        "device_exposure_start_date", "device_exposure_start_datetime",
        "device_exposure_end_date", "device_exposure_end_datetime",
    },
    "specimen": {
        "specimen_date", "specimen_datetime",
    },
    "note": {
        "note_date", "note_datetime",
    },
    "episode": {
        "episode_start_date", "episode_start_datetime",
        "episode_end_date", "episode_end_datetime",
    },
}

CLINICAL_EVENT_DATE_COLUMNS: Set[str] = set()
for cols in CLINICAL_EVENT_TABLES_DATES.values():
    CLINICAL_EVENT_DATE_COLUMNS.update(cols)


MINIMUM_YEAR_THRESHOLD = 1900


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


def _extract_date_literal_year(node: exp.Expression) -> Optional[int]:
    if isinstance(node, exp.Literal):
        date_str = str(node.this).strip("'\"")
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
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


def _contains_event_date(
    node: exp.Expression,
    aliases: Dict[str, str],
) -> Optional[Tuple[str, str]]:
    for col in node.find_all(exp.Column):
        table, col_name = resolve_table_col(col, aliases)
        col_norm = _norm(col_name)

        if col_norm not in CLINICAL_EVENT_DATE_COLUMNS:
            continue

        if table:
            table_norm = _norm(table)
            if (
                table_norm in CLINICAL_EVENT_TABLES_DATES
                and col_norm in CLINICAL_EVENT_TABLES_DATES[table_norm]
            ):
                return table_norm, col_norm
        else:
            if len(aliases) == 1:
                table_norm = _norm(next(iter(aliases.values())))
                if table_norm in CLINICAL_EVENT_TABLES_DATES:
                    return table_norm, col_norm

    return None


# --- Detection -------------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    violations: List[Tuple[str, str, str]] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_or(node):
            continue

        if isinstance(node, (exp.Is, exp.Not)):
            continue

        # --- Comparisons ---
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            left = node.this
            right = node.expression

            left_info = _contains_event_date(left, aliases)
            right_info = _contains_event_date(right, aliases)

            # --- column OP literal ---
            if left_info and not right_info:
                table, col = left_info
                year = _extract_date_literal_year(right)

                if year is not None and year <= MINIMUM_YEAR_THRESHOLD:
                    if isinstance(node, (exp.LT, exp.LTE)):
                        key = f"{node.sql()}_{table}_{col}_{year}"
                        if key not in seen:
                            seen.add(key)
                            violations.append((
                                f"{table}.{col} is filtered for dates before {MINIMUM_YEAR_THRESHOLD} (year={year}). "
                                f"This may indicate implausible or placeholder dates.",
                                table,
                                col,
                            ))

            # --- literal OP column ---
            elif right_info and not left_info:
                table, col = right_info
                year = _extract_date_literal_year(left)

                if year is not None and year < MINIMUM_YEAR_THRESHOLD:
                    # When literal is on left, any comparison with ancient date is suspicious
                    # '1850-01-01' < col OR '1850-01-01' > col both fire
                    key = f"{node.sql()}_{table}_{col}_{year}"
                    if key not in seen:
                        seen.add(key)
                        violations.append((
                            f"{table}.{col} is filtered for dates before {MINIMUM_YEAR_THRESHOLD} (year={year}). "
                            f"This may indicate implausible or placeholder dates.",
                            table,
                            col,
                        ))

        # --- BETWEEN ---
        elif isinstance(node, exp.Between):
            info = _contains_event_date(node.this, aliases)
            if info:
                table, col = info
                low = node.args.get("low")
                high = node.args.get("high")

                low_year = _extract_date_literal_year(low)
                high_year = _extract_date_literal_year(high)

                if low_year and high_year:
                    if low_year < MINIMUM_YEAR_THRESHOLD and high_year < MINIMUM_YEAR_THRESHOLD:
                        violations.append((
                            f"{table}.{col} BETWEEN targets dates before {MINIMUM_YEAR_THRESHOLD}.",
                            table,
                            col,
                        ))

        # --- IN ---
        elif isinstance(node, exp.In):
            info = _contains_event_date(node.this, aliases)
            if info:
                table, col = info
                has_ancient_date = False

                for val in node.expressions or []:
                    year = _extract_date_literal_year(val)
                    if year is not None and year < MINIMUM_YEAR_THRESHOLD:
                        has_ancient_date = True
                        break

                if has_ancient_date:
                    key = f"{node.sql()}_{table}_{col}"
                    if key not in seen:
                        seen.add(key)
                        violations.append((
                            f"{table}.{col} IN clause includes date before 1900.",
                            table,
                            col,
                        ))

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ClinicalEventDateBefore1900ValidationRule(Rule):
    """Validate that clinical event dates are not filtered for dates before 1900."""

    rule_id = "data_quality.clinical_event_date_before_1900_validation"
    name = "Clinical Event Date Should Not Be Before 1900"

    description = (
        "Detects filtering logic that targets implausible historical dates (<1900) "
        "in clinical event tables."
    )

    severity = Severity.WARNING
    suggested_fix = "REPLACE: `<date_col> < '1900-01-01'` (or earlier-cutoff predicates) WITH `<date_col> >= '1900-01-01'`. Pre-1900 clinical event dates are almost always ETL sentinels or year_of_birth misuse."
    long_description = (
        "Dates before 1900 in OMOP clinical tables are almost always "
        "placeholders for missing or unparseable source dates, not real "
        "clinical events. Filtering for these dates typically surfaces "
        "data-quality artefacts rather than clinical signal. Restrict "
        "queries to >= 1900-01-01 unless the intent is explicitly a "
        "data-quality audit of the placeholder rows."
    )
    example_bad = (
        "SELECT *\n"
        "FROM condition_occurrence\n"
        "WHERE condition_start_date < '1900-01-01';"
    )
    example_good = (
        "SELECT *\n"
        "FROM condition_occurrence\n"
        "WHERE condition_start_date >= '1900-01-01';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not any(has_table_reference(tree, t) for t in CLINICAL_EVENT_TABLES_DATES):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg, table, col in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        details={
                            "table": table,
                            "column": col,
                            "minimum_year_threshold": MINIMUM_YEAR_THRESHOLD,
                        },
                    )
                )

        return violations


__all__ = ["ClinicalEventDateBefore1900ValidationRule"]

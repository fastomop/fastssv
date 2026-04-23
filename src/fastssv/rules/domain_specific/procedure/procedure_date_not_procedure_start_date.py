"""Event Date Column Correctness Rule.

OMOP semantic rules OMOP_146, OMOP_550:
Certain OMOP clinical event tables use simplified date column names without '_start'
suffix. Referencing non-existent *_start_date columns is a column name error.

The Problem:
    The OMOP CDM has inconsistent naming patterns across clinical event tables:

    Tables WITH *_start_date columns:
    - condition_occurrence: condition_start_date, condition_end_date
    - drug_exposure: drug_exposure_start_date, drug_exposure_end_date
    - device_exposure: device_exposure_start_date, device_exposure_end_date
    - visit_occurrence: visit_start_date, visit_end_date
    - visit_detail: visit_detail_start_date, visit_detail_end_date

    Tables WITHOUT *_start_date columns (use simplified names):
    - procedure_occurrence: procedure_date (NOT procedure_start_date)
    - measurement: measurement_date (NOT measurement_start_date)
    - observation: observation_date (NOT observation_start_date)
    - specimen: specimen_date (NOT specimen_start_date)
    - note: note_date (NOT note_start_date)

    Developers familiar with tables that have *_start_date naturally expect all
    clinical event tables to follow the same pattern, but they don't.

    Common mistakes:
    1. Referencing procedure_occurrence.procedure_start_date (doesn't exist)
    2. Referencing measurement.measurement_start_date (doesn't exist)
    3. Referencing observation.observation_start_date (doesn't exist)
    4. Referencing specimen.specimen_start_date (doesn't exist)
    5. Referencing note.note_start_date (doesn't exist)
    6. Copying query patterns from condition_occurrence without adapting column names
    7. Assuming all clinical event tables follow the same naming convention

Why this is wrong:
    These tables do not include *_start_date columns in their schema.
    Attempting to reference them:
    - Causes SQL errors (column does not exist)
    - Indicates misunderstanding of table schema
    - Breaks query execution
    - Results from copy-paste errors across different clinical event tables

Violation patterns:
    SELECT procedure_start_date FROM procedure_occurrence
    -- ERROR: use procedure_date

    SELECT measurement_start_date FROM measurement
    -- ERROR: use measurement_date

    SELECT observation_start_date FROM observation WHERE ...
    -- ERROR: use observation_date

    SELECT specimen_start_date FROM specimen
    -- ERROR: use specimen_date

    SELECT note_start_date FROM note
    -- ERROR: use note_date

Correct patterns:
    SELECT procedure_date FROM procedure_occurrence
    SELECT measurement_date FROM measurement
    SELECT observation_date FROM observation
    SELECT specimen_date FROM specimen
    SELECT note_date FROM note

Note:
    This is an ERROR, not a WARNING. Attempting to reference these non-existent
    columns will cause query failures.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

# Table -> (incorrect_column, correct_column, correct_datetime_column)
TABLE_COLUMN_MAPPINGS = {
    "procedure_occurrence": ("procedure_start_date", "procedure_date", "procedure_datetime"),
    "measurement": ("measurement_start_date", "measurement_date", "measurement_datetime"),
    "observation": ("observation_start_date", "observation_date", "observation_datetime"),
    "specimen": ("specimen_start_date", "specimen_date", "specimen_datetime"),
    "note": ("note_start_date", "note_date", "note_datetime"),
}


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    return {
        _norm(cte.alias_or_name)
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _resolve_column(
    column: exp.Column,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> Tuple[Optional[str], Optional[str]]:
    table, col = resolve_table_col(column, aliases)
    table = _norm(table)
    col = _norm(col)

    if table in cte_names:
        return None, None

    return table, col


def _collect_tables(tree: exp.Expression, cte_names: Set[str]) -> Set[str]:
    tables = set()

    for tbl in tree.find_all(exp.Table):
        name = _norm(tbl.name)
        if name and name not in cte_names:
            tables.add(name)

    return tables


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> List[str]:
    issues: List[str] = []

    # Fast guards - check if any of our tables are used
    tables_in_query = _collect_tables(tree, cte_names)
    relevant_tables = set(TABLE_COLUMN_MAPPINGS.keys()) & tables_in_query

    if not relevant_tables:
        return issues

    # Check if any relevant tables are CTEs (skip them)
    relevant_tables = relevant_tables - cte_names

    if not relevant_tables:
        return issues

    other_tables = tables_in_query - relevant_tables

    for col in tree.find_all(exp.Column):
        t, c = _resolve_column(col, aliases, cte_names)

        if not c:
            continue

        # --- Case 1: Explicit misuse ---
        if t and t in TABLE_COLUMN_MAPPINGS:
            incorrect_col, correct_col, correct_datetime = TABLE_COLUMN_MAPPINGS[t]
            if c == _norm(incorrect_col):
                issues.append(
                    f"Reference to {t}.{incorrect_col} is invalid. "
                    f"The {t} table has no {incorrect_col} column. "
                    f"Use {correct_col} or {correct_datetime} instead."
                )
                continue

        # --- Case 2: Unqualified misuse (safe heuristic) ---
        if not t:
            # Check if this column name matches any of our incorrect columns
            for table_name, (incorrect_col, correct_col, correct_datetime) in TABLE_COLUMN_MAPPINGS.items():
                if c == _norm(incorrect_col) and table_name in relevant_tables:
                    # Only flag if no other tables could own the column
                    if not other_tables:
                        issues.append(
                            f"Unqualified {incorrect_col} likely refers to "
                            f"{table_name}.{incorrect_col}, which does not exist. "
                            f"Use {correct_col} or {correct_datetime} instead."
                        )
                        break

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class EventDateColumnCorrectnessRule(Rule):
    """
    OMOP_146, OMOP_550: Ensure correct date columns are used for clinical event tables.
    """

    rule_id = "domain_specific.event_date_column_correctness"
    name = "Event Date Column Correctness"

    description = (
        "Certain OMOP clinical event tables (procedure_occurrence, measurement, "
        "observation, specimen, note) use simplified date column names without "
        "'_start' suffix. Referencing non-existent *_start_date columns will cause errors."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use the correct date column names: procedure_date (not procedure_start_date), "
        "measurement_date (not measurement_start_date), observation_date (not observation_start_date), "
        "specimen_date (not specimen_start_date), note_date (not note_start_date). "
        "Use *_datetime columns for timestamp precision."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter: check if any relevant tables or incorrect columns are present
        has_relevant_table = any(table in sql_lower for table in TABLE_COLUMN_MAPPINGS.keys())
        has_incorrect_col = any(incorrect_col in sql_lower for incorrect_col, _, _ in TABLE_COLUMN_MAPPINGS.values())

        if not has_relevant_table or not has_incorrect_col:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for event_date_column_correctness",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            cte_names = _extract_cte_names(tree)

            issues = _find_violations(tree, aliases, cte_names)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["EventDateColumnCorrectnessRule"]

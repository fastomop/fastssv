"""Procedure Date Not Procedure Start Date Rule.

OMOP semantic rule OMOP_146:
procedure_occurrence uses procedure_date (not procedure_start_date). Unlike
condition_occurrence which has condition_start_date, the column name for procedures
omits 'start'. Referencing procedure_start_date is a column name error.

The Problem:
    The OMOP CDM has inconsistent naming patterns across clinical event tables:

    - condition_occurrence: condition_start_date, condition_end_date
    - drug_exposure: drug_exposure_start_date, drug_exposure_end_date
    - procedure_occurrence: procedure_date, procedure_end_date (NO "start")

    The procedure_occurrence table schema is:
    - procedure_date: Date of the procedure (not procedure_start_date)
    - procedure_datetime: Timestamp of the procedure
    - procedure_end_date: End date (for procedures spanning multiple days)

    The procedure_occurrence table does NOT have a procedure_start_date column.

    Developers familiar with condition_occurrence or drug_exposure naturally expect
    procedure_start_date to exist, but it doesn't. The correct column is procedure_date.

    Common mistakes:
    1. Referencing procedure_occurrence.procedure_start_date (column doesn't exist)
    2. Using procedure_start_date in WHERE/SELECT/JOIN/ORDER BY
    3. Copying query patterns from condition_occurrence without adapting column names
    4. Assuming all clinical event tables follow the same naming convention

Why this is wrong:
    The procedure_occurrence table schema does not include procedure_start_date.
    Attempting to reference it:
    - Causes SQL errors (column does not exist)
    - Indicates misunderstanding of procedure_occurrence schema
    - Breaks query execution
    - Results from copy-paste errors across different clinical event tables

    The correct column for procedure start is procedure_date (or procedure_datetime
    for timestamp precision).

Violation patterns:
    SELECT procedure_start_date FROM procedure_occurrence
    -- ERROR: procedure_occurrence has no procedure_start_date column

    SELECT po.procedure_start_date FROM procedure_occurrence po
    -- ERROR: procedure_occurrence has no procedure_start_date column

    SELECT * FROM procedure_occurrence
    WHERE procedure_start_date >= '2023-01-01'
    -- ERROR: procedure_occurrence has no procedure_start_date column

    SELECT * FROM procedure_occurrence po
    JOIN visit_occurrence vo
      ON vo.visit_start_date = po.procedure_start_date
    -- ERROR: procedure_occurrence has no procedure_start_date column

    SELECT procedure_start_date, procedure_end_date
    FROM procedure_occurrence
    -- ERROR: procedure_start_date doesn't exist

Correct patterns:
    SELECT procedure_date FROM procedure_occurrence
    -- OK: Correct column for procedure start date

    SELECT procedure_datetime FROM procedure_occurrence
    -- OK: Timestamp version of procedure start

    SELECT procedure_date, procedure_end_date
    FROM procedure_occurrence
    -- OK: Both columns exist

    SELECT * FROM procedure_occurrence po
    WHERE po.procedure_date >= '2023-01-01'
    -- OK: Using correct column name

    SELECT * FROM procedure_occurrence po
    JOIN visit_occurrence vo
      ON vo.visit_start_date = po.procedure_date
    -- OK: Correct column for procedure start

Note:
    This is an ERROR, not a WARNING. The procedure_occurrence table schema does
    not include procedure_start_date, and attempting to reference it will cause
    query failures.
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
    uses_table,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

PROCEDURE_OCCURRENCE_TABLE = "procedure_occurrence"
PROCEDURE_START_DATE_COL = "procedure_start_date"


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_procedure_occurrence(table: Optional[str]) -> bool:
    return table == PROCEDURE_OCCURRENCE_TABLE


def _is_procedure_start_date(col: Optional[str]) -> bool:
    return col == PROCEDURE_START_DATE_COL


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

    # Fast guards
    if not uses_table(tree, PROCEDURE_OCCURRENCE_TABLE):
        return issues

    if PROCEDURE_OCCURRENCE_TABLE in cte_names:
        return issues

    tables_in_query = _collect_tables(tree, cte_names)
    other_tables = tables_in_query - {PROCEDURE_OCCURRENCE_TABLE}

    for col in tree.find_all(exp.Column):
        t, c = _resolve_column(col, aliases, cte_names)

        if not c:
            continue

        # --- Case 1: Explicit misuse ---
        if _is_procedure_occurrence(t) and _is_procedure_start_date(c):
            issues.append(
                "Reference to procedure_occurrence.procedure_start_date is invalid. "
                "procedure_occurrence table has no procedure_start_date column. "
                "Use procedure_date or procedure_datetime instead."
            )
            continue

        # --- Case 2: Unqualified misuse (safe heuristic) ---
        if not t and _is_procedure_start_date(c):
            # Only flag if no other tables could own the column
            if not other_tables:
                issues.append(
                    "Unqualified procedure_start_date likely refers to "
                    "procedure_occurrence.procedure_start_date, which does not exist. "
                    "Use procedure_date or procedure_datetime instead."
                )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class ProcedureDateNotProcedureStartDateRule(Rule):
    """
    OMOP_146: Ensure procedure_occurrence.procedure_start_date is not referenced.
    """

    rule_id = "domain_specific.procedure_date_not_procedure_start_date"
    name = "Procedure Date Not Procedure Start Date"

    description = (
        "procedure_occurrence table has no procedure_start_date column. "
        "The correct column is procedure_date (not procedure_start_date). "
        "This differs from condition_occurrence which has condition_start_date."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use procedure_date for date or procedure_datetime for timestamp. "
        "procedure_end_date is available for end dates."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filters
        if PROCEDURE_OCCURRENCE_TABLE not in sql_lower:
            return []

        if PROCEDURE_START_DATE_COL not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_146",
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


__all__ = ["ProcedureDateNotProcedureStartDateRule"]
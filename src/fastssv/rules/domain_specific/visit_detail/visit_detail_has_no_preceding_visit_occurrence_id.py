"""Visit Detail Has No Preceding Visit Occurrence ID Rule.

OMOP semantic rule OMOP_142:
visit_detail does not have a preceding_visit_occurrence_id column. The temporal
chain in visit_detail uses preceding_visit_detail_id. Confusing these two columns
across tables is an error.

The Problem:
    The OMOP CDM has two separate temporal chains for visits:

    1. visit_occurrence temporal chain:
       - Uses preceding_visit_occurrence_id to link to previous visits
       - visit_occurrence.preceding_visit_occurrence_id → visit_occurrence.visit_occurrence_id

    2. visit_detail temporal chain:
       - Uses preceding_visit_detail_id to link to previous visit details
       - visit_detail.preceding_visit_detail_id → visit_detail.visit_detail_id

    The visit_detail table does NOT have a preceding_visit_occurrence_id column.

    Common mistakes:
    1. Referencing visit_detail.preceding_visit_occurrence_id (column doesn't exist)
    2. Confusing the two temporal chains
    3. Trying to join visit_detail to visit_occurrence via preceding_visit_occurrence_id
    4. Selecting/filtering on visit_detail.preceding_visit_occurrence_id

Why this is wrong:
    The visit_detail table schema does not include preceding_visit_occurrence_id.
    Attempting to reference it:
    - Causes SQL errors (column does not exist)
    - Indicates misunderstanding of visit temporal chain structure
    - Breaks query execution

    The correct column for visit_detail temporal chain is preceding_visit_detail_id.

Violation patterns:
    SELECT preceding_visit_occurrence_id FROM visit_detail
    -- ERROR: visit_detail has no preceding_visit_occurrence_id column

    SELECT vd.preceding_visit_occurrence_id FROM visit_detail vd
    -- ERROR: visit_detail has no preceding_visit_occurrence_id column

    SELECT * FROM visit_detail
    WHERE preceding_visit_occurrence_id IS NOT NULL
    -- ERROR: visit_detail has no preceding_visit_occurrence_id column

    SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo
      ON vd.preceding_visit_occurrence_id = vo.visit_occurrence_id
    -- ERROR: visit_detail has no preceding_visit_occurrence_id column

Correct patterns:
    SELECT preceding_visit_detail_id FROM visit_detail
    -- OK: Correct column for visit_detail temporal chain

    SELECT vd.preceding_visit_detail_id
    FROM visit_detail vd
    WHERE preceding_visit_detail_id IS NOT NULL
    -- OK: Using correct column

    SELECT preceding_visit_occurrence_id FROM visit_occurrence
    -- OK: visit_occurrence has this column

    SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    -- OK: Proper join to parent visit_occurrence via visit_occurrence_id

Note:
    This is an ERROR, not a WARNING. The visit_detail table schema does not
    include preceding_visit_occurrence_id, and attempting to reference it will
    cause query failures.
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
    has_table_reference,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

VISIT_DETAIL_TABLE = "visit_detail"
PRECEDING_VISIT_OCCURRENCE_ID_COL = "preceding_visit_occurrence_id"


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_visit_detail(table: Optional[str]) -> bool:
    return table == VISIT_DETAIL_TABLE


def _is_preceding_visit_occurrence_id(col: Optional[str]) -> bool:
    return col == PRECEDING_VISIT_OCCURRENCE_ID_COL


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

    # Avoid CTE shadowing
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

    # Fast guard
    if not has_table_reference(tree, VISIT_DETAIL_TABLE):
        return issues

    # Avoid CTE shadowing
    if VISIT_DETAIL_TABLE in cte_names:
        return issues

    tables_in_query = _collect_tables(tree, cte_names)
    has_visit_detail = VISIT_DETAIL_TABLE in tables_in_query

    for col in tree.find_all(exp.Column):
        t, c = _resolve_column(col, aliases, cte_names)

        if not c:
            continue

        # --- Case 1: Explicit misuse ---
        if _is_visit_detail(t) and _is_preceding_visit_occurrence_id(c):
            issues.append(
                "Reference to visit_detail.preceding_visit_occurrence_id is invalid. "
                "visit_detail table has no preceding_visit_occurrence_id column. "
                "Use preceding_visit_detail_id for visit_detail temporal chain."
            )
            continue

        # --- Case 2: Unqualified misuse ---
        if not t and _is_preceding_visit_occurrence_id(c):
            if has_visit_detail:
                issues.append(
                    "Unqualified preceding_visit_occurrence_id may refer to "
                    "visit_detail.preceding_visit_occurrence_id, which does not exist. "
                    "Use preceding_visit_detail_id for visit_detail temporal chain."
                )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class VisitDetailHasNoPrecedingVisitOccurrenceIdRule(Rule):
    """
    OMOP_142: Ensure visit_detail.preceding_visit_occurrence_id is not referenced.
    """

    rule_id = "domain_specific.visit_detail_has_no_preceding_visit_occurrence_id"
    name = "Visit Detail Has No Preceding Visit Occurrence ID"

    description = (
        "visit_detail table has no preceding_visit_occurrence_id column. "
        "The temporal chain in visit_detail uses preceding_visit_detail_id. "
        "Use preceding_visit_occurrence_id only with visit_occurrence table."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use preceding_visit_detail_id for visit_detail temporal chain. "
        "Use preceding_visit_occurrence_id only with visit_occurrence table."
    )

    example_bad = (
        "SELECT visit_detail_id, preceding_visit_occurrence_id\n"
        "FROM visit_detail;"
    )
    example_good = (
        "SELECT visit_detail_id, preceding_visit_detail_id\n"
        "FROM visit_detail;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter
        if VISIT_DETAIL_TABLE not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_142",
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


__all__ = ["VisitDetailHasNoPrecedingVisitOccurrenceIdRule"]

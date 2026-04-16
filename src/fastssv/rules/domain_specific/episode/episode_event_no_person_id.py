"""Episode Event No Person ID Rule.

OMOP semantic rule OMOP_139:
episode_event has no person_id column. To get patient-level data from episode events,
join episode_event to episode on episode_id, then episode to person on person_id.

The Problem:
    The episode_event table is a linking table that connects episodes to their
    constituent clinical events. The table schema is:
    - episode_id (FK to episode.episode_id)
    - event_id (polymorphic ID to clinical event)
    - episode_event_field_concept_id (indicates which domain event_id refers to)

    The episode_event table does NOT have a person_id column.

    Developers sometimes mistakenly try to:
    1. Join episode_event directly to person on person_id (column doesn't exist)
    2. Filter episode_event by person_id (column doesn't exist)
    3. Select episode_event.person_id (column doesn't exist)
    4. Use person_id in WHERE/ORDER BY/GROUP BY with episode_event

Why this is wrong:
    The episode_event table is intentionally designed without person_id to avoid
    denormalization. Person information is accessed through the episode table:
    - episode_event contains the event linkages
    - episode contains the person_id and episode metadata
    - This ensures data consistency and proper normalization

    Attempting to use person_id on episode_event:
    - Causes SQL errors (column does not exist)
    - Indicates misunderstanding of episode_event table structure
    - Breaks query execution

Violation patterns:
    SELECT * FROM episode_event ee
    JOIN person p ON ee.person_id = p.person_id
    -- ERROR: episode_event has no person_id column

    SELECT * FROM episode_event
    WHERE person_id = 12345
    -- ERROR: episode_event has no person_id column

    SELECT episode_event.person_id, event_id
    FROM episode_event
    -- ERROR: episode_event has no person_id column

    SELECT ee.person_id FROM episode_event ee
    -- ERROR: episode_event has no person_id column

Correct patterns:
    SELECT * FROM episode_event ee
    JOIN episode e ON ee.episode_id = e.episode_id
    JOIN person p ON e.person_id = p.person_id
    -- OK: Proper join path through episode table

    SELECT ee.*, e.person_id
    FROM episode_event ee
    JOIN episode e ON ee.episode_id = e.episode_id
    WHERE e.person_id = 12345
    -- OK: person_id comes from episode table

    SELECT e.person_id, COUNT(ee.event_id) AS event_count
    FROM episode_event ee
    JOIN episode e ON ee.episode_id = e.episode_id
    GROUP BY e.person_id
    -- OK: person_id qualified by episode table

Note:
    This is an ERROR, not a WARNING. The episode_event table schema does not
    include person_id, and attempting to reference it will cause query failures.
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

EPISODE_EVENT_TABLE = "episode_event"
PERSON_ID_COL = "person_id"


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_episode_event(table: Optional[str]) -> bool:
    return table == EPISODE_EVENT_TABLE


def _is_person_id(col: Optional[str]) -> bool:
    return col == PERSON_ID_COL


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
    """
    Collect normalized table names used in query (excluding CTEs).
    """
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

    # Ensure real table usage (not CTE shadowing)
    if not uses_table(tree, EPISODE_EVENT_TABLE):
        return issues

    if EPISODE_EVENT_TABLE in cte_names:
        return issues

    tables_in_query = _collect_tables(tree, cte_names)

    # Heuristic: check if other tables likely provide person_id
    # (reduces false positives)
    other_tables = tables_in_query - {EPISODE_EVENT_TABLE}
    has_other_tables = len(other_tables) > 0

    for col in tree.find_all(exp.Column):
        t, c = _resolve_column(col, aliases, cte_names)

        if not c:
            continue

        # --- Case 1: Explicit misuse ---
        if _is_episode_event(t) and _is_person_id(c):
            issues.append(
                "Reference to episode_event.person_id is invalid. "
                "episode_event table has no person_id column. "
                "Join to episode first, then use episode.person_id."
            )
            continue

        # --- Case 2: Unqualified person_id ---
        if not t and _is_person_id(c):
            # Only flag if episode_event is the only table (or dominant)
            if not has_other_tables:
                issues.append(
                    "Unqualified person_id likely refers to episode_event.person_id, "
                    "which does not exist. Join to episode first and use episode.person_id."
                )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class EpisodeEventNoPersonIdRule(Rule):
    """
    OMOP_139: Ensure episode_event.person_id is not referenced.
    """

    rule_id = "domain_specific.episode_event_no_person_id"
    name = "Episode Event No Person ID"

    description = (
        "episode_event table has no person_id column. "
        "To access person data, join episode_event to episode, then episode to person."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use: FROM episode_event ee "
        "JOIN episode e ON ee.episode_id = e.episode_id "
        "JOIN person p ON e.person_id = p.person_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter
        if EPISODE_EVENT_TABLE not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_139",
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


__all__ = ["EpisodeEventNoPersonIdRule"]

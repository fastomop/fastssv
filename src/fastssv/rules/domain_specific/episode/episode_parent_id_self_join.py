"""Episode Parent ID Self Join Rule.

OMOP semantic rule OMOP_138:
episode.episode_parent_id references another episode.episode_id in the same table
(hierarchical episode nesting). This self-join must target the episode table on
episode_id, not another table.

The Problem:
    The episode table supports hierarchical nesting where an episode can have a
    parent episode. For example:
    - Parent episode: "Hospital admission" (episode_id = 100)
    - Child episode: "ICU stay" (episode_id = 101, episode_parent_id = 100)
    - Child episode: "Surgery during admission" (episode_id = 102, episode_parent_id = 100)

    The episode_parent_id column (INTEGER) is a self-referential foreign key that
    should ONLY join to episode.episode_id in the same table.

    Common mistakes:
    1. Joining episode_parent_id to other clinical tables
       - episode.episode_parent_id = condition_occurrence.condition_occurrence_id
       - episode.episode_parent_id = visit_occurrence.visit_occurrence_id
       - Type matches (both INTEGER) but semantics are completely wrong

    2. Joining episode_parent_id to wrong column in episode table
       - episode.episode_parent_id = episode.episode_concept_id
       - Wrong column - must be episode_id

    3. Confusing episode_parent_id with episode_event_id
       - episode_parent_id is for hierarchical nesting (parent episodes)
       - episode_event_id is for linking to clinical events (polymorphic FK)

Why this is wrong:
    The episode_parent_id exists specifically to create hierarchical relationships
    between episodes. Using it to join other tables:
    - Returns incorrect episode hierarchies
    - Produces nonsensical parent-child relationships
    - Breaks episode tree traversal queries
    - Corrupts episode analytics and care pathway analysis

Violation patterns:
    SELECT * FROM episode e
    JOIN condition_occurrence co ON e.episode_parent_id = co.condition_occurrence_id
    -- ERROR: episode_parent_id should join to episode.episode_id

    SELECT * FROM episode e
    JOIN visit_occurrence vo ON e.episode_parent_id = vo.visit_occurrence_id
    -- ERROR: Wrong table - must be episode self-join

    SELECT * FROM episode e1
    JOIN episode e2 ON e1.episode_parent_id = e2.episode_concept_id
    -- ERROR: Wrong column - must be episode_id

Correct patterns:
    SELECT child.*, parent.episode_concept_id AS parent_type
    FROM episode child
    JOIN episode parent ON child.episode_parent_id = parent.episode_id
    -- OK: Correct self-join to episode.episode_id

    SELECT e1.*, e2.*, e3.*
    FROM episode e1
    LEFT JOIN episode e2 ON e1.episode_parent_id = e2.episode_id
    LEFT JOIN episode e3 ON e2.episode_parent_id = e3.episode_id
    -- OK: Multi-level hierarchy traversal

    SELECT * FROM episode
    WHERE episode_parent_id IS NOT NULL
    -- OK: Filter for child episodes, no join

Note:
    This is an ERROR, not a WARNING. The episode_parent_id column has a specific
    self-referential purpose and must only join to episode.episode_id.
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

EPISODE_TABLE = "episode"
EPISODE_PARENT_ID_COL = "episode_parent_id"
EPISODE_ID_COL = "episode_id"


# --- Helpers -----------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_episode(table: Optional[str]) -> bool:
    return table == EPISODE_TABLE


def _is_episode_parent_id(col: Optional[str]) -> bool:
    return col == EPISODE_PARENT_ID_COL


def _is_episode_id(col: Optional[str]) -> bool:
    return col == EPISODE_ID_COL


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    return {_norm(cte.alias_or_name) for cte in tree.find_all(exp.CTE) if cte.alias_or_name}


def _resolve_column(
    column: exp.Column,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> Tuple[Optional[str], Optional[str]]:
    table, col = resolve_table_col(column, aliases)
    table = _norm(table)
    col = _norm(col)

    # Exclude CTE shadowing
    if table in cte_names:
        return None, None

    return table, col


def _is_valid_self_join(
    t1: str,
    c1: str,
    t2: str,
    c2: str,
) -> bool:
    return (_is_episode(t1) and _is_episode_parent_id(c1) and _is_episode(t2) and _is_episode_id(c2)) or (
        _is_episode(t1) and _is_episode_id(c1) and _is_episode(t2) and _is_episode_parent_id(c2)
    )


def _analyze_conditions(
    condition: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> Tuple[bool, bool, bool]:
    """
    Returns:
        (has_parent_id_join, has_valid_join, has_invalid_join)
    """
    has_parent_id_join = False
    has_valid_join = False
    has_invalid_join = False

    for eq in condition.find_all(exp.EQ):
        left, right = eq.this, eq.expression

        # Only consider column-to-column comparisons (real joins)
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        t1, c1 = _resolve_column(left, aliases, cte_names)
        t2, c2 = _resolve_column(right, aliases, cte_names)

        if not t1 or not t2:
            continue

        if _is_episode_parent_id(c1) or _is_episode_parent_id(c2):
            has_parent_id_join = True

            if _is_valid_self_join(t1, c1, t2, c2):
                has_valid_join = True
            else:
                has_invalid_join = True

    return has_parent_id_join, has_valid_join, has_invalid_join


def _check_joins(tree: exp.Expression) -> List[str]:
    issues: List[str] = []

    if not has_table_reference(tree, EPISODE_TABLE):
        return issues

    aliases = extract_aliases(tree)
    cte_names = _extract_cte_names(tree)

    # --- Explicit JOINs ---
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        has_parent_id, has_valid, has_invalid = _analyze_conditions(on_clause, aliases, cte_names)

        if has_parent_id and (has_invalid or not has_valid):
            issues.append(
                "Invalid JOIN using episode_parent_id. "
                "episode_parent_id must join to episode.episode_id (self-join), "
                "not to other tables or columns."
            )

    # --- Implicit JOINs via WHERE ---
    for where in tree.find_all(exp.Where):
        has_parent_id, has_valid, has_invalid = _analyze_conditions(where.this, aliases, cte_names)

        if has_parent_id and (has_invalid or not has_valid):
            issues.append(
                "Invalid implicit JOIN using episode_parent_id. "
                "episode_parent_id must join to episode.episode_id (self-join), "
                "not to other tables or columns."
            )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------


@register
class EpisodeParentIdSelfJoinRule(Rule):
    """
    OMOP_138: Ensure episode_parent_id only joins to episode.episode_id.
    """

    rule_id = "domain_specific.episode_parent_id_self_join"
    name = "Episode Parent ID Self Join"

    description = (
        "episode.episode_parent_id is a self-referential FK to episode.episode_id. "
        "It must only join to episode.episode_id."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: the join target WITH `episode.episode_id`. episode_parent_id is a self-FK to episode.episode_id (not person_id, not visit_occurrence_id)."
    example_bad = "SELECT e.episode_id FROM episode e\nJOIN episode parent ON e.episode_parent_id = parent.person_id;"
    example_good = "SELECT e.episode_id FROM episode e\nJOIN episode parent ON e.episode_parent_id = parent.episode_id;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if EPISODE_TABLE not in sql_lower or EPISODE_PARENT_ID_COL not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_138",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            issues = _check_joins(tree)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["EpisodeParentIdSelfJoinRule"]

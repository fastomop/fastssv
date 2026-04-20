"""Episode Requires Concept Filter Rule.

OMOP semantic rules OMOP_240, OMOP_505:
Episode queries should filter by episode_concept_id to ensure semantic clarity
and optimal query performance. Additionally, episode_event records must be linked
to episode using episode_event.episode_id = episode.episode_id.

The Problem:
    The episode table in OMOP CDM represents aggregated clinical events spanning
    multiple dates (e.g., treatment regimens, disease episodes, hospitalizations).
    The episode_concept_id column defines the TYPE of episode being tracked.

    Querying the episode or episode_event tables without filtering by
    episode_concept_id can lead to:
    - Poor query performance (scanning all episode types)
    - Semantic ambiguity (unclear query intent)
    - Logical errors (mixing incompatible episode types)

Common episode types include:
    - Disease Episode (concept_id 32533)
    - Treatment Episode
    - Hospitalization Episode
    - Drug Era Episode

Violation patterns:
    -- WRONG: No episode_concept_id filter
    SELECT p.person_id, e.episode_start_date
    FROM episode e
    JOIN person p ON e.person_id = p.person_id;

    -- WRONG: Filtering on other columns but not episode_concept_id
    SELECT *
    FROM episode e
    WHERE e.episode_start_date > '2020-01-01';

    -- WRONG: episode_event without concept filter (OMOP_505)
    SELECT ee.*
    FROM episode_event ee
    JOIN condition_occurrence co ON ee.event_id = co.condition_occurrence_id;

Correct patterns:
    -- CORRECT: Filter by specific episode type
    SELECT p.person_id, e.episode_start_date
    FROM episode e
    JOIN person p ON e.person_id = p.person_id
    WHERE e.episode_concept_id = 32533;  -- Disease Episode

    -- CORRECT: Filter by multiple episode types
    SELECT *
    FROM episode e
    WHERE e.episode_concept_id IN (32533, 32534, 32535)
      AND e.episode_start_date > '2020-01-01';

    -- CORRECT: Using concept table for dynamic filtering
    SELECT e.*
    FROM episode e
    JOIN concept c ON e.episode_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Episode'
      AND c.standard_concept = 'S';

    -- CORRECT: episode_event with concept filter via join (OMOP_505)
    SELECT ee.*
    FROM episode_event ee
    JOIN episode e ON ee.episode_id = e.episode_id
    WHERE e.episode_concept_id = 32533;
"""

from typing import Dict, List, Optional, Set

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


# --- Constants -------------------------------------------------------------

EPISODE = "episode"
EPISODE_EVENT = "episode_event"
CONCEPT = "concept"

EPISODE_CONCEPT_ID = "episode_concept_id"
CONCEPT_ID = "concept_id"

NORM_EPISODE = normalize_name(EPISODE)
NORM_EPISODE_EVENT = normalize_name(EPISODE_EVENT)
NORM_EPISODE_CONCEPT_ID = normalize_name(EPISODE_CONCEPT_ID)
NORM_CONCEPT = normalize_name(CONCEPT)
NORM_CONCEPT_ID = normalize_name(CONCEPT_ID)


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_episode_column(
    col: exp.Column,
    aliases: Dict[str, str],
) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != NORM_EPISODE_CONCEPT_ID:
        return False

    if table:
        return _norm(table) == NORM_EPISODE

    # Unqualified column → only valid if episode exists in query
    return any(_norm(t) == NORM_EPISODE for t in aliases.values())


def _has_direct_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Detect direct filters on episode_concept_id."""

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        # Equality / inequality
        if isinstance(node, (exp.EQ, exp.NEQ)):
            for side in [node.this, node.expression]:
                if isinstance(side, exp.Column) and _is_episode_column(side, aliases):
                    return True

        # IN clause
        if isinstance(node, exp.In):
            if isinstance(node.this, exp.Column) and _is_episode_column(node.this, aliases):
                return True

        # IS NOT NULL
        if isinstance(node, exp.Is):
            if isinstance(node.this, exp.Column) and _is_episode_column(node.this, aliases):
                return True

    return False


def _has_concept_filtered_join(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """
    Require BOTH:
    - join on episode_concept_id = concept.concept_id
    - AND a filter on concept table
    """

    has_join = False
    concept_aliases: Set[str] = set()

    for join in tree.find_all(exp.Join):
        if not isinstance(join.this, exp.Table):
            continue

        if _norm(join.this.name) != NORM_CONCEPT:
            continue

        concept_alias = join.this.alias_or_name
        if concept_alias:
            concept_aliases.add(_norm(concept_alias))

        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            cols = [c for c in (eq.this, eq.expression) if isinstance(c, exp.Column)]

            if len(cols) != 2:
                continue

            _, left_col = resolve_table_col(cols[0], aliases)
            _, right_col = resolve_table_col(cols[1], aliases)

            if (
                (_norm(left_col) == NORM_EPISODE_CONCEPT_ID and _norm(right_col) == NORM_CONCEPT_ID)
                or (_norm(right_col) == NORM_EPISODE_CONCEPT_ID and _norm(left_col) == NORM_CONCEPT_ID)
            ):
                has_join = True

    if not has_join:
        return False

    # Now require filtering on concept table
    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if isinstance(node, (exp.EQ, exp.In)):
            for col in node.find_all(exp.Column):
                table, _ = resolve_table_col(col, aliases)
                if table and _norm(table) in concept_aliases:
                    return True

    return False


def _has_subquery_filter(tree: exp.Expression) -> bool:
    """Recursively check subqueries."""
    for sub in tree.find_all(exp.Subquery):
        inner = sub.this
        if isinstance(inner, exp.Expression):
            aliases = extract_aliases(inner)
            if (
                _has_direct_filter(inner, aliases)
                or _has_concept_filtered_join(inner, aliases)
                or _has_subquery_filter(inner)
            ):
                return True
    return False


def _has_episode_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    return (
        _has_direct_filter(tree, aliases)
        or _has_concept_filtered_join(tree, aliases)
        or _has_subquery_filter(tree)
    )


def _episode_event_has_valid_path(tree: exp.Expression) -> bool:
    """
    episode_event is valid if:
    - joins to episode with filter
    - OR subquery provides filter
    """

    for sub in tree.walk():
        if isinstance(sub, exp.Expression):
            aliases = extract_aliases(sub)

            if has_table_reference(sub, EPISODE) and _has_episode_filter(sub, aliases):
                return True

    return False


# --- Rule ------------------------------------------------------------------

@register
class EpisodeRequiresConceptFilterRule(Rule):
    rule_id = "data_quality.episode_requires_concept_filter"
    name = "Episode Requires Concept Filter"

    description = (
        "Episode queries should filter by episode_concept_id to ensure semantic "
        "clarity and optimal query performance."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Add a filter on episode_concept_id (e.g., WHERE episode_concept_id = <id>) "
        "or join to concept with appropriate filtering."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if "episode" not in sql_lower and "episode_event" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            uses_episode = has_table_reference(tree, EPISODE)
            uses_episode_event = has_table_reference(tree, EPISODE_EVENT)

            if not uses_episode and not uses_episode_event:
                continue

            has_filter = _has_episode_filter(tree, aliases)

            if uses_episode_event and not uses_episode:
                if not _episode_event_has_valid_path(tree):
                    key = "episode_event_no_filter"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            self.create_violation(
                                message=(
                                    "episode_event used without a valid episode_concept_id filter path."
                                ),
                                suggested_fix=(
                                    "JOIN episode e ON ee.episode_id = e.episode_id "
                                    "AND filter e.episode_concept_id"
                                ),
                                details={"table": "episode_event"},
                            )
                        )

            elif uses_episode and not has_filter:
                key = "episode_no_filter"
                if key not in seen:
                    seen.add(key)
                    violations.append(
                        self.create_violation(
                            message=(
                                "episode table used without filtering by episode_concept_id."
                            ),
                            suggested_fix=self.suggested_fix,
                            details={"table": "episode"},
                        )
                    )

        return violations


__all__ = ["EpisodeRequiresConceptFilterRule"]
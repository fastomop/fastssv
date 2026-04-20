"""Location History Entity ID Requires Domain ID Rule.

OMOP semantic rule OMOP_145:
location_history.entity_id is a polymorphic FK whose target is identified by
location_history.domain_id. Without filtering domain_id, entity_id may match IDs
from any table (person, provider, care_site).

The Problem:
    The location_history table tracks location changes for different entity types
    using a polymorphic foreign key pattern:

    - entity_id: Polymorphic FK to person_id, provider_id, or care_site_id
    - domain_id: Discriminator identifying which table entity_id refers to
      - 'Person' → entity_id refers to person.person_id
      - 'Provider' → entity_id refers to provider.provider_id
      - 'Care Site' → entity_id refers to care_site.care_site_id

    Without filtering on domain_id, joins on entity_id are ambiguous because:
    - Integer IDs can collide across tables (person_id=123, provider_id=123)
    - Query may incorrectly match entities from wrong domain
    - Results will include mixed entity types

    Common mistakes:
    1. Joining location_history.entity_id to person.person_id without domain_id filter
    2. Joining location_history.entity_id to provider.provider_id without domain_id filter
    3. Joining location_history.entity_id to care_site.care_site_id without domain_id filter
    4. Including domain_id in SELECT but not in WHERE/JOIN ON conditions

Why this is wrong:
    Without domain_id filtering:
    - Query matches entities from ALL domains, not just the target domain
    - ID collisions across tables produce incorrect joins
    - Results mix person, provider, and care_site location histories
    - Violates referential integrity assumptions
    - Produces semantically incorrect result sets

Violation patterns:
    SELECT * FROM location_history lh
    JOIN person p ON lh.entity_id = p.person_id
    -- ERROR: Missing domain_id filter, may match provider_id or care_site_id

    SELECT * FROM location_history lh
    JOIN provider pr ON lh.entity_id = pr.provider_id
    -- ERROR: May incorrectly match person_id or care_site_id values

    SELECT * FROM location_history lh
    JOIN care_site cs ON lh.entity_id = cs.care_site_id
    -- ERROR: May incorrectly match person_id or provider_id values

    SELECT lh.entity_id, lh.domain_id, p.person_id
    FROM location_history lh
    JOIN person p ON lh.entity_id = p.person_id
    -- ERROR: domain_id in SELECT but not filtered in WHERE/JOIN ON

Correct patterns:
    SELECT * FROM location_history lh
    JOIN person p ON lh.entity_id = p.person_id
    WHERE lh.domain_id = 'Person'
    -- OK: domain_id filter in WHERE clause

    SELECT * FROM location_history lh
    JOIN person p ON lh.entity_id = p.person_id
      AND lh.domain_id = 'Person'
    -- OK: domain_id filter in JOIN ON clause

    WITH person_locations AS (
        SELECT * FROM location_history
        WHERE domain_id = 'Person'
    )
    SELECT * FROM person_locations lh
    JOIN person p ON lh.entity_id = p.person_id
    -- OK: domain_id pre-filtered in CTE

    SELECT * FROM location_history lh
    JOIN provider pr ON lh.entity_id = pr.provider_id
      AND lh.domain_id = 'Provider'
    -- OK: Correct domain for provider

Note:
    This is an ERROR, not a WARNING. Polymorphic FK joins without domain
    filtering produce incorrect results and violate data integrity assumptions.
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

LOCATION_HISTORY_TABLE = "location_history"
ENTITY_ID_COL = "entity_id"
DOMAIN_ID_COL = "domain_id"

ENTITY_TABLE_TO_DOMAIN = {
    "person": "Person",
    "provider": "Provider",
    "care_site": "Care Site",
}

ENTITY_TABLE_ID_COLS = {
    "person": "person_id",
    "provider": "provider_id",
    "care_site": "care_site_id",
}


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_location_history(table: Optional[str]) -> bool:
    return table == LOCATION_HISTORY_TABLE


def _is_entity_id(col: Optional[str]) -> bool:
    return col == ENTITY_ID_COL


def _is_domain_id(col: Optional[str]) -> bool:
    return col == DOMAIN_ID_COL


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


def _get_expected_domain(table: str) -> Optional[str]:
    return ENTITY_TABLE_TO_DOMAIN.get(table)


def _get_entity_id_column(table: str) -> Optional[str]:
    return ENTITY_TABLE_ID_COLS.get(table)


def _extract_domain_filters(
    condition: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> Set[str]:
    """
    Extract all domain_id values used in a condition.
    """
    values = set()

    for eq in condition.find_all(exp.EQ):
        left, right = eq.this, eq.expression

        for side, other in [(left, right), (right, left)]:
            if isinstance(side, exp.Column):
                t, c = _resolve_column(side, aliases, cte_names)

                if _is_location_history(t) and _is_domain_id(c):
                    if isinstance(other, exp.Literal):
                        val = other.this
                        if isinstance(val, str):
                            values.add(val.strip("'\""))

    return values


def _find_entity_joins(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> List[Tuple[str, exp.Expression]]:
    """
    Find joins of location_history.entity_id to entity tables.

    Returns list of (target_table, join_condition)
    """
    results = []

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left, right = eq.this, eq.expression

            if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                continue

            t1, c1 = _resolve_column(left, aliases, cte_names)
            t2, c2 = _resolve_column(right, aliases, cte_names)

            # Check both directions
            for lh_t, lh_c, other_t, other_c in [
                (t1, c1, t2, c2),
                (t2, c2, t1, c1),
            ]:
                if _is_location_history(lh_t) and _is_entity_id(lh_c):
                    if other_t in ENTITY_TABLE_ID_COLS:
                        expected_id = _get_entity_id_column(other_t)
                        if other_c == expected_id:
                            results.append((other_t, on_clause))

    return results


def _collect_where_domain_values(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> Set[str]:
    values = set()

    for where in tree.find_all(exp.Where):
        values |= _extract_domain_filters(where.this, aliases, cte_names)

    return values


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> List[str]:
    issues: List[str] = []

    if not has_table_reference(tree, LOCATION_HISTORY_TABLE):
        return issues

    if LOCATION_HISTORY_TABLE in cte_names:
        return issues

    entity_joins = _find_entity_joins(tree, aliases, cte_names)
    where_domains = _collect_where_domain_values(tree, aliases, cte_names)

    for target_table, on_clause in entity_joins:
        expected_domain = _get_expected_domain(target_table)
        if not expected_domain:
            continue

        # Domain filters in JOIN
        join_domains = _extract_domain_filters(on_clause, aliases, cte_names)

        all_domains = join_domains | where_domains

        if not all_domains:
            issues.append(
                f"Join between location_history.entity_id and {target_table} "
                f"missing domain_id filter. Expected '{expected_domain}'."
            )
            continue

        # Wrong domain
        if expected_domain not in all_domains:
            issues.append(
                f"Incorrect domain_id filter for join with {target_table}. "
                f"Expected '{expected_domain}', found {sorted(all_domains)}."
            )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class LocationHistoryEntityIdRequiresDomainIdRule(Rule):
    """
    OMOP_145: Ensure location_history.entity_id joins include correct domain_id filter.
    """

    rule_id = "domain_specific.location_history_entity_id_requires_domain_id"
    name = "Location History Entity ID Requires Domain ID"

    description = (
        "location_history.entity_id is a polymorphic FK identified by domain_id. "
        "Joins must filter domain_id to match the target table."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Add WHERE location_history.domain_id = '<Domain>' matching the joined table."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if LOCATION_HISTORY_TABLE not in sql_lower:
            return []

        if ENTITY_ID_COL not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_145",
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


__all__ = ["LocationHistoryEntityIdRequiresDomainIdRule"]
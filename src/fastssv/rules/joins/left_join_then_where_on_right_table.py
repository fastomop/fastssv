"""Left Join Then Where On Right Table Rule.

OMOP semantic rule OMOP_149:
A LEFT JOIN followed by a WHERE clause filtering on a column of the right table
(other than IS NULL) effectively converts the LEFT JOIN to an INNER JOIN, silently
dropping the unmatched rows the LEFT JOIN was meant to preserve.

The Problem:
    LEFT JOIN semantics:
    - Returns ALL rows from the left table
    - Matched rows from right table have values
    - Unmatched rows from right table have NULL values

    When a WHERE clause filters on a right table column with non-NULL conditions:
    - Rows where right table column is NULL are filtered out
    - This defeats the purpose of LEFT JOIN
    - Effectively converts LEFT JOIN to INNER JOIN
    - Developer likely intended INNER JOIN or should move filter to JOIN ON

    This is a common SQL anti-pattern that produces unexpected results.

    Common mistakes:
    1. Using LEFT JOIN when INNER JOIN is needed
    2. Filtering right table columns in WHERE instead of JOIN ON
    3. Not understanding LEFT JOIN NULL behavior
    4. Copy-paste errors changing JOIN to LEFT JOIN without adjusting WHERE

Why this is wrong:
    The LEFT JOIN is meant to preserve all left table rows, but the WHERE clause
    silently removes them:
    - Produces incorrect result set (missing expected rows)
    - Creates confusion about query intent
    - Leads to subtle bugs in analytics
    - Performance impact (LEFT JOIN is more expensive than INNER JOIN)

    If filtering right table is needed:
    - Use INNER JOIN if you want only matched rows
    - Move filter to JOIN ON clause to preserve LEFT JOIN semantics

Violation patterns:
    SELECT co.* FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_concept_id = 9201
    -- WARNING: Filters right table, converts to INNER JOIN

    SELECT p.* FROM person p
    LEFT JOIN location l ON p.location_id = l.location_id
    WHERE l.state = 'CA' AND l.city = 'Los Angeles'
    -- WARNING: Multiple conditions on right table

    SELECT * FROM drug_exposure de
    LEFT JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.domain_id = 'Drug'
    -- WARNING: Right table filter defeats LEFT JOIN

Correct patterns:
    SELECT co.* FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_concept_id = 9201
    -- OK: Use INNER JOIN when filtering right table

    SELECT co.* FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_occurrence_id IS NULL
    -- OK: IS NULL check is intentional (finding unmatched rows)

    SELECT co.* FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE co.condition_concept_id = 201826
    -- OK: Filter on left table only

    SELECT co.* FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo
      ON co.visit_occurrence_id = vo.visit_occurrence_id
      AND vo.visit_concept_id = 9201
    -- OK: Right table filter in JOIN ON preserves LEFT JOIN semantics

Note:
    This is a WARNING, not an ERROR. Some developers intentionally use this pattern,
    though it's usually a mistake. IS NULL and IS NOT NULL checks are allowed as they
    are often intentional (finding unmatched rows or excluding them).
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


def _is_left_join(join: exp.Join) -> bool:
    """
    Robust LEFT JOIN detection across dialects.
    """
    kind = join.args.get("kind")
    side = join.args.get("side")

    return (
        (kind and _norm(kind) == "left")
        or (side and _norm(side) == "left")
    )


def _get_right_table(join: exp.Join) -> Optional[str]:
    """
    Get right table or alias (no mutation of alias map).
    """
    if isinstance(join.this, exp.Table):
        return _norm(join.this.alias_or_name)
    return None


def _collect_left_join_right_tables(tree: exp.Expression, aliases: Dict[str, str]) -> Set[str]:
    """Collect all right table names/aliases from LEFT JOINs."""
    right_tables = set()

    for join in tree.find_all(exp.Join):
        if not _is_left_join(join):
            continue

        t = _get_right_table(join)
        if t:
            right_tables.add(t)
            # Also add resolved table name if it's an alias
            resolved = aliases.get(t)
            if resolved and resolved != t:
                right_tables.add(resolved)

    return right_tables


def _is_safe_null_check(node: exp.Expression) -> bool:
    """
    Only IS NULL is safe for LEFT JOIN semantics.
    IS NOT NULL must NOT be ignored.
    """
    return isinstance(node, exp.Is) and not node.args.get("negated")


def _flatten_and(node: exp.Expression) -> List[exp.Expression]:
    if isinstance(node, exp.And):
        return _flatten_and(node.this) + _flatten_and(node.expression)
    return [node]


def _contains_or(node: exp.Expression) -> bool:
    return any(isinstance(n, exp.Or) for n in node.walk())


def _collect_where_right_table_filters(
    where_clause: exp.Expression,
    right_tables: Set[str],
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> List[str]:
    violations = []

    # Avoid OR complexity (reduce false positives)
    if _contains_or(where_clause):
        return violations

    conditions = _flatten_and(where_clause)

    for cond in conditions:
        # Skip safe IS NULL only
        if _is_safe_null_check(cond):
            continue

        # Skip subqueries (scope isolation)
        if isinstance(cond, (exp.Subquery, exp.Exists)):
            continue

        for col in cond.find_all(exp.Column):
            t, c = _resolve_column(col, aliases, cte_names)

            if not t or not c:
                continue

            # Check alias or table name
            if t in right_tables:
                violations.append(f"{t}.{c}")

            # Resolve alias → table
            resolved = aliases.get(t)
            if resolved and resolved in right_tables:
                violations.append(f"{t}.{c}")

    return violations


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> List[str]:
    issues: List[str] = []

    right_tables = _collect_left_join_right_tables(tree, aliases)

    if not right_tables:
        return issues

    for where in tree.find_all(exp.Where):
        violations = _collect_where_right_table_filters(
            where.this, right_tables, aliases, cte_names
        )

        if violations:
            cols = ", ".join(sorted(set(violations)))
            issues.append(
                f"LEFT JOIN followed by WHERE filtering right table column(s): {cols}. "
                f"This converts LEFT JOIN into INNER JOIN behavior. "
                f"Use INNER JOIN or move condition into JOIN ON."
            )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class LeftJoinThenWhereOnRightTableRule(Rule):
    """
    OMOP_149: Warn when LEFT JOIN is followed by WHERE filtering on right table.
    """

    rule_id = "joins.left_join_then_where_on_right_table"
    name = "Left Join Then Where On Right Table"

    description = (
        "Filtering right table columns in WHERE after LEFT JOIN "
        "turns it effectively into INNER JOIN."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use INNER JOIN or move filter into JOIN ON clause."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filters
        if "left" not in sql_lower or "join" not in sql_lower:
            return []

        if "where" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_149",
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


__all__ = ["LeftJoinThenWhereOnRightTableRule"]
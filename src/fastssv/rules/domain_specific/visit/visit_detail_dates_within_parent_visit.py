"""Visit Detail Dates Within Parent Visit Rule.

OMOP semantic rules CLIN_047, OMOP_510, OMOP_519: visit_detail_dates_within_parent_visit

visit_detail represents sub-visit details (ICU stays, ward transfers, operating
room time) that are nested within a parent visit_occurrence. By definition,
visit_detail dates must fall within the parent visit_occurrence date range.

The Problem:
    Queries that filter for visit_detail dates OUTSIDE the parent visit range
    indicate a logic error or misunderstanding of the visit hierarchy:
    - visit_detail_start_date should be >= visit_start_date
    - visit_detail_end_date should be <= visit_end_date

Violation patterns:
    -- Filtering for visit_detail that starts before parent visit
    SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vd.visit_detail_start_date < vo.visit_start_date
    -- This contradicts the nested hierarchy

    -- Filtering for visit_detail that ends after parent visit
    SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vd.visit_detail_end_date > vo.visit_end_date
    -- Sub-visit cannot extend beyond parent visit

Correct patterns:
    -- Check if visit_detail dates are properly within parent range
    SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vd.visit_detail_start_date >= vo.visit_start_date
      AND vd.visit_detail_end_date <= vo.visit_end_date

    -- Or use temporal containment check
    SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_start_date <= vd.visit_detail_start_date
      AND vd.visit_detail_end_date <= vo.visit_end_date
"""

from typing import List, Dict, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


VISIT_DETAIL = "visit_detail"
VISIT_OCCURRENCE = "visit_occurrence"
VISIT_OCCURRENCE_ID = "visit_occurrence_id"

VISIT_DETAIL_START_DATE = "visit_detail_start_date"
VISIT_DETAIL_END_DATE = "visit_detail_end_date"
VISIT_START_DATE = "visit_start_date"
VISIT_END_DATE = "visit_end_date"


def _norm(x: str) -> str:
    return normalize_name(x) if x else ""


def _is_in_or(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Or):
            return True
        parent = parent.parent
    return False


def _has_valid_join(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if visit_detail and visit_occurrence are properly joined via visit_occurrence_id."""
    # Check JOIN ON clauses
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left, right = eq.this, eq.expression

            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue

            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            if not (lt and lc and rt and rc):
                continue

            if _norm(lc) != VISIT_OCCURRENCE_ID or _norm(rc) != VISIT_OCCURRENCE_ID:
                continue

            if (
                (_norm(lt) == VISIT_DETAIL and _norm(rt) == VISIT_OCCURRENCE)
                or (_norm(rt) == VISIT_DETAIL and _norm(lt) == VISIT_OCCURRENCE)
            ):
                return True

    # Check WHERE clauses (for cross joins with WHERE filters)
    for where in tree.find_all(exp.Where):
        for eq in where.find_all(exp.EQ):
            left, right = eq.this, eq.expression

            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue

            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            if not (lt and lc and rt and rc):
                continue

            if _norm(lc) != VISIT_OCCURRENCE_ID or _norm(rc) != VISIT_OCCURRENCE_ID:
                continue

            if (
                (_norm(lt) == VISIT_DETAIL and _norm(rt) == VISIT_OCCURRENCE)
                or (_norm(rt) == VISIT_DETAIL and _norm(lt) == VISIT_OCCURRENCE)
            ):
                return True

    return False


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    violations: List[str] = []
    seen: Set[str] = set()

    if not _has_valid_join(tree, aliases):
        return violations

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_or(node):
            continue

        if not isinstance(node, (exp.LT, exp.LTE, exp.GT, exp.GTE)):
            continue

        left, right = node.this, node.expression

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not (lt and lc and rt and rc):
            continue

        key = f"{_norm(lt)}.{_norm(lc)}|{type(node).__name__}|{_norm(rt)}.{_norm(rc)}"
        if key in seen:
            continue

        # --- Violations ---
        if (
            _norm(lt) == VISIT_DETAIL and _norm(lc) == VISIT_DETAIL_START_DATE and
            _norm(rt) == VISIT_OCCURRENCE and _norm(rc) == VISIT_START_DATE and
            isinstance(node, (exp.LT, exp.LTE))
        ):
            seen.add(key)
            violations.append(
                "visit_detail_start_date occurs before visit_start_date. "
                "This may contradict the visit hierarchy."
            )

        elif (
            _norm(lt) == VISIT_DETAIL and _norm(lc) == VISIT_DETAIL_END_DATE and
            _norm(rt) == VISIT_OCCURRENCE and _norm(rc) == VISIT_END_DATE and
            isinstance(node, (exp.GT, exp.GTE))
        ):
            seen.add(key)
            violations.append(
                "visit_detail_end_date occurs after visit_end_date. "
                "This may contradict the visit hierarchy."
            )

        elif (
            _norm(lt) == VISIT_OCCURRENCE and _norm(lc) == VISIT_START_DATE and
            _norm(rt) == VISIT_DETAIL and _norm(rc) == VISIT_DETAIL_START_DATE and
            isinstance(node, (exp.GT, exp.GTE))
        ):
            seen.add(key)
            violations.append(
                "visit_start_date occurs after visit_detail_start_date. "
                "This may contradict the visit hierarchy."
            )

        elif (
            _norm(lt) == VISIT_OCCURRENCE and _norm(lc) == VISIT_END_DATE and
            _norm(rt) == VISIT_DETAIL and _norm(rc) == VISIT_DETAIL_END_DATE and
            isinstance(node, (exp.LT, exp.LTE))
        ):
            seen.add(key)
            violations.append(
                "visit_end_date occurs before visit_detail_end_date. "
                "This may contradict the visit hierarchy."
            )

    return violations


@register
class VisitDetailDatesWithinParentVisitRule(Rule):
    rule_id = "domain_specific.visit_detail_dates_within_parent_visit"
    name = "Visit Detail Dates Within Parent Visit"

    description = (
        "Detects conditions where visit_detail dates fall outside the parent "
        "visit_occurrence range."
    )

    severity = Severity.WARNING
    suggested_fix = (
        "Ensure visit_detail dates are within visit_start_date and visit_end_date"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not uses_table(tree, VISIT_DETAIL):
                continue

            if not uses_table(tree, VISIT_OCCURRENCE):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["VisitDetailDatesWithinParentVisitRule"]
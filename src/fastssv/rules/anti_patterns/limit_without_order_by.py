"""LIMIT/TOP Without ORDER BY Rule.

Detects ``LIMIT N`` or ``TOP N`` (or ``FETCH FIRST N``) on a query that
has no ``ORDER BY`` clause. Without an explicit order, the rows returned
are non-deterministic across engines, runs, and even successive
executions on the same engine — most engines make no ordering guarantee.

This is a frequent source of:

- Sampling bugs ("show me 100 patients" returns different patients each run)
- Pagination bugs (page 2 overlaps page 1)
- Test/CI flakiness when tests assert specific row content
- Subtle reproducibility bugs in cohort builds that LIMIT for performance

The rule is scoped to top-level (or CTE-level) SELECT statements that
declare a row limit; subqueries used for ``EXISTS`` or set-membership
correlation aren't flagged because they don't return rows directly.
"""

from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import add as patch_add, locate
from fastssv.core.registry import register


def _is_inside_existence_predicate(select: exp.Select) -> bool:
    """True if ``select`` lives inside an ``EXISTS (...)`` or ``x IN (...)``
    predicate — its row order is irrelevant in those contexts, so we
    don't flag a missing ORDER BY there.
    """
    parent = select.parent
    while parent is not None:
        if isinstance(parent, (exp.Exists, exp.In)):
            return True
        # If we hit an outer Select before the predicate boundary, this
        # subquery is being used as a derived table (FROM (SELECT ...))
        # and ordering still matters. Stop walking.
        if isinstance(parent, exp.Select) and parent is not select:
            return False
        parent = parent.parent
    return False


def _is_inside_inner_subquery(node: exp.Expression, root: exp.Expression) -> bool:
    """True if ``node`` is enclosed by an ``exp.Subquery`` that is a strict
    descendant of ``root`` — used to scope walks to a single projection
    expression without leaking into scalar subqueries inside it.
    """
    cursor = node.parent
    while cursor is not None and cursor is not root:
        if isinstance(cursor, exp.Subquery):
            return True
        cursor = cursor.parent
    return False


def _projection_contains_agg(expr: exp.Expression) -> bool:
    """True if any aggregate function appears within ``expr``, ignoring
    aggregates that live inside scalar subqueries (those don't aggregate
    the outer SELECT's rows)."""
    if isinstance(expr, exp.AggFunc):
        return True
    for node in expr.find_all(exp.AggFunc):
        if _is_inside_inner_subquery(node, expr):
            continue
        return True
    return False


def _projection_contains_bare_column(expr: exp.Expression) -> bool:
    """True if ``expr`` has a Column reference that is NOT wrapped by an
    aggregate (within ``expr``) and NOT inside a scalar subquery. A bare
    column reference makes the result multi-row, defeating scalar-agg
    suppression."""
    for col in expr.find_all(exp.Column):
        if _is_inside_inner_subquery(col, expr):
            continue
        cursor = col.parent
        inside_agg = False
        while cursor is not None and cursor is not expr.parent:
            if isinstance(cursor, exp.AggFunc):
                inside_agg = True
                break
            cursor = cursor.parent
        if not inside_agg:
            return True
    return False


def _is_scalar_aggregate(select: exp.Select) -> bool:
    """True if ``select`` provably returns exactly one row.

    Scalar aggregation has no ``GROUP BY`` / ``HAVING``, at least one
    projection contains an aggregate, and no projection has a bare
    (non-aggregated) column reference. In that shape, ``LIMIT N`` is a
    no-op — ordering is irrelevant — so the missing-ORDER-BY warning
    would be a false positive. ``SELECT COUNT(DISTINCT x) FROM ... LIMIT 1000``
    is the canonical Atlas/OHDSI pattern this skips.
    """
    if select.args.get("group") is not None:
        return False
    if select.args.get("having") is not None:
        return False
    if not select.expressions:
        return False

    has_any_agg = False
    for proj in select.expressions:
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(expr, exp.Star):
            return False
        if _projection_contains_agg(expr):
            has_any_agg = True
        if _projection_contains_bare_column(expr):
            return False
    return has_any_agg


def _select_has_explicit_limit(select: exp.Select) -> Optional[str]:
    """Return a short SQL fragment describing the row-limiting clause if
    one is present (LIMIT, FETCH FIRST, or T-SQL TOP), else None.

    Note: sqlglot normalizes T-SQL ``SELECT TOP N`` into a LIMIT node when
    parsed with ``dialect='tsql'``, so we only need to look at the
    ``limit`` and ``fetch`` slots.
    """
    limit = select.args.get("limit")
    if limit is not None:
        try:
            return limit.sql()
        except Exception:
            return "LIMIT"

    fetch = select.args.get("fetch")
    if fetch is not None:
        try:
            return fetch.sql()
        except Exception:
            return "FETCH"

    return None


def _select_has_order_by(select: exp.Select) -> bool:
    return select.args.get("order") is not None


@register
class LimitWithoutOrderByRule(Rule):
    """Warn on LIMIT / TOP / FETCH FIRST without an ORDER BY."""

    rule_id = "anti_patterns.limit_without_order_by"
    name = "LIMIT Without ORDER BY"

    description = (
        "A SELECT with LIMIT, TOP, or FETCH FIRST but no ORDER BY returns "
        "non-deterministic rows. Different engines and even successive runs "
        "may return different rows. Add ORDER BY to make the result reproducible."
    )

    severity = Severity.WARNING

    suggested_fix = "ADD: ORDER BY <stable_key> before LIMIT/TOP/FETCH. Use the table's primary key (e.g. ORDER BY condition_occurrence_id) or a composite key that uniquely identifies a row."
    long_description = (
        "Most SQL engines make no guarantee about row order in the absence of "
        "an explicit ``ORDER BY`` clause. Pairing ``LIMIT N`` (or ``TOP N``, "
        "or ``FETCH FIRST N ROWS ONLY``) with no ordering produces "
        'non-deterministic results: a query that asks for "100 patients" '
        "may return a different 100 each run, depending on plan choice, "
        "parallel-execution interleaving, or storage layout. The same shape "
        "breaks pagination (page 2 may overlap page 1) and CI tests that "
        "assert on specific rows. The fix is always the same: add a stable "
        "``ORDER BY`` (typically the table's primary key or a composite key "
        "that uniquely identifies a row) before the row-limiting clause."
    )

    example_bad = "SELECT person_id, condition_concept_id\nFROM condition_occurrence\nLIMIT 100;"
    example_good = (
        "SELECT person_id, condition_concept_id\n"
        "FROM condition_occurrence\n"
        "ORDER BY condition_occurrence_id\n"
        "LIMIT 100;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        # Fast pre-filter: must mention at least one row-limiting keyword
        if not any(kw in sql_lower for kw in ("limit", "top ", "fetch")):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: set = set()

        for tree in trees:
            if not tree:
                continue

            for select in tree.find_all(exp.Select):
                if _is_inside_existence_predicate(select):
                    continue

                limit_sql = _select_has_explicit_limit(select)
                if not limit_sql:
                    continue

                if _select_has_order_by(select):
                    continue

                if _is_scalar_aggregate(select):
                    continue

                # Use the SELECT's text fingerprint so we don't double-fire
                # on the same select walked twice.
                key = id(select)
                if key in seen:
                    continue
                seen.add(key)

                # Mechanical ADD: insert `ORDER BY <stable_key>\n` directly
                # before the row-limiting clause. The actual key is unknown
                # (a primary key on the leading table is the usual choice),
                # so emit a `<stable_key>` placeholder for the outer loop
                # / LLM to resolve.
                patch = None
                span = locate(sql, limit_sql)
                if span is not None:
                    patch = patch_add(span[0], "ORDER BY <stable_key>\n")

                violations.append(
                    self.create_violation(
                        message=(
                            f"SELECT uses `{limit_sql}` without ORDER BY. "
                            f"Row order is non-deterministic — successive runs "
                            f"may return different rows."
                        ),
                        suggested_fix_patch=patch,
                        details={
                            "limit_clause": limit_sql,
                        },
                    )
                )

        return violations


__all__ = ["LimitWithoutOrderByRule"]

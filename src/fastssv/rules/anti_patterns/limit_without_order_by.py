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

Every SELECT or set operation (UNION / INTERSECT / EXCEPT) that declares a
row limit (LIMIT, TOP, FETCH FIRST, OFFSET, Oracle ``ROWNUM <= n``) is checked,
including CTEs, derived tables, scalar subqueries and ``IN (...)`` lists, where
the arbitrary subset feeds the enclosing query.

Exempt, because the result cannot depend on which rows are kept:
- the query of an ``EXISTS (...)``, where only existence matters;
- a limited derived table or CTE whose only reader counts its rows
  (``SELECT COUNT(*) FROM (SELECT ... LIMIT 1) q``);
- a query that returns at most one row by construction: no FROM clause, an
  aggregate without GROUP BY (or with ``GROUP BY ()``), or a FROM list whose
  sources are all one row; and a set operation whose arms are all such queries
  with a LIMIT no smaller than the arm count;
- ``LIMIT 0``, which returns no rows, and ``LIMIT ALL`` / ``LIMIT NULL``, which
  mean no limit (PostgreSQL).
"""

from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.deduplication import scope_key
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import add as patch_add, locate
from fastssv.core.registry import register


def _is_inside_exists(node: exp.Expression) -> bool:
    """True if ``node`` is the query of an ``EXISTS (...)`` predicate, where only row existence matters.

    ``x IN (SELECT ... LIMIT n)`` is not exempt: which n rows survive decides the IN-list."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Exists):
            return True
        if isinstance(parent, (exp.Select, exp.SetOperation)):
            return False
        parent = parent.parent
    return False


def _is_nested(node: exp.Expression) -> bool:
    """True if the limited query feeds an enclosing query (CTE, derived table, subquery, set-operation arm)."""
    return node.find_ancestor(exp.Select, exp.SetOperation) is not None


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


def _projection_contains_window(expr: exp.Expression) -> bool:
    """True if ``expr`` contains a window function (``f(...) OVER (...)``).

    Window functions are *per-row* — they compute over a frame but produce
    one output row per input row, so a SELECT with any window projection
    cannot be a scalar aggregation no matter what aggregates appear
    inside the OVER. sqlglot parses ``SUM(x) OVER (...)`` as an
    ``exp.Window`` node *wrapping* an ``exp.AggFunc`` (the inner Sum),
    so the inner aggregate is not evidence of a scalar aggregate: any Window
    in the projection is a veto.
    """
    if isinstance(expr, exp.Window):
        return True
    for node in expr.find_all(exp.Window):
        if _is_inside_inner_subquery(node, expr):
            continue
        return True
    return False


def _is_scalar_aggregate(select: exp.Select) -> bool:
    """True if ``select`` provably returns at most one row.

    An aggregate query without GROUP BY forms a single group and returns one row (PostgreSQL, SELECT): no
    ``GROUP BY`` other than ``GROUP BY ()``, no ``HAVING``, no window function in any projection (windowed
    aggregates are per-row), at least one aggregate, and no bare column. A SELECT without FROM also returns one
    row. ``LIMIT N`` cuts nothing from such a query, so ordering cannot matter.
    """
    group = select.args.get("group")
    if group is not None and not all(isinstance(e, exp.Tuple) and not e.expressions for e in group.expressions):
        return False
    if not (select.args.get("from_") or select.args.get("from")) and select.expressions:
        return not any(isinstance(p, exp.Star) for p in select.expressions)
    if select.args.get("having") is not None:
        return False
    if not select.expressions:
        return False

    has_any_agg = False
    for proj in select.expressions:
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(expr, exp.Star):
            return False
        # Veto: any window function makes the SELECT non-scalar.
        if _projection_contains_window(expr):
            return False
        if _projection_contains_agg(expr):
            has_any_agg = True
        if _projection_contains_bare_column(expr):
            return False
    return has_any_agg


def _limit_is_zero(node: exp.Expression) -> bool:
    """``LIMIT 0`` returns no rows (a common schema probe), so ordering cannot matter."""
    limit = node.args.get("limit")
    value = limit.expression if isinstance(limit, exp.Limit) else None
    return isinstance(value, exp.Literal) and not value.is_string and str(value.this) == "0"


def _set_operation_arms(node: exp.Expression) -> List[exp.Expression]:
    while isinstance(node, exp.Subquery):
        node = node.this
    if isinstance(node, exp.SetOperation):
        return _set_operation_arms(node.this) + _set_operation_arms(node.expression)
    return [node]


def _limit_covers_scalar_arms(node: exp.SetOperation) -> bool:
    """True if every arm returns exactly one row and the LIMIT is at least the arm count: nothing is cut."""
    limit = node.args.get("limit")
    value = limit.expression if isinstance(limit, exp.Limit) else None
    if not isinstance(value, exp.Literal) or value.is_string:
        return False
    arms = _set_operation_arms(node)
    if not all(isinstance(a, exp.Select) and _is_scalar_aggregate(a) for a in arms):
        return False
    return int(value.this) >= len(arms)


def _is_unbounded_limit(limit: exp.Expression) -> bool:
    """``LIMIT ALL`` and ``LIMIT NULL`` mean no limit in PostgreSQL, so no rows are cut."""
    value = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(value, exp.Null):
        return True
    return (
        isinstance(value, exp.Column)
        and not value.table
        and isinstance(value.this, exp.Identifier)
        and not value.this.quoted
        and value.name.upper() == "ALL"
    )


def _rownum_limit(select: exp.Select) -> Optional[str]:
    """Oracle ``WHERE ROWNUM <= n``: a row limit written as a predicate."""
    where = select.args.get("where")
    if where is None:
        return None
    from_node = select.args.get("from_") or select.args.get("from")
    src = from_node.this if from_node is not None else None
    if not select.args.get("joins") and isinstance(src, exp.Subquery) and src.this.args.get("order") is not None:
        # Oracle top-n idiom: ROWNUM numbers the rows of an ordered subquery.
        return None
    for cmp in where.find_all(exp.LT, exp.LTE, exp.EQ):
        if cmp.find_ancestor(exp.Select) is not select:
            continue
        for side in (cmp.this, cmp.expression):
            if isinstance(side, exp.Column) and not side.table and side.name.upper() == "ROWNUM":
                return cmp.sql()
    return None


def _counted_only(node: exp.Expression) -> bool:
    """True if ``node`` is a derived table or CTE body whose readers only count its rows: COUNT(*) does not depend
    on which rows a LIMIT keeps."""
    parent = node.parent
    readers: List[exp.Select] = []
    if isinstance(parent, exp.Subquery) and isinstance(parent.parent, (exp.From, exp.Join)):
        outer = parent.find_ancestor(exp.Select)
        if outer is not None:
            readers.append(outer)
    elif isinstance(parent, exp.CTE):
        scope = parent.parent.parent if isinstance(parent.parent, exp.With) else None
        name = parent.alias_or_name.lower()
        if scope is None:
            return False
        for table in scope.find_all(exp.Table):
            if table.name.lower() == name and not table.args.get("db") and table.find_ancestor(exp.CTE) is not parent:
                outer = table.find_ancestor(exp.Select)
                if outer is None:
                    return False
                readers.append(outer)
    if not readers:
        return False
    for outer in readers:
        if outer.args.get("joins") or outer.args.get("where") or outer.args.get("group") or outer.args.get("having"):
            return False
        for proj in outer.expressions:
            expr = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(expr, exp.Literal):
                continue
            if not (isinstance(expr, exp.Count) and isinstance(expr.this, exp.Star)):
                return False
    return True


def _all_sources_one_row(select: exp.Select) -> bool:
    from fastssv.rules.anti_patterns._ap_product import Scope

    if not (select.args.get("from_") or select.args.get("from")):
        return False
    sources = Scope(select).sources
    return bool(sources) and all(s.one_row for s in sources)


def _select_has_explicit_limit(select: exp.Select) -> Optional[str]:
    """Return a short SQL fragment describing the row-limiting clause if
    one is present (LIMIT, FETCH FIRST, or T-SQL TOP), else None.

    Note: sqlglot normalizes T-SQL ``SELECT TOP N`` into a LIMIT node when
    parsed with ``dialect='tsql'``, so we only need to look at the
    ``limit`` and ``fetch`` slots.
    """
    limit = select.args.get("limit")
    if limit is not None and not _is_unbounded_limit(limit):
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

    offset = select.args.get("offset")
    if offset is not None:
        value = offset.expression if isinstance(offset, exp.Offset) else None
        if not (isinstance(value, exp.Literal) and str(value.this) == "0") and not isinstance(value, exp.Null):
            return offset.sql()

    return _rownum_limit(select) if isinstance(select, exp.Select) else None


def _select_has_order_by(select: exp.Select) -> bool:
    return select.args.get("order") is not None


@register
class LimitWithoutOrderByRule(Rule):
    """Warn on LIMIT / TOP / FETCH FIRST without an ORDER BY."""

    rule_id = "anti_patterns.limit_without_order_by"
    name = "LIMIT Without ORDER BY"

    description = (
        "A SELECT or UNION with LIMIT, TOP, FETCH FIRST or OFFSET but no ORDER BY returns "
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
    extra_examples = (
        RuleExample(
            sql=(
                "WITH sample_patients AS (\n"
                "    SELECT person_id\n"
                "    FROM condition_occurrence\n"
                "    WHERE condition_concept_id = 201826\n"
                "    LIMIT 100\n"
                ")\n"
                "SELECT COUNT(*)\n"
                "FROM drug_exposure de\n"
                "JOIN sample_patients sp ON de.person_id = sp.person_id;"
            ),
            severity=Severity.WARNING,
            note="Limited CTE feeding an enclosing query: the outer count depends on which rows survive",
        ),
    )
    references = (
        Reference(
            "PostgreSQL documentation: LIMIT and OFFSET", "https://www.postgresql.org/docs/current/queries-limit.html"
        ),
        Reference(
            "SQL Server documentation: TOP (Transact-SQL), best practices",
            "https://learn.microsoft.com/en-us/sql/t-sql/queries/top-transact-sql#best-practices",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        # Fast pre-filter: must mention at least one row-limiting keyword
        if not any(kw in sql_lower for kw in ("limit", "top", "fetch", "offset", "rownum")):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: set = set()

        for tree in trees:
            if not tree:
                continue

            for node in tree.find_all(exp.Select, exp.SetOperation):
                if _is_inside_exists(node):
                    continue

                limit_sql = _select_has_explicit_limit(node)
                if not limit_sql:
                    continue

                if _select_has_order_by(node) or _limit_is_zero(node):
                    continue

                if isinstance(node, exp.Select) and (_is_scalar_aggregate(node) or _all_sources_one_row(node)):
                    continue

                if _counted_only(node):
                    continue

                if isinstance(node, exp.SetOperation) and _limit_covers_scalar_arms(node):
                    continue

                key = id(node)
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

                kind = "SELECT" if isinstance(node, exp.Select) else node.key.upper()
                nested = _is_nested(node)
                message = (
                    f"{kind} uses `{limit_sql}` without ORDER BY. "
                    f"Row order is non-deterministic — successive runs "
                    f"may return different rows."
                )
                if nested:
                    message += (
                        " This query feeds an enclosing query: whenever more rows qualify than the limit, "
                        "which rows survive, and so the enclosing result, is arbitrary."
                    )

                violations.append(
                    self.create_violation(
                        message=message,
                        suggested_fix_patch=patch,
                        details={
                            "limit_clause": limit_sql,
                            "nested": nested,
                            "limit_scope": scope_key(node),
                        },
                    )
                )

        return violations


__all__ = ["LimitWithoutOrderByRule"]

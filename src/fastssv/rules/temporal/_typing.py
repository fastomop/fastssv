"""Lineage-based typing of date expressions, shared by the temporal rules.

``sql_type`` gives the PostgreSQL / DuckDB result type of an expression built from CDM date columns: a column is
followed through CTEs, derived tables and set operations to the CDM column or the expression that produces it, so a
name such as ``first_date`` says nothing about its type. The operator results follow the PostgreSQL date/time operator
table (``date - date`` is integer, ``date + interval`` and ``date - interval`` are timestamp, ``timestamp - timestamp``
is interval).
"""

import re
from datetime import datetime
from typing import List, NamedTuple, Optional, Tuple

from sqlglot import exp

from fastssv.core.helpers import (
    _explicit_projection,
    _has_modified_star,
    _pick_source,
    _provides,
    _query_branches,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    _star_qualifiers,
    column_origins,
    normalize_name,
)
from fastssv.rules.temporal._cdm_dates import DATE_COLUMN_TYPES
from fastssv.schemas.cdm_column_types import INTEGER, get_column_type

DATE, TIMESTAMP, INTERVAL, INT = "date", "timestamp", "interval", "integer"

_MAX_DEPTH = 12
_DATE_TYPES = {exp.DataType.Type.DATE, exp.DataType.Type.DATE32}
_INTERVAL_TYPES = {exp.DataType.Type.INTERVAL}
_INT_TYPES = {
    exp.DataType.Type.INT,
    exp.DataType.Type.BIGINT,
    exp.DataType.Type.SMALLINT,
    exp.DataType.Type.TINYINT,
}
_NOW_NAMES = {"now", "getdate", "sysdate", "sysdatetime", "current_timestamp", "localtimestamp", "getutcdate"}
_TODAY_NAMES = {"current_date", "today", "curdate"}
_DATE_TEXT = re.compile(r"^\s*(\d{4})[-/]?(\d{2})[-/]?(\d{2})\s*$")
_TIMESTAMP_TEXT = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?")


class Terminal(NamedTuple):
    """Where a column value comes from: a base-table column, or the expression a query projects it from."""

    select: exp.Select
    table: Optional[str] = None
    column: Optional[str] = None
    alias: Optional[str] = None
    expr: Optional[exp.Expression] = None


def _through(select: exp.Select, src: exp.Expression, name: str, depth: int) -> Optional[List[Terminal]]:
    query = _source_query(select, src)
    if query is None:
        if isinstance(src, exp.Table):
            return [Terminal(select, normalize_name(src.name), name, normalize_name(src.alias_or_name))]
        return None
    branches = _query_branches(query)
    if not branches:
        return None
    # A set operation names its columns after the first branch and lines the others up by position.
    position = None
    if len(branches) > 1 and not _star_qualifiers(branches[0]):
        names = [normalize_name(p.alias_or_name) for p in branches[0].expressions]
        position = names.index(name) if name in names else None
    out: List[Terminal] = []
    for branch in branches:
        if position is not None and not _star_qualifiers(branch) and position < len(branch.expressions):
            proj = branch.expressions[position]
        else:
            proj = _explicit_projection(branch, name)
        if proj is not None:
            value = proj.this if isinstance(proj, exp.Alias) else proj
            inner = value
            while isinstance(inner, exp.Paren):
                inner = inner.this
            if isinstance(inner, exp.Column):
                found = _resolve(
                    branch, normalize_name(inner.table) if inner.table else None, normalize_name(inner.name), depth + 1
                )
            else:
                found = [Terminal(branch, expr=value)]
        else:
            stars = _star_qualifiers(branch)
            if not stars or _has_modified_star(branch):
                return None
            candidates = [
                s for s in _select_sources(branch) if None in stars or normalize_name(s.alias_or_name) in stars
            ]
            picked = _pick_source(branch, candidates, name, depth)
            found = _through(branch, picked, name, depth + 1) if picked is not None else None
        if found is None:
            return None
        out.extend(found)
    return out


def _resolve(select: Optional[exp.Select], qualifier: Optional[str], name: str, depth: int) -> Optional[List[Terminal]]:
    if select is None or depth > _MAX_DEPTH:
        return None
    sources = _select_sources(select)
    if qualifier:
        matches = [s for s in sources if normalize_name(s.alias_or_name) == qualifier]
        if len(matches) == 1:
            return _through(select, matches[0], name, depth)
        if matches:
            return None
        outer, src = _source_by_qualifier(select, qualifier)
        return _through(outer, src, name, depth) if src is not None else None
    src = _pick_source(select, sources, name, depth)
    if src is None or (len(sources) == 1 and _provides(select, src, name, depth) is False):
        # An unqualified name the local sources do not supply is a correlated reference to an enclosing SELECT.
        parent = select.parent
        outer = parent.find_ancestor(exp.Select) if parent is not None else None
        return _resolve(outer, None, name, depth + 1) if outer is not None else None
    return _through(select, src, name, depth)


def terminals(col: exp.Column) -> Optional[List[Terminal]]:
    """Base columns and projected expressions ``col`` reads; None when its source cannot be settled."""
    select = col.find_ancestor(exp.Select)
    if select is None or isinstance(col.this, exp.Star):
        return None
    return _resolve(select, normalize_name(col.table) if col.table else None, normalize_name(col.name), 0)


def base_column(col: exp.Column) -> Optional[Tuple[str, str]]:
    """The single CDM ``(table, column)`` a reference reads through renames, casts, CTEs and derived tables."""
    origins = column_origins(col)
    if origins is None or len(origins) != 1:
        return None
    return next(iter(origins))


def unwrap(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _cast_type(to: exp.Expression) -> Optional[str]:
    kind = to.this if isinstance(to, exp.DataType) else None
    if kind in _DATE_TYPES:
        return DATE
    if kind in exp.DataType.TEMPORAL_TYPES:
        return TIMESTAMP
    if kind in _INTERVAL_TYPES:
        return INTERVAL
    if kind in _INT_TYPES:
        return INT
    return None


def is_now(node: Optional[exp.Expression]) -> bool:
    """CURRENT_DATE, NOW(), GETDATE() and their relatives, including a cast or a bare ``(SELECT CURRENT_DATE)``."""
    node = unwrap(node)
    if isinstance(node, (exp.Cast, exp.TryCast, exp.TsOrDsToDate, exp.Date)) and node.this is not None:
        return is_now(node.this)
    if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Select):
        inner = node.this
        if len(inner.expressions) == 1 and not (inner.args.get("from_") or inner.args.get("from")):
            return is_now(inner.expressions[0])
        return False
    if isinstance(node, (exp.CurrentDate, exp.CurrentTimestamp, exp.CurrentDatetime, exp.Localtimestamp)):
        return True
    if isinstance(node, (exp.Anonymous, exp.Column)) and not getattr(node, "table", None):
        name = normalize_name(node.name)
        if isinstance(node, exp.Anonymous):
            return name in _NOW_NAMES | _TODAY_NAMES
        return name in {"sysdate", "localtimestamp"}
    return False


def sql_type(node: Optional[exp.Expression], depth: int = 0) -> Optional[str]:
    """DATE, TIMESTAMP, INTERVAL or INT for an expression whose type can be settled; None otherwise."""
    node = unwrap(node)
    if node is None or depth > _MAX_DEPTH:
        return None
    if isinstance(node, (exp.Cast, exp.TryCast)):
        return _cast_type(node.to)
    if isinstance(node, exp.Convert) and isinstance(node.this, exp.DataType):
        return _cast_type(node.this)
    if isinstance(node, (exp.TsOrDsToDate, exp.Date, exp.StrToDate, exp.DateFromParts, exp.CurrentDate)):
        return DATE
    if isinstance(
        node, (exp.CurrentTimestamp, exp.CurrentDatetime, exp.Localtimestamp, exp.TimestampTrunc, exp.DateTrunc)
    ):
        return TIMESTAMP
    if isinstance(node, (exp.Interval, exp.MakeInterval)):
        return INTERVAL
    if isinstance(node, exp.Anonymous):
        name = normalize_name(node.name)
        if name in {"make_date", "datefromparts", "date_from_parts"} | _TODAY_NAMES:
            return DATE
        if name in _NOW_NAMES:
            return TIMESTAMP
        if name in {"make_interval", "age"}:
            return INTERVAL
        return None
    if isinstance(node, exp.Literal):
        return None if node.is_string else (INT if re.fullmatch(r"\d+", str(node.this)) else None)
    if isinstance(node, exp.Neg):
        return sql_type(node.this, depth + 1)
    if isinstance(node, exp.Abs):
        return sql_type(node.this, depth + 1)
    if isinstance(node, (exp.Min, exp.Max)):
        return sql_type(node.this, depth + 1)
    if isinstance(node, (exp.Coalesce, exp.Greatest, exp.Least)):
        kinds = {sql_type(a, depth + 1) for a in [node.this, *node.expressions]}
        return kinds.pop() if len(kinds) == 1 else None
    if isinstance(node, exp.Column):
        found = terminals(node)
        if not found:
            return None
        kinds = set()
        for t in found:
            if t.expr is not None:
                kinds.add(sql_type(t.expr, depth + 1))
            else:
                declared = DATE_COLUMN_TYPES.get(t.table, {}).get(t.column)
                if declared is not None:
                    kinds.add(DATE if declared == "date" else TIMESTAMP)
                else:
                    kinds.add(INT if get_column_type(t.table, t.column) == INTEGER else None)
        return kinds.pop() if len(kinds) == 1 else None
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul)):
        left, right = sql_type(node.this, depth + 1), sql_type(node.expression, depth + 1)
        if left is None or right is None:
            return None
        if isinstance(node, exp.Mul):
            return INTERVAL if {left, right} == {INT, INTERVAL} else (INT if left == right == INT else None)
        if isinstance(node, exp.Add):
            table = {
                (DATE, INT): DATE, (INT, DATE): DATE, (DATE, INTERVAL): TIMESTAMP, (INTERVAL, DATE): TIMESTAMP,
                (TIMESTAMP, INTERVAL): TIMESTAMP, (INTERVAL, TIMESTAMP): TIMESTAMP, (INT, INT): INT,
                (INTERVAL, INTERVAL): INTERVAL,
            }  # fmt: skip
            return table.get((left, right))
        table = {
            (DATE, DATE): INT, (DATE, INT): DATE, (DATE, INTERVAL): TIMESTAMP, (TIMESTAMP, TIMESTAMP): INTERVAL,
            (TIMESTAMP, DATE): INTERVAL, (DATE, TIMESTAMP): INTERVAL, (TIMESTAMP, INTERVAL): TIMESTAMP,
            (INT, INT): INT, (INTERVAL, INTERVAL): INTERVAL,
        }  # fmt: skip
        return table.get((left, right))
    return None


def literal_datetime(node: Optional[exp.Expression]) -> Optional[Tuple[datetime, bool]]:
    """``(value, date_only)`` for a constant calendar value: ``'2023-01-31'``, ``'20230131'``, ``DATE '...'``,
    ``CAST('...' AS DATE)``, ``TIMESTAMP '2023-01-31 08:00'``, ``TO_DATE('...', fmt)``, ``MAKE_DATE(y, m, d)`` /
    ``DATEFROMPARTS(y, m, d)``. ``date_only`` is True when no time of day is written."""
    node = unwrap(node)
    if node is None:
        return None
    if isinstance(node, (exp.Cast, exp.TryCast)):
        inner = literal_datetime(node.this)
        if inner is None:
            return None
        if _cast_type(node.to) == DATE:
            return inner[0].replace(hour=0, minute=0, second=0, microsecond=0), True
        return inner if _cast_type(node.to) == TIMESTAMP else None
    if isinstance(node, (exp.TsOrDsToDate, exp.Date, exp.StrToDate, exp.TimeStrToDate, exp.TimeStrToTime)):
        return literal_datetime(node.this)
    if isinstance(node, exp.Literal) and node.is_string:
        text = str(node.this)
        try:
            m = _DATE_TEXT.match(text)
            if m:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))), True
            m = _TIMESTAMP_TEXT.match(text)
            if m:
                parts = [int(g) if g else 0 for g in m.groups()]
                return datetime(*parts), False
        except ValueError:
            return None
        return None
    parts: List[Optional[exp.Expression]] = []
    if isinstance(node, exp.DateFromParts):
        parts = [node.args.get("year"), node.args.get("month"), node.args.get("day")]
    elif isinstance(node, exp.Anonymous) and normalize_name(node.name) in {
        "make_date",
        "datefromparts",
        "date_from_parts",
    }:
        parts = list(node.expressions)
    if len(parts) == 3 and all(isinstance(p, exp.Literal) and not p.is_string for p in parts):
        try:
            return datetime(*(int(p.this) for p in parts)), True
        except ValueError:
            return None
    return None


def conjuncts(node: Optional[exp.Expression]) -> List[exp.Expression]:
    """AND-ed leaves of a predicate; an OR / NOT subtree stays one leaf and is never looked into."""
    node = unwrap(node)
    if node is None:
        return []
    if isinstance(node, exp.And):
        return conjuncts(node.this) + conjuncts(node.expression)
    return [node]

"""Row identity and predicate position, shared by the domain_specific rules that reason about one table row.

A column reference is traced through casts, CTEs and derived tables to the base-table read it comes from. The
trace keeps the chain of FROM/JOIN sources it passed through, so two references denote the same row exactly when
one chain ends with the other: a filter written inside a CTE applies to every row read from that CTE, while two
aliases of the same CTE (a self-join) stay apart.
"""

from typing import Dict, Iterable, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.helpers import (
    _explicit_projection,
    _has_modified_star,
    _pick_source,
    _query_branches,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    _star_qualifiers,
    normalize_name,
)

# Chain of (SELECT, source alias) steps from the reference down to the base table read.
RowPath = Tuple[Tuple[int, str], ...]
# (row path, base table, base column)
Read = Tuple[RowPath, str, str]

_PASS_THROUGH = (exp.Cast, exp.TryCast, exp.Paren)
_MAX_DEPTH = 12


def peel(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    """``node`` without casts and parentheses: they change neither the row nor the value compared."""
    while isinstance(node, _PASS_THROUGH):
        node = node.this
    return node


def _through(scope: exp.Select, src: exp.Expression, name: str, depth: int) -> Optional[Read]:
    step = (id(scope), normalize_name(src.alias_or_name))
    query = _source_query(scope, src)
    if query is None:
        if not (isinstance(src, exp.Table) and src.name):
            return None
        return (step,), normalize_name(src.name), name
    branches = _query_branches(query)
    # A set operation merges rows of several reads; no single row stands behind the column.
    if len(branches) != 1 or depth > _MAX_DEPTH:
        return None
    branch = branches[0]
    proj = _explicit_projection(branch, name)
    if proj is not None:
        expr = peel(proj.this if isinstance(proj, exp.Alias) else proj)
        inner = read_of(expr, depth + 1) if isinstance(expr, exp.Column) else None
    else:
        stars = _star_qualifiers(branch)
        if not stars or _has_modified_star(branch):
            return None
        candidates = [s for s in _select_sources(branch) if None in stars or normalize_name(s.alias_or_name) in stars]
        picked = _pick_source(branch, candidates, name, depth)
        inner = _through(branch, picked, name, depth + 1) if picked is not None else None
    if inner is None:
        return None
    return (step,) + inner[0], inner[1], inner[2]


def read_of(node: Optional[exp.Expression], depth: int = 0) -> Optional[Read]:
    """The base-table column ``node`` reads unchanged (through casts, renames, CTEs, derived tables), with the row
    path it is read through; None for a computed value or a reference that cannot be resolved."""
    col = peel(node)
    if not isinstance(col, exp.Column) or isinstance(col.this, exp.Star) or depth > _MAX_DEPTH:
        return None
    select = col.find_ancestor(exp.Select)
    if select is None:
        return None
    name = normalize_name(col.name)
    if col.table:
        scope, src = _source_by_qualifier(select, normalize_name(col.table))
    else:
        scope, src = select, _pick_source(select, _select_sources(select), name, depth)
    if scope is None or src is None:
        return None
    return _through(scope, src, name, depth)


def projection_behind(col: exp.Column) -> Optional[exp.Expression]:
    """The expression a CTE / derived table projects under the name ``col`` reads, when it is computed there
    (``end_date - start_date AS los``); None for a base column or an unresolvable reference."""
    select = col.find_ancestor(exp.Select)
    if select is None:
        return None
    name = normalize_name(col.name)
    if col.table:
        scope, src = _source_by_qualifier(select, normalize_name(col.table))
    else:
        scope, src = select, _pick_source(select, _select_sources(select), name, 0)
    if scope is None or src is None:
        return None
    query = _source_query(scope, src)
    branches = _query_branches(query) if query is not None else []
    if len(branches) != 1:
        return None
    proj = _explicit_projection(branches[0], name)
    if proj is None:
        return None
    return proj.this if isinstance(proj, exp.Alias) else proj


def same_row(a: RowPath, b: RowPath) -> bool:
    """Both paths end at the same base read and one passes through the other's sources."""
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer[len(longer) - len(shorter) :] == shorter


def group_by_row(items: Iterable[Tuple[RowPath, object]]) -> List[Tuple[RowPath, List[object]]]:
    """Payloads grouped per row: each distinct path collects its own payloads and those attached to a path it
    passes through (a filter inside a CTE holds for every row read from the CTE). Paths that are only a prefix of
    another reported path are dropped, so one row is reported once."""
    by_path: Dict[RowPath, List[object]] = {}
    for path, payload in items:
        by_path.setdefault(path, []).append(payload)
    groups: List[Tuple[RowPath, List[object]]] = []
    for path in by_path:
        if any(other != path and len(other) > len(path) and same_row(path, other) for other in by_path):
            continue
        merged: List[object] = []
        for other, payloads in by_path.items():
            if len(other) <= len(path) and same_row(path, other):
                merged.extend(payloads)
        groups.append((path, merged))
    return groups


def restricting_clause(node: exp.Expression) -> Optional[exp.Expression]:
    """The WHERE, HAVING or JOIN whose rows ``node`` restricts: reached through AND and parentheses only. Under OR,
    NOT, CASE or a function the predicate does not have to hold for every returned row."""
    parent = node.parent
    while isinstance(parent, (exp.And, exp.Paren)):
        parent = parent.parent
    return parent if isinstance(parent, (exp.Where, exp.Having, exp.Join)) else None


def restricts_rows(node: exp.Expression) -> bool:
    """``node`` must hold for every row its SELECT returns: a conjunct of WHERE, HAVING or an inner join's ON. In an
    outer join's ON it only decides which rows of the optional side are attached."""
    clause = restricting_clause(node)
    if isinstance(clause, exp.Join):
        side = normalize_name(clause.side or "")
        kind = normalize_name(clause.kind or "")
        return side not in {"left", "right", "full"} and kind not in {"outer", "anti", "semi"}
    return clause is not None


def in_negated_scope(node: exp.Expression) -> bool:
    """``node`` sits under NOT (including NOT EXISTS / NOT IN subqueries) or in the right branch of EXCEPT: its rows
    are the ones the query removes."""
    child, parent = node, node.parent
    while parent is not None:
        if isinstance(parent, exp.Not):
            return True
        if isinstance(parent, exp.Except) and parent.expression is child:
            return True
        child, parent = parent, parent.parent
    return False


def int_literal(node: Optional[exp.Expression]) -> Optional[int]:
    """Integer value of a numeric literal, a negated one, or a quoted integer (``'8554'``); else None."""
    node = peel(node)
    sign = 1
    if isinstance(node, exp.Neg):
        sign, node = -1, peel(node.this)
    if isinstance(node, exp.Literal):
        try:
            return sign * int(str(node.this).strip())
        except ValueError:
            return None
    return None


def number_literal(node: Optional[exp.Expression]) -> Optional[float]:
    """Numeric value of an unquoted numeric literal (optionally negated or cast); else None."""
    node = peel(node)
    sign = 1.0
    if isinstance(node, exp.Neg):
        sign, node = -1.0, peel(node.this)
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return sign * float(node.this)
        except ValueError:
            return None
    return None


__all__ = [
    "Read",
    "RowPath",
    "group_by_row",
    "in_negated_scope",
    "int_literal",
    "number_literal",
    "peel",
    "projection_behind",
    "read_of",
    "restricting_clause",
    "restricts_rows",
    "same_row",
]

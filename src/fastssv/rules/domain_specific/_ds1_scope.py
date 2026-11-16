"""Read-instance lineage and predicate-context helpers shared by the domain_specific rules.

A *read instance* is one FROM/JOIN entry of one SELECT. Rules that relate two columns of the same row (a key and the
column that qualifies it, an amount and its currency) must know that both references reach the same read instance,
and by which route, so that a filter on one alias, UNION branch or subquery is never credited to another.
"""

from typing import FrozenSet, Iterator, List, Optional, Set, Tuple

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
    normalize_name,
    unwrap_cast,
)

# One step of a route: (id of the SELECT, alias of the FROM/JOIN source read in it).
Hop = Tuple[int, str]
# The route from a column reference to a base table: outermost reference first, base-table read last.
Path = Tuple[Hop, ...]
# (route, base table, base column)
Origin = Tuple[Path, str, str]

_MAX_DEPTH = 12


def trace_source(select: exp.Select, src: exp.Expression, name: str, depth: int = 0, path: Path = ()) -> Set[Origin]:
    """Base columns that output column ``name`` of FROM/JOIN source ``src`` carries unchanged (casts and renaming
    aliases are followed; a computed value has no origin)."""
    if depth > _MAX_DEPTH:
        return set()
    path = path + ((id(select), normalize_name(src.alias_or_name)),)
    query = _source_query(select, src)
    if query is None:
        if isinstance(src, exp.Table) and src.name:
            return {(path, normalize_name(src.name), name)}
        return set()
    branches = _query_branches(query)
    # A set operation names its columns after the first branch and lines later branches up by position.
    position = None
    if len(branches) > 1 and not _star_qualifiers(branches[0]):
        names = [normalize_name(p.alias_or_name) for p in branches[0].expressions]
        position = names.index(name) if name in names else None
    out: Set[Origin] = set()
    for branch in branches:
        if position is not None and not _star_qualifiers(branch) and position < len(branch.expressions):
            proj = branch.expressions[position]
        else:
            proj = _explicit_projection(branch, name)
        if proj is not None:
            expr = unwrap_cast(proj.this if isinstance(proj, exp.Alias) else proj)
            if isinstance(expr, exp.Column) and not isinstance(expr.this, exp.Star):
                out |= column_origins_with_paths(expr, depth + 1, path)
            continue
        stars = _star_qualifiers(branch)
        if not stars or _has_modified_star(branch):
            continue
        candidates = [s for s in _select_sources(branch) if None in stars or normalize_name(s.alias_or_name) in stars]
        picked = _pick_source(branch, candidates, name, depth)
        if picked is not None:
            out |= trace_source(branch, picked, name, depth + 1, path)
    return out


def column_origins_with_paths(col: exp.Column, depth: int = 0, path: Path = ()) -> Set[Origin]:
    """Base columns ``col`` reads, each with the route of read instances it is reached through. A correlated reference
    resolves to the enclosing SELECT's source; an unresolvable reference has no origin."""
    select = col.find_ancestor(exp.Select)
    if select is None:
        return set()
    name = normalize_name(col.name)
    if col.table:
        scope, src = _source_by_qualifier(select, normalize_name(col.table))
    else:
        scope, src = select, _pick_source(select, _select_sources(select), name, depth)
    if scope is None or src is None:
        return set()
    return trace_source(scope, src, name, depth, path)


def using_origins(select: exp.Select, name: str) -> List[Set[Origin]]:
    """Per FROM/JOIN source of ``select`` that can supply ``name``: the base columns a ``USING (name)`` equates."""
    out: List[Set[Origin]] = []
    for src in _select_sources(select):
        if _provides(select, src, name, 0) is False:
            continue
        origins = trace_source(select, src, name)
        if origins:
            out.append(origins)
    return out


def same_route(a: Path, b: Path) -> bool:
    """Whether two routes reach the same base read through one chain of sources: one is the tail of the other. A
    filter written inside a CTE and a join written outside it (or the reverse) are on one route; two references to the
    same CTE from different SELECTs are not."""
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return bool(short) and long[len(long) - len(short) :] == short


_PREDICATE_ROOTS = (exp.Where, exp.Join, exp.Having)


def predicate_root(node: exp.Expression) -> Optional[exp.Expression]:
    """The WHERE / JOIN / HAVING that ``node`` is a condition of, or None when it sits in a projection, GROUP BY or
    ORDER BY of its own SELECT."""
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, _PREDICATE_ROOTS):
            return parent
        parent = parent.parent
    return None


def or_branches(node: exp.Expression) -> FrozenSet[Tuple[int, int]]:
    """(OR node, operand of it that holds ``node``) for every OR between ``node`` and its predicate root."""
    out = set()
    child, parent = node, node.parent
    while parent is not None and not isinstance(parent, (*_PREDICATE_ROOTS, exp.Select)):
        if isinstance(parent, exp.Or):
            out.add((id(parent), id(child)))
        child, parent = parent, parent.parent
    return frozenset(out)


def is_negated(node: exp.Expression) -> bool:
    """Whether an odd number of NOTs stands above ``node``, counting ``NOT EXISTS`` / ``NOT IN`` around the subquery
    it sits in: the condition then rules rows out instead of selecting them."""
    count = 0
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Not):
            count += 1
        parent = parent.parent
    return count % 2 == 1


def outer_join_of(node: exp.Expression) -> Optional[exp.Join]:
    """The LEFT / RIGHT / FULL join whose ON clause holds ``node``: such a condition only decides which rows of the
    optional side are attached, so it constrains nothing outside that join."""
    root = predicate_root(node)
    if isinstance(root, exp.Join) and normalize_name(root.side or "") in {"left", "right", "full"}:
        return root
    return None


def holds_wherever(condition: exp.Expression, use: exp.Expression) -> bool:
    """Whether ``condition`` is true for every row that satisfies ``use``: no OR separates the two (each OR above the
    condition also encloses the use, through the same operand)."""
    if not or_branches(condition):
        return True
    ancestors = set()
    node: Optional[exp.Expression] = use
    while node is not None:
        ancestors.add(id(node))
        node = node.parent
    return all(branch in ancestors for _, branch in or_branches(condition))


def flatten_or(node: exp.Expression) -> List[exp.Expression]:
    """Operands of a chain of ORs, parentheses removed."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Or):
        return flatten_or(node.this) + flatten_or(node.expression)
    return [node]


def top_or(node: exp.Expression) -> Optional[exp.Or]:
    """The outermost OR of the OR chain directly above ``node`` (through parentheses), or None."""
    found = None
    parent = node.parent
    while isinstance(parent, (exp.Paren, exp.Or)):
        if isinstance(parent, exp.Or):
            found = parent
        parent = parent.parent
    return found


def own_nodes(node: Optional[exp.Expression]) -> Iterator[exp.Expression]:
    """Nodes of ``node`` that belong to its own SELECT; nested queries are their own scope."""
    if node is None:
        return
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for child in cur.iter_expressions():
            if not isinstance(child, (exp.Select, exp.Subquery, exp.SetOperation)):
                stack.append(child)


__all__ = [
    "Hop",
    "Origin",
    "Path",
    "column_origins_with_paths",
    "flatten_or",
    "holds_wherever",
    "is_negated",
    "or_branches",
    "outer_join_of",
    "own_nodes",
    "predicate_root",
    "same_route",
    "top_or",
    "trace_source",
    "using_origins",
]

"""Polymorphic keys and the column that qualifies them.

``cost.cost_event_id`` (qualified by ``cost_domain_id``) and the ``*_event_id`` keys of measurement, observation,
note and episode_event (qualified by their ``*_event_field_concept_id``) hold the primary key of whichever table the
qualifying column names. This module finds every comparison that uses such a key to link or look up rows, and the
restrictions of the qualifying column that hold for the same row at that point. What a restriction value means (a
domain name, a field concept) is left to the calling rule.
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.helpers import _predicate_nodes, _query_branches, column_owner, normalize_name, unwrap_cast

from ._ds1_scope import (
    Path,
    column_origins_with_paths,
    flatten_or,
    holds_wherever,
    is_negated,
    outer_join_of,
    own_nodes,
    predicate_root,
    same_route,
    top_or,
    using_origins,
)

# Vocabulary tables a qualifying column is looked up in; joining one labels the value without choosing it.
_LOOKUP_TABLES = {"concept", "domain"}

VALUES = "values"  # restricted to one of the literals
EXCLUDED = "excluded"  # every literal is ruled out, anything else remains
PATTERN = "pattern"  # LIKE / ILIKE pattern
OPAQUE = "opaque"  # restricted to a value the SQL does not show (parameter, subquery, another column)


@dataclass(frozen=True)
class Restriction:
    """One condition on the qualifying column. ``literals`` are (text, is_string) pairs; ``fold`` is the case
    handling applied before the comparison: None, "lower", "upper" or "ilike"."""

    node: exp.Expression
    paths: FrozenSet[Path]
    kind: str
    literals: Tuple[Tuple[str, bool], ...] = ()
    fold: Optional[str] = None


@dataclass(frozen=True)
class KeyUse:
    """One comparison of the polymorphic key. ``targets`` are the base (table, column) pairs on the other side; empty
    for a literal, a parameter or a computed value."""

    node: exp.Expression
    text: str
    paths: FrozenSet[Path]
    targets: FrozenSet[Tuple[str, str]]
    restrictions: Tuple[Restriction, ...]


def _paths_to(node: Optional[exp.Expression], table: str, column: str) -> FrozenSet[Path]:
    node = unwrap_cast(node) if node is not None else None
    if not isinstance(node, exp.Column):
        return frozenset()
    return frozenset(p for p, t, c in column_origins_with_paths(node) if (t, c) == (table, column))


def _peel_case(node: exp.Expression) -> Tuple[exp.Expression, Optional[str]]:
    fold = None
    node = unwrap_cast(node)
    while isinstance(node, (exp.Lower, exp.Upper)):
        fold = "lower" if isinstance(node, exp.Lower) else "upper"
        node = unwrap_cast(node.this)
    return node, fold


def _literal(node: Optional[exp.Expression]) -> Optional[Tuple[str, bool]]:
    """A cast literal compares as the literal itself, so casts are looked through."""
    node = unwrap_cast(node) if node is not None else None
    if isinstance(node, exp.Neg) and isinstance(unwrap_cast(node.this), exp.Literal):
        return "-" + str(unwrap_cast(node.this).this), False
    if isinstance(node, exp.Literal):
        return str(node.this), bool(node.is_string)
    return None


def _is_within(node: exp.Expression, ancestor: exp.Expression) -> bool:
    cur: Optional[exp.Expression] = node
    while cur is not None:
        if cur is ancestor:
            return True
        cur = cur.parent
    return False


def _is_bare_lookup(other: exp.Column, comparison: exp.Expression) -> bool:
    """``other`` is the key of a concept / domain row read directly in this SELECT and nothing else in the SELECT's
    conditions refers to that row: the join names the value but does not choose it."""
    origins = column_origins_with_paths(other)
    if not origins or any(len(p) != 1 or t not in _LOOKUP_TABLES for p, t, _ in origins):
        return False
    select = other.find_ancestor(exp.Select)
    owner = column_owner(other, select) if select is not None else None
    if owner is None:
        return False
    for pred in _predicate_nodes(select):
        for node in own_nodes(pred):
            if (
                isinstance(node, exp.Column)
                and column_owner(node, select) == owner
                and not _is_within(node, comparison)
            ):
                return False
    return True


def _atoms(node: exp.Expression, table: str, qualifier: str) -> List[Restriction]:
    if isinstance(node, (exp.EQ, exp.NEQ)):
        sides = [(node.this, node.expression), (node.expression, node.this)]
    elif isinstance(node, (exp.In, exp.Like, exp.ILike)):
        sides = [(node.this, node.args.get("expression"))]
    else:
        return []
    negated = is_negated(node)
    out: List[Restriction] = []
    for column_side, other in sides:
        inner, fold = _peel_case(column_side)
        paths = _paths_to(inner, table, qualifier)
        if not paths:
            continue
        if isinstance(node, exp.In):
            items = node.expressions or []
            literals = [_literal(v) for v in items]
            if node.args.get("query") is not None or not items or any(v is None for v in literals):
                if not negated:
                    out.append(Restriction(node, paths, OPAQUE))
                continue
            out.append(Restriction(node, paths, EXCLUDED if negated else VALUES, tuple(literals), fold))
            continue
        if isinstance(node, (exp.Like, exp.ILike)):
            if negated:
                continue
            literal = _literal(other)
            if literal is None or not literal[1]:
                out.append(Restriction(node, paths, OPAQUE))
            else:
                kind_fold = "ilike" if isinstance(node, exp.ILike) else fold
                out.append(Restriction(node, paths, PATTERN, (literal,), kind_fold))
            continue
        literal = _literal(other)
        excluding = isinstance(node, exp.NEQ) != negated
        if literal is not None:
            out.append(Restriction(node, paths, EXCLUDED if excluding else VALUES, (literal,), fold))
            continue
        other_inner = unwrap_cast(other)
        if excluding or isinstance(other_inner, exp.Null):
            continue
        if isinstance(other_inner, exp.Column) and _is_bare_lookup(other_inner, node):
            continue
        out.append(Restriction(node, paths, OPAQUE))
    return out


def restrictions(tree: exp.Expression, table: str, qualifier: str) -> List[Restriction]:
    """Every condition on ``table.qualifier`` in a WHERE, JOIN ON or HAVING clause. An OR whose operands all restrict
    the same read's qualifying column to literals is one restriction to the union of those literals; any other
    condition under an OR holds only inside its own operand."""
    atoms: Dict[int, List[Restriction]] = {}
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.Like, exp.ILike):
        if predicate_root(node) is None:
            continue
        found = _atoms(node, table, qualifier)
        if found:
            atoms[id(node)] = found
    out: List[Restriction] = []
    grouped = set()
    for key, found in atoms.items():
        if key in grouped:
            continue
        atom = found[0]
        group = top_or(atom.node)
        if group is not None and len(found) == 1:
            members = [atoms.get(id(op)) for op in flatten_or(group)]
            if all(m is not None and len(m) == 1 and m[0].kind == VALUES and m[0].fold == atom.fold for m in members):
                shared = frozenset.intersection(*(m[0].paths for m in members))
                if shared:
                    grouped.update(id(m[0].node) for m in members)
                    literals = tuple(lit for m in members for lit in m[0].literals)
                    out.append(Restriction(group, shared, VALUES, literals, atom.fold))
                    continue
        out.extend(found)
    return out


def _targets(other: Optional[exp.Expression]) -> FrozenSet[Tuple[str, str]]:
    """Base columns compared with the key: a column, or the first projection of an IN / scalar subquery."""
    if other is None:
        return frozenset()
    other = unwrap_cast(other)
    if isinstance(other, exp.Column):
        return frozenset((t, c) for _, t, c in column_origins_with_paths(other))
    query = other.this if isinstance(other, exp.Subquery) else other
    found = set()
    if isinstance(query, (exp.Select, exp.SetOperation)):
        for branch in _query_branches(query):
            if not branch.expressions:
                continue
            first = branch.expressions[0]
            expr = unwrap_cast(first.this if isinstance(first, exp.Alias) else first)
            if isinstance(expr, exp.Column):
                found |= {(t, c) for _, t, c in column_origins_with_paths(expr)}
    return frozenset(found)


def _applies(restriction: Restriction, use_node: exp.Expression, use_paths: FrozenSet[Path]) -> bool:
    if not any(same_route(a, b) for a in restriction.paths for b in use_paths):
        return False
    if not holds_wherever(restriction.node, use_node):
        return False
    join = outer_join_of(restriction.node)
    return join is None or _is_within(use_node, join)


def key_uses(tree: exp.Expression, table: str, key: str, qualifier: str) -> List[KeyUse]:
    """Comparisons of ``table.key`` with a column, a subquery, a literal or a parameter, in WHERE, JOIN ON, HAVING or
    USING, each with the restrictions of ``table.qualifier`` that hold for the same row. NULL tests are not uses: they
    do not interpret the key."""
    found = restrictions(tree, table, qualifier)

    def applicable(node: exp.Expression, paths: FrozenSet[Path]) -> Tuple[Restriction, ...]:
        return tuple(r for r in found if _applies(r, node, paths))

    uses: List[KeyUse] = []
    for node in tree.find_all(exp.EQ, exp.NullSafeEQ, exp.In):
        if predicate_root(node) is None:
            continue
        if isinstance(node, exp.In):
            sides = [(node.this, node.args.get("query"))]
        else:
            sides = [(node.this, node.expression), (node.expression, node.this)]
        for mine, other in sides:
            paths = _paths_to(mine, table, key)
            if not paths:
                continue
            if other is not None and isinstance(unwrap_cast(other), exp.Null):
                continue
            uses.append(KeyUse(node, unwrap_cast(mine).sql(), paths, _targets(other), applicable(node, paths)))
    for select in tree.find_all(exp.Select):
        for join in select.args.get("joins") or []:
            for ident in join.args.get("using") or []:
                name = normalize_name(ident.name)
                groups = using_origins(select, name)
                for i, group in enumerate(groups):
                    paths = frozenset(p for p, t, c in group if (t, c) == (table, key))
                    if not paths:
                        continue
                    others = frozenset((t, c) for j, g in enumerate(groups) if j != i for _, t, c in g)
                    uses.append(KeyUse(join, f"USING ({name})", paths, others, applicable(join, paths)))
    return uses


def like_matches(pattern: str, value: str, ignore_case: bool) -> bool:
    """SQL LIKE semantics without an ESCAPE clause."""
    import re

    regex = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern)
    return re.fullmatch(regex, value, re.S | (re.I if ignore_case else 0)) is not None


__all__ = [
    "EXCLUDED",
    "OPAQUE",
    "PATTERN",
    "VALUES",
    "KeyUse",
    "Restriction",
    "key_uses",
    "like_matches",
    "restrictions",
]

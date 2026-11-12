"""Shared detection for tables whose rows are only comparable within one type (episode, fact_relationship).

A read of such a table is reported unless the type column is restricted or shown for that table instance: in its
own SELECT or downstream on a projected copy, followed through CTEs and derived tables.
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.helpers import (
    InstanceKey,
    _instance_key,
    _is_filter_position,
    _predicate_atom,
    _reads,
    _select_sources,
    _source_query,
    normalize_name,
    select_is_profiling_read,
    unwrap_cast,
)

CONCEPT = "concept"
# Atoms that let every type through: not a restriction.
_NON_RESTRICTING_ATOMS = (exp.NEQ, exp.NullSafeNEQ, exp.Is)


def _is_shown_position(col: exp.Column) -> bool:
    """``col`` sits in the projection list or GROUP BY of its SELECT."""
    node: exp.Expression = col
    while node.parent is not None and not isinstance(node.parent, exp.Select):
        node = node.parent
    return node.parent is not None and node.arg_key in ("expressions", "group")


def _reads_only(node: exp.Expression, table: str) -> bool:
    node = unwrap_cast(node)
    if not isinstance(node, exp.Column):
        return False
    reads = _reads(node, relay=False)
    return bool(reads) and all(r[2] == table for r in reads)


def _is_bare_concept_join(atom: exp.Expression, table: str) -> bool:
    """``<table>.<type column> = concept.concept_id``: the join alone neither restricts nor shows the type. A filter
    on, or a projection of, the joined concept row does, and reaches the type column through the relay in ``_reads``."""
    if not isinstance(atom, exp.EQ):
        return False
    left, right = atom.this, atom.expression
    return (_reads_only(left, table) and _reads_only(right, CONCEPT)) or (
        _reads_only(right, table) and _reads_only(left, CONCEPT)
    )


def instances_using(tree: exp.Expression, table: str, column: str, restrict_only: bool = True) -> Set[InstanceKey]:
    """Instances of ``table`` whose ``column`` is restricted by a WHERE / ON / HAVING predicate, or shown in a
    projection or GROUP BY. With ``restrict_only=False`` any predicate on the column counts, including ``<>``."""
    used: Set[InstanceKey] = set()
    for col in tree.find_all(exp.Column):
        hits = {_instance_key(s, a) for s, a, t, c in _reads(col) if t == table and c == column}
        if not hits:
            continue
        if _is_filter_position(col):
            atom, negated = _predicate_atom(col)
            if atom is col:
                continue
            if restrict_only and (negated or isinstance(atom, _NON_RESTRICTING_ATOMS)):
                continue
            if _is_bare_concept_join(atom, table):
                continue
            used |= hits
        elif _is_shown_position(col):
            used |= hits
    return used


def _literal_number(node: exp.Expression) -> Optional[float]:
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return float(node.this)
        except ValueError:
            return None
    return None


def _is_constant_false(select: exp.Select) -> bool:
    """``WHERE FALSE`` / ``WHERE (0 = 1)``: a zero-row schema probe that analyses nothing."""
    where = select.args.get("where")
    if where is None:
        return False
    cond = where.this
    while isinstance(cond, exp.Paren):
        cond = cond.this
    if isinstance(cond, exp.Boolean):
        return not cond.this
    if not (
        isinstance(cond, exp.EQ) and isinstance(cond.this, exp.Literal) and isinstance(cond.expression, exp.Literal)
    ):
        return False
    left, right = _literal_number(cond.this), _literal_number(cond.expression)
    if left is not None and right is not None:
        return left != right
    return cond.this.is_string == cond.expression.is_string and cond.this.this != cond.expression.this


def instances_pinned_by_key(tree: exp.Expression, table: str, key: str) -> Set[InstanceKey]:
    """Instances of ``table`` restricted to literal values of its primary key (``episode_id = 42``, ``IN (1, 2)``):
    a lookup of known rows, each of one type."""
    pinned: Set[InstanceKey] = set()
    for col in tree.find_all(exp.Column):
        hits = {_instance_key(s, a) for s, a, t, c in _reads(col, relay=False) if t == table and c == key}
        if not hits or not _is_filter_position(col):
            continue
        atom, negated = _predicate_atom(col)
        if negated:
            continue
        if isinstance(atom, exp.EQ):
            other = atom.expression if unwrap_cast(atom.this) is col else atom.this
            if isinstance(unwrap_cast(other), exp.Literal):
                pinned |= hits
        elif isinstance(atom, exp.In) and unwrap_cast(atom.this) is col and atom.expressions:
            if all(isinstance(unwrap_cast(v), exp.Literal) for v in atom.expressions):
                pinned |= hits
    return pinned


def unused_instances(
    tree: exp.Expression, table: str, used: Set[InstanceKey], profile_keys: Set[str]
) -> List[Tuple[exp.Select, str]]:
    """(SELECT, alias) for every base-table read of ``table`` that is not in ``used`` and is not a profiling read."""
    out: List[Tuple[exp.Select, str]] = []
    for select in tree.find_all(exp.Select):
        for src in _select_sources(select):
            if not isinstance(src, exp.Table) or normalize_name(src.name) != table:
                continue
            if _source_query(select, src) is not None:
                continue
            alias = normalize_name(src.alias_or_name)
            if _instance_key(select, alias) in used:
                continue
            if _is_constant_false(select) or select_is_profiling_read(select, table, profile_keys):
                continue
            out.append((select, alias))
    return out


__all__ = ["instances_pinned_by_key", "instances_using", "unused_instances"]

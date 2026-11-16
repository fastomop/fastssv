"""Lineage-based building blocks shared by the concept_standardization rules.

Everything here works on table *instances* (one FROM/JOIN source of one SELECT), found by following column values
through CTEs, derived tables, set operations and IN / EXISTS subqueries. Alias and column names are never compared
across scopes.
"""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.helpers import (
    DERIVED,
    _query_branches,
    _select_joins,
    column_origins,
    normalize_name,
    side_reads,
)

# (id of the SELECT that reads the table, alias of the table in that SELECT)
InstanceKey = Tuple[int, str]

_CAST = (exp.Cast, exp.TryCast, exp.Paren)
_CASE_FOLD = (exp.Lower, exp.Upper)
_TRANSPARENT = (*_CAST, *_CASE_FOLD, exp.Trim)
_OUTER_SIDES = {"LEFT", "RIGHT", "FULL"}
_PREDICATE_SUBQUERY_PARENTS = (exp.In, exp.Exists, exp.Any, exp.All, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def peel_cast(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, _CAST):
        node = node.this
    return node


def string_value(node: Optional[exp.Expression]) -> Optional[str]:
    """The text of a string literal as written (casts peeled), else None."""
    node = peel_cast(node)
    return str(node.this) if isinstance(node, exp.Literal) and node.is_string else None


def int_value(node: Optional[exp.Expression]) -> Optional[int]:
    """The value of an integer literal, also when quoted (``'201820'``) or cast, else None."""
    node = peel_cast(node)
    if isinstance(node, exp.Neg):
        inner = int_value(node.this)
        return -inner if inner is not None else None
    if isinstance(node, exp.Literal):
        try:
            return int(str(node.this))
        except (TypeError, ValueError):
            return None
    return None


def _join_is_outer(join: exp.Join) -> bool:
    return (join.side or "").upper() in _OUTER_SIDES


def _locally_required(node: exp.Expression) -> Tuple[bool, bool, Optional[exp.Expression]]:
    """(required, in the ON clause of an outer join, the WHERE / HAVING / JOIN it belongs to).

    Required means every row the SELECT keeps satisfies ``node``: it is reached from WHERE, HAVING or an inner join's
    ON through AND only. OR, NOT, CASE and aggregate FILTER clauses all break that."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, (exp.Where, exp.Having)):
            # `COUNT(*) FILTER (WHERE ...)` restricts one aggregate, not the rows.
            if isinstance(parent.parent, exp.Filter):
                return False, False, None
            return True, False, parent
        if isinstance(parent, exp.Join):
            outer = _join_is_outer(parent)
            return (not outer), outer, parent
        if not isinstance(parent, (exp.And, exp.Paren)):
            return False, False, None
        parent = parent.parent
    return False, False, None


def is_required(node: exp.Expression) -> bool:
    """``node`` holds for every row that reaches the statement's result through its SELECT: it is required locally,
    and when that SELECT is the body of an IN / EXISTS / scalar subquery, the predicate using the subquery is required
    in turn. A CTE, derived table or set-operation branch is judged on its own rows."""
    required, _, _ = _locally_required(node)
    if not required:
        return False
    select = node.find_ancestor(exp.Select)
    while select is not None:
        holder = select.parent
        while isinstance(holder, (exp.Subquery, exp.Paren, exp.SetOperation)):
            holder = holder.parent
        if isinstance(holder, _PREDICATE_SUBQUERY_PARENTS):
            required, _, _ = _locally_required(holder)
            if not required:
                return False
            select = holder.find_ancestor(exp.Select)
            continue
        return True
    return True


def dead_scalar_column(node: exp.Expression) -> Optional[str]:
    """What a required ``node`` that no row satisfies does, when its chain of required subqueries ends in a scalar
    subquery of an enclosing select list: a phrase naming the output column it empties. None when ``node`` restricts
    the statement's rows instead, because then the query returns no rows.

    The outer query still returns its rows; only that column loses its value."""
    select = node.find_ancestor(exp.Select)
    while select is not None:
        holder = select.parent
        while isinstance(holder, (exp.Subquery, exp.Paren, exp.SetOperation)):
            holder = holder.parent
        if isinstance(holder, _PREDICATE_SUBQUERY_PARENTS):
            select = holder.find_ancestor(exp.Select)
            continue
        if not isinstance(select.parent, exp.Subquery):
            return None
        projection: exp.Expression = select.parent
        while projection.parent is not None and not isinstance(projection.parent, exp.Select):
            projection = projection.parent
        outer = projection.parent
        if not isinstance(outer, exp.Select) or projection not in outer.expressions:
            return None
        inner = projection.this if isinstance(projection, exp.Alias) else projection
        label = f"column `{projection.alias}`" if isinstance(projection, exp.Alias) else "its select-list column"
        aggregated = select.args.get("group") is None and any(
            agg.find_ancestor(exp.Select) is select for p in select.expressions for agg in p.find_all(exp.AggFunc)
        )
        # An ungrouped aggregate over no rows still returns one row: 0 for COUNT, NULL for the others.
        value = "an aggregate of no rows (0 for COUNT, NULL otherwise)" if aggregated else "NULL"
        if inner is select.parent:
            return f"the scalar subquery matches no row and {label} is {value} on every row"
        return f"the scalar subquery in {label} matches no row and yields {value} on every row"
    return None


def in_outer_join_on(node: exp.Expression) -> bool:
    return _locally_required(node)[1]


def operand_reads(side: Optional[exp.Expression]) -> Set[Tuple[exp.Select, str, str, str]]:
    """Base-table reads ``(SELECT, alias, table, column)`` behind one comparison operand (a column, or the first
    projection of a subquery). Casts and case folding are looked through; concept / concept_ancestor are not relayed."""
    while isinstance(side, _TRANSPARENT):
        side = side.this
    if side is None:
        return set()
    return side_reads(side, relay=False)


def operand_origin(side: Optional[exp.Expression]) -> Optional[Tuple[str, str]]:
    """The single base ``(table, column)`` an operand passes through unchanged, else None (computed, merged from
    several columns, or unresolvable)."""
    side = peel_cast(side)
    if isinstance(side, exp.Column):
        origins = column_origins(side)
    elif isinstance(side, (exp.Subquery, exp.Select, exp.SetOperation)):
        origins = set()
        for branch in _query_branches(side):
            if not branch.expressions:
                return None
            first = branch.expressions[0]
            expr = peel_cast(first.this if isinstance(first, exp.Alias) else first)
            if not isinstance(expr, exp.Column):
                return None
            branch_origins = column_origins(expr)
            if branch_origins is None:
                return None
            origins |= branch_origins
    else:
        return None
    if not origins or len(origins) != 1:
        return None
    table, name = next(iter(origins))
    return None if table == DERIVED else (table, name)


@dataclass
class ColumnFilter:
    """One predicate on a column of a table instance.

    op: ``eq`` / ``neq`` / ``in`` / ``not_in`` / ``is_null`` / ``is_not_null`` / ``like`` / ``gt`` / ``gte`` / ``lt`` /
    ``lte`` / ``between`` / ``other``, always read as ``<column> <op> <values>`` (a swapped comparison is flipped). A
    NOT around the predicate is already folded into ``op``. ``values`` are the literal operands as written (strings
    unquoted, numbers as text; ``between`` has low and high); ``complete`` is False when some operand is not a
    literal. ``conjunction`` identifies the AND-chain the predicate belongs to, so predicates that must hold together
    (``x > 0 AND x < 2``) can be combined."""

    node: exp.Expression
    column: exp.Column
    op: str
    values: Tuple[str, ...]
    complete: bool
    required: bool
    outer_join_on: bool
    case_folded: bool
    conjunction: int = 0

    @property
    def sql(self) -> str:
        return self.node.sql()


_NEGATION = {
    "eq": "neq",
    "neq": "eq",
    "in": "not_in",
    "not_in": "in",
    "is_null": "is_not_null",
    "is_not_null": "is_null",
    "like": "other",
    "gt": "lte",
    "gte": "lt",
    "lt": "gte",
    "lte": "gt",
    "between": "other",
    "other": "other",
}

_FLIP = {exp.GT: "lt", exp.GTE: "lte", exp.LT: "gt", exp.LTE: "gte"}
_SAME = {exp.GT: "gt", exp.GTE: "gte", exp.LT: "lt", exp.LTE: "lte"}


def _literal_text(node: Optional[exp.Expression]) -> Optional[str]:
    node = peel_cast(node)
    if isinstance(node, exp.Literal):
        return str(node.this)
    if isinstance(node, exp.Boolean):
        return "TRUE" if node.this else "FALSE"
    return None


def _classify(col: exp.Column) -> Optional[Tuple[exp.Expression, str, Tuple[str, ...], bool, bool]]:
    """(predicate node, op, values, complete, case folded) for the comparison ``col`` is a direct operand of."""
    top: exp.Expression = col
    folded = False
    while isinstance(top.parent, _TRANSPARENT):
        top = top.parent
        folded = folded or isinstance(top, _CASE_FOLD)
    atom = top.parent
    if atom is None:
        return None
    if isinstance(atom, (exp.EQ, exp.NullSafeEQ, exp.NEQ, exp.NullSafeNEQ)):
        other = atom.expression if atom.this is top else atom.this
        value = _literal_text(other)
        op = "eq" if isinstance(atom, (exp.EQ, exp.NullSafeEQ)) else "neq"
        if value is None:
            return atom, "other", (), False, folded
        return atom, op, (value,), True, folded
    if isinstance(atom, exp.In) and atom.this is top:
        if atom.args.get("query") is not None or atom.args.get("field") is not None:
            return atom, "other", (), False, folded
        values = [_literal_text(v) for v in atom.expressions or []]
        known = tuple(v for v in values if v is not None)
        return atom, "in", known, len(known) == len(values) and bool(values), folded
    if isinstance(atom, exp.Is) and atom.this is top and isinstance(atom.expression, exp.Null):
        return atom, "is_null", (), True, folded
    if isinstance(atom, (exp.Like, exp.ILike)) and atom.this is top:
        value = _literal_text(atom.expression)
        return atom, "like", (value,) if value is not None else (), value is not None, folded
    if isinstance(atom, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
        mine_left = atom.this is top
        other = atom.expression if mine_left else atom.this
        value = _literal_text(other)
        op = _SAME[type(atom)] if mine_left else _FLIP[type(atom)]
        if value is None:
            return atom, "other", (), False, folded
        return atom, op, (value,), True, folded
    if isinstance(atom, exp.Between) and atom.this is top:
        low, high = _literal_text(atom.args.get("low")), _literal_text(atom.args.get("high"))
        if low is None or high is None:
            return atom, "other", (), False, folded
        return atom, "between", (low, high), True, folded
    return None


def _conjunction_root(node: exp.Expression) -> int:
    parent = node.parent
    while isinstance(parent, (exp.And, exp.Paren)):
        parent = parent.parent
    return id(parent)


def _strip_not(atom: exp.Expression) -> Tuple[exp.Expression, bool]:
    """The outermost node of ``NOT (...)`` wrappers directly around ``atom`` and whether they negate it."""
    node, negated = atom, False
    while True:
        parent = node.parent
        while isinstance(parent, exp.Paren):
            parent = parent.parent
        if isinstance(parent, exp.Not):
            node, negated = parent, not negated
            continue
        return node, negated


def instance_filters(tree: exp.Expression, table: str, column: str) -> Dict[InstanceKey, List[ColumnFilter]]:
    """Predicates on ``table.column`` per table instance. A predicate counts wherever the value is filtered: in the
    SELECT that reads the table, or downstream on a CTE / derived-table column that passes it through."""
    out: Dict[InstanceKey, List[ColumnFilter]] = {}
    seen: Set[int] = set()
    for col in tree.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue
        classified = _classify(col)
        if classified is None:
            continue
        atom, op, values, complete, folded = classified
        origins = column_origins(col)
        if origins is None or origins != {(table, column)}:
            continue
        hits = [r for r in operand_reads(col) if r[2] == table and r[3] == column]
        if not hits or id(atom) in seen:
            continue
        seen.add(id(atom))
        outermost, negated = _strip_not(atom)
        if negated:
            op = _NEGATION[op]
        flt = ColumnFilter(
            node=outermost,
            column=col,
            op=op,
            values=values,
            complete=complete,
            required=is_required(outermost),
            outer_join_on=in_outer_join_on(outermost),
            case_folded=folded,
            conjunction=_conjunction_root(outermost),
        )
        for select, alias, _, _ in hits:
            out.setdefault((id(select), alias), []).append(flt)
    return out


@dataclass
class Link:
    """A value compared for equality with ``target_table.target_column`` of one table instance."""

    origin: Tuple[str, str]
    instance: InstanceKey
    instance_select: exp.Select
    alias: str
    node: exp.Expression
    required: bool
    outer_join_on: bool


def _comparisons(tree: exp.Expression) -> Iterator[Tuple[exp.Expression, exp.Expression, exp.Expression]]:
    for node in tree.find_all(exp.EQ, exp.NullSafeEQ):
        yield node, node.this, node.expression
    for node in tree.find_all(exp.In):
        query = node.args.get("query")
        if query is not None:
            yield node, node.this, query
    for join in tree.find_all(exp.Join):
        # USING (c) is `left.c = right.c`; sqlglot keeps it as a bare identifier list.
        for ident in join.args.get("using") or []:
            select = join.find_ancestor(exp.Select)
            if select is None:
                continue
            right = exp.column(ident.name, table=join.this.alias_or_name)
            right.set("this", exp.to_identifier(ident.name))
            yield join, ident, right


def links(tree: exp.Expression, target_table: str, target_column: str) -> List[Link]:
    """Every equality or IN-subquery that matches some other base column against ``target_table.target_column``.

    The link is found through lineage, so the target may sit behind a CTE, a derived table or an IN / EXISTS
    subquery, and either operand may be unqualified, cast or written on either side."""
    out: List[Link] = []
    for node, left, right in _comparisons(tree):
        if isinstance(node, exp.Join):
            continue
        for mine, other in ((left, right), (right, left)):
            origin = operand_origin(mine)
            if origin is None or origin == (target_table, target_column):
                continue
            if operand_origin(other) != (target_table, target_column):
                continue
            hits = [r for r in operand_reads(other) if r[2] == target_table and r[3] == target_column]
            if not hits:
                continue
            outermost, negated = _strip_not(node)
            required = (not negated) and is_required(outermost)
            for select, alias, _, _ in hits:
                out.append(
                    Link(
                        origin=(normalize_name(origin[0]), normalize_name(origin[1])),
                        instance=(id(select), alias),
                        instance_select=select,
                        alias=alias,
                        node=node,
                        required=required,
                        outer_join_on=in_outer_join_on(outermost),
                    )
                )
    return out


def instance_is_null_extended(link: Link) -> bool:
    """The linked instance is on the nullable side of an outer join in its SELECT: a failed match keeps the other
    side's row with NULLs, so predicates in that ON clause remove nothing."""
    for join in _select_joins(link.instance_select):
        src = join.this
        if normalize_name(getattr(src, "alias_or_name", "") or "") == link.alias and _join_is_outer(join):
            return True
    return False


__all__ = [
    "ColumnFilter",
    "InstanceKey",
    "dead_scalar_column",
    "Link",
    "in_outer_join_on",
    "instance_filters",
    "instance_is_null_extended",
    "int_value",
    "is_required",
    "links",
    "operand_origin",
    "operand_reads",
    "peel_cast",
    "string_value",
]

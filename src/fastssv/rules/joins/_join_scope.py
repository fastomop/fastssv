"""Join predicates per SELECT scope, with each operand resolved to the CDM column it reads.

Shared by the key-join rules of this package. An operand is followed through casts, parentheses, CTEs, derived
tables and set operations (``core.helpers.column_origins``), so a rule judges the base column a value comes from,
never the alias or the name it happens to carry.
"""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.helpers import (
    DERIVED,
    _projection_origins,
    _query_branches,
    _select_sources,
    _source_query,
    _star_qualifiers,
    column_origins,
    column_owner,
    normalize_name,
    scope_nodes,
)
from fastssv.schemas.cdm_column_types import get_table_columns

from ._person_keys import FOREIGN_KEYS, POLYMORPHIC_KEYS, Key, key_domain

_CASTS = (exp.Cast, exp.TryCast, exp.Paren)
_TEXT_NORMALISERS = (exp.Lower, exp.Upper, exp.Trim)
_TRIM_FUNCTIONS = {"btrim", "ltrim", "rtrim"}
_MAX_DEPTH = 12


@dataclass(frozen=True)
class Side:
    """One operand of a comparison."""

    expr: exp.Expression
    column: Optional[exp.Column]
    origin: Optional[Key]

    @property
    def table(self) -> Optional[str]:
        return self.origin[0] if self.origin else None

    @property
    def name(self) -> Optional[str]:
        return self.origin[1] if self.origin else None

    @property
    def domain(self) -> Optional[Key]:
        return key_domain(*self.origin) if self.origin else None

    def sql(self) -> str:
        return self.expr.sql()


@dataclass(frozen=True)
class Equality:
    """``left = right`` or ``left IN (SELECT right ...)`` in the ON / WHERE of ``select``. ``conjunct`` is True when
    the predicate is reached through AND only, so it must hold for every returned row."""

    select: exp.Select
    node: exp.Expression
    left: Side
    right: Side
    conjunct: bool

    def sides(self) -> Tuple[Tuple[Side, Side], Tuple[Side, Side]]:
        return (self.left, self.right), (self.right, self.left)

    def sql(self) -> str:
        return self.node.sql()


def peel(node: exp.Expression, text: bool = False) -> exp.Expression:
    """Strip casts and parentheses, which keep a key's value; with ``text`` also case folding and trimming, which
    keep a string comparable."""
    while True:
        if isinstance(node, _CASTS) or (text and isinstance(node, _TEXT_NORMALISERS)):
            node = node.this
        elif text and isinstance(node, exp.Anonymous) and node.name.lower() in _TRIM_FUNCTIONS and node.expressions:
            node = node.expressions[0]
        else:
            return node


def hoist_joins(tree: exp.Expression) -> exp.Expression:
    """DuckDB's FROM-first form (``FROM a JOIN b ON ... SELECT *``) parses with the joins attached to the FROM table;
    return a copy with them on the SELECT, where every other form keeps them. The cached parse is never mutated."""
    if not any(t.args.get("joins") for t in tree.find_all(exp.Table)):
        return tree
    tree = tree.copy()
    for table in list(tree.find_all(exp.Table)):
        joins = table.args.get("joins")
        if not joins or not isinstance(table.parent, exp.From) or not isinstance(table.parent.parent, exp.Select):
            continue
        select = table.parent.parent
        table.set("joins", None)
        select.set("joins", list(joins) + list(select.args.get("joins") or []))
    return tree


def shadowed_tables(tree: exp.Expression) -> Set[str]:
    """Names that do not denote the CDM table in this statement: session temporary tables (T-SQL ``#cohort``) and
    tables created earlier in the same batch."""
    from fastssv.core.validation_context import get_validation_context

    names = {normalize_name(n) for n in get_validation_context().local_unqualified_tables}
    for table in tree.find_all(exp.Table):
        ident = table.this
        if isinstance(ident, exp.Identifier) and ident.args.get("temporary"):
            names.add(normalize_name(table.name))
    return names


def _single(origins: Optional[Set[Key]], shadowed: Set[str]) -> Optional[Key]:
    if not origins or len(origins) != 1:
        return None
    table, column = next(iter(origins))
    if table == DERIVED or table in shadowed or column not in get_table_columns(table):
        return None
    return table, column


def resolve_side(expr: exp.Expression, shadowed: Set[str], text: bool = False) -> Side:
    """The CDM column an operand reads. A subquery operand (``IN (SELECT x ...)``, scalar subquery) is read through
    its first projection; every branch of a set operation must agree."""
    inner = peel(expr, text)
    if isinstance(inner, exp.Column):
        return Side(expr, inner, _single(column_origins(inner), shadowed))
    query = inner.this if isinstance(inner, exp.Subquery) else inner
    if isinstance(query, (exp.Select, exp.SetOperation)):
        found: Set[Key] = set()
        column = None
        for branch in _query_branches(query):
            if not branch.expressions:
                return Side(expr, None, None)
            first = branch.expressions[0]
            proj = peel(first.this if isinstance(first, exp.Alias) else first, text)
            if not isinstance(proj, exp.Column):
                return Side(expr, None, None)
            origins = column_origins(proj)
            if not origins:
                return Side(expr, None, None)
            column = column or proj
            found |= origins
        return Side(expr, column, _single(found, shadowed))
    return Side(expr, None, None)


def _predicates(select: exp.Select) -> List[exp.Expression]:
    roots = [j.args["on"] for j in select.args.get("joins") or [] if j.args.get("on") is not None]
    for key in ("where", "having"):
        clause = select.args.get(key)
        if clause is not None:
            roots.append(clause.this)
    return roots


def _is_conjunct(node: exp.Expression, root: exp.Expression) -> bool:
    cursor = node
    while cursor is not root:
        cursor = cursor.parent
        if cursor is None or not isinstance(cursor, (exp.And, exp.Paren)):
            return False
    return True


def select_equalities(select: exp.Select, shadowed: Set[str], text: bool = False) -> List[Equality]:
    """Equalities and IN-subqueries of ``select``'s own ON / WHERE / HAVING clauses (nested queries are their own
    scope)."""
    out: List[Equality] = []
    for root in _predicates(select):
        for node in scope_nodes(root):
            if isinstance(node, exp.EQ):
                left, right = node.this, node.expression
            elif isinstance(node, exp.In) and node.args.get("query") is not None:
                left, right = node.this, node.args["query"]
            else:
                continue
            out.append(
                Equality(
                    select,
                    node,
                    resolve_side(left, shadowed, text),
                    resolve_side(right, shadowed, text),
                    _is_conjunct(node, root),
                )
            )
    return out


def tree_equalities(tree: exp.Expression, text: bool = False) -> Iterator[Equality]:
    """Every SELECT's equalities, innermost scopes included."""
    shadowed = shadowed_tables(tree)
    for select in tree.find_all(exp.Select):
        yield from select_equalities(select, shadowed, text)


def key_mismatches(tree: exp.Expression) -> Iterator[Tuple[Equality, Side, Side]]:
    """Equalities whose operands hold keys of two different entities (polymorphic keys excluded)."""
    for eq in tree_equalities(tree):
        a, b = eq.left, eq.right
        if a.origin is None or b.origin is None:
            continue
        if a.origin in POLYMORPHIC_KEYS or b.origin in POLYMORPHIC_KEYS:
            continue
        if a.domain is None or b.domain is None or a.domain == b.domain:
            continue
        yield eq, a, b


def foreign_key_to(table: str, target: Key) -> Optional[str]:
    """The single column of ``table`` that references ``target``, if the CDM defines exactly one."""
    columns = [c for (t, c), ref in FOREIGN_KEYS.items() if t == table and ref == target]
    return columns[0] if len(columns) == 1 else None


def fk_replace_patch(sql: str, a: Side, b: Side, fallback: str) -> Optional[dict]:
    """REPLACE one operand with its table's foreign key to the other operand's entity (``nnlp.note_nlp_id`` ->
    ``nnlp.note_id``) when the CDM defines exactly one such column and both operands are qualified columns."""
    from fastssv.core.patch import build_join_replace_patch

    if a.column is None or b.column is None or not a.column.table or not b.column.table:
        return None
    for x, y in ((a, b), (b, a)):
        fk = foreign_key_to(x.table, y.domain)
        if fk and normalize_name(x.column.name) == x.name and fk != x.name:
            return build_join_replace_patch(
                sql, x.column.table, x.column.name, y.column.table, y.column.name, fk, y.column.name, fallback
            )
    return None


def scope_qualifier(side: Side, select: exp.Select) -> Optional[str]:
    """Alias of the FROM/JOIN source of ``select`` that supplies the operand's column."""
    if side.column is None or side.column.find_ancestor(exp.Select) is not select:
        return None
    return column_owner(side.column, select)


def local_aliases(select: exp.Select) -> Dict[str, str]:
    """alias -> source name for ``select``'s own FROM/JOIN list, in the shape ``build_join_replace_patch`` expects."""
    out: Dict[str, str] = {}
    for src in _select_sources(select):
        if isinstance(src, exp.Table) and src.name:
            out[normalize_name(src.alias_or_name)] = normalize_name(src.name)
    return out


def exposed_columns(select: exp.Select, src: exp.Expression, shadowed: Set[str], depth: int = 0) -> Dict[str, Key]:
    """Output column -> CDM column it reads, for a FROM/JOIN source of ``select``: every catalogue column of a base
    table, or the pass-through projections of a CTE / derived table (computed columns are left out)."""
    if depth > _MAX_DEPTH:
        return {}
    query = _source_query(select, src)
    if query is None:
        if not isinstance(src, exp.Table) or not src.name:
            return {}
        table = normalize_name(src.name)
        if table in shadowed:
            return {}
        return {column: (table, column) for column in get_table_columns(table)}
    branches = _query_branches(query)
    if not branches:
        return {}
    out: Dict[str, Key] = {}
    first = branches[0]
    for proj in first.expressions:
        is_star = isinstance(proj, exp.Star) or (isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star))
        if is_star:
            if len(branches) > 1:
                continue
            wanted = _star_qualifiers(first)
            for inner in _select_sources(first):
                if None in wanted or normalize_name(inner.alias_or_name) in wanted:
                    for name, origin in exposed_columns(first, inner, shadowed, depth + 1).items():
                        out.setdefault(name, origin)
            continue
        name = normalize_name(proj.alias_or_name)
        if not name:
            continue
        origin = _single(_projection_origins(query, name, depth), shadowed)
        if origin is not None:
            out[name] = origin
    return out


def scope_sources(select: exp.Select) -> List[Tuple[str, exp.Expression]]:
    """(alias, source node) for ``select``'s own FROM/JOIN list."""
    return [(normalize_name(s.alias_or_name), s) for s in _select_sources(select) if s.alias_or_name]


__all__ = [
    "Equality",
    "fk_replace_patch",
    "hoist_joins",
    "foreign_key_to",
    "key_mismatches",
    "Side",
    "exposed_columns",
    "local_aliases",
    "peel",
    "resolve_side",
    "scope_qualifier",
    "scope_sources",
    "select_equalities",
    "shadowed_tables",
    "tree_equalities",
]

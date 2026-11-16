"""Which FROM / JOIN sources of one SELECT are tied together by a predicate.

Two sources of a SELECT that no predicate connects, directly or through other sources, form a Cartesian product:
every row of one is paired with every row of the other (PostgreSQL: ``FROM T1 CROSS JOIN T2`` is equivalent to
``FROM T1 INNER JOIN T2 ON TRUE`` and to ``FROM T1, T2``). The spelling of the join does not matter, only the
predicates do.

A predicate ties the sources whose columns it reads: an equality, a range comparison, ``BETWEEN`` or ``IN`` with a
column list, written in WHERE or in any ON clause, plus ``USING`` / ``NATURAL``. A predicate under ``OR`` or
``NOT`` ties nothing, because the rows it does not constrain still pair freely; an OR ties a source only when
every branch ties it to some other source. Two columns equated to the same
literal are tied (equality is transitive). A column of an enclosing query, read in a predicate or inside a derived table, ties the source to the outer row.

A source is one row when it is a CDM table whose primary key is equated to a literal, or a query that returns one
row by construction (no FROM clause, an aggregate query without GROUP BY, or ``LIMIT 1``). Pairing with a one-row source does not
multiply rows. Row generators (``VALUES``, ``UNNEST``, set-returning functions) exist to expand rows and are
reported by neither caller.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.helpers import _find_cte, _pick_source, _provides, is_schema_qualified, normalize_name
from fastssv.rules.anti_patterns._ap_cdm_fields import PATIENT_LEVEL_TABLES, PRIMARY_KEYS

METADATA_TABLES = frozenset({"cdm_source", "metadata"})

_LINKING_PREDICATES = (exp.EQ, exp.NullSafeEQ, exp.LT, exp.LTE, exp.GT, exp.GTE, exp.Between, exp.In)
_OUTER = -1


@dataclass
class Source:
    node: exp.Expression
    alias: str
    kind: str  # "table" (stored table), "query" (CTE or derived table), "generator"
    name: Optional[str] = None  # stored-table name
    query: Optional[exp.Expression] = None
    reads: Set[str] = field(default_factory=set)  # stored tables read, through CTEs
    one_row: bool = False

    @property
    def patient_level(self) -> bool:
        return bool(self.reads & PATIENT_LEVEL_TABLES)

    @property
    def metadata(self) -> bool:
        return bool(self.reads) and self.reads <= METADATA_TABLES

    @property
    def label(self) -> str:
        shown = self.name or ("(subquery)" if self.kind == "query" else "(generator)")
        return shown if not self.alias or self.alias == shown else f"{shown} {self.alias}"


def _query_of(select: exp.Select, node: exp.Expression) -> Optional[exp.Expression]:
    if isinstance(node, exp.Subquery) and isinstance(node.this, (exp.Select, exp.SetOperation)):
        return node.this
    if isinstance(node, exp.Lateral):
        inner = node.this
        return inner.this if isinstance(inner, exp.Subquery) else None
    if isinstance(node, exp.Table) and isinstance(node.this, exp.Identifier) and not is_schema_qualified(node):
        cte = _find_cte(select, normalize_name(node.name))
        if cte is not None and node.find_ancestor(exp.CTE) is not cte:
            return cte.this
    return None


def _tables_read(query: exp.Expression, depth: int = 0) -> Set[str]:
    """Stored tables a query reads, following the CTEs it references."""
    out: Set[str] = set()
    if depth > 8:
        return out
    for table in query.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue
        name = normalize_name(table.name)
        cte = None if is_schema_qualified(table) else _find_cte(table, name)
        if cte is not None and table.find_ancestor(exp.CTE) is not cte:
            out |= _tables_read(cte.this, depth + 1)
        elif cte is None:
            out.add(name)
    return out


def _is_one_row_query(query: exp.Expression) -> bool:
    while isinstance(query, exp.Subquery):
        query = query.this
    if not isinstance(query, exp.Select):
        return False
    limit = query.args.get("limit")
    value = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(value, exp.Literal) and not value.is_string and str(value.this) in ("0", "1"):
        return True
    if query.args.get("group") is not None or not query.expressions:
        return False
    if not (query.args.get("from_") or query.args.get("from")):
        return True
    for proj in query.expressions:
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(expr, exp.Star) or list(expr.find_all(exp.Window)):
            return False
        if not isinstance(expr, exp.AggFunc) and not list(expr.find_all(exp.AggFunc)):
            if list(expr.find_all(exp.Column)):
                return False
    return any(list((p.this if isinstance(p, exp.Alias) else p).find_all(exp.AggFunc)) for p in query.expressions)


def _make_source(select: exp.Select, node: exp.Expression) -> Source:
    alias = normalize_name(node.alias_or_name or "")
    if isinstance(node, (exp.Unnest, exp.Values)) or (
        isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier)
    ):
        return Source(node, alias, "generator")
    query = _query_of(select, node)
    if query is not None:
        if isinstance(query, exp.Values):
            return Source(node, alias, "generator")
        return Source(node, alias, "query", query=query, reads=_tables_read(query), one_row=_is_one_row_query(query))
    if isinstance(node, exp.Table):
        name = normalize_name(node.name)
        return Source(node, alias or name, "table", name=name, reads={name})
    return Source(node, alias, "generator")


def _flatten(select: exp.Select):
    """(source node, join) pairs of ``select``, with parenthesised join trees flattened."""
    from_node = select.args.get("from_") or select.args.get("from")
    pairs = []

    def add(node: exp.Expression, join: Optional[exp.Join]) -> None:
        if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Table) and node.this.args.get("joins"):
            inner = node.this
            pairs.append((inner, join))
            for j in inner.args.get("joins") or []:
                add(j.this, j)
            return
        pairs.append((node, join))

    if from_node is not None and from_node.this is not None:
        add(from_node.this, None)
    for join in select.args.get("joins") or []:
        if join.this is not None:
            add(join.this, join)
    return pairs


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def _atoms(clause: Optional[exp.Expression]) -> List[exp.Expression]:
    """Conjuncts of ``clause``. A NOT is dropped; an OR is kept whole and judged by ``Scope._or_ties``."""
    if clause is None:
        return []
    if isinstance(clause, (exp.Where, exp.Paren)):
        return _atoms(clause.this)
    if isinstance(clause, exp.And):
        return _atoms(clause.left) + _atoms(clause.right)
    if isinstance(clause, exp.Not):
        return []
    return [clause]


def _disjuncts(node: exp.Expression) -> List[exp.Expression]:
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Or):
        return _disjuncts(node.left) + _disjuncts(node.right)
    return [node]


class Scope:
    """The sources of one SELECT and the groups of them that predicates tie together."""

    def __init__(self, select: exp.Select) -> None:
        self.select = select
        pairs = _flatten(select)
        self.sources: List[Source] = [_make_source(select, node) for node, _ in pairs]
        self._uf = _UnionFind()
        for i in range(len(self.sources)):
            self._uf.find(i)
        clauses: List[exp.Expression] = []
        if select.args.get("where") is not None:
            clauses.append(select.args["where"])
        for i, (node, join) in enumerate(pairs):
            if join is None:
                continue
            if join.args.get("on") is not None:
                clauses.append(join.args["on"])
            self._link_using(i, join)
        atoms = [a for c in clauses for a in _atoms(c)]
        self.atoms = atoms
        self._pin_primary_keys(atoms)
        self._link_predicates(atoms)
        self._link_constants(atoms)
        self._link_correlated()

    # --- column attribution -------------------------------------------------------------------------------------

    def _owner(self, col: exp.Column) -> Optional[int]:
        """Index of the source ``col`` reads, ``_OUTER`` for a column of an enclosing query, None if unknown."""
        if col.table:
            qualifier = normalize_name(col.table)
            hits = [i for i, s in enumerate(self.sources) if s.alias == qualifier]
            return hits[0] if len(hits) == 1 else (_OUTER if not hits else None)
        name = normalize_name(col.name)
        nodes = [s.node for s in self.sources]
        src = _pick_source(self.select, nodes, name, 0)
        if src is not None:
            return nodes.index(src)
        if all(_provides(self.select, n, name, 0) is False for n in nodes) and self.select.find_ancestor(exp.Select):
            return _OUTER
        return None

    def _owners(self, node: exp.Expression) -> Set[int]:
        found: Set[int] = set()
        for col in node.find_all(exp.Column):
            if col.find_ancestor(exp.Select) is not self.select or isinstance(col.this, exp.Star):
                continue
            owner = self._owner(col)
            if owner is not None:
                found.add(owner)
        return found

    # --- linking ------------------------------------------------------------------------------------------------

    def _link_using(self, i: int, join: exp.Join) -> None:
        using = [normalize_name(u.name) for u in join.args.get("using") or []]
        natural = (join.args.get("method") or "").upper() == "NATURAL"
        if not using and not natural:
            return
        for j in range(i):
            node = self.sources[j].node
            if natural or any(_provides(self.select, node, name, 0) is not False for name in using):
                self._uf.union(i, j)

    def _tied_by(self, atom: exp.Expression) -> List[int]:
        """Sources (with ``_OUTER``) a single conjunct ties together; empty when it ties nothing."""
        if isinstance(atom, exp.Or):
            return []
        if not isinstance(atom, _LINKING_PREDICATES):
            return []
        if isinstance(atom, exp.In) and atom.args.get("query") is not None:
            return []
        owners = self._owners(atom)
        local = sorted(o for o in owners if o != _OUTER)
        if _OUTER in owners and local:
            local.append(_OUTER)
        return local if len(local) >= 2 else []

    def _or_ties(self, atom: exp.Or) -> List[List[int]]:
        """An OR ties a source only if every branch ties it to something: rows a branch leaves untied pair freely.
        Returns the groups to unite (each such source with all its partners)."""
        branches = []
        for branch in _disjuncts(atom):
            tied = [t for conjunct in _atoms(branch) for t in [self._tied_by(conjunct)] if t]
            branches.append({o for group in tied for o in group})
        if not branches or any(not b for b in branches):
            return []
        common = set.intersection(*branches)
        return [sorted(set.union(*branches))] if common else []

    def _link_predicates(self, atoms: List[exp.Expression]) -> None:
        for atom in atoms:
            groups = self._or_ties(atom) if isinstance(atom, exp.Or) else [self._tied_by(atom)]
            for local in groups:
                for a, b in zip(local, local[1:]):
                    self._uf.union(a, b)

    def _link_constants(self, atoms: List[exp.Expression]) -> None:
        by_value: Dict[str, int] = {}
        for atom in atoms:
            if not isinstance(atom, exp.EQ):
                continue
            for col_side, lit_side in ((atom.left, atom.right), (atom.right, atom.left)):
                col_side = col_side.unnest() if isinstance(col_side, exp.Paren) else col_side
                while isinstance(col_side, (exp.Cast, exp.TryCast)):
                    col_side = col_side.this
                while isinstance(lit_side, (exp.Cast, exp.TryCast, exp.Paren)):
                    lit_side = lit_side.this
                if isinstance(col_side, exp.Column) and isinstance(lit_side, exp.Literal):
                    owner = self._owner(col_side)
                    if owner is None or owner == _OUTER:
                        continue
                    key = ("s:" if lit_side.is_string else "n:") + str(lit_side.this)
                    if key in by_value:
                        self._uf.union(owner, by_value[key])
                    else:
                        by_value[key] = owner

    def _pin_primary_keys(self, atoms: List[exp.Expression]) -> None:
        for atom in atoms:
            if not isinstance(atom, exp.EQ):
                continue
            for col_side, lit_side in ((atom.left, atom.right), (atom.right, atom.left)):
                while isinstance(lit_side, (exp.Cast, exp.TryCast, exp.Paren)):
                    lit_side = lit_side.this
                if not isinstance(col_side, exp.Column) or not isinstance(lit_side, exp.Literal):
                    continue
                owner = self._owner(col_side)
                if owner is None or owner == _OUTER:
                    continue
                src = self.sources[owner]
                if src.kind == "table" and PRIMARY_KEYS.get(src.name or "") == normalize_name(col_side.name):
                    src.one_row = True

    def _link_correlated(self) -> None:
        """A derived or LATERAL table whose query reads another source of this SELECT is tied to it; one that reads
        a source of an enclosing SELECT is tied to the outer row."""
        outer_aliases: Set[str] = set()
        enclosing = self.select.find_ancestor(exp.Select)
        while enclosing is not None:
            outer_aliases |= {normalize_name(n.alias_or_name) for n, _ in _flatten(enclosing)}
            enclosing = enclosing.find_ancestor(exp.Select)
        for i, src in enumerate(self.sources):
            if src.kind != "query" or src.query is None or isinstance(src.node, exp.Table):
                continue
            inner_aliases = {normalize_name(t.alias_or_name) for t in src.query.find_all(exp.Table, exp.Subquery)}
            for col in src.query.find_all(exp.Column):
                qualifier = normalize_name(col.table) if col.table else ""
                if not qualifier or qualifier in inner_aliases:
                    continue
                matched = False
                for j, other in enumerate(self.sources):
                    if j != i and other.alias == qualifier:
                        self._uf.union(i, j)
                        matched = True
                if not matched and qualifier in outer_aliases:
                    self._uf.union(i, _OUTER)

    def column_equalities(self) -> List[Tuple[int, exp.Column, int, exp.Column]]:
        """(source, column, source, column) for each conjunct that equates columns of two different sources."""
        out = []
        for atom in self.atoms:
            if not isinstance(atom, (exp.EQ, exp.NullSafeEQ)):
                continue
            left, right = atom.left, atom.right
            while isinstance(left, (exp.Cast, exp.TryCast, exp.Paren)):
                left = left.this
            while isinstance(right, (exp.Cast, exp.TryCast, exp.Paren)):
                right = right.this
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                a, b = self._owner(left), self._owner(right)
                if a is not None and b is not None and a != b and _OUTER not in (a, b):
                    out.append((a, left, b, right))
        return out

    # --- results ------------------------------------------------------------------------------------------------

    def groups(self) -> List[List[int]]:
        """Indexes of the sources, grouped by what predicates tie together (the outer row counts as a member)."""
        by_root: Dict[int, List[int]] = {}
        for i in range(len(self.sources)):
            by_root.setdefault(self._uf.find(i), []).append(i)
        return list(by_root.values())


__all__ = ["METADATA_TABLES", "Scope", "Source"]

"""Which FROM/JOIN sources of one SELECT are tied to the same person.

Equalities that must hold (AND-connected, in ON / WHERE, plus USING) put columns into classes of equal values. Two
person-level sources are linked when one class holds, for each of them, a key of the same person-level entity
(CDM v5.4 foreign keys: ``person_id``, ``visit_occurrence_id``, ``visit_detail_id``, an event primary key, ...), a
polymorphic event key matched with such a key, or when both are attached to the same FACT_RELATIONSHIP /
EPISODE_EVENT row. Shared by the person-linkage and cohort rules.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.helpers import _source_query, column_origins, column_owner, normalize_name

from ._person_keys import PERSON_SCOPED_KEYS, PERSON_TABLES, POLYMORPHIC_KEYS, Key, key_domain
from ._join_scope import exposed_columns, peel, scope_sources

Node = Tuple[str, str]

# Link tables: one row relates the two records its key columns point at.
_LINK_ROW_KEYS = {
    "fact_relationship": ("fact_id_1", "fact_id_2"),
    "episode_event": ("episode_id", "event_id"),
}


def _is_conjunct(node: exp.Expression, root: exp.Expression) -> bool:
    cursor = node
    while cursor is not root:
        cursor = cursor.parent
        if cursor is None or not isinstance(cursor, (exp.And, exp.Paren)):
            return False
    return True


class ScopeLinks:
    """Equality classes and person links among the sources of ``select``."""

    def __init__(self, select: exp.Select, shadowed: Set[str]):
        self.select = select
        self.sources = scope_sources(select)
        self.exposed: Dict[str, Dict[str, Key]] = {
            alias: exposed_columns(select, src, shadowed) for alias, src in self.sources
        }
        self.base: Dict[str, str] = {
            alias: normalize_name(src.name)
            for alias, src in self.sources
            if isinstance(src, exp.Table) and src.name and self.exposed[alias] and _source_query(select, src) is None
        }
        # A LATERAL / APPLY source is evaluated per row of the sources before it; its link is inside the subquery.
        self.dependent: Set[str] = {alias for alias, src in self.sources if isinstance(src, exp.Lateral)}
        self._parent: Dict[Node, Node] = {}
        self._alternatives: List[List[Tuple[Node, Node]]] = []
        self._build()

    # --- equality classes -------------------------------------------------------------------------------------

    def _find(self, x: Node) -> Node:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def _union(self, a: Node, b: Node) -> None:
        self._parent[self._find(a)] = self._find(b)

    def _node(self, expr: exp.Expression) -> Optional[Node]:
        col = peel(expr)
        if not isinstance(col, exp.Column):
            return None
        name = normalize_name(col.name)
        if col.table:
            return normalize_name(col.table), name
        owner = column_owner(col, self.select)
        return (owner, name) if owner else None

    def _nodes(self, expr: exp.Expression) -> List[Node]:
        """Class members for an operand: a column, or each column argument of COALESCE (whose value is one of them,
        as in ``FULL JOIN b ON COALESCE(a1.person_id, a2.person_id) = b.person_id``)."""
        inner = peel(expr)
        if isinstance(inner, exp.Coalesce):
            args = [inner.this, *inner.expressions]
            nodes = [self._node(a) for a in args]
            return [n for n in nodes if n] if all(nodes) else []
        node = self._node(expr)
        return [node] if node else []

    def _disjunct_pairs(self, disjunction: exp.Or) -> List[Tuple[Node, Node]]:
        """One column pair per disjunct when every disjunct is a single column equality, else []."""
        parts: List[exp.Expression] = []
        stack: List[exp.Expression] = [disjunction]
        while stack:
            node = stack.pop()
            while isinstance(node, exp.Paren):
                node = node.this
            if isinstance(node, exp.Or):
                stack.extend([node.this, node.expression])
            else:
                parts.append(node)
        pairs = []
        for part in parts:
            if not isinstance(part, exp.EQ):
                return []
            left, right = self._node(part.this), self._node(part.expression)
            if not left or not right:
                return []
            pairs.append((left, right))
        return pairs

    def _pin_literals_inside_sources(self) -> None:
        """A CTE / derived source whose own WHERE pins a passed-on column to a literal (``SELECT * FROM condition_era
        WHERE person_id = 42``) carries that literal for every row."""
        for alias, src in self.sources:
            query = _source_query(self.select, src)
            if not isinstance(query, exp.Select) or query.args.get("where") is None:
                continue
            root = query.args["where"].this
            for eq in root.find_all(exp.EQ):
                if eq.find_ancestor(exp.Select) is not query or not _is_conjunct(eq, root):
                    continue
                for col, lit in ((peel(eq.this), peel(eq.expression)), (peel(eq.expression), peel(eq.this))):
                    if not isinstance(col, exp.Column) or not isinstance(lit, exp.Literal):
                        continue
                    origins = column_origins(col)
                    if not origins or len(origins) != 1:
                        continue
                    origin = next(iter(origins))
                    for name, exposed in self.exposed.get(alias, {}).items():
                        if exposed == origin:
                            self._union((alias, name), ("#literal", lit.sql()))

    def _build(self) -> None:
        select = self.select
        roots = [j.args["on"] for j in select.args.get("joins") or [] if j.args.get("on") is not None]
        if select.args.get("where") is not None:
            roots.append(select.args["where"].this)
        for root in roots:
            for disjunction in root.find_all(exp.Or):
                if disjunction.find_ancestor(exp.Select) is not select or not _is_conjunct(disjunction, root):
                    continue
                pairs = self._disjunct_pairs(disjunction)
                if pairs:
                    self._alternatives.append(pairs)
            for eq in root.find_all(exp.EQ):
                if eq.find_ancestor(exp.Select) is not select or not _is_conjunct(eq, root):
                    continue
                left_nodes, right_nodes = self._nodes(eq.this), self._nodes(eq.expression)
                lit_l, lit_r = peel(eq.this), peel(eq.expression)
                for left in left_nodes:
                    for right in right_nodes:
                        self._union(left, right)
                if left_nodes and not right_nodes and isinstance(lit_r, exp.Literal):
                    for left in left_nodes:
                        self._union(left, ("#literal", lit_r.sql()))
                elif right_nodes and not left_nodes and isinstance(lit_l, exp.Literal):
                    for right in right_nodes:
                        self._union(right, ("#literal", lit_l.sql()))
        self._pin_literals_inside_sources()
        seen: List[str] = []
        from_node = select.args.get("from_") or select.args.get("from")
        if from_node is not None and from_node.this.alias_or_name:
            seen.append(normalize_name(from_node.this.alias_or_name))
        for join in select.args.get("joins") or []:
            alias = normalize_name(join.this.alias_or_name) if join.this.alias_or_name else None
            for ident in join.args.get("using") or []:
                name = normalize_name(ident.name)
                for earlier in seen:
                    if alias and (name in self.exposed.get(earlier, {}) or not self.exposed.get(earlier)):
                        self._union((earlier, name), (alias, name))
            if alias:
                seen.append(alias)

    def class_of(self, node: Node) -> List[Node]:
        root = self._find(node)
        return [n for n in list(self._parent) if self._find(n) == root]

    def origin(self, node: Node) -> Optional[Key]:
        return self.exposed.get(node[0], {}).get(node[1])

    # --- person links -----------------------------------------------------------------------------------------

    def person_level(self) -> Dict[str, str]:
        """alias -> label, for sources that carry a key of a person-level entity."""
        out: Dict[str, str] = {}
        for alias, _ in self.sources:
            if alias in self.dependent:
                continue
            origins = self.exposed[alias].values()
            if self.base.get(alias) in PERSON_TABLES:
                out[alias] = self.base[alias]
            elif alias not in self.base and any(
                o[0] in PERSON_TABLES and key_domain(*o) in PERSON_SCOPED_KEYS for o in origins
            ):
                tables = sorted({o[0] for o in origins if o[0] in PERSON_TABLES})
                out[alias] = f"{alias} ({', '.join(tables)})"
        return out

    def _person_keys(self, nodes: List[Node], level: Dict[str, str]) -> List[Tuple[str, Optional[Key], bool]]:
        """(alias, key domain, is polymorphic event key) for the class members that belong to person-level sources."""
        out = []
        for node in nodes:
            if node[0] not in level:
                continue
            origin = self.origin(node)
            if origin is None:
                continue
            out.append((node[0], key_domain(*origin), origin in POLYMORPHIC_KEYS))
        return out

    def person_graph(self) -> Dict[str, Set[str]]:
        level = self.person_level()
        graph: Dict[str, Set[str]] = defaultdict(set)

        def connect(a: str, b: str) -> None:
            if a != b:
                graph[a].add(b)
                graph[b].add(a)

        classes: Dict[Node, List[Node]] = defaultdict(list)
        for node in list(self._parent):
            classes[self._find(node)].append(node)
        for nodes in classes.values():
            keys = self._person_keys(nodes, level)
            for i, (a, da, pa) in enumerate(keys):
                for b, db, pb in keys[i + 1 :]:
                    same_entity = da is not None and da == db and da in PERSON_SCOPED_KEYS
                    event_link = (pa and db in PERSON_SCOPED_KEYS) or (pb and da in PERSON_SCOPED_KEYS)
                    if same_entity or event_link:
                        connect(a, b)
        # ``ON x.k = a.k OR x.k = b.k``: when every alternative ties x to a source of one already linked group, x
        # is tied to that group whichever alternative holds.
        changed = True
        while changed:
            changed = False
            component = _component_index(level, graph)
            for pairs in self._alternatives:
                links = []
                for p, q in pairs:
                    if not self._same_person_key(p, q, level):
                        links = []
                        break
                    links.append((p[0], q[0]))
                if not links:
                    continue
                for x in {links[0][0], links[0][1]}:
                    others = [b if a == x else a if b == x else None for a, b in links]
                    if None in others or x in others:
                        continue
                    groups = {component.get(o) for o in others}
                    if len(groups) == 1 and component.get(x) not in groups:
                        connect(x, others[0])
                        changed = True
        for alias, table in self.base.items():
            if table not in _LINK_ROW_KEYS:
                continue
            attached: List[str] = []
            for column in _LINK_ROW_KEYS[table]:
                for other, domain, _ in self._person_keys(self.class_of((alias, column)), level):
                    if domain in PERSON_SCOPED_KEYS:
                        attached.append(other)
            for a in attached:
                for b in attached:
                    connect(a, b)
        return graph

    def _same_person_key(self, p: Node, q: Node, level: Dict[str, str]) -> bool:
        if p[0] not in level or q[0] not in level or p[0] == q[0]:
            return False
        op, oq = self.origin(p), self.origin(q)
        if op is None or oq is None:
            return False
        dp, dq = key_domain(*op), key_domain(*oq)
        return dp is not None and dp == dq and dp in PERSON_SCOPED_KEYS

    def components(self) -> List[Set[str]]:
        level = self.person_level()
        graph = self.person_graph()
        seen: Set[str] = set()
        out: List[Set[str]] = []
        for start in level:
            if start in seen:
                continue
            comp, stack = set(), [start]
            while stack:
                cur = stack.pop()
                if cur in comp:
                    continue
                comp.add(cur)
                stack.extend(graph.get(cur, ()))
            seen |= comp
            out.append(comp)
        return out


def _component_index(level: Dict[str, str], graph: Dict[str, Set[str]]) -> Dict[str, int]:
    index: Dict[str, int] = {}
    for start in level:
        if start in index:
            continue
        marker, stack = len(index), [start]
        while stack:
            cur = stack.pop()
            if cur in index:
                continue
            index[cur] = marker
            stack.extend(graph.get(cur, ()))
    return index


__all__ = ["ScopeLinks"]

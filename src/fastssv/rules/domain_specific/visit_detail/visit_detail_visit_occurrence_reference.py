"""Visit Detail / Visit Occurrence linkage rule.

CDM v5.4 links each ``visit_detail`` row to the visit that subsumes it through the required foreign key
``visit_detail.visit_occurrence_id``. A SELECT that combines ``visit_detail`` and ``visit_occurrence`` rows of one
person without equating their ``visit_occurrence_id`` pairs each detail with every visit of that person, not only
with its own. Whether that is a mistake depends on what the query asks, so the finding is a WARNING.

Each SELECT is judged on its own. The two rows are identified by lineage (through casts, CTEs and derived tables,
and correlated references to an enclosing query), and the predicates of the SELECT are followed through any other
table's columns (``vd.person_id = c.subject_id = vo.person_id``). The rule fires when the two rows are tied by
``person_id`` and their ``visit_occurrence_id`` values are never equated. It is silent when:

* their ``visit_occurrence_id`` values are equated (directly, through ``CAST``, ``USING`` or another table);
* both rows are fixed by their own primary keys, each equated with another table's column (an event row's
  ``visit_detail_id`` and ``visit_occurrence_id``): the ETL recorded which detail and visit belong together;
* the visit_detail is also joined to its own parent visit, so the extra visit_occurrence row is a second visit;
* a row filter excludes the parent visit: ``vd.visit_occurrence_id <> vo.visit_occurrence_id``,
  ``vo.preceding_visit_occurrence_id = vd.visit_occurrence_id``, or a date comparison that places the visit after the
  detail ends or before it starts (``vo.visit_start_date > vd.visit_detail_end_date``, ``vo.visit_start_date
  BETWEEN vd.visit_detail_end_date AND …``, ``vo.visit_end_date < vd.visit_detail_start_date``): a visit_detail lies
  inside the visit that subsumes it, so the parent satisfies such a comparison at most on a shared boundary day;
* the SELECT cannot be changed by the pairing: it is the query of ``IN`` / ``EXISTS``, uses ``DISTINCT``, only
  ``COUNT(DISTINCT …)`` / ``MIN`` / ``MAX``, or keeps one row per ``ROW_NUMBER()`` partition, and reads only one of
  the two rows beyond ``person_id`` (the numbering window itself is not an output);
* both rows are attached by outer joins to a third row, are not compared with each other, and the SELECT keeps one
  row per partition (``ROW_NUMBER() OVER (PARTITION BY …) = 1`` in an enclosing query): two independent look-ups
  for the third row, with no row multiplied.

Joins on a non-person key (dates, ``care_site_id``, ``visit_detail_id = visit_occurrence_id``) cross persons; they
are left to the person-linkage and primary-key join rules, as is an unlinked cross join.

Violation pattern:
    SELECT vd.*, vo.* FROM visit_detail vd JOIN visit_occurrence vo ON vd.person_id = vo.person_id

Correct pattern:
    SELECT vd.*, vo.* FROM visit_detail vd JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import _select_sources, normalize_name, parse_sql, scope_nodes
from fastssv.core.references import cdm_table
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import RowPath, peel, read_of, restricts_rows

VISIT_DETAIL = "visit_detail"
VISIT_OCCURRENCE = "visit_occurrence"
VISIT_OCCURRENCE_ID = "visit_occurrence_id"
PRECEDING_VISIT_OCCURRENCE_ID = "preceding_visit_occurrence_id"
PERSON_ID = "person_id"
VISIT_DETAIL_ID = "visit_detail_id"
_PROBES = (PERSON_ID, VISIT_DETAIL_ID, VISIT_OCCURRENCE_ID)
_VO_STARTS = {"visit_start_date", "visit_start_datetime"}
_VO_ENDS = {"visit_end_date", "visit_end_datetime"}
_VD_DATES = {
    "visit_detail_start_date",
    "visit_detail_start_datetime",
    "visit_detail_end_date",
    "visit_detail_end_datetime",
}

Node = Tuple[RowPath, str]


def _probe(select: exp.Select, alias: str, name: str):
    """Read of ``alias.name`` as seen from ``select`` (a synthetic column scoped to the SELECT)."""
    col = exp.column(name, table=alias)
    col.parent = select
    return read_of(col)


def _instances(select: exp.Select, reads: List) -> Dict[RowPath, str]:
    """visit_detail / visit_occurrence rows visible in ``select``: its own sources and correlated outer ones."""
    out: Dict[RowPath, str] = {}
    for src in _select_sources(select):
        alias = normalize_name(src.alias_or_name)
        for name in _PROBES:
            read = _probe(select, alias, name)
            if read is not None and read[1] in (VISIT_DETAIL, VISIT_OCCURRENCE):
                out[read[0]] = read[1]
                break
    for path, table, _ in reads:
        if table in (VISIT_DETAIL, VISIT_OCCURRENCE):
            out.setdefault(path, table)
    return out


def _clauses(select: exp.Select) -> List[exp.Expression]:
    out = [j.args["on"] for j in select.args.get("joins") or [] if j.args.get("on") is not None]
    for key in ("where", "having"):
        if select.args.get(key) is not None:
            out.append(select.args[key].this)
    return out


def _keeps_first_row_per_partition(select: exp.Select) -> bool:
    """``select`` numbers its rows with ``ROW_NUMBER() OVER (PARTITION BY …)`` and an enclosing query keeps only
    number 1."""
    numbered = {
        normalize_name(proj.alias)
        for proj in select.expressions
        if isinstance(proj, exp.Alias)
        and isinstance(proj.this, exp.Window)
        and isinstance(proj.this.this, exp.RowNumber)
        and proj.this.args.get("partition_by")
    }
    if not numbered:
        return False
    outer = select.parent
    while outer is not None:
        if isinstance(outer, exp.Select) and outer is not select and outer.args.get("where"):
            for eq in outer.args["where"].find_all(exp.EQ):
                sides = (peel(eq.this), peel(eq.expression))
                if any(isinstance(s, exp.Column) and normalize_name(s.name) in numbered for s in sides) and any(
                    isinstance(s, exp.Literal) and s.this == "1" for s in sides
                ):
                    return True
        outer = outer.parent
    return False


def _in_semi_join(select: exp.Select) -> bool:
    node: exp.Expression = select
    while isinstance(node.parent, (exp.Subquery, exp.Paren, exp.SetOperation)):
        node = node.parent
    parent = node.parent
    return isinstance(parent, exp.Exists) or (isinstance(parent, exp.In) and node.arg_key == "query")


def _duplicate_insensitive(select: exp.Select) -> bool:
    if _in_semi_join(select) or select.args.get("distinct") is not None:
        return True
    aggs = [
        n
        for e in [*select.expressions, select.args.get("having")]
        if e is not None
        for n in scope_nodes(e)
        if isinstance(n, exp.AggFunc)
    ]
    if not aggs or any(isinstance(a.parent, exp.Window) for a in aggs):
        return False
    return all(
        isinstance(a, (exp.Min, exp.Max)) or (isinstance(a, exp.Count) and isinstance(a.this, exp.Distinct))
        for a in aggs
    )


def _outer_joined(select: exp.Select, path: RowPath) -> bool:
    if path[0][0] != id(select):
        return False
    for join in select.args.get("joins") or []:
        if normalize_name(join.this.alias_or_name) == path[0][1]:
            return normalize_name(join.side or "") in {"left", "right", "full"}
    return False


class _Links:
    """Union-find over (row, column) nodes of one SELECT's equalities."""

    def __init__(self) -> None:
        self.parent: Dict[Node, Node] = {}

    def find(self, x: Node) -> Node:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: Node, b: Node) -> None:
        self.parent[self.find(a)] = self.find(b)

    def tied(self, a: Node, b: Node) -> bool:
        return self.find(a) == self.find(b)

    def pinned(self, row: RowPath, key: str) -> bool:
        root = self.find((row, key))
        return any(node[0] != row and self.find(node) == root for node in list(self.parent))


def _excludes_parent(cmp: exp.Expression, vd: RowPath, vo: RowPath) -> bool:
    """A row filter that places the visit outside the visit_detail: the visit that subsumes the detail can satisfy it
    at most on a shared boundary day."""
    if isinstance(cmp, exp.Between):
        target, low = read_of(cmp.this), read_of(cmp.args.get("low"))
        return (
            target is not None
            and low is not None
            and target[0] == vo
            and target[2] in _VO_STARTS
            and low[0] == vd
            and low[2] in _VD_DATES
        )
    left, right = read_of(cmp.this), read_of(cmp.expression)
    if left is None or right is None:
        return False
    sides = {left[0]: left[2], right[0]: right[2]}
    if set(sides) != {vd, vo}:
        return False
    if isinstance(cmp, exp.NEQ):
        return sides[vd] == VISIT_OCCURRENCE_ID and sides[vo] == VISIT_OCCURRENCE_ID
    if isinstance(cmp, exp.EQ):
        return sides[vd] == VISIT_OCCURRENCE_ID and sides[vo] == PRECEDING_VISIT_OCCURRENCE_ID
    later, earlier = (left, right) if isinstance(cmp, (exp.GT, exp.GTE)) else (right, left)
    if later[0] == vo and earlier[0] == vd:
        return later[2] in _VO_STARTS and earlier[2] in _VD_DATES
    return earlier[0] == vo and later[0] == vd and earlier[2] in _VO_ENDS and later[2] in _VD_DATES


def _pairings(select: exp.Select) -> List[str]:
    clauses = _clauses(select)
    nodes = [n for c in clauses for n in scope_nodes(c)]
    reads = []
    for node in nodes:
        if isinstance(node, exp.Column):
            read = read_of(node)
            if read is not None:
                reads.append(read)
    instances = _instances(select, reads)
    vds = [p for p, t in instances.items() if t == VISIT_DETAIL]
    vos = [p for p, t in instances.items() if t == VISIT_OCCURRENCE]
    if not vds or not vos:
        return []

    links = _Links()
    direct: Set[Tuple[RowPath, RowPath]] = set()
    for node in nodes:
        if isinstance(node, exp.EQ):
            a, b = read_of(node.this), read_of(node.expression)
            if a is not None and b is not None:
                links.union((a[0], a[2]), (b[0], b[2]))
                direct.add((a[0], b[0]))
                direct.add((b[0], a[0]))
    earlier: List[str] = []
    for src in _select_sources(select):
        alias = normalize_name(src.alias_or_name)
        join = src.parent if isinstance(src.parent, exp.Join) else None
        for name in [normalize_name(u.name) for u in (join.args.get("using") or [])] if join is not None else []:
            mine = _probe(select, alias, name)
            for other in earlier:
                theirs = _probe(select, other, name)
                if mine is not None and theirs is not None:
                    links.union((mine[0], mine[2]), (theirs[0], theirs[2]))
                    direct.add((mine[0], theirs[0]))
                    direct.add((theirs[0], mine[0]))
        earlier.append(alias)

    # Rows read beyond person_id anywhere in the SELECT (outputs, predicates, grouping).
    read_beyond_person: Set[RowPath] = set()
    first_row_only = _keeps_first_row_per_partition(select)
    # The ROW_NUMBER() window only chooses which row survives; it is not an output.
    outputs = [
        e
        for e in select.expressions
        if not (first_row_only and isinstance(e, exp.Alias) and isinstance(e.this, exp.Window))
    ]
    scope = [*outputs, *clauses]
    if select.args.get("group") is not None:
        scope.append(select.args["group"])
    for part in scope:
        for node in scope_nodes(part):
            if isinstance(node, exp.Column):
                read = read_of(node)
                if read is not None and read[2] != PERSON_ID:
                    read_beyond_person.add(read[0])
    # A row of an enclosing query is that query's row: the subquery decides which of them are kept.
    read_beyond_person |= {path for path in instances if path[0][0] != id(select)}

    parented = {vd for vd in vds for vo in vos if links.tied((vd, VISIT_OCCURRENCE_ID), (vo, VISIT_OCCURRENCE_ID))}
    found: List[str] = []
    for vd in vds:
        if vd in parented:
            continue
        for vo in vos:
            if not links.tied((vd, PERSON_ID), (vo, PERSON_ID)):
                continue
            if links.pinned(vd, VISIT_DETAIL_ID) and links.pinned(vo, VISIT_OCCURRENCE_ID):
                continue
            if any(
                isinstance(n, (exp.NEQ, exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between))
                and restricts_rows(n)
                and _excludes_parent(n, vd, vo)
                for n in nodes
            ):
                continue
            if (_duplicate_insensitive(select) or first_row_only) and not {vd, vo} <= read_beyond_person:
                continue
            if (
                (vd, vo) not in direct
                and _outer_joined(select, vd)
                and _outer_joined(select, vo)
                and (first_row_only or _duplicate_insensitive(select))
            ):
                continue
            found.append(f"{vd[0][1]} ↔ {vo[0][1]}")
    return found


@register
class VisitDetailVisitOccurrenceReferenceRule(Rule):
    rule_id = "domain_specific.visit_detail_visit_occurrence_reference"
    name = "Visit Detail Visit Occurrence Linkage"

    description = (
        "A SELECT that combines visit_detail and visit_occurrence rows of one person should link them on "
        "visit_occurrence_id; a person_id join pairs each visit_detail with every visit of the person, which is "
        "intended only when the query is about the person's other visits."
    )

    severity = Severity.WARNING
    suggested_fix = (
        "ADD: `vd.visit_occurrence_id = vo.visit_occurrence_id` to the join, or exclude the parent visit explicitly "
        "(`vd.visit_occurrence_id <> vo.visit_occurrence_id`) when the other visits of the person are intended."
    )
    long_description = (
        "CDM v5.4 links every visit_detail to the visit that subsumes it through the required foreign key "
        "visit_detail.visit_occurrence_id. Joining the two tables on person_id instead pairs each detail with all "
        "visits of the person. The rule follows CTEs, derived tables, casts and joins through other tables, and stays "
        "silent when the visit ids are equated, when the parent visit is excluded on purpose (`<>` on visit ids, a "
        "preceding-visit link, or a date comparison the parent cannot satisfy), when both rows are fixed by an event "
        "row's keys, or when duplicated rows cannot change the result and only one of the two tables is read."
    )
    example_bad = (
        "SELECT vd.visit_detail_id, vd.visit_detail_concept_id, vo.visit_concept_id\n"
        "FROM visit_detail vd\n"
        "JOIN visit_occurrence vo ON vd.person_id = vo.person_id;"
    )
    example_good = (
        "SELECT vd.visit_detail_id, vd.visit_detail_concept_id, vo.visit_concept_id\n"
        "FROM visit_detail vd\n"
        "JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id;"
    )
    references = (cdm_table("visit_detail"),)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []
        lowered = sql.lower()
        if VISIT_DETAIL not in lowered or VISIT_OCCURRENCE not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees:
            if not tree:
                continue
            for select in tree.find_all(exp.Select):
                for pair in _pairings(select):
                    if pair in seen:
                        continue
                    seen.add(pair)
                    violations.append(
                        self.create_violation(
                            message=(
                                f"visit_detail and visit_occurrence ({pair}) are combined on person_id without "
                                "equating visit_occurrence_id, so each visit_detail is paired with every visit of "
                                "the person, not only the visit it belongs to. Join on "
                                "visit_detail.visit_occurrence_id = visit_occurrence.visit_occurrence_id unless the "
                                "person's other visits are what the query is about."
                            ),
                            details={"pair": pair, "expected_join_column": VISIT_OCCURRENCE_ID},
                        )
                    )
        return violations


__all__ = ["VisitDetailVisitOccurrenceReferenceRule"]

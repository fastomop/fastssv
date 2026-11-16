"""Visit Detail Dates Within Parent Visit Rule.

CDM v5.4 defines ``visit_detail.visit_occurrence_id`` as the visit_occurrence that *subsumes* the visit_detail
record ("Put the VISIT_OCCURRENCE_ID that subsumes the VISIT_DETAIL record here"), so a visit_detail is expected to
lie inside its parent visit: it starts no earlier than ``visit_start_date`` and ends no later than
``visit_end_date``. The specification allows the detail to share the visit's dates ("This may or may not be equal
to the date of the Visit"). A filter that keeps only visit_detail rows dated strictly outside the visit they are
joined to selects misnested records: ETL defects, or a deliberate audit of them. It is a WARNING.

Fires on a comparison between a visit_detail date and the date of the visit_occurrence row it is linked to on
``visit_occurrence_id`` when the comparison can only hold outside the parent visit:

    vd.visit_detail_start_date < vo.visit_start_date        (also visit_detail_end_date)
    vd.visit_detail_end_date   > vo.visit_end_date          (also visit_detail_start_date)

in either operand order, as a difference (``vd.visit_detail_start_date - vo.visit_start_date < 0``) or with a
non-negative offset that only widens the gap (``< vo.visit_start_date - INTERVAL '1 day'``). ``<=`` / ``>=`` count
only with a strictly widening offset, because equal dates are allowed. Dates are compared with dates and datetimes
with datetimes; a date against a datetime compares midnight with a time and is not judged.

Rows are followed through casts, CTEs and derived tables, and the visit_occurrence_id link may run through another
table (``vd.visit_occurrence_id = co.visit_occurrence_id = vo.visit_occurrence_id``) or ``USING`` / NATURAL joins.
The comparison must restrict rows: a conjunct of WHERE or of an inner join's ON clause, outside NOT / NOT EXISTS /
EXCEPT. Inside OR, CASE or an outer join's ON the same predicate excludes or labels misnested rows.

Violation:
    SELECT vd.visit_detail_id FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vd.visit_detail_start_date < vo.visit_start_date

Not flagged:
    ... WHERE vd.visit_detail_start_date <= vo.visit_start_date     -- same-day admission
    ... JOIN visit_occurrence prev ON prev.person_id = vo.person_id
        WHERE prev.visit_end_date < vd.visit_detail_start_date      -- a different visit
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import _select_sources, normalize_name, parse_sql, scope_nodes
from fastssv.core.references import cdm_table
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import (
    Read,
    RowPath,
    in_negated_scope,
    number_literal,
    peel,
    read_of,
    restricts_rows,
)

VISIT_DETAIL = "visit_detail"
VISIT_OCCURRENCE = "visit_occurrence"
VISIT_OCCURRENCE_ID = "visit_occurrence_id"

# column -> (kind, role)
_DATES: Dict[Tuple[str, str], Tuple[str, str]] = {
    (VISIT_DETAIL, "visit_detail_start_date"): ("date", "detail"),
    (VISIT_DETAIL, "visit_detail_end_date"): ("date", "detail"),
    (VISIT_DETAIL, "visit_detail_start_datetime"): ("datetime", "detail"),
    (VISIT_DETAIL, "visit_detail_end_datetime"): ("datetime", "detail"),
    (VISIT_OCCURRENCE, "visit_start_date"): ("date", "start"),
    (VISIT_OCCURRENCE, "visit_end_date"): ("date", "end"),
    (VISIT_OCCURRENCE, "visit_start_datetime"): ("datetime", "start"),
    (VISIT_OCCURRENCE, "visit_end_datetime"): ("datetime", "end"),
}

# A term: (read, offset in days) for `column + offset`.
Term = Tuple[Read, float]


def _offset_days(node: Optional[exp.Expression]) -> Optional[float]:
    """Days in a numeric literal (PostgreSQL date arithmetic) or an ``INTERVAL 'n' DAY`` / ``INTERVAL 'n days'``."""
    node = peel(node)
    if not isinstance(node, exp.Interval):
        return number_literal(node)
    words = str(node.this.this if isinstance(node.this, exp.Literal) else node.this).split()
    unit = node.args.get("unit")
    unit_name = normalize_name(unit.name) if unit is not None else (words[1].lower() if len(words) > 1 else "")
    try:
        amount = float(words[0])
    except (ValueError, IndexError):
        return None
    return amount if unit_name.rstrip("s") == "day" else None


def _term(node: Optional[exp.Expression]) -> Optional[Term]:
    node = peel(node)
    if isinstance(node, (exp.Add, exp.Sub)):
        inner = _term(node.this)
        offset = _offset_days(node.expression)
        if inner is None or offset is None:
            return None
        return inner[0], inner[1] + (offset if isinstance(node, exp.Add) else -offset)
    read = read_of(node)
    if read is None or (read[1], read[2]) not in _DATES:
        return None
    return read, 0.0


def _ordered(cmp: exp.Expression) -> Optional[Tuple[Term, Term, float, bool]]:
    """``cmp`` as ``low + 0 < high + k`` (strict) or ``<=``: (low, high, k, strict)."""
    strict = isinstance(cmp, (exp.LT, exp.GT))
    left, right = (cmp.this, cmp.expression) if isinstance(cmp, (exp.LT, exp.LTE)) else (cmp.expression, cmp.this)
    # difference form: a - b < c  ->  a < b + c ;  c < a - b  ->  b + c < a
    for diff, const, diff_is_low in ((left, right, True), (right, left, False)):
        diff = peel(diff)
        c = number_literal(const)
        if isinstance(diff, exp.Sub) and c is not None:
            a, b = _term(diff.this), _term(diff.expression)
            if a is not None and b is not None:
                if diff_is_low:  # a - b < c
                    return a, b, c + b[1] - a[1], strict
                return b, a, a[1] - b[1] - c, strict  # c < a - b  ->  b < a - c
    low, high = _term(left), _term(right)
    if low is None or high is None:
        return None
    return low, high, high[1] - low[1], strict


def _widens(k: float, strict: bool) -> bool:
    return k <= 0 if strict else k < 0


class _Links:
    def __init__(self) -> None:
        self.parent: Dict[Tuple[RowPath, str], Tuple[RowPath, str]] = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> None:
        self.parent[self.find(a)] = self.find(b)


def _probe(select: exp.Select, alias: str, name: str) -> Optional[Read]:
    col = exp.column(name, table=alias)
    col.parent = select
    return read_of(col)


def _parent_links(tree: exp.Expression) -> List[Tuple[RowPath, RowPath]]:
    """(visit_detail row, visit_occurrence row) pairs whose visit_occurrence_id are equated in some SELECT."""
    pairs: List[Tuple[RowPath, RowPath]] = []
    for select in tree.find_all(exp.Select):
        links = _Links()
        rows: Dict[RowPath, str] = {}
        clauses = [j.args["on"] for j in select.args.get("joins") or [] if j.args.get("on") is not None]
        if select.args.get("where") is not None:
            clauses.append(select.args["where"].this)
        for clause in clauses:
            for node in scope_nodes(clause):
                if isinstance(node, exp.EQ):
                    a, b = read_of(node.this), read_of(node.expression)
                    if a is not None and b is not None:
                        links.union((a[0], a[2]), (b[0], b[2]))
                        for r in (a, b):
                            rows[r[0]] = r[1]
        earlier: List[str] = []
        for src in _select_sources(select):
            alias = normalize_name(src.alias_or_name)
            join = src.parent if isinstance(src.parent, exp.Join) else None
            names: List[str] = []
            if join is not None:
                names = [normalize_name(u.name) for u in join.args.get("using") or []]
                if normalize_name(join.args.get("method") or "") == "natural":
                    names.append(VISIT_OCCURRENCE_ID)
            if VISIT_OCCURRENCE_ID in names:
                mine = _probe(select, alias, VISIT_OCCURRENCE_ID)
                for other in earlier:
                    theirs = _probe(select, other, VISIT_OCCURRENCE_ID)
                    if mine is not None and theirs is not None:
                        links.union((mine[0], mine[2]), (theirs[0], theirs[2]))
                        rows[mine[0]], rows[theirs[0]] = mine[1], theirs[1]
            earlier.append(alias)
        vds = [p for p, t in rows.items() if t == VISIT_DETAIL]
        vos = [p for p, t in rows.items() if t == VISIT_OCCURRENCE]
        for vd in vds:
            for vo in vos:
                if links.find((vd, VISIT_OCCURRENCE_ID)) == links.find((vo, VISIT_OCCURRENCE_ID)):
                    pairs.append((vd, vo))
    return pairs


def _linked(vd: RowPath, vo: RowPath, links: List[Tuple[RowPath, RowPath]]) -> bool:
    """``vd`` and ``vo`` read the two rows of one parent link, through the same outer sources."""
    for lvd, lvo in links:
        if (
            len(vd) >= len(lvd)
            and len(vo) >= len(lvo)
            and vd[len(vd) - len(lvd) :] == lvd
            and vo[len(vo) - len(lvo) :] == lvo
        ):
            if vd[: len(vd) - len(lvd)] == vo[: len(vo) - len(lvo)]:
                return True
    return False


def _find_violations(tree: exp.Expression) -> List[str]:
    links = _parent_links(tree)
    if not links:
        return []
    found: List[str] = []
    for cmp in tree.find_all(exp.LT, exp.GT, exp.LTE, exp.GTE):
        if not restricts_rows(cmp) or in_negated_scope(cmp):
            continue
        ordered = _ordered(cmp)
        if ordered is None:
            continue
        (low, _), (high, _), k, strict = ordered
        low_kind, low_role = _DATES[(low[1], low[2])]
        high_kind, high_role = _DATES[(high[1], high[2])]
        if low_kind != high_kind or not _widens(k, strict):
            continue
        # detail before the visit starts, or detail after the visit ends
        if low_role == "detail" and high_role == "start":
            vd, vo, shape = low, high, f"{low[2]} before {high[2]}"
        elif low_role == "end" and high_role == "detail":
            vd, vo, shape = high, low, f"{high[2]} after {low[2]}"
        else:
            continue
        if not _linked(vd[0], vo[0], links):
            continue
        text = f"`{cmp.sql()}` ({shape})"
        if text not in found:
            found.append(text)
    return found


@register
class VisitDetailDatesWithinParentVisitRule(Rule):
    rule_id = "domain_specific.visit_detail_dates_within_parent_visit"
    name = "Visit Detail Dates Within Parent Visit"

    description = (
        "Flags a filter that keeps only visit_detail rows dated strictly before the start or after the end of the "
        "visit_occurrence they are linked to on visit_occurrence_id. CDM v5.4 nests a visit_detail inside the visit "
        "that subsumes it, so such a filter selects misnested records."
    )

    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the outside-the-visit comparison WITH the nesting it violates, "
        "`vd.visit_detail_start_date >= vo.visit_start_date AND vd.visit_detail_end_date <= vo.visit_end_date`, "
        "or remove it. Keep it only when auditing misnested records on purpose."
    )
    long_description = (
        "CDM v5.4 puts in visit_detail.visit_occurrence_id the visit that subsumes the detail, and allows the detail "
        "to share the visit's dates. A row filter that holds only when the detail starts before its parent visit "
        "starts, or ends after it ends, keeps only misnested records. Dates are compared with dates and datetimes "
        "with datetimes; differences and widening offsets are recognised; rows are followed through CTEs and derived "
        "tables."
    )
    example_bad = (
        "SELECT vd.visit_detail_id FROM visit_detail vd\n"
        "JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id\n"
        "WHERE vd.visit_detail_start_date < vo.visit_start_date;"
    )
    example_good = (
        "SELECT vd.visit_detail_id FROM visit_detail vd\n"
        "JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id\n"
        "WHERE vd.visit_detail_start_date = vo.visit_start_date;"
    )
    references = (cdm_table("visit_detail"),)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql or VISIT_DETAIL not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees:
            if not tree:
                continue
            for shape in _find_violations(tree):
                if shape in seen:
                    continue
                seen.add(shape)
                violations.append(
                    self.create_violation(
                        message=(
                            f"Filter {shape} keeps only visit_detail rows dated outside the visit_occurrence they are "
                            "linked to. CDM v5.4 places a visit_detail inside the visit that subsumes it, so this "
                            "selects misnested records, which is what an audit of them wants and not a clinical "
                            "filter."
                        ),
                        details={"comparison": shape},
                    )
                )
        return violations


__all__ = ["VisitDetailDatesWithinParentVisitRule"]

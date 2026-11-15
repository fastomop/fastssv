"""Visit Length-of-Stay Arithmetic Rule.

CDM v5.4, VISIT_OCCURRENCE: "The Visit duration, or 'length of stay', is defined as VISIT_END_DATE -
VISIT_START_DATE. For all Visits this is <1 day, except Inpatient Visits and Non-hospital institution Visits."
Outpatient and emergency room visits without an end date in the source get ``visit_end_datetime =
visit_start_datetime``. Length of stay (LOS), written ``visit_end_date - visit_start_date``,
``DATEDIFF(day, visit_start_date, visit_end_date)`` or ``DATE_DIFF(...)``, is therefore checked in two situations:

1. **Diluted average.** LOS is aggregated (AVG, MEDIAN, percentiles, STDDEV, VARIANCE, MIN) over visit types that
   mix multi-day and same-day visits, so the same-day zeros pull the result down; SUM is not checked because zeros
   do not change a total. The LOS may be computed inside the aggregate or projected by a CTE or derived table and
   aggregated outside. Not flagged when the visit row is restricted to multi-day types (descendants of 9201
   Inpatient Visit, 262 Emergency Room and Inpatient Visit, 42898160 Non-hospital institution Visit), or restricted
   in a way whose types cannot be read statically (a subquery, a join to concept, ``<>``, a care_site
   place-of-service filter); when the visit row keeps only visits that end after they start (``visit_end_date >
   visit_start_date``, ``LOS >= 1``); or when the aggregate is grouped / partitioned by the visit's
   visit_concept_id.
2. **Multi-day threshold on same-day visits.** The visit row is restricted to the outpatient or ER hierarchy and a
   row filter requires a LOS on dates of two days or more (``> 1``, ``>= 2``, ``BETWEEN 5 AND 10``,
   ``visit_end_date > visit_start_date + 1``). Such visits are expected to last less than a day, so the filter keeps
   only records that break the convention. A one-day difference is not flagged, because a visit shorter than a day
   can cross midnight.

Restrictions, groupings and filters count only for the visit row the LOS is computed from, followed through casts,
CTEs and derived tables; a filter on another row (a subquery, another alias) does not. The concept sets are
generated from the vocabulary (``_ds2_visit_type_concepts.py``).
"""

import re
from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import display_sql, has_table_reference, normalize_name, parse_sql
from fastssv.core.references import cdm_table
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import (
    RowPath,
    in_negated_scope,
    number_literal,
    peel,
    projection_behind,
    read_of,
    restricts_rows,
    same_row,
)

from ._ds2_visit_type_concepts import MULTI_DAY_VISIT_CONCEPT_IDS, SAME_DAY_VISIT_CONCEPT_IDS

VISIT_OCCURRENCE = "visit_occurrence"
START_COLS = {"visit_start_date", "visit_start_datetime"}
END_COLS = {"visit_end_date", "visit_end_datetime"}
DATE_ONLY_COLS = {"visit_start_date", "visit_end_date"}
VISIT_CONCEPT_ID = "visit_concept_id"

_AGG_TYPES = tuple(
    getattr(exp, n)
    for n in (
        "Avg",
        "Median",
        "PercentileCont",
        "PercentileDisc",
        "Quantile",
        "ApproxQuantile",
        "Stddev",
        "StddevPop",
        "StddevSamp",
        "Variance",
        "VariancePop",
        "Min",
    )
    if hasattr(exp, n)
)
_AGG_NAMES = {"median", "percentile_cont", "percentile_disc", "quantile", "stdev", "var"}
_DIFF_FUNC_NAMES = {"datediff", "date_diff", "timestampdiff"}
_DATE_PARTS = {"day", "days", "dd", "d", "hour", "hh", "minute", "mi", "second", "week", "wk", "month", "mm", "year"}
_PEEL = (exp.Cast, exp.TryCast, exp.Paren, exp.TsOrDsToDate, exp.TimeStrToTime, exp.TimeStrToDate)
_FLIP = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE, exp.EQ: exp.EQ}

# (sign, on_dates, visit row): sign +1 for end - start, -1 for start - end.
Los = Tuple[int, bool, RowPath]


def _peel(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, _PEEL):
        node = node.this
    return node


def _visit_date(node: Optional[exp.Expression], context: exp.Expression) -> Optional[Tuple[RowPath, str]]:
    """(row, column) when ``node`` reads a visit_occurrence start / end column. A bare name parsed as a variable
    (``DATEDIFF(day, visit_start_date, visit_end_date)`` in PostgreSQL) is resolved in ``context``'s SELECT."""
    node = _peel(node)
    if isinstance(node, exp.Var):
        select = context.find_ancestor(exp.Select)
        if select is None:
            return None
        node = exp.column(normalize_name(node.name))
        node.parent = select
    read = read_of(node)
    if read is None or read[1] != VISIT_OCCURRENCE or read[2] not in START_COLS | END_COLS:
        return None
    return read[0], read[2]


def _los_operands(node: exp.Expression) -> Optional[Los]:
    """LOS when ``node`` is visit end minus visit start of one row (or the reverse)."""
    later = earlier = None
    if isinstance(node, exp.Sub):
        later, earlier = node.this, node.expression
    elif isinstance(node, exp.DateDiff):
        this = _peel(node.this)
        unit = node.args.get("unit")
        if (
            isinstance(this, exp.Column)
            and normalize_name(this.name) in _DATE_PARTS
            and isinstance(unit, exp.Var)
            and _visit_date(unit, node)
        ):
            # Postgres parses T-SQL style DATEDIFF(day, start, end) as this=day, expression=start, unit=end.
            later, earlier = unit, node.expression
        else:
            later, earlier = node.this, node.expression
    elif isinstance(node, exp.Anonymous) and normalize_name(node.name) in _DIFF_FUNC_NAMES:
        args = node.expressions
        if len(args) == 3:
            earlier, later = args[1], args[2]
        elif len(args) == 2:
            later, earlier = args[0], args[1]
    if later is None or earlier is None:
        return None
    a, b = _visit_date(later, node), _visit_date(earlier, node)
    if not a or not b or not same_row(a[0], b[0]):
        return None
    row = a[0] if len(a[0]) >= len(b[0]) else b[0]
    on_dates = a[1] in DATE_ONLY_COLS and b[1] in DATE_ONLY_COLS
    if a[1] in END_COLS and b[1] in START_COLS:
        return 1, on_dates, row
    if a[1] in START_COLS and b[1] in END_COLS:
        return -1, on_dates, row
    return None


_DIFF_CALL_RE = re.compile(r"\b(?:datediff|date_diff|timestampdiff)\s*\(", re.IGNORECASE)


def _source_text(sql: str, node: exp.Expression) -> str:
    """The date-difference call as written in ``sql`` (sqlglot rewrites T-SQL DATEDIFF with casts), falling back
    to the rendered node."""
    if isinstance(node, (exp.DateDiff, exp.Anonymous)):
        names = {normalize_name(n.name) for n in node.find_all(exp.Column, exp.Var)} & (START_COLS | END_COLS)
        for match in _DIFF_CALL_RE.finditer(sql):
            depth, end = 0, None
            for k in range(match.end() - 1, len(sql)):
                if sql[k] == "(":
                    depth += 1
                elif sql[k] == ")":
                    depth -= 1
                    if depth == 0:
                        end = k + 1
                        break
            if end is not None:
                text = sql[match.start() : end]
                if all(n in text.lower() for n in names):
                    return text
    return display_sql(node)


def _restriction_nodes(tree: exp.Expression) -> List[exp.Expression]:
    return [
        n
        for n in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)
        if restricts_rows(n) and not in_negated_scope(n)
    ]


def _visit_type_restriction(nodes: List[exp.Expression], row: RowPath) -> Tuple[Set[int], bool]:
    """(literal visit_concept_ids, non-literal restriction present) that row filters put on ``row``."""
    literals: Set[int] = set()
    opaque = False
    for node in nodes:
        if not isinstance(node, (exp.EQ, exp.In, exp.NEQ)):
            continue
        sides = [node.this] if isinstance(node, exp.In) else [node.this, node.expression]
        for side in sides:
            read = read_of(side)
            if read is None or read[1:] != (VISIT_OCCURRENCE, VISIT_CONCEPT_ID) or not same_row(read[0], row):
                continue
            if isinstance(node, exp.NEQ) or (isinstance(node, exp.In) and node.args.get("query") is not None):
                opaque = True
                break
            values = node.expressions if isinstance(node, exp.In) else [s for s in sides if s is not side]
            other = read_of(values[0]) if len(values) == 1 else None
            if other is not None and other[1:] == (VISIT_OCCURRENCE, VISIT_CONCEPT_ID):
                # Equating two visit rows' types restricts neither.
                break
            for value in values:
                number = number_literal(value)
                if number is None:
                    opaque = True
                else:
                    literals.add(int(number))
            break
    return literals, opaque


def _boundaries_restrict(node: exp.Expression) -> bool:
    """Every IN / EXISTS subquery ``node`` sits in is itself a row filter of its enclosing query."""
    child, parent = node, node.parent
    while parent is not None:
        if isinstance(parent, exp.Exists) or (isinstance(parent, exp.In) and child.arg_key == "query"):
            if not restricts_rows(parent) or in_negated_scope(parent):
                return False
        child, parent = parent, parent.parent
    return True


def _key_equivalent_rows(nodes: List[exp.Expression], row: RowPath) -> List[RowPath]:
    """``row`` plus the visit_occurrence rows a row filter equates with it on the primary key (a join, a correlated
    EXISTS, or ``vo.visit_occurrence_id IN (SELECT visit_occurrence_id FROM visit_occurrence …)``): a restriction on
    one of them keeps only the same visits."""
    key = (VISIT_OCCURRENCE, "visit_occurrence_id")
    links: List[Tuple[RowPath, RowPath]] = []
    for node in nodes:
        if not _boundaries_restrict(node):
            continue
        if isinstance(node, exp.EQ):
            a, b = read_of(node.this), read_of(node.expression)
        elif isinstance(node, exp.In) and node.args.get("query") is not None:
            query = node.args["query"]
            query = query.this if isinstance(query, exp.Subquery) else query
            first = query.expressions[0] if isinstance(query, exp.Select) and query.expressions else None
            a, b = read_of(node.this), read_of(first.this if isinstance(first, exp.Alias) else first)
        else:
            continue
        if a is not None and b is not None and a[1:] == key and b[1:] == key:
            links.append((a[0], b[0]))
    rows = [row]
    changed = True
    while changed:
        changed = False
        for a, b in links:
            for mine, other in ((a, b), (b, a)):
                if any(same_row(mine, r) for r in rows) and not any(same_row(other, r) for r in rows):
                    rows.append(other)
                    changed = True
    return rows


def _row_type_restriction(nodes: List[exp.Expression], row: RowPath) -> Tuple[Set[int], bool]:
    """Visit-type restriction on ``row`` or on a row equated with it on visit_occurrence_id."""
    literals: Set[int] = set()
    opaque = False
    for equivalent in _key_equivalent_rows(nodes, row):
        found, unknown = _visit_type_restriction(nodes, equivalent)
        literals |= found
        opaque = opaque or unknown
    return literals, opaque


def _care_site_restricted(nodes: List[exp.Expression], tree: exp.Expression, row: RowPath) -> bool:
    """The visit row's care site is filtered on place_of_service_concept_id: its visit types are restricted in a way
    that cannot be read statically."""
    filtered: List[RowPath] = []
    for node in nodes:
        for side in (node.this, node.expression):
            read = read_of(side)
            if read is not None and read[1:] == ("care_site", "place_of_service_concept_id"):
                filtered.append(read[0])
    if not filtered:
        return False
    for eq in tree.find_all(exp.EQ):
        a, b = read_of(eq.this), read_of(eq.expression)
        if a is None or b is None:
            continue
        for mine, other in ((a, b), (b, a)):
            if (
                mine[1:] == (VISIT_OCCURRENCE, "care_site_id")
                and same_row(mine[0], row)
                and other[1:] == ("care_site", "care_site_id")
                and any(same_row(other[0], f) for f in filtered)
            ):
                return True
    return False


def _stratified(agg: exp.Expression, row: RowPath) -> bool:
    """The aggregate is grouped or partitioned by the visit row's visit_concept_id."""
    columns: List[exp.Column] = []
    window = agg.find_ancestor(exp.Window)
    if window is not None:
        for part in window.args.get("partition_by") or []:
            columns.extend(part.find_all(exp.Column))
    select = agg.find_ancestor(exp.Select)
    group = select.args.get("group") if select is not None else None
    if group is not None:
        columns.extend(group.find_all(exp.Column))
    for col in columns:
        read = read_of(col)
        if read is not None and read[1:] == (VISIT_OCCURRENCE, VISIT_CONCEPT_ID) and same_row(read[0], row):
            return True
    return False


def _enclosing_aggregate(node: exp.Expression) -> Optional[exp.Expression]:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, _AGG_TYPES) or (
            isinstance(parent, exp.Anonymous) and normalize_name(parent.name) in _AGG_NAMES
        ):
            return parent
        if isinstance(parent, exp.Predicate):
            return None
        parent = parent.parent
    return None


def _projected_los(col: exp.Column) -> Optional[Tuple[exp.Expression, Los]]:
    """The LOS expression a CTE / derived table projects under the name ``col`` reads, followed through renames."""
    current: Optional[exp.Expression] = col
    for _ in range(8):
        if not isinstance(current, exp.Column):
            return None
        expr = projection_behind(current)
        if expr is None:
            return None
        expr = _peel(expr)
        if isinstance(expr, (exp.Add, exp.Sub)) and _los_operands(expr) is None:
            # LOS + 1 (inclusive days) still averages LOS
            inner = _peel(expr.this)
            if inner is not None and _los_operands(inner) is not None:
                expr = inner
        los = _los_operands(expr) if expr is not None else None
        if los is not None:
            return expr, los
        current = expr
    return None


def _los_bound(cmp: exp.Expression, los_side: str, sign: int, offset: float) -> Optional[Tuple[float, bool]]:
    """(lower bound, strict) of the LOS values a comparison admits; ``offset`` is added to LOS on ``los_side``."""
    if isinstance(cmp, exp.Between):
        low, high = number_literal(cmp.args.get("low")), number_literal(cmp.args.get("high"))
        if low is None or high is None:
            return None
        return ((low - offset) * 1.0, False) if sign > 0 else (-(high - offset), False)
    other = cmp.expression if los_side == "this" else cmp.this
    c = number_literal(other)
    if c is None:
        return None
    op = type(cmp) if los_side == "this" else _FLIP[type(cmp)]
    c -= offset
    if sign < 0:
        op, c = _FLIP[op], -c
    if op is exp.GT:
        return c, True
    if op in (exp.GTE, exp.EQ):
        return c, False
    return None


def _admits_only_above(bound: Optional[Tuple[float, bool]], days: float) -> bool:
    return bound is not None and (bound[0] > days or (bound[0] == days and bound[1]))


def _date_term(node: Optional[exp.Expression], context: exp.Expression) -> Optional[Tuple[RowPath, str, float]]:
    """``visit date + n`` (n days, possibly 0) as (row, column, n)."""
    node = _peel(node)
    if isinstance(node, (exp.Add, exp.Sub)):
        inner = _date_term(node.this, context)
        n = number_literal(node.expression)
        if inner is None or n is None:
            return None
        return inner[0], inner[1], inner[2] + (n if isinstance(node, exp.Add) else -n)
    found = _visit_date(node, context)
    return (found[0], found[1], 0.0) if found else None


def _end_vs_start_bound(cmp: exp.Expression) -> Optional[Tuple[RowPath, bool, float, bool]]:
    """``end + a <op> start + b`` read as a lower bound on LOS: (row, on_dates, bound, strict)."""
    if not isinstance(cmp, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return None
    left, right = _date_term(cmp.this, cmp), _date_term(cmp.expression, cmp)
    if left is None or right is None or not same_row(left[0], right[0]):
        return None
    op = type(cmp)
    if left[1] in START_COLS and right[1] in END_COLS:
        left, right, op = right, left, _FLIP[op]
    if not (left[1] in END_COLS and right[1] in START_COLS) or op not in (exp.GT, exp.GTE):
        return None
    # end + a > start + b  ->  LOS > b - a
    on_dates = left[1] in DATE_ONLY_COLS and right[1] in DATE_ONLY_COLS
    row = left[0] if len(left[0]) >= len(right[0]) else right[0]
    return row, on_dates, right[2] - left[2], op is exp.GT


def _excludes_same_day(nodes: List[exp.Expression], row: RowPath) -> bool:
    """A row filter keeps only visits of ``row`` that end after they start (`end > start`, `end - start >= 1`)."""
    for node in nodes:
        direct = _end_vs_start_bound(node)
        if direct is not None and same_row(direct[0], row) and _admits_only_above(direct[2:], 0):
            return True
        for side_name in ("this", "expression"):
            side = node.args.get(side_name)
            while isinstance(side, exp.Paren):
                side = side.this
            if side is None or (isinstance(node, exp.Between) and side_name != "this"):
                continue
            los = _los_operands(side)
            offset = 0.0
            if los is None and isinstance(side, exp.Column):
                projected = _projected_los(side)
                los = projected[1] if projected else None
            if los is None or not same_row(los[2], row):
                continue
            if _admits_only_above(_los_bound(node, side_name, los[0], offset), 0):
                return True
    return False


@register
class VisitLengthOfStayArithmeticRule(Rule):
    """Flag length-of-stay arithmetic that does not match the visit types it covers."""

    rule_id = "domain_specific.visit_length_of_stay_arithmetic"
    name = "Visit Length-of-Stay Arithmetic"
    description = (
        "Flags visit length-of-stay arithmetic that ignores visit type: an aggregate (AVG, MEDIAN, ...) over "
        "visit types mixing multi-day and same-day visits, or a two-day-plus LOS filter on outpatient / ER visits, "
        "which by CDM convention last less than a day."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: a visit_concept_id restriction to inpatient-like visits before aggregating LOS, e.g. "
        "`vo.visit_concept_id IN (SELECT descendant_concept_id FROM concept_ancestor WHERE ancestor_concept_id IN "
        "(9201, 262, 42898160))`, OR GROUP BY visit_concept_id; for a multi-day LOS filter, REPLACE the outpatient "
        "/ ER visit_concept_id with the inpatient one."
    )
    long_description = (
        "CDM v5.4 visit conventions: only Inpatient and Non-hospital institution visits are expected to last more "
        "than a day; Outpatient and Emergency Room visits last less than a day and default to "
        "visit_end_date = visit_start_date. Averaging length of stay over all visit types therefore mixes many "
        "same-day values into the result, and requiring two or more days of stay on outpatient or ER visits keeps "
        "only records that break the convention. Restrictions, groupings and filters count only for the visit row "
        "the length of stay is computed from, followed through CTEs and derived tables."
    )
    example_bad = "SELECT AVG(visit_end_date - visit_start_date) AS avg_los\nFROM visit_occurrence;"
    example_good = "SELECT AVG(visit_end_date - visit_start_date) AS avg_los\nFROM visit_occurrence\nWHERE visit_concept_id = 9201;"
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT visit_occurrence_id FROM visit_occurrence\n"
                "WHERE visit_concept_id = 9202\n"
                "  AND visit_end_date - visit_start_date >= 2;"
            ),
            severity=Severity.WARNING,
            note="multi-day length-of-stay filter on outpatient visits, which last less than a day",
        ),
    )
    references = (cdm_table("visit_occurrence"),)

    def _diluted(self, tree: exp.Expression, nodes: List[exp.Expression], agg: exp.Expression, los: Los) -> bool:
        sign, _, row = los
        if sign <= 0 or _stratified(agg, row):
            return False
        literals, opaque = _row_type_restriction(nodes, row)
        if opaque or _care_site_restricted(nodes, tree, row):
            return False
        if literals and not (literals & MULTI_DAY_VISIT_CONCEPT_IDS and literals & SAME_DAY_VISIT_CONCEPT_IDS):
            return False
        return not _excludes_same_day(nodes, row)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if VISIT_OCCURRENCE not in lowered or "visit_end" not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None or not has_table_reference(tree, VISIT_OCCURRENCE):
                continue
            nodes = _restriction_nodes(tree)
            seen = set()

            def dilution(text: str, agg: exp.Expression) -> None:
                if ("dilution", text) in seen:
                    return
                seen.add(("dilution", text))
                violations.append(
                    self.create_violation(
                        message=(
                            f"Length of stay `{text}` is aggregated with {agg.key.upper()} across visit types that "
                            "include same-day visits (outpatient, emergency room: end date = start date by CDM "
                            "convention), which pulls the result toward 0. Restrict visit_concept_id to inpatient-like "
                            "visits or stratify by it."
                        ),
                        details={"expression": text, "kind": "diluted_aggregate"},
                    )
                )

            # LOS computed inside the aggregate
            for node in tree.find_all(exp.Sub, exp.DateDiff, exp.Anonymous):
                los = _los_operands(node)
                if los is None:
                    continue
                agg = _enclosing_aggregate(node)
                if agg is not None and self._diluted(tree, nodes, agg, los):
                    dilution(_source_text(sql, node), agg)
            # LOS projected by a CTE / derived table and aggregated outside
            for col in tree.find_all(exp.Column):
                agg = _enclosing_aggregate(col)
                if agg is None:
                    continue
                projected = _projected_los(col)
                if projected is None:
                    continue
                # The visit row is the one read inside the CTE / derived table; filters written on it there or
                # outside (through the same source) share its path suffix.
                expr, los = projected
                if self._diluted(tree, nodes, agg, los):
                    dilution(_source_text(sql, expr), agg)

            # Multi-day threshold on same-day visit types
            for cmp in nodes:
                found = self._threshold(cmp)
                if found is None:
                    continue
                row, cmp_sql = found
                literals, opaque = _row_type_restriction(nodes, row)
                if opaque or not literals or not literals <= SAME_DAY_VISIT_CONCEPT_IDS:
                    continue
                cmp_sql = display_sql(cmp)
                if ("same_day", cmp_sql) in seen:
                    continue
                seen.add(("same_day", cmp_sql))
                violations.append(
                    self.create_violation(
                        message=(
                            f"The query restricts visit_concept_id to outpatient / emergency room visits, "
                            f"which last less than a day by CDM convention, but `{cmp_sql}` keeps only "
                            "stays of two or more days; it can only select records that break the "
                            "convention. Use inpatient visits (9201) for multi-day stays."
                        ),
                        details={"expression": cmp_sql, "kind": "multi_day_filter_on_same_day_visits"},
                    )
                )
        return violations

    @staticmethod
    def _threshold(cmp: exp.Expression) -> Optional[Tuple[RowPath, str]]:
        """(visit row, text) when ``cmp`` is a row filter admitting only date LOS of two days or more."""
        direct = _end_vs_start_bound(cmp)
        if direct is not None:
            row, on_dates, bound, strict = direct
            return (row, display_sql(cmp)) if on_dates and _admits_only_above((bound, strict), 1) else None
        if not isinstance(cmp, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.Between)):
            return None
        for side_name in ("this", "expression"):
            side = cmp.args.get(side_name)
            offset = 0.0
            side = peel(side)
            if isinstance(side, exp.Add) and number_literal(side.expression) is not None:
                offset, side = number_literal(side.expression), peel(side.this)
            if side is None or (isinstance(cmp, exp.Between) and side_name != "this"):
                continue
            los = _los_operands(side)
            if los is None and isinstance(side, exp.Column):
                projected = _projected_los(side)
                los = projected[1] if projected else None
            if los is None or not los[1]:
                continue
            if _admits_only_above(_los_bound(cmp, side_name, los[0], offset), 1):
                return los[2], display_sql(cmp)
        return None


__all__ = ["VisitLengthOfStayArithmeticRule", "MULTI_DAY_VISIT_CONCEPT_IDS", "SAME_DAY_VISIT_CONCEPT_IDS"]

"""Period Join Requires Date Overlap Rule.

``observation_period`` and ``payer_plan_period`` hold one row per period, and a person can have several of each.
OMOP CDM v5.4: "For each Person, one or more OBSERVATION_PERIOD records may be present, but they will not overlap or
be back to back to each other", and "A Person can have multiple, overlapping, Payer_Plan_Periods". Joining an event
table to a period table on ``person_id`` alone pairs every event with every period of that person, so each event row
repeats once per period and counts, sums and period attributes are wrong. The Book of OHDSI makes the same point for
its exposure cohort: "because a person can have several observation periods we must make sure we only join to the
period containing the therapy". The person_id link may be direct or run through other columns of the same SELECT
(``op.person_id = p.person_id AND co.person_id = p.person_id``).

The join is bounded to the period(s) containing the event when the same SELECT bounds the period on both sides
around one date: that date ``>=`` the period start and ``<=`` the period end, or ``BETWEEN start AND end``, also
written as a non-negative date difference (``x - start >= 365``, ``DATEDIFF(day, start, x) >= 0``). Offsets such as
``op.observation_period_start_date + 365 * INTERVAL '1 day'`` are the same bound. An interval whose start is after the
period start and whose end is before the period end is contained too. Because observation periods of one person do
not overlap, this leaves one observation period per event, and so does a bound equal to one value (a date, or a
per-person ``(SELECT MIN(observation_period_start_date) …)``). Payer plan periods may overlap: containment keeps the
plans in force at the event, which can be more than one, and a bound equal to one value does not single out a plan.

A one-sided washout (``start <= event - 365``) does not bound the period, nor does an overlap test against a window
(``start <= window_end AND end >= window_start``): an earlier or later period satisfies either. A date held between
the two bounds the wrong way round (``x BETWEEN end AND start``, ``x >= end AND x <= start``) is not reported here:
temporal.observation_period_date_range_logic reports the reversal.

A SELECT whose result cannot change under row duplication is not reported: the query of an ``IN (…)`` /
``EXISTS (…)``, ``SELECT DISTINCT``, or an aggregate whose only aggregates are ``COUNT(DISTINCT …)``, ``MIN`` or
``MAX`` with no period column in the output or the GROUP BY. That exemption does not apply when a predicate compares a
period column with another table's column without bounding it (e.g. a one-sided washout): the wrong period is then
admitted, and de-duplication does not repair that. ``person`` joined to a period table is not reported: the join yields one row
per period, and counting periods is a legitimate question that the SQL alone cannot tell apart from a mistake.

Tables wrapped in a CTE or derived table that passes their rows through are judged as the tables themselves; a
wrapper around the period table counts only when it applies no filter of its own (a filter can select one period).

Violation pattern:
    SELECT COUNT(*) FROM condition_occurrence co
    JOIN observation_period op ON co.person_id = op.person_id

Correct pattern:
    SELECT COUNT(*) FROM condition_occurrence co
    JOIN observation_period op
      ON co.person_id = op.person_id
     AND co.condition_start_date BETWEEN op.observation_period_start_date
                                     AND op.observation_period_end_date
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    _select_sources,
    column_owner,
    normalize_name,
    parse_sql,
    PERIOD_BOUNDS,
    period_is_pinned,
    _scope_columns,
    scope_nodes,
    scope_predicates,
    unwrap_cast,
)
from fastssv.core.references import BOOK_SQL, cdm_table, section
from fastssv.core.registry import register

from ._cdm_keys import PERSON_ID_TABLES
from ._lineage import base_table_of, wrapper_selects


PERSON = "person"
# Person-level tables other than the period tables themselves (CDM v5.4 tables with a person_id column).
CLINICAL_EVENT_TABLES = frozenset(PERSON_ID_TABLES) - set(PERIOD_BOUNDS) - {PERSON}

PERSON_ID = "person_id"
_DUP_INSENSITIVE_AGGS = (exp.Min, exp.Max)


def _person_id_pairs(select: exp.Select, local: Dict[str, str]) -> Set[Tuple[str, str]]:
    """(period_alias, clinical_alias) pairs whose person_id values ``select`` equates, directly or through other
    columns (``op.person_id = p.person_id AND co.person_id = p.person_id``), in ON/WHERE or ``USING (person_id)``."""
    parent: Dict[Tuple[str, str], Tuple[str, str]] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for pred in scope_predicates(select):
        for node in scope_nodes(pred):
            if not isinstance(node, exp.EQ):
                continue
            left, right = unwrap_cast(node.this), unwrap_cast(node.expression)
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                lo, ro = column_owner(left, select), column_owner(right, select)
                if lo and ro:
                    union((lo, normalize_name(left.name)), (ro, normalize_name(right.name)))

    seen: List[str] = []
    for src in [select.args.get("from_") or select.args.get("from"), *(select.args.get("joins") or [])]:
        if src is None or not isinstance(src.this, (exp.Table, exp.Subquery)) or not src.this.alias_or_name:
            continue
        alias = normalize_name(src.this.alias_or_name)
        using = [normalize_name(u.name) for u in (src.args.get("using") or [])] if isinstance(src, exp.Join) else []
        for col in using:
            for earlier in seen:
                union((alias, col), (earlier, col))
        seen.append(alias)

    period = [a for a, t in local.items() if t in PERIOD_BOUNDS]
    clinical = [a for a, t in local.items() if t in CLINICAL_EVENT_TABLES]
    return {
        (p, c)
        for p in period
        for c in clinical
        if (p, PERSON_ID) in parent and find((p, PERSON_ID)) == find((c, PERSON_ID))
    }


def _in_semi_join(select: exp.Select) -> bool:
    """``select`` is the query of an ``IN (…)`` or ``EXISTS (…)``: only whether a row exists matters."""
    node: exp.Expression = select
    while isinstance(node.parent, (exp.Subquery, exp.Paren, exp.SetOperation)):
        node = node.parent
    parent = node.parent
    return isinstance(parent, exp.Exists) or (isinstance(parent, exp.In) and node.arg_key == "query")


def _period_compared_across(select: exp.Select, period_alias: str) -> bool:
    """A predicate compares a period column with another source's column (e.g. a one-sided washout): a missing
    bound then admits the wrong period, which deduplication does not repair."""
    for pred in scope_predicates(select):
        for node in scope_nodes(pred):
            if not isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.EQ, exp.NEQ)):
                continue
            owners = {(column_owner(c, select), normalize_name(c.name)) for c in _scope_columns(node)}
            mine = {n for o, n in owners if o == period_alias}
            if mine - {PERSON_ID} and any(o != period_alias for o, _ in owners):
                return True
    return False


def _is_duplicate_insensitive(select: exp.Select, period_alias: str) -> bool:
    """The SELECT's result is unchanged if its input rows are duplicated, and no period column reaches its output."""
    outputs = list(select.expressions)
    group = select.args.get("group")
    if group is not None:
        outputs.extend(group.expressions)
    for out in outputs:
        if any(column_owner(col, select) == period_alias for col in _scope_columns(out)):
            return False
    if select.args.get("distinct") is not None:
        return True
    aggs = [n for e in [*select.expressions, select.args.get("having")] if e is not None for n in scope_nodes(e)]
    aggs = [n for n in aggs if isinstance(n, exp.AggFunc)]
    if not aggs:
        return False
    if any(isinstance(n.parent, exp.Window) for n in aggs):
        return False
    return all(
        isinstance(a, _DUP_INSENSITIVE_AGGS) or (isinstance(a, exp.Count) and isinstance(a.this, exp.Distinct))
        for a in aggs
    )


PAYER_PLAN_PERIOD = "payer_plan_period"

# Whether the column on the left of the comparison is above or below the right operand.
_LEFT_ABOVE = {exp.GT: True, exp.GTE: True, exp.LT: False, exp.LTE: False}


def _attempts_containment(select: exp.Select, period_alias: str, period_table: str) -> bool:
    """One outside column is held between the two bounds of the period, whichever bound is taken as the lower one
    (``x BETWEEN end AND start``, ``x >= end AND x <= start``). The reversed form is reported by
    temporal.observation_period_date_range_logic: the join has a containment constraint, written the wrong way
    round."""
    bounds = set(PERIOD_BOUNDS[period_table])

    def period_bound(node: exp.Expression):
        col = unwrap_cast(node)
        if isinstance(col, exp.Column) and column_owner(col, select) == period_alias:
            name = normalize_name(col.name)
            return name if name in bounds else None
        return None

    def outside_column(node: exp.Expression):
        col = unwrap_cast(node)
        if isinstance(col, exp.Column):
            owner = column_owner(col, select)
            if owner and owner != period_alias:
                return owner, normalize_name(col.name)
        return None

    # outside column -> {(bound, the column is above that bound)}
    compared: Dict[Tuple[str, str], Set[Tuple[str, bool]]] = {}
    for pred in scope_predicates(select):
        for node in scope_nodes(pred):
            if isinstance(node, exp.Between):
                outside = outside_column(node.this)
                low, high = period_bound(node.args.get("low")), period_bound(node.args.get("high"))
                if outside and low and high:
                    compared.setdefault(outside, set()).update({(low, True), (high, False)})
            elif type(node) in _LEFT_ABOVE:
                above = _LEFT_ABOVE[type(node)]
                for outer, bound_side, outer_above in (
                    (node.this, node.expression, above),
                    (node.expression, node.this, not above),
                ):
                    bound, outside = period_bound(bound_side), outside_column(outer)
                    if bound and outside:
                        compared.setdefault(outside, set()).add((bound, outer_above))
    return any(b1 != b2 and a1 and not a2 for found in compared.values() for b1, a1 in found for b2, a2 in found)


def _period_scope(select: exp.Select) -> Dict[str, str]:
    """alias -> base table for ``select``'s sources that supply a period table's or a person-level table's rows. A
    CTE or derived table around a period table counts only when no layer filters it: a filter can select one
    period."""
    out: Dict[str, str] = {}
    for src in _select_sources(select):
        if src is None or not src.alias_or_name:
            continue
        table = base_table_of(select, src)
        if table is None:
            continue
        if table in PERIOD_BOUNDS and any(
            inner.args.get("where") is not None for inner in wrapper_selects(select, src)
        ):
            continue
        out[normalize_name(src.alias_or_name)] = table
    return out


def _message(period_table: str, clinical_table: str) -> str:
    if period_table == PAYER_PLAN_PERIOD:
        return (
            f"payer_plan_period joined to {clinical_table} on person_id without a date-containment constraint. A "
            f"person can have several, overlapping payer_plan_period rows, so each {clinical_table} row repeats once "
            f"per plan, including plans not in force at the event. Bound the event date by the plan's start and end "
            f"to keep the plans in force at the event; there can still be more than one."
        )
    return (
        f"observation_period joined to {clinical_table} on person_id without a date-containment constraint. A person "
        f"can have several observation_period rows, so each {clinical_table} row repeats once per period and any "
        f"date condition is tested against every period rather than the one containing the event."
    )


@register
class PeriodJoinRequiresDateOverlapRule(Rule):
    """Period tables joined on person_id must be pinned to the period containing the event."""

    rule_id = "joins.period_join_requires_date_overlap"
    name = "Period Join Requires Date Overlap"
    description = (
        "observation_period and payer_plan_period joined to an event table on person_id need a date-containment "
        "constraint as well: a person can have several periods, so person_id alone repeats each event once per "
        "period. Observation periods of one person do not overlap, so containment leaves one; payer plan periods may "
        "overlap, so containment leaves the plans in force at the event."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: `AND <event>.<event_date> BETWEEN p.<period>_start_date AND p.<period>_end_date` (or both `>=` start "
        "and `<=` end) to the period join, in addition to person_id. For payer_plan_period this keeps the plans in "
        "force at the event; a person can hold more than one at a time (e.g. medical and drug coverage)."
    )
    example_bad = (
        "SELECT COUNT(*) FROM condition_occurrence co\nJOIN observation_period op ON co.person_id = op.person_id;"
    )
    example_good = (
        "SELECT COUNT(*) FROM condition_occurrence co\n"
        "JOIN observation_period op\n"
        "  ON co.person_id = op.person_id\n"
        "  AND co.condition_start_date BETWEEN op.observation_period_start_date\n"
        "                                  AND op.observation_period_end_date;"
    )
    extra_examples = (
        RuleExample(
            "SELECT ppp.payer_concept_id, SUM(de.quantity)\n"
            "FROM drug_exposure de\n"
            "JOIN payer_plan_period ppp ON de.person_id = ppp.person_id\n"
            "GROUP BY ppp.payer_concept_id;",
            Severity.WARNING,
            "payer_plan_period: each exposure is credited to every plan the person ever held.",
        ),
        RuleExample(
            "SELECT DISTINCT co.person_id\n"
            "FROM condition_occurrence co\n"
            "JOIN observation_period op ON co.person_id = op.person_id\n"
            "WHERE op.observation_period_start_date <= co.condition_start_date - INTERVAL '365 days';",
            Severity.WARNING,
            "A one-sided washout bound is met by any earlier period, not only the one containing the event; "
            "DISTINCT does not repair that.",
        ),
    )
    references = (
        section(BOOK_SQL, "exposure-cohort", "Exposure cohort"),
        cdm_table("observation_period"),
        cdm_table("payer_plan_period"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if not any(t in lowered for t in PERIOD_BOUNDS):
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, str]] = set()
        for tree in trees:
            if tree is None:
                continue
            for select in tree.find_all(exp.Select):
                local = _period_scope(select)
                for period_alias, clinical_alias in sorted(_person_id_pairs(select, local)):
                    period_table, clinical_table = local[period_alias], local[clinical_alias]
                    overlapping = period_table == PAYER_PLAN_PERIOD
                    if period_is_pinned(
                        select,
                        period_alias,
                        period_table,
                        bound_equality_pins=not overlapping,
                        date_differences=True,
                    ) or _attempts_containment(select, period_alias, period_table):
                        continue
                    if not _period_compared_across(select, period_alias) and (
                        _in_semi_join(select) or _is_duplicate_insensitive(select, period_alias)
                    ):
                        continue
                    key = (period_table, clinical_table)
                    if key in seen:
                        continue
                    seen.add(key)
                    violations.append(
                        self.create_violation(
                            message=_message(period_table, clinical_table),
                            details={"period_table": period_table, "clinical_table": clinical_table},
                        )
                    )
        return violations


__all__ = ["PeriodJoinRequiresDateOverlapRule"]

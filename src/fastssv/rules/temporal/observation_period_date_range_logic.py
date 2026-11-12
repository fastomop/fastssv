"""Observation Period Date Range Logic Rule.

OMOP semantic rules OMOP_033, OMOP_512:
A date lies inside an observation period when
``observation_period_start_date <= date AND date <= observation_period_end_date``
(or ``date BETWEEN start AND end``). The rule flags the reversed form, where one date is bounded from above by the
period start and from below by the period end of the same observation_period row:

    co.condition_start_date BETWEEN op.observation_period_end_date AND op.observation_period_start_date
    op.observation_period_start_date >= co.condition_start_date AND op.observation_period_end_date <= co.condition_start_date
    co.condition_start_date < op.observation_period_start_date AND co.condition_start_date > op.observation_period_end_date

A period never starts after it ends, so such a pair holds for no row (strict operators) or only when the period is
a single day (non-strict operators).

Only certain contradictions are reported:

- Both predicates must be AND-ed conjuncts of the WHERE / JOIN ON clauses of one SELECT and compare the same column
  (same qualifier, same cast) with the start and end of the same observation_period instance. Anything under OR
  or NOT is ignored, so the audit ``date < start OR date > end`` is not flagged.
- Either operand order, strict and non-strict operators, ``=``, ``BETWEEN`` and constant day offsets
  (``+ 365``, ``- INTERVAL '30 days'``, ``DATEADD(day, 365, ...)``) are understood. Offsets are taken into account:
  ``date <= start + 30 AND date >= end`` only limits the period length and is not flagged.
- One-sided tests (washout ``start <= date - 365``), interval tests that compare two different columns
  (``start >= event_start AND end <= event_end``, ``start BETWEEN event_start AND event_end``) and offsets in months
  or years are satisfiable or cannot be judged, and are not flagged.
- The period bounds are identified by lineage, so observation_period read through a CTE or derived table that passes
  one row through (including renamed bounds) is judged; a bound computed by an aggregate is not a bound of one row. A
  day difference compared with a constant (``x - start < 0``, ``DATEDIFF(day, start, x) < 0``) is read as the
  comparison it states (``x < start``), and a cast of a DATE value to DATE is ignored.
"""

import re
from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    _pick_source,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    normalize_name,
    parse_sql,
    scope_predicates,
)
from fastssv.core.references import BOOK_COHORTS, DQD_FIELD_THRESHOLDS, cdm_table, section
from fastssv.core.registry import register
from fastssv.rules.temporal._typing import DATE, sql_type, terminals


OBSERVATION_PERIOD_TABLE = "observation_period"
OP_START = "observation_period_start_date"
OP_END = "observation_period_end_date"

_UNIT_DAYS = {"day": 1, "days": 1, "dd": 1, "d": 1, "week": 7, "weeks": 7, "wk": 7, "ww": 7}
_INTERVAL_TEXT = re.compile(r"^\s*([+-]?\d+)\s*([a-z]*)\s*$", re.IGNORECASE)
_SHIFT_FUNCS = (exp.DateAdd, exp.DateSub, exp.TsOrDsAdd)

# A period bound is (id of the SELECT that reads observation_period, alias); an event value adds the column name and
# the cast applied to it, so `x` and `CAST(x AS DATE)` are never treated as one value.
PeriodKey = Tuple[int, str]
ValueKey = Tuple[int, str, str, str]


def _unit_days(unit: Optional[exp.Expression]) -> Optional[int]:
    return _UNIT_DAYS.get(normalize_name(unit.name)) if unit is not None else None


def _day_count(node: Optional[exp.Expression], unit_days: Optional[int] = None) -> Optional[int]:
    """A constant whole number of days: 30, -30, INTERVAL '30 days', INTERVAL '2' WEEK; None for anything else
    (months and years have no fixed length). ``unit_days`` is the unit supplied by an enclosing DATEADD."""
    while isinstance(node, exp.Paren):
        node = node.this
    if node is None:
        return None
    if isinstance(node, exp.Neg):
        inner = _day_count(node.this, unit_days)
        return None if inner is None else -inner
    if isinstance(node, exp.Interval):
        if not isinstance(node.this, exp.Literal):
            return None
        match = _INTERVAL_TEXT.match(str(node.this.this))
        if match is None:
            return None
        unit_arg, text_unit = node.args.get("unit"), match.group(2).lower()
        if unit_arg is not None and text_unit:
            return None
        unit = _unit_days(unit_arg) if unit_arg is not None else _UNIT_DAYS.get(text_unit)
        return None if unit is None else int(match.group(1)) * unit
    if isinstance(node, exp.Literal):
        text = str(node.this).strip()
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text) * (unit_days or 1)
    return None


def _shifted(node: Optional[exp.Expression]) -> Optional[Tuple[exp.Column, int, str]]:
    """(column, day offset, cast) when ``node`` is one column plus or minus a constant number of days."""
    if node is None:
        return None
    if isinstance(node, (exp.Paren, exp.TimeStrToTime, exp.TsOrDsToDate)):
        return _shifted(node.this)
    if isinstance(node, exp.Column):
        return (node, 0, "") if not isinstance(node.this, exp.Star) else None
    if isinstance(node, (exp.Cast, exp.TryCast)):
        inner = _shifted(node.this)
        if inner is None:
            return None
        # A cast of a DATE value to DATE does not change it.
        if not inner[2] and sql_type(node) == DATE and sql_type(inner[0]) == DATE:
            return inner
        return inner[0], inner[1], f"{inner[2]}>{node.to.sql().lower()}"
    if isinstance(node, (exp.Add, exp.Sub)):
        base, days = _shifted(node.this), _day_count(node.expression)
        if (base is None or days is None) and isinstance(node, exp.Add):
            base, days = _shifted(node.expression), _day_count(node.this)
        if base is None or days is None:
            return None
        return base[0], base[1] + (days if isinstance(node, exp.Add) else -days), base[2]
    if isinstance(node, _SHIFT_FUNCS):
        unit = node.args.get("unit")
        unit_days = _unit_days(unit) if unit is not None else 1
        base = _shifted(node.this)
        days = _day_count(node.expression, unit_days) if unit_days is not None else None
        if base is None or days is None:
            return None
        return base[0], base[1] + (-days if isinstance(node, exp.DateSub) else days), base[2]
    if isinstance(node, exp.Anonymous) and normalize_name(node.name) == "dateadd" and len(node.expressions) == 3:
        # DATEADD(day, n, x) read under a dialect that has no such function.
        unit, amount, value = node.expressions
        unit_days = _UNIT_DAYS.get(normalize_name(unit.name)) if isinstance(unit, (exp.Column, exp.Var)) else None
        base = _shifted(value)
        days = _day_count(amount, unit_days) if unit_days is not None else None
        if base is None or days is None:
            return None
        return base[0], base[1] + days, base[2]
    return None


def _is_observation_period(scope: exp.Select, src: Optional[exp.Expression]) -> bool:
    return (
        isinstance(src, exp.Table)
        and _source_query(scope, src) is None
        and normalize_name(src.name) == OBSERVATION_PERIOD_TABLE
    )


def _operand(side: exp.Expression, select: exp.Select):
    """("period", PeriodKey, bound name, offset, column) or ("value", ValueKey, None, offset, column); None when the
    side is not one column plus a day offset, or its source cannot be settled."""
    parsed = _shifted(side)
    if parsed is None:
        return None
    col, offset, cast = parsed
    name = normalize_name(col.name)
    # A period bound is identified by lineage: the observation_period row it is read from, through CTEs, derived
    # tables and renames. A projection that aggregates or computes it is not a bound of one row.
    found = terminals(col)
    if found is not None and len(found) == 1 and found[0].expr is None and found[0].table == OBSERVATION_PERIOD_TABLE:
        terminal = found[0]
        if terminal.column not in (OP_START, OP_END) or cast:
            return None
        return "period", (id(terminal.select), terminal.alias), terminal.column, offset, col
    if col.table:
        qualifier = normalize_name(col.table)
        scope, src = _source_by_qualifier(select, qualifier)
    else:
        scope, src = select, _pick_source(select, _select_sources(select), name, 0)
        qualifier = normalize_name(src.alias_or_name) if src is not None else ""
    if scope is not None and _is_observation_period(scope, src):
        if name not in (OP_START, OP_END):
            return None
        return "period", (id(scope), qualifier), name, offset, col
    if name in (OP_START, OP_END):
        # A bound read through a CTE or derived table may pair the start and end of different rows.
        return None
    return "value", (id(scope) if scope is not None else id(select), qualifier, name, cast), None, offset, col


def _conjuncts(node: exp.Expression) -> List[exp.Expression]:
    """AND-ed leaves of a predicate; an OR / NOT subtree stays one leaf and is never looked into."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node]


# (operator with the event value on the left) per comparison type: value OP bound.
_VALUE_LEFT = {exp.LT: "<", exp.LTE: "<=", exp.GT: ">", exp.GTE: ">=", exp.EQ: "="}
_FLIPPED = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "="}


class _Bound:
    """``value - bound <= limit`` (upper) or ``value - bound >= limit`` (lower), in days."""

    def __init__(self, limit: int, strict: bool, equality: bool, node: exp.Expression, value: exp.Column):
        self.limit = limit
        self.strict = strict
        self.equality = equality
        self.node = node
        self.value = value


def _difference(node: exp.Expression) -> Optional[Tuple[exp.Expression, exp.Expression]]:
    """(b, a) when ``node`` is the day difference b - a: ``b - a`` of two dates or ``DATEDIFF(day, a, b)``."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.DateDiff):
        unit = node.args.get("unit")
        if unit is None or normalize_name(unit.name) in ("day", "days", "dd", "d"):
            return node.this, node.expression
        return None
    if isinstance(node, exp.Sub) and _day_count(node.expression) is None:
        return node.this, node.expression
    return None


def _atoms(leaf: exp.Expression) -> List[Tuple[exp.Expression, str, exp.Expression]]:
    """(left, operator, right) comparisons a conjunct states; BETWEEN is its two bounds, and a day difference
    compared with a constant, ``b - a OP n``, is ``b OP a + n``."""
    if isinstance(leaf, exp.Between):
        low, high = leaf.args.get("low"), leaf.args.get("high")
        if low is None or high is None or leaf.args.get("symmetric"):
            return []
        return [(leaf.this, ">=", low), (leaf.this, "<=", high)]
    op = _VALUE_LEFT.get(type(leaf))
    if not op:
        return []
    for diff, number, operator in ((leaf.this, leaf.expression, op), (leaf.expression, leaf.this, _FLIPPED[op])):
        pair = _difference(diff)
        days = _day_count(number)
        if pair is not None and days is not None and not isinstance(number, exp.Interval):
            later, earlier = pair
            return [(later, operator, exp.Add(this=earlier.copy(), expression=exp.Literal.number(days)))]
    return [(leaf.this, op, leaf.expression)]


def _find_reversed(select: exp.Select) -> List[Tuple[str, _Bound, _Bound, str]]:
    """(verdict, upper bound by the period start, lower bound by the period end, period alias) per reversed pair."""
    uppers: Dict[Tuple[PeriodKey, ValueKey], List[_Bound]] = {}
    lowers: Dict[Tuple[PeriodKey, ValueKey], List[_Bound]] = {}

    for predicate in scope_predicates(select):
        for leaf in _conjuncts(predicate):
            for left, op, right in _atoms(leaf):
                a, b = _operand(left, select), _operand(right, select)
                if a is None or b is None or {a[0], b[0]} != {"period", "value"}:
                    continue
                if a[0] == "period":
                    a, b, op = b, a, _FLIPPED[op]
                _, value_key, _, value_offset, value_col = a
                _, period_key, bound_name, bound_offset, _ = b
                limit = bound_offset - value_offset
                key = (period_key, value_key)
                strict, equality = op in ("<", ">"), op == "="
                if bound_name == OP_START and op in ("<", "<=", "="):
                    uppers.setdefault(key, []).append(_Bound(limit, strict, equality, leaf, value_col))
                if bound_name == OP_END and op in (">", ">=", "="):
                    lowers.setdefault(key, []).append(_Bound(limit, strict, equality, leaf, value_col))

    found = []
    for key, upper_bounds in uppers.items():
        best = None
        for upper in upper_bounds:
            for lower in lowers.get(key, []):
                # start + upper.limit >= value >= end + lower.limit, and start <= end.
                gap = lower.limit - upper.limit
                if gap > 0 or (gap == 0 and (upper.strict or lower.strict)):
                    best = ("never", upper, lower)
                    break
                # Two equalities pin a one-day period on purpose; an inequality pair that only allows it is reversed.
                if gap == 0 and not (upper.equality and lower.equality) and best is None:
                    best = ("single_day", upper, lower)
            if best is not None and best[0] == "never":
                break
        if best is not None:
            found.append((*best, key[0][1]))
    return found


@register
class ObservationPeriodDateRangeLogicRule(Rule):
    """Reversed observation_period bounds: a date held on or before the period start and on or after its end."""

    rule_id = "temporal.observation_period_date_range_logic"
    name = "Observation Period Date Range Logic"
    description = (
        "Detects reversed observation_period bounds: the same date is required to be on or before "
        "observation_period_start_date and on or after observation_period_end_date. A period never starts after it "
        "ends, so the condition matches nothing, or only one-day periods."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the reversed bounds WITH `<event_date> BETWEEN op.observation_period_start_date AND "
        "op.observation_period_end_date` (the same as `op.observation_period_start_date <= <event_date> AND "
        "<event_date> <= op.observation_period_end_date`). To find events outside the period, join the two tests "
        "with OR: `<event_date> < op.observation_period_start_date OR <event_date> > op.observation_period_end_date`."
    )
    long_description = (
        "A date lies inside an observation period when observation_period_start_date <= date AND date <= "
        "observation_period_end_date. Swapping the BETWEEN bounds, flipping both comparison operators, or joining "
        "the two 'outside the period' tests with AND instead of OR yields the reverse: the date must be on or "
        "before the period start and on or after the period end. Because a period never starts after it ends, "
        "that holds for no row when either operator is strict, and only for one-day periods otherwise. The rule "
        "reports only this certain contradiction, on AND-ed predicates of one SELECT that compare the same column "
        "with both bounds of the same observation_period row; constant day offsets are taken into account. "
        "One-sided tests such as a washout, tests under OR or NOT, and interval tests that compare the bounds "
        "with two different columns are not flagged."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN observation_period op ON co.person_id = op.person_id\n"
        "WHERE co.condition_start_date\n"
        "      BETWEEN op.observation_period_end_date AND op.observation_period_start_date;"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN observation_period op ON co.person_id = op.person_id\n"
        "WHERE co.condition_start_date\n"
        "      BETWEEN op.observation_period_start_date AND op.observation_period_end_date;"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT de.person_id\n"
                "FROM drug_exposure de\n"
                "JOIN observation_period op ON op.person_id = de.person_id\n"
                " AND op.observation_period_start_date >= de.drug_exposure_start_date\n"
                " AND op.observation_period_end_date <= de.drug_exposure_start_date;"
            ),
            severity=Severity.ERROR,
            note="Both comparison operators flipped: holds only when the period is a single day",
        ),
        RuleExample(
            sql=(
                "SELECT m.measurement_id\n"
                "FROM measurement m\n"
                "JOIN observation_period op ON op.person_id = m.person_id\n"
                "WHERE m.measurement_date < op.observation_period_start_date\n"
                "  AND m.measurement_date > op.observation_period_end_date;"
            ),
            severity=Severity.ERROR,
            note="'Outside the period' tests joined with AND instead of OR: can never hold",
        ),
    )
    references = (
        cdm_table("observation_period"),
        section(BOOK_COHORTS, "require-365-days-of-prior-observation", "Require 365 days of prior observation"),
        DQD_FIELD_THRESHOLDS,
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if OBSERVATION_PERIOD_TABLE not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            for select in tree.find_all(exp.Select):
                for verdict, upper, lower, alias in _find_reversed(select):
                    value_sql = upper.value.sql()
                    if upper.node is lower.node:
                        predicates = f"`{upper.node.sql()}` bounds"
                    else:
                        predicates = f"`{upper.node.sql()}` and `{lower.node.sql()}` bound"
                    outcome = (
                        "the two can never both be true"
                        if verdict == "never"
                        else "this holds only when the observation period is a single day"
                    )
                    violations.append(
                        self.create_violation(
                            message=(
                                f"Reversed observation_period bounds: {predicates} {value_sql} from above by "
                                f"{OP_START} and from below by {OP_END}. A period never starts after it ends, so "
                                f"{outcome}. A date inside the period satisfies `{alias}.{OP_START} <= {value_sql} "
                                f"AND {value_sql} <= {alias}.{OP_END}`."
                            ),
                            details={
                                "issue": "reversed_period_bounds",
                                "satisfiable": "never" if verdict == "never" else "single_day_period",
                                "period_alias": alias,
                                "value": value_sql,
                                "predicates": sorted({upper.node.sql(), lower.node.sql()}),
                            },
                        )
                    )
        return violations


__all__ = ["ObservationPeriodDateRangeLogicRule"]

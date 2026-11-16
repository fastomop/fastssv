"""Datetime Column With a Date-Only Inclusive Upper Bound.

``a BETWEEN x AND y`` is ``a >= x AND a <= y`` (PostgreSQL comparison predicates), and when a date is compared with a
timestamp, PostgreSQL takes the date as midnight of that day. So an inclusive upper bound written as a date on a
timestamp column stops at 00:00:00 of the last day:

    WHERE measurement_datetime BETWEEN '2023-01-01' AND '2023-12-31'
    WHERE measurement_datetime >= '2023-01-01' AND measurement_datetime <= '2023-12-31'
    -- both drop measurements taken on 2023-12-31 after midnight

Whether rows are lost depends on the data: the CDM conventions set an unknown time to 00:00:00, so a source without
times loses nothing. The finding is therefore a WARNING.

The bounded value must be a TIMESTAMP by lineage: a CDM ``*_datetime`` column (typed by the CDM v5.4 field-level
specification) followed through CTEs, derived tables and renames, optionally under MIN / MAX or a cast to
TIMESTAMP. A value truncated to a day (``CAST(x AS DATE)``, ``x::date``, ``DATE(x)``, ``CONVERT(date, x)``,
``DATE_TRUNC('day', x)``) or turned into text (``TO_CHAR``) loses nothing and is not reported. The bound is
date-only when it is a date literal without a time of day: ``'2023-12-31'``, ``'20231231'``, ``DATE '...'``,
``CAST('...' AS DATE)``, ``TIMESTAMP '2023-12-31'``, ``TO_DATE(...)``, ``MAKE_DATE(...)``.
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import CDM_CONVENTIONS, section
from fastssv.core.registry import register
from fastssv.rules.temporal._typing import TIMESTAMP, literal_datetime, sql_type, unwrap


def _timestamp_column(node: Optional[exp.Expression]) -> Optional[exp.Column]:
    """The column when ``node`` is a TIMESTAMP-typed column, MIN / MAX of one, or one cast to TIMESTAMP."""
    node = unwrap(node)
    if isinstance(node, (exp.Min, exp.Max)):
        node = unwrap(node.this)
    if isinstance(node, (exp.Cast, exp.TryCast)) and sql_type(node) == TIMESTAMP:
        node = unwrap(node.this)
    if isinstance(node, exp.Column) and sql_type(node) == TIMESTAMP:
        return node
    return None


def _date_only(node: Optional[exp.Expression]) -> Optional[str]:
    value = literal_datetime(node)
    if value is None or not value[1]:
        return None
    return value[0].date().isoformat()


def _upper_bounds(tree: exp.Expression) -> List[Tuple[exp.Expression, exp.Column, str]]:
    """(predicate, timestamp column, date) for every inclusive upper bound given as a date-only literal."""
    found = []
    for node in tree.find_all(exp.Between, exp.LTE, exp.GTE):
        if isinstance(node, exp.Between):
            if node.args.get("symmetric"):
                continue
            col, bound = _timestamp_column(node.this), node.args.get("high")
        elif isinstance(node, exp.LTE):
            col, bound = _timestamp_column(node.this), node.expression
        else:
            col, bound = _timestamp_column(node.expression), node.this
        day = _date_only(bound) if col is not None else None
        if day is not None:
            found.append((node, col, day))
    return found


@register
class DatetimeBetweenDateLiteralRule(Rule):
    """Inclusive upper bound written as a date on a timestamp column."""

    rule_id = "temporal.datetime_between_date_literal"
    name = "Datetime Column With a Date-Only Inclusive Upper Bound"
    description = (
        "A timestamp column bounded inclusively above by a date without a time (BETWEEN ... AND '2023-12-31', "
        "<= '2023-12-31') stops at midnight of that day, dropping later records from the last day."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: `<datetime_col> BETWEEN '<start>' AND '<end>'` WITH `<datetime_col> >= '<start>' AND "
        "<datetime_col> < '<end_plus_one_day>'` (half-open), OR compare the day: `CAST(<datetime_col> AS DATE) "
        "BETWEEN '<start>' AND '<end>'`."
    )
    long_description = (
        "BETWEEN includes both ends, and PostgreSQL compares a date with a timestamp as midnight of that day. "
        "measurement_datetime BETWEEN '2023-01-01' AND '2023-12-31' (or <= '2023-12-31') therefore keeps "
        "2023-12-31 00:00:00 but drops every later time on 2023-12-31. The same holds for any inclusive upper bound "
        "written as a date: <=, a literal on the left with >=, TO_DATE, MAKE_DATE, CAST(... AS DATE). The bounded "
        "value is typed by lineage (a CDM *_datetime column through CTEs and renames), so a value already cast to "
        "DATE, truncated to the day or formatted as text is not reported. The finding is a WARNING: the CDM "
        "conventions set unknown times to 00:00:00, so a source without times loses nothing. Use a half-open "
        "interval (>= start AND < the day after the end) or compare CAST(column AS DATE)."
    )
    example_bad = (
        "SELECT *\nFROM measurement\nWHERE measurement_datetime BETWEEN DATE '2023-01-01' AND DATE '2023-12-31';"
    )
    example_good = (
        "SELECT *\n"
        "FROM measurement\n"
        "WHERE measurement_datetime >= TIMESTAMP '2023-01-01 00:00:00'\n"
        "  AND measurement_datetime <  TIMESTAMP '2024-01-01 00:00:00';"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT *\nFROM measurement\n"
                "WHERE measurement_datetime >= '2023-01-01'\n  AND measurement_datetime <= '2023-12-31';"
            ),
            severity=Severity.WARNING,
            note="The same range written with >= and <=",
        ),
    )
    references = (
        Reference(
            "PostgreSQL documentation: Comparison Functions and Operators, comparison predicates (BETWEEN)",
            "https://www.postgresql.org/docs/current/functions-comparison.html#FUNCTIONS-COMPARISON-PRED-TABLE",
        ),
        Reference(
            "PostgreSQL documentation: Date/Time Functions and Operators",
            "https://www.postgresql.org/docs/current/functions-datetime.html",
        ),
        section(CDM_CONVENTIONS, "time-span-of-available-data", "Time span of available data"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees:
            if not tree:
                continue
            for predicate, col, day in _upper_bounds(tree):
                key = predicate.sql()
                if key in seen:
                    continue
                seen.add(key)
                name = col.sql()
                details = {"column": name, "end": day, "issue": "datetime_inclusive_date_upper_bound"}
                if isinstance(predicate, exp.Between):
                    low = literal_datetime(predicate.args.get("low"))
                    if low is not None:
                        details["start"] = low[0].date().isoformat() if low[1] else low[0].isoformat(sep=" ")
                violations.append(
                    self.create_violation(
                        message=(
                            f"`{predicate.sql()[:120]}` bounds the timestamp `{name}` inclusively by "
                            f"the date {day}, which compares as {day} 00:00:00: records later on {day} are "
                            "excluded. If the data carries times of day, use a half-open bound or compare the day."
                        ),
                        suggested_fix=(
                            f"REPLACE: the upper bound WITH `{name} < '<{day} plus one day>'`, OR compare "
                            f"`CAST({name} AS DATE)` with the date."
                        ),
                        details=details,
                    )
                )
        return violations


__all__ = ["DatetimeBetweenDateLiteralRule"]

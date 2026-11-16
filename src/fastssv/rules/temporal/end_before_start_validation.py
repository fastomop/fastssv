"""End Before Start Validation Rule.

Judges constant date bounds on the start and end columns that the OHDSI Data Quality Dashboard pairs in its
``plausibleStartBeforeEnd`` check (``visit_start_date`` / ``visit_end_date``, ``drug_exposure_start_date`` /
``drug_exposure_end_date``, ``procedure_date`` / ``procedure_end_date``, the ``*_datetime`` pairs, the era and
period tables, ...).

Two findings, both read from the AND-ed predicates of one SELECT about one row source:

* ERROR, bounds on ONE column that no value satisfies::

      WHERE visit_start_date >= '2023-06-01' AND visit_start_date < '2023-01-01'
      WHERE visit_start_date BETWEEN '2023-12-31' AND '2023-01-01'

  The predicate is false for every row, whatever the data holds.

* WARNING, bounds on a start column and its end column that only a record ending before it starts can satisfy::

      WHERE drug_exposure_start_date >= '2023-06-01' AND drug_exposure_end_date < '2023-06-01'

  The CDM specification does not state that an end date follows its start date; the Data Quality Dashboard treats
  the reverse as implausible and tolerates a share of such rows, so the result depends on the data. On data that
  passes the check without violations the query returns nothing.

Not reported:

* a comparison of the two columns of one row (``visit_end_date < visit_start_date``). That is the plausibility check
  itself: it selects exactly the records the Data Quality Dashboard counts, in any projection.
  ``temporal.nullable_date_column`` treats it the same way;
* comparisons between two row sources (``v2.visit_start_date > v1.visit_end_date``, the readmission or treatment-gap
  join), and bounds that belong to different row sources or different SELECTs;
* anything under OR or NOT, and bounds that are not constants.

An untyped string literal compared with a DATE column is read as a date, as PostgreSQL and DuckDB coerce it, so
``visit_start_date >= '2023-06-01 10:00'`` bounds the column at 2023-06-01. A ``TIMESTAMP`` literal, a cast of the
column to TIMESTAMP and a datetime column keep the time of day.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import column_owner, normalize_name, parse_sql, scope_predicates
from fastssv.core.references import DQD_FIELD_THRESHOLDS
from fastssv.core.registry import register
from fastssv.rules.temporal._cdm_dates import START_BEFORE_END_PAIRS
from fastssv.rules.temporal._typing import DATE, base_column, conjuncts, literal_datetime, sql_type, unwrap

# (table, column) -> the (start, end) pairs it takes part in.
_PAIRS: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
for _table, _start, _end in START_BEFORE_END_PAIRS:
    _PAIRS.setdefault((_table, _start), []).append((_start, _end))
    _PAIRS.setdefault((_table, _end), []).append((_start, _end))

_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "=": "="}
_OPERATOR = {exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<=", exp.EQ: "="}

# (id of the SELECT, row-source alias, table, column)
BoundKey = Tuple[int, str, str, str]


class _Bounds:
    """Constant range a column is held to: ``low`` (exclusive when ``low_strict``) to ``high``."""

    def __init__(self) -> None:
        self.low: Optional[datetime] = None
        self.low_strict = False
        self.low_sql = ""
        self.high: Optional[datetime] = None
        self.high_strict = False
        self.high_sql = ""

    def add_low(self, value: datetime, strict: bool, sql: str) -> None:
        if self.low is None or value > self.low or (value == self.low and strict and not self.low_strict):
            self.low, self.low_strict, self.low_sql = value, strict, sql

    def add_high(self, value: datetime, strict: bool, sql: str) -> None:
        if self.high is None or value < self.high or (value == self.high and strict and not self.high_strict):
            self.high, self.high_strict, self.high_sql = value, strict, sql

    def empty(self) -> bool:
        if self.low is None or self.high is None:
            return False
        return self.low > self.high or (self.low == self.high and (self.low_strict or self.high_strict))


def _date_column(node: Optional[exp.Expression]) -> Optional[exp.Column]:
    """The column a bound applies to. A cast of a DATE column to DATE or TIMESTAMP keeps its value; any other
    wrapper changes it, so the bound is not read."""
    node = unwrap(node)
    if isinstance(node, (exp.Cast, exp.TryCast)):
        inner = unwrap(node.this)
        if isinstance(inner, exp.Column) and sql_type(inner) == DATE and sql_type(node) is not None:
            return inner
        return None
    return node if isinstance(node, exp.Column) else None


def _constant_bounds(select: exp.Select) -> Dict[BoundKey, _Bounds]:
    bounds: Dict[BoundKey, _Bounds] = {}

    def record(col_node: Optional[exp.Expression], operator: str, value_node: Optional[exp.Expression], sql: str):
        col = _date_column(col_node)
        value = literal_datetime(value_node)
        if col is None or value is None:
            return
        origin = base_column(col)
        if origin is None or origin not in _PAIRS:
            return
        owner = normalize_name(col.table) if col.table else column_owner(col, select)
        if not owner:
            return
        entry = bounds.setdefault((id(select), owner, origin[0], origin[1]), _Bounds())
        when = value[0]
        literal = unwrap(value_node)
        if sql_type(col_node) == DATE and isinstance(literal, exp.Literal) and literal.is_string:
            # An untyped string compared with a DATE is read as a DATE (PostgreSQL, DuckDB), dropping any time.
            when = when.replace(hour=0, minute=0, second=0, microsecond=0)
        if operator in (">", ">=", "="):
            entry.add_low(when, operator == ">", sql)
        if operator in ("<", "<=", "="):
            entry.add_high(when, operator == "<", sql)

    for predicate in scope_predicates(select):
        for leaf in conjuncts(predicate):
            if isinstance(leaf, exp.Between):
                if leaf.args.get("symmetric"):
                    continue
                record(leaf.this, ">=", leaf.args.get("low"), leaf.sql())
                record(leaf.this, "<=", leaf.args.get("high"), leaf.sql())
                continue
            operator = _OPERATOR.get(type(leaf))
            if operator is None:
                continue
            record(leaf.this, operator, leaf.expression, leaf.sql())
            record(leaf.expression, _FLIP[operator], leaf.this, leaf.sql())
    return bounds


@register
class EndBeforeStartValidationRule(Rule):
    rule_id = "temporal.end_before_start_validation"
    name = "End Before Start Validation"
    description = (
        "Detects constant date bounds on a start/end column pair that only a record ending before it starts can "
        "satisfy (WARNING), and bounds on one such column that no value satisfies (ERROR)."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the two bounds WITH an overlap test, `<start_col> <= '<window_end>' AND <end_col> >= "
        "'<window_start>'`. To list records whose end precedes their start as a data-quality check, compare the two "
        "columns directly: `<end_col> < <start_col>`."
    )
    long_description = (
        "The OHDSI Data Quality Dashboard pairs each start date with its end date (plausibleStartBeforeEnd) and "
        "counts the records whose start is after their end. A filter such as drug_exposure_start_date >= "
        "'2023-06-01' AND drug_exposure_end_date < '2023-06-01' admits only those records: the usual cause is a "
        "window test with one operator the wrong way round. The finding is a WARNING because the CDM specification "
        "does not rule such records out and the dashboard tolerates a share of them, so the result depends on the "
        "data. Bounds on a single column that cannot both hold (start >= '2023-06-01' AND start < '2023-01-01', or "
        "BETWEEN with the larger value first) are false for every row and are an ERROR. Bounds are read from the "
        "AND-ed predicates of one SELECT, per row source, with the literal on either side. Comparing the two "
        "columns of a row with each other is the plausibility check itself and is not reported; neither are "
        "comparisons between two rows, such as a readmission that starts after the previous visit ended."
    )
    example_bad = (
        "SELECT drug_exposure_id\n"
        "FROM drug_exposure\n"
        "WHERE drug_exposure_start_date >= DATE '2023-06-01'\n"
        "  AND drug_exposure_end_date < DATE '2023-06-01';"
    )
    example_good = (
        "SELECT drug_exposure_id\n"
        "FROM drug_exposure\n"
        "WHERE drug_exposure_start_date <= DATE '2023-06-30'\n"
        "  AND drug_exposure_end_date >= DATE '2023-06-01';"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT visit_occurrence_id\n"
                "FROM visit_occurrence\n"
                "WHERE visit_start_date >= DATE '2023-06-01'\n"
                "  AND visit_start_date < DATE '2023-01-01';"
            ),
            severity=Severity.ERROR,
            note="Bounds on one column that no date satisfies: the query returns no rows on any data",
        ),
    )
    references = (DQD_FIELD_THRESHOLDS,)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            for select in tree.find_all(exp.Select):
                bounds = _constant_bounds(select)
                for (_, owner, table, column), entry in bounds.items():
                    if entry.empty():
                        violations.append(
                            self.create_violation(
                                severity=Severity.ERROR,
                                message=(
                                    f"{table}.{column} is bounded by `{entry.low_sql}` and `{entry.high_sql}`; no "
                                    "date satisfies both, so the filter is false for every row and the query "
                                    "returns nothing."
                                ),
                                suggested_fix=(
                                    f"REPLACE: one of the two bounds on `{column}` so that the lower bound does "
                                    "not exceed the upper bound."
                                ),
                                details={"table": table, "column": column, "issue": "empty_range"},
                            )
                        )
                reported = set()
                for (scope, owner, table, column), entry in bounds.items():
                    for start, end in _PAIRS[(table, column)]:
                        if column != start or (scope, owner, start, end) in reported:
                            continue
                        other = bounds.get((scope, owner, table, end))
                        if other is None or entry.low is None or other.high is None:
                            continue
                        if entry.empty() or other.empty():
                            continue
                        forced = other.high < entry.low or (
                            other.high == entry.low and (entry.low_strict or other.high_strict)
                        )
                        if not forced:
                            continue
                        reported.add((scope, owner, start, end))
                        violations.append(
                            self.create_violation(
                                severity=Severity.WARNING,
                                message=(
                                    f"`{entry.low_sql}` and `{other.high_sql}` admit only {table} records whose "
                                    f"{end} precedes their {start}. The OHDSI Data Quality Dashboard counts such "
                                    "records as implausible, so on data without them this returns nothing. If the "
                                    "aim is a window test, one bound is the wrong way round."
                                ),
                                suggested_fix=(
                                    f"REPLACE: the two bounds WITH an overlap test, `{start} <= '<window_end>' AND "
                                    f"{end} >= '<window_start>'`. For a data-quality check, compare the columns "
                                    f"directly: `{end} < {start}`."
                                ),
                                details={
                                    "table": table,
                                    "start_column": start,
                                    "end_column": end,
                                    "issue": "end_before_start_bounds",
                                },
                            )
                        )
        return violations


__all__ = ["EndBeforeStartValidationRule"]

"""Implausible Date Filter Rule.

Flags a predicate on a CDM date whose admitted range lies entirely outside the dates the OHDSI Data Quality
Dashboard (DQD) accepts for that column. The columns and their bounds are generated from the DQD field-level file:
every date column with ``plausibleValueHigh = DATEADD(dd,1,GETDATE())``, with its ``plausibleValueLow`` (1950-01-01
for clinical events, 1850-01-01 for birth_datetime).

* on or after the current date, for start / point-in-time dates only: ``<date> > CURRENT_DATE``,
  ``<date> >= CURRENT_DATE`` (today or later), ``<date> >= CURRENT_DATE + INTERVAL '30 days'``,
  ``CURRENT_DATE < <date>``, ``<date> BETWEEN CURRENT_DATE AND ...``, ``<date> - CURRENT_DATE > 0``,
  ``DATEDIFF(day, GETDATE(), <date>) > 0`` or ``EXTRACT(YEAR FROM <date>) > EXTRACT(YEAR FROM CURRENT_DATE)``. The
  CDM conventions add that no record can follow the last observation period, which ends at the extract. End dates
  (the columns the DQD requires to follow another date of the same row) are exempt: they are often derived (days
  supply, era logic) and legitimately extend past the extract, as in the "currently exposed" idiom
  ``start <= CURRENT_DATE AND end >= CURRENT_DATE``;
* before the column's DQD floor, for any listed date: ``<date> < '1950-01-01'``, ``<date> <= DATE '1949-12-31'``,
  ``EXTRACT(YEAR FROM <date>) < 1950``, ``BETWEEN`` / ``IN`` / ``=`` with only earlier values.

A comparison against ``CURRENT_DATE - INTERVAL ...`` is a look-back window and is not flagged.
Far-future calendar literals are deliberately not flagged: MIMIC-IV shifts its dates into 2100-2200, so
``condition_start_date >= '2150-01-01'`` can be a correct query there. For the same reason, and because the DQD
tolerates a share of out-of-range values, the rule stays a warning.

Predicates inside ``OR`` or ``NOT`` are skipped: that is the shape of a data-quality audit that
searches for implausible dates on purpose.
"""

import re
from datetime import date
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import CDM_CONVENTIONS, DQD_FIELD_THRESHOLDS, section
from fastssv.core.helpers import has_table_reference, normalize_name, parse_sql, resolve_table_col, extract_aliases
from fastssv.core.registry import register
from fastssv.rules.temporal._cdm_dates import AFTER_SAME_ROW, PLAUSIBLE_DATE_FLOOR, START_BEFORE_END_PAIRS
from fastssv.rules.temporal._typing import base_column
from fastssv.rules.temporal._typing import is_now as _typed_is_now


# Date columns the Data Quality Dashboard bounds by plausibleValueLow / plausibleValueHigh (high = the day after the
# run), mapped to their low bound; generated from the DQD field-level CSV (see _cdm_dates.py). cdm_source describes the
# CDM release, not events, and is left out.
FLOOR_BY_COLUMN: Dict[Tuple[str, str], date] = {
    (table, column): date.fromisoformat(low)
    for table, columns in PLAUSIBLE_DATE_FLOOR.items()
    if table != "cdm_source"
    for column, low in columns.items()
}
DATE_COLUMNS_BY_TABLE: Dict[str, Set[str]] = {}
for _table, _column in FLOOR_BY_COLUMN:
    DATE_COLUMNS_BY_TABLE.setdefault(_table, set()).add(_column)

_TABLE_OF_COLUMN = {col: table for table, cols in DATE_COLUMNS_BY_TABLE.items() for col in cols}

_NOW_FUNCS = {"now", "getdate", "sysdate", "sysdatetime", "current_date", "current_timestamp", "today"}
_WRAPPERS = (exp.Cast, exp.TryCast, exp.Paren, exp.TsOrDsToDate, exp.TimeStrToTime, exp.TimeStrToDate)
_DATE_RE = re.compile(r"^\s*(\d{4})-?(\d{2})-?(\d{2})")
_SIGNED_NUMBER_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)")


def _peel(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, _WRAPPERS):
        node = node.this
    return node


def _year_extract_arg(node: exp.Expression) -> Optional[exp.Expression]:
    """Argument of EXTRACT(YEAR FROM x) / YEAR(x) / DATE_PART('year', x), else None."""
    node = _peel(node)
    if isinstance(node, exp.Extract) and normalize_name(node.this.name) == "year":
        return node.expression
    if isinstance(node, exp.Year):
        return node.this
    if isinstance(node, exp.Anonymous) and normalize_name(node.name) == "date_part" and len(node.expressions) == 2:
        part = node.expressions[0]
        if isinstance(part, exp.Literal) and normalize_name(part.this) == "year":
            return node.expressions[1]
    return None


def _date_column(node: exp.Expression, aliases) -> Optional[Tuple[str, str]]:
    node = _peel(node)
    # MIN / MAX keep a date's range, and truncation to a day / month / year start keeps it on the same side of a
    # current-date or year-start bound.
    while isinstance(node, (exp.Min, exp.Max, exp.TimestampTrunc, exp.DateTrunc)):
        node = _peel(node.this)
    if not isinstance(node, exp.Column):
        return None
    origin = base_column(node)
    if origin is not None:
        return origin if origin in FLOOR_BY_COLUMN else None
    table, col = resolve_table_col(node, aliases)
    owner = _TABLE_OF_COLUMN.get(col)
    if owner is None:
        return None
    # A qualifier that names a different OMOP table means the name is coincidental; CTE/derived aliases
    # carrying the OMOP column name keep its meaning.
    if table and table in DATE_COLUMNS_BY_TABLE and table != owner:
        return None
    return owner, col


def _is_now(node: exp.Expression) -> bool:
    node = _peel(node)
    if _typed_is_now(node):
        return True
    return isinstance(node, exp.Anonymous) and normalize_name(node.name) in _NOW_FUNCS


def _offset_sign(node: exp.Expression) -> Optional[int]:
    """Sign of a constant offset (INTERVAL '30 days', 30, INTERVAL '-3' DAY); None when not constant."""
    node = _peel(node)
    if isinstance(node, exp.Interval):
        node = node.this
    if isinstance(node, exp.Neg):
        inner = _offset_sign(node.this)
        return None if inner is None else -inner
    if isinstance(node, exp.Literal):
        m = _SIGNED_NUMBER_RE.match(str(node.this))
        if m:
            v = float(m.group(1))
            return (v > 0) - (v < 0)
    return None


def _now_offset(node: exp.Expression) -> Optional[int]:
    """For an expression equal to now + c, the sign of c (0 for bare now); None if not of that form."""
    node = _peel(node)
    if _is_now(node):
        return 0
    if isinstance(node, (exp.Add, exp.Sub)):
        if _is_now(node.this):
            sign = _offset_sign(node.expression)
            if sign is None:
                return None
            return sign if isinstance(node, exp.Add) else -sign
        if isinstance(node, exp.Add) and _is_now(node.expression):
            return _offset_sign(node.this)
        return None
    if isinstance(node, (exp.DateAdd, exp.DateSub, exp.TsOrDsAdd)) and _is_now(node.this):
        sign = _offset_sign(node.expression)
        if sign is None:
            return None
        return -sign if isinstance(node, exp.DateSub) else sign
    return None


def _is_current_year(node: exp.Expression) -> bool:
    arg = _year_extract_arg(node)
    return arg is not None and _now_offset(arg) == 0


def _literal_date(node: Optional[exp.Expression]) -> Optional[date]:
    """A constant calendar date: '1899-12-31', DATE '...', CAST('...' AS DATE), TO_DATE('18991231', ...),
    DATEFROMPARTS(1899, 12, 31) / MAKE_DATE(1899, 12, 31)."""
    node = _peel(node)
    if node is None:
        return None
    if isinstance(node, exp.StrToDate):
        node = _peel(node.this)
    if isinstance(node, exp.Literal) and node.is_string:
        m = _DATE_RE.match(node.this)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        return None
    parts: List[Optional[exp.Expression]] = []
    if isinstance(node, exp.DateFromParts):
        parts = [node.args.get("year"), node.args.get("month"), node.args.get("day")]
    elif isinstance(node, exp.Anonymous) and normalize_name(node.name) in {
        "make_date",
        "datefromparts",
        "date_from_parts",
    }:
        parts = list(node.expressions)
    if len(parts) == 3 and all(isinstance(p, exp.Literal) and not p.is_string for p in parts):
        try:
            return date(*(int(p.this) for p in parts))
        except ValueError:
            return None
    return None


def _literal_int(node: Optional[exp.Expression]) -> Optional[int]:
    node = _peel(node)
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return int(node.this)
        except ValueError:
            return None
    return None


def _guarded(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None and not isinstance(parent, (exp.Where, exp.Join, exp.Having, exp.Select)):
        if isinstance(parent, (exp.Or, exp.Not)):
            return True
        parent = parent.parent
    return not isinstance(parent, (exp.Where, exp.Join, exp.Having))


# Operators normalised so the date operand is on the left: `x OP date` becomes `date FLIP[OP] x`.
_FLIP = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE, exp.EQ: exp.EQ}


def _subject(node: exp.Expression, aliases) -> Optional[Tuple[str, str, bool]]:
    """(table, column, is_year_extract) when ``node`` is an event date or its year."""
    info = _date_column(node, aliases)
    if info:
        return info[0], info[1], False
    arg = _year_extract_arg(node)
    if arg is not None:
        info = _date_column(arg, aliases)
        if info:
            return info[0], info[1], True
    return None


def _before_floor(value: exp.Expression, floor: date, is_year: bool, inclusive: bool) -> bool:
    """True when every date on the low side of ``value`` precedes ``floor``."""
    if is_year:
        y = _literal_int(value)
        return y is not None and (y < floor.year if inclusive else y <= floor.year)
    d = _literal_date(value)
    return d is not None and (d < floor if inclusive else d <= floor)


# End dates are often derived (days supply, era logic) and legitimately lie after the extract date, as in the
# "currently exposed" idiom `start <= CURRENT_DATE AND end >= CURRENT_DATE`. They are the columns the Data Quality
# Dashboard requires to follow another date of the same row.
_END_COLUMNS = AFTER_SAME_ROW | {(table, end) for table, _start, end in START_BEFORE_END_PAIRS}


def _is_end_column(table: str, col: str) -> bool:
    return (table, col) in _END_COLUMNS


def _difference_from_now(node: exp.Expression, aliases) -> Optional[Tuple[str, str]]:
    """(table, column) when ``node`` is ``<date> - <now>`` or ``DATEDIFF(day, <now>, <date>)``: days after today."""
    node = _peel(node)
    if isinstance(node, exp.Sub) and _is_now(node.expression):
        return _date_column(node.this, aliases)
    if isinstance(node, exp.DateDiff) and _is_now(node.expression):
        unit = node.args.get("unit")
        if unit is None or normalize_name(unit.name) in {"day", "days", "dd", "d"}:
            return _date_column(node.this, aliases)
    return None


Finding = Tuple[str, str, str, Optional[date]]


def _future_kind(op, offset: Optional[int]) -> Optional[str]:
    """For ``<date> OP now + offset`` (offset sign), the kind of future-only filter, or None."""
    if op not in (exp.GT, exp.GTE) or offset is None or offset < 0:
        return None
    return "future_inclusive" if op is exp.GTE and offset == 0 else "future"


def _comparison_finding(node: exp.Expression, aliases) -> Optional[Finding]:
    op = type(node)
    left, right = node.this, node.expression
    for days, other, operator in ((left, right, op), (right, left, _FLIP[op])):
        info = _difference_from_now(days, aliases)
        number = _literal_int(other)
        if info and number is not None and not _is_end_column(*info):
            kind = _future_kind(operator, (number > 0) - (number < 0))
            if kind:
                return info[0], info[1], kind, None
    subj = _subject(left, aliases)
    if subj is None:
        subj = _subject(right, aliases)
        if subj is None:
            return None
        op = _FLIP[op]
        left, right = right, left
    table, col, is_year = subj
    floor = FLOOR_BY_COLUMN[(table, col)]

    if _is_end_column(table, col):
        future = None
    elif is_year:
        # `>= current year` still admits the current year, so only the strict form is implausible.
        future = "future" if op is exp.GT and _is_current_year(right) else None
    else:
        future = _future_kind(op, _now_offset(right) if op in (exp.GT, exp.GTE) else None)
    if future:
        return table, col, future, None
    if op in (exp.LT, exp.LTE, exp.EQ) and _before_floor(right, floor, is_year, inclusive=op is not exp.LT):
        return table, col, "before_floor", floor
    return None


def _between_finding(node: exp.Between, aliases) -> Optional[Finding]:
    subj = _subject(node.this, aliases)
    if subj is None:
        return None
    table, col, is_year = subj
    floor = FLOOR_BY_COLUMN[(table, col)]
    low, high = node.args.get("low"), node.args.get("high")
    if not is_year and not _is_end_column(table, col):
        offset = _now_offset(low)
        if offset is not None and offset >= 0:
            return table, col, "future_inclusive" if offset == 0 else "future", None
    if _before_floor(high, floor, is_year, inclusive=True):
        return table, col, "before_floor", floor
    return None


def _in_finding(node: exp.In, aliases) -> Optional[Finding]:
    subj = _subject(node.this, aliases)
    if subj is None or not node.expressions:
        return None
    table, col, is_year = subj
    floor = FLOOR_BY_COLUMN[(table, col)]
    if all(_before_floor(v, floor, is_year, inclusive=True) for v in node.expressions):
        return table, col, "before_floor", floor
    return None


_FUTURE_TAIL = (
    " Recorded events cannot postdate the data extract, so on a CDM with real calendar dates this selects {what}; "
    "check the comparison direction or anchor the window to the end of the data instead of CURRENT_DATE."
)
_MESSAGES = {
    "future": "{table}.{col} is filtered to dates after the current date (`{sql}`)."
    + _FUTURE_TAIL.format(what="nothing"),
    "future_inclusive": "{table}.{col} is filtered to the current date or later (`{sql}`)."
    + _FUTURE_TAIL.format(what="at most the events dated on the extract day"),
    "before_floor": (
        "{table}.{col} is filtered to dates before {floor} (`{sql}`). The OHDSI Data Quality Dashboard treats "
        "{table}.{col} values before {floor} as implausible, so this selects only records it would report as "
        "data errors."
    ),
}


@register
class ImplausibleDateFilterRule(Rule):
    """Flag CDM date predicates that admit only future dates or dates before the DQD floor."""

    rule_id = "temporal.implausible_date_filter"
    name = "Implausible Date Filter"
    description = (
        "Detects predicates on CDM dates that admit only dates on or after the current date (start and "
        "point-in-time dates) or only dates before the Data Quality Dashboard's plausible floor (1950-01-01 for "
        "events), which select no real records or only ones the dashboard reports as errors."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: `<event_date> > CURRENT_DATE` WITH `<event_date> <= CURRENT_DATE` (or a look-back such as "
        "`>= CURRENT_DATE - INTERVAL '30 days'`) if the direction is reversed; REPLACE a cutoff before the "
        "plausible floor WITH `<event_date> >= '1950-01-01'` if the direction is reversed."
    )
    long_description = (
        "The OHDSI Data Quality Dashboard bounds every CDM event date between its plausibleValueLow (1950-01-01; "
        "1850-01-01 for birth_datetime) and the day after the check runs, and the CDM conventions say no record "
        "can follow the last observation period. A filter whose admitted range lies entirely after the current "
        "date, or entirely before the column's floor, therefore selects nothing, or only records the dashboard "
        "reports as errors. The columns and floors are generated from the dashboard's field-level file, so era, "
        "observation_period, payer_plan_period and cohort dates are covered too. The usual cause is a reversed "
        "comparison operator. Date-shifted research CDMs (for example "
        "MIMIC-IV, whose dates fall in 2100-2200) legitimately hold 'future' dates, so the rule is a warning "
        "and does not flag far-future calendar literals. End dates are exempt from the future check because "
        "they are often derived and can legitimately extend past the extract. Predicates under OR or NOT are "
        "treated as data-quality audits and are not flagged."
    )
    example_bad = "SELECT person_id\nFROM condition_occurrence\nWHERE condition_start_date > CURRENT_DATE;"
    example_good = (
        "SELECT person_id\nFROM condition_occurrence\nWHERE condition_start_date >= CURRENT_DATE - INTERVAL '30 days';"
    )
    extra_examples = (
        RuleExample(
            sql="SELECT person_id\nFROM condition_occurrence\nWHERE condition_start_date < DATE '1950-01-01';",
            severity=Severity.WARNING,
            note="Cutoff before the 1950 floor: admits only dates the Data Quality Dashboard treats as implausible",
        ),
    )
    references = (
        DQD_FIELD_THRESHOLDS,
        section(CDM_CONVENTIONS, "time-span-of-available-data", "Time span of available data"),
        Reference("MIMIC-IV v3.1 on PhysioNet: deidentified dates", "https://physionet.org/content/mimiciv/3.1/"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None or not any(has_table_reference(tree, t) for t in DATE_COLUMNS_BY_TABLE):
                continue
            aliases = extract_aliases(tree)
            seen = set()
            for node in tree.find_all(exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.Between, exp.In):
                if _guarded(node):
                    continue
                if isinstance(node, exp.Between):
                    found = _between_finding(node, aliases)
                elif isinstance(node, exp.In):
                    found = _in_finding(node, aliases)
                else:
                    found = _comparison_finding(node, aliases)
                if found is None:
                    continue
                table, col, kind, floor = found
                key = (kind, node.sql())
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    self.create_violation(
                        message=_MESSAGES[kind].format(table=table, col=col, sql=node.sql(), floor=floor),
                        details={
                            "table": table,
                            "column": col,
                            "kind": kind,
                            "floor": floor.isoformat() if floor else None,
                        },
                    )
                )
        return violations


__all__ = ["ImplausibleDateFilterRule"]

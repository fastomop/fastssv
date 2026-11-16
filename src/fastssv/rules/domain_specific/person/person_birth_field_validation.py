"""Person Birth Field Validation Rule.

A row filter on a person birth field whose admitted values cannot overlap the field's range. The ranges are the
plausibleValueLow / plausibleValueHigh thresholds of the OHDSI Data Quality Dashboard (DQD) for CDM v5.4
(``_ds2_birth_bounds.py``):

* ``month_of_birth`` 1-12 and ``day_of_birth`` 1-31. CDM v5.4 fills them by extracting the month and day of a
  precise date of birth, so no other value is a month or a day: a filter that admits none of them (``= 13``,
  ``> 12``, ``IN (0, 13)``, ``BETWEEN 13 AND 20``) matches rows only in non-conformant data. WARNING: DQD checks
  these ranges with thresholds that tolerate a share of violating rows, so the CDM does not guarantee that none
  exist, and DQD's own check query selects exactly those rows. A month and day that never occur together
  (``month_of_birth = 2 AND day_of_birth = 30``) are reported the same way.
* ``year_of_birth`` from 1850. DQD flags earlier years as implausible, not impossible, so a filter that admits only
  earlier years is a WARNING. DQD's upper bound is the current year plus one; it depends on the day the check runs
  and does not hold on date-shifted databases, so years are never judged against an upper bound.
* A ``BETWEEN`` whose lower bound exceeds its upper bound is empty for any data: ERROR (``BETWEEN SYMMETRIC``
  sorts its bounds and is judged as a range).

``EXTRACT(YEAR | MONTH | DAY FROM birth_datetime)``, ``DATE_PART(...)`` and ``YEAR() / MONTH() / DAY()`` are judged
like the matching field. Columns are followed through casts, CTEs and derived tables; quoted integers are read.

Only row filters are judged: a conjunct of WHERE, HAVING or an inner join's ON, outside NOT / NOT EXISTS / EXCEPT.
Under OR, in CASE or in an outer join's ON the predicate does not restrict the returned rows.

Violation patterns:
    SELECT * FROM person WHERE month_of_birth = 13            -- WARNING
    SELECT * FROM person WHERE day_of_birth IN (32, 33)       -- WARNING
    SELECT * FROM person WHERE month_of_birth > 12            -- WARNING
    SELECT * FROM person WHERE year_of_birth < 1800           -- WARNING
    SELECT * FROM person WHERE year_of_birth BETWEEN 2000 AND 1990

Correct patterns:
    SELECT * FROM person WHERE month_of_birth = 6
    SELECT * FROM person WHERE year_of_birth BETWEEN 1950 AND 2000
    SELECT * FROM person WHERE year_of_birth = 2092           -- a date-shifted CDM
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.references import DQD_FIELD_THRESHOLDS, cdm_table
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import (
    RowPath,
    group_by_row,
    in_negated_scope,
    int_literal,
    peel,
    read_of,
    restricts_rows,
)

from ._ds2_birth_bounds import BIRTH_FIELD_BOUNDS

PERSON = "person"
BIRTH_DATETIME = "birth_datetime"
_PART_TO_FIELD = {"year": "year_of_birth", "month": "month_of_birth", "day": "day_of_birth"}
_PART_FUNCTIONS = {exp.Year: "year", exp.Month: "month", exp.Day: "day"}
# Longest day of each month in any year (29 February included).
_DAYS_IN_MONTH = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
_MIRROR = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE, exp.EQ: exp.EQ}

# Admitted values: an inclusive interval (None = unbounded) or a finite set.
Interval = Tuple[Optional[int], Optional[int]]


def _birth_field(node: Optional[exp.Expression]) -> Optional[Tuple[RowPath, str, str]]:
    """``(row, field, text)`` when ``node`` is a person birth field, or a date part of person.birth_datetime."""
    node = peel(node)
    part: Optional[str] = None
    target: Optional[exp.Expression] = None
    if isinstance(node, exp.Extract):
        part, target = normalize_name(node.this.name), node.expression
    elif type(node) in _PART_FUNCTIONS:
        part, target = _PART_FUNCTIONS[type(node)], node.this
    elif isinstance(node, exp.Anonymous) and normalize_name(node.name) == "date_part" and len(node.expressions) == 2:
        first = node.expressions[0]
        part, target = (
            (normalize_name(str(first.this)) if isinstance(first, exp.Literal) else None),
            node.expressions[1],
        )
    if part is not None:
        field = _PART_TO_FIELD.get(part.strip("'\""))
        read = read_of(target)
        if field and read is not None and read[1:] == (PERSON, BIRTH_DATETIME):
            return read[0], field, node.sql()
        return None
    read = read_of(node)
    if read is not None and read[1] == PERSON and read[2] in BIRTH_FIELD_BOUNDS:
        return read[0], read[2], node.sql()
    return None


def _admitted(node: exp.Expression) -> Optional[Tuple[Tuple[RowPath, str, str], object]]:
    """The birth field ``node`` restricts and the values it admits (an Interval or a set of ints)."""
    if isinstance(node, exp.In):
        field = _birth_field(node.this)
        if field is None or isinstance(node.parent, exp.Not) or node.args.get("query") is not None:
            return None
        values = [int_literal(v) for v in node.expressions or []]
        return (field, set(values)) if values and all(v is not None for v in values) else None
    if isinstance(node, exp.Between):
        field = _birth_field(node.this)
        low, high = int_literal(node.args.get("low")), int_literal(node.args.get("high"))
        if field is None or low is None or high is None:
            return None
        if node.args.get("symmetric"):
            low, high = min(low, high), max(low, high)
        return field, (low, high)
    for field_side, value_side, op in ((node.this, node.expression, type(node)), (node.expression, node.this, None)):
        field = _birth_field(field_side)
        if field is None:
            continue
        value = int_literal(value_side)
        if value is None:
            return None
        op = op or _MIRROR[type(node)]
        interval: Interval = {
            exp.EQ: (value, value),
            exp.GT: (value + 1, None),
            exp.GTE: (value, None),
            exp.LT: (None, value - 1),
            exp.LTE: (None, value),
        }[op]
        return field, interval
    return None


def _overlaps(admitted: object, low: int, high: Optional[int]) -> bool:
    if isinstance(admitted, set):
        return any(v >= low and (high is None or v <= high) for v in admitted)
    a_low, a_high = admitted
    return (a_high is None or a_high >= low) and (high is None or a_low is None or a_low <= high)


def _as_set(admitted: object, low: int, high: int) -> Set[int]:
    if isinstance(admitted, set):
        return {v for v in admitted if low <= v <= high}
    a_low, a_high = admitted
    return set(
        range(max(low, a_low if a_low is not None else low), min(high, a_high if a_high is not None else high) + 1)
    )


def _range_text(field: str) -> str:
    low, high = BIRTH_FIELD_BOUNDS[field]
    return f"{low} to {high}" if high is not None else f"from {low}"


def _find_violations(tree: exp.Expression) -> List[Tuple[str, str, Severity]]:
    """``(field, message, severity)`` per filter whose admitted values miss the field's range."""
    issues: List[Tuple[str, str, Severity]] = []
    seen: Set[str] = set()
    per_row: List[Tuple[RowPath, Tuple[str, Set[int]]]] = []

    def add(field: str, message: str, severity: Severity) -> None:
        if message not in seen:
            seen.add(message)
            issues.append((field, message, severity))

    for node in tree.find_all(exp.EQ, exp.In, exp.Between, exp.GT, exp.GTE, exp.LT, exp.LTE):
        if not restricts_rows(node) or in_negated_scope(node):
            continue
        found = _admitted(node)
        if found is None:
            continue
        (row, field, _), admitted = found
        predicate = node.sql()
        if isinstance(admitted, tuple) and admitted[0] is not None and admitted[1] is not None:
            if admitted[0] > admitted[1]:
                add(field, f"`{predicate}` is empty: its lower bound is above its upper bound.", Severity.ERROR)
                continue
        low, high = BIRTH_FIELD_BOUNDS[field]
        if not _overlaps(admitted, low, high):
            if field == "year_of_birth":
                add(
                    field,
                    f"`{predicate}` admits only year_of_birth values before {low}, the earliest year of birth the "
                    "OHDSI Data Quality Dashboard treats as plausible; such persons are implausible, so the filter "
                    "probably selects nobody or only data errors.",
                    Severity.WARNING,
                )
            else:
                add(
                    field,
                    f"`{predicate}` admits no {field} from {low} to {high}. {field} holds the "
                    f"{field.split('_')[0]} of a date of birth, so rows can match only in non-conformant data: the "
                    "OHDSI Data Quality Dashboard flags such values but tolerates a share of them.",
                    Severity.WARNING,
                )
            continue
        if high is not None and field in ("month_of_birth", "day_of_birth"):
            values = _as_set(admitted, low, high)
            per_row.append((row, (field, values)))

    for _, restrictions in group_by_row(per_row):
        months: Optional[Set[int]] = None
        days: Optional[Set[int]] = None
        for field, values in restrictions:
            if field == "month_of_birth":
                months = values if months is None else months & values
            else:
                days = values if days is None else days & values
        if months is None or days is None or not months or not days:
            continue
        if not any(d <= _DAYS_IN_MONTH[m] for m in months for d in days):
            add(
                "day_of_birth",
                f"month_of_birth in {sorted(months)} and day_of_birth in {sorted(days)} never occur together in a "
                "real date: no month has that many days, so rows can match only in non-conformant data.",
                Severity.WARNING,
            )
    return issues


@register
class PersonBirthFieldValidationRule(Rule):
    """A row filter on a person birth field that admits no value in the field's range."""

    rule_id = "domain_specific.person_birth_field_validation"
    name = "Person Birth Field Validation"
    description = (
        "Flags row filters on person birth fields: an inverted BETWEEN is empty for any data (error); month_of_birth "
        "outside 1-12, day_of_birth outside 1-31, an impossible month/day pair, or year_of_birth only before 1850 "
        "match only non-conformant rows (warning). Years have no upper bound."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the literal WITH a value in range: month_of_birth 1-12, day_of_birth 1-31, year_of_birth from 1850; "
        "for an inverted BETWEEN, put the lower bound first."
    )
    long_description = (
        "CDM v5.4 fills month_of_birth and day_of_birth from a precise date of birth, and the OHDSI Data Quality "
        "Dashboard checks them against 1-12 and 1-31; a filter that admits no such value (=, IN, BETWEEN or a "
        "one-sided comparison, any literal) matches rows only in non-conformant data. It is a warning, because the "
        "DQD thresholds tolerate a share of violating rows, so the CDM does not guarantee none exist (DQD's own "
        "check query selects them). An inverted BETWEEN on any birth field is empty for any data and is an error. "
        "A year_of_birth before 1850 is implausible by the same checks, so a filter that admits only such years "
        "is a warning too. The rule sets no upper bound on "
        "year_of_birth: DQD derives it from the current date, and date-shifted databases store later years."
    )
    example_bad = "SELECT person_id FROM person\nWHERE year_of_birth BETWEEN 2000 AND 1990;"
    example_good = "SELECT person_id FROM person\nWHERE month_of_birth = 12;"
    extra_examples = (
        RuleExample(
            sql="SELECT person_id FROM person\nWHERE year_of_birth < 1800;",
            severity=Severity.WARNING,
            note="year_of_birth only before 1850, the DQD plausibility bound",
        ),
        RuleExample(
            sql="SELECT person_id FROM person\nWHERE month_of_birth = 13;",
            severity=Severity.WARNING,
            note="month 13 is outside 1-12: only non-conformant rows match",
        ),
        RuleExample(
            sql="SELECT person_id FROM person\nWHERE month_of_birth = 0;",
            severity=Severity.WARNING,
            note="month 0 is outside 1-12 like any other non-month: only non-conformant rows match",
        ),
    )
    references = (
        cdm_table("person"),
        DQD_FIELD_THRESHOLDS,
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql or "birth" not in sql.lower():
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            for field, message, severity in _find_violations(tree):
                low, high = BIRTH_FIELD_BOUNDS[field]
                violations.append(
                    self.create_violation(
                        severity=severity,
                        message=message,
                        suggested_fix=(
                            f"REPLACE: the `{field}` literal WITH a value {_range_text(field)}; for an inverted "
                            "BETWEEN, put the lower bound first."
                        ),
                        details={"field": field, "min_value": low, "max_value": high},
                    )
                )
        return violations


__all__ = ["PersonBirthFieldValidationRule"]

"""Non-Standard Date Literal Format Rule.

A string literal used as a date is converted by the engine's date input routine, and only ISO 8601 ``YYYY-MM-DD``
reads the same everywhere. What the other common formats do, per engine:

* ``'01/02/2020'`` and ``'01.02.2020'`` (with or without a time part): PostgreSQL reads them by its DateStyle
  setting ("1/8/1999: January 8 in MDY mode; August 1 in DMY mode"), SQL Server by SET DATEFORMAT, so the same
  query selects different days on differently configured servers.
* ``'08-Jan-1999'``: PostgreSQL reads it the same in every mode, but month names follow the session language on
  SQL Server and Oracle (NLS_DATE_LANGUAGE), so the query is not portable. A two-digit year (``'08-Jan-99'``) is
  ambiguous on PostgreSQL too ("error in YMD mode").
* ``'19990108'`` (ISO 8601 basic format): PostgreSQL reads it the same in every mode and SQL Server treats it as
  unambiguous.

DuckDB documents ISO 8601 ``YYYY-MM-DD`` input. It reads a date year first, ``year SEP month SEP day`` with ``-``,
``/`` or a space as separator and a one- or two-digit day (checked on DuckDB 2.0). It therefore rejects the dot,
month-name and ``YYYYMMDD`` forms, and a slash date whose last field has four digits (``'01/31/2020'``). A slash date
whose last field has two digits it accepts when the fields form a valid year-first date, and reads as that date:
``'01/02/20'`` is 0001-02-20.

Severity: ERROR on DuckDB where it rejects the literal, so the query fails. WARNING when DuckDB accepts it as a
year-first date: the query runs, and only the rows are wrong when another year was meant. WARNING on other dialects,
where the outcome depends on server settings or on the engine the query is moved to. ``YYYYMMDD`` is reported only on
DuckDB.

A literal is judged when it is used as a date: compared with (=, <>, <, >, BETWEEN, IN) a column whose lineage is a
CDM date or datetime column, cast to a date type (``DATE '...'``, ``CAST('...' AS DATE)``, ``::date``,
``CONVERT(DATE, '...')`` without a style), or passed as a date to DATEDIFF / DATEADD. Conversions with an explicit
format (``TO_DATE('01/31/2020', 'MM/DD/YYYY')``, ``CONVERT(DATE, '...', 101)``) are not flagged.
"""

import re
from datetime import datetime
from typing import Iterator, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import DERIVED, column_origins, parse_sql
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register
from fastssv.schemas import DATE, DATETIME, get_column_type

_TIME = r"(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"
_FORMATS = [
    ("slash", re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}" + _TIME), "M/D/YYYY or D/M/YYYY (read by DateStyle / DATEFORMAT)"),
    ("dot", re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}" + _TIME), "D.M.YYYY or M.D.YYYY (read by DateStyle / DATEFORMAT)"),
    ("mon4", re.compile(r"\d{1,2}-[a-zA-Z]{3}-\d{4}" + _TIME), "DD-MON-YYYY (month name in the session language)"),
    ("mon2", re.compile(r"\d{1,2}-[a-zA-Z]{3}-\d{2}" + _TIME), "DD-MON-YY (month name and two-digit year)"),
    ("basic", re.compile(r"\d{8}"), "YYYYMMDD (ISO 8601 basic format)"),
]
_DATE_TYPES = {exp.DataType.Type.DATE, exp.DataType.Type.DATETIME, exp.DataType.Type.TIMESTAMP}
_DUCKDB = {"duckdb"}
_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def _try_canonicalize_to_iso(literal: str) -> Optional[str]:
    """ISO 8601 form of a ``DD-MON-YYYY`` or ``YYYYMMDD`` literal; None for ambiguous or two-digit-year forms."""
    s = literal.strip()
    for pattern, fmt in ((r"\d{1,2}-[a-zA-Z]{3}-\d{4}", "%d-%b-%Y"), (r"\d{8}", "%Y%m%d")):
        if re.fullmatch(pattern, s):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                return None
    return None


def _duckdb_reading(literal: str, kind: str) -> Optional[str]:
    """The date DuckDB reads a slash literal as (year first, a one- or two-digit day); None when it rejects it."""
    if kind != "slash":
        return None
    fields = re.split(r"[ T]", literal.strip(), maxsplit=1)[0].split("/")
    if len(fields) != 3 or len(fields[2]) > 2:
        return None
    year, month, day = (int(f) for f in fields)
    try:
        # Year 0 is 1 BC, a leap year in the proleptic Gregorian calendar, like 2000.
        datetime(year or 2000, month, day)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}" + (" (1 BC)" if year == 0 else "")


def _format(literal: str) -> Optional[Tuple[str, str]]:
    s = literal.strip()
    for kind, pattern, description in _FORMATS:
        if pattern.fullmatch(s):
            return kind, description
    return None


def _peel(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, (exp.Paren, exp.Cast, exp.TimeStrToTime, exp.TsOrDsToDate)):
        node = node.this
    return node


def _string(node: Optional[exp.Expression]) -> Optional[exp.Literal]:
    node = _peel(node)
    return node if isinstance(node, exp.Literal) and node.is_string else None


def _is_date_column(node: Optional[exp.Expression]) -> bool:
    node = _peel(node)
    if not isinstance(node, exp.Column):
        return False
    origins = column_origins(node)
    return bool(origins) and all(t != DERIVED and get_column_type(t, c) in (DATE, DATETIME) for t, c in origins)


def _date_literals(tree: exp.Expression) -> Iterator[exp.Literal]:
    """String literals the statement uses as dates."""
    for node in tree.find_all(*_COMPARISONS, exp.Between, exp.In):
        if isinstance(node, exp.Between):
            if _is_date_column(node.this):
                yield from (lit for lit in (_string(node.args.get("low")), _string(node.args.get("high"))) if lit)
        elif isinstance(node, exp.In):
            if _is_date_column(node.this):
                yield from (lit for lit in (_string(v) for v in node.expressions or []) if lit)
        else:
            for column_side, literal_side in ((node.this, node.expression), (node.expression, node.this)):
                lit = _string(literal_side)
                if lit is not None and _is_date_column(column_side):
                    yield lit
    for cast in tree.find_all(exp.Cast):
        to = cast.args.get("to")
        if isinstance(to, exp.DataType) and to.this in _DATE_TYPES and isinstance(cast.this, exp.Literal):
            if cast.this.is_string:
                yield cast.this
    for convert in tree.find_all(exp.Convert):
        to = convert.this
        if isinstance(to, exp.DataType) and to.this in _DATE_TYPES and convert.args.get("style") is None:
            lit = _string(convert.expression)
            if lit is not None:
                yield lit
    for fn in tree.find_all(exp.DateDiff, exp.DateAdd, exp.DateSub):
        for arg in (fn.this, fn.expression):
            if isinstance(arg, (exp.TimeStrToTime, exp.TsOrDsToDate)) or isinstance(arg, exp.Literal):
                lit = _string(arg)
                if lit is not None:
                    yield lit


@register
class NonStandardDateLiteralFormatRule(Rule):
    """Date literals in formats whose meaning depends on server settings or on the engine."""

    rule_id = "data_quality.non_standard_date_literal_format"
    name = "Non-Standard Date Literal Format"
    description = (
        "Detects date literals in formats other than ISO 8601 YYYY-MM-DD: slash and dot dates ('12/31/2020'), which "
        "PostgreSQL and SQL Server read by a server setting, and month-name dates ('01-jan-2011'), which follow the "
        "session language on SQL Server and Oracle. DuckDB reads dates year first: it rejects these formats (ERROR "
        "there) except a slash date with a two-digit last field, which it reads as another date (WARNING); elsewhere "
        "a WARNING."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the date literal WITH ISO 8601 ('2011-01-01', '2020-12-31'), e.g. DATE '2020-12-31'. To keep "
        "another format, convert it with an explicit format (TO_DATE('31/12/2020', 'DD/MM/YYYY'))."
    )
    long_description = (
        "A string used as a date is read by the engine's date input routine. '01/02/2020' is January 2 under "
        "PostgreSQL's MDY DateStyle and February 1 under DMY, and SQL Server reads it by SET DATEFORMAT: the same "
        "query selects different days on differently configured servers. PostgreSQL reads '08-Jan-1999' the same "
        "in every mode, but SQL Server and Oracle read month names in the session language, and a two-digit year "
        "is ambiguous on PostgreSQL too. DuckDB reads a date year first (year, month, day separated by '-', '/' or "
        "a space, with a one- or two-digit day): it rejects the dot, month-name and YYYYMMDD forms and a slash date "
        "with a four-digit year, so on DuckDB those are an ERROR. A slash date with a two-digit last field runs on "
        "DuckDB but is read year first ('01/02/20' is 0001-02-20), so it is a WARNING: only the rows are wrong when "
        "another date was meant. Elsewhere the finding is a WARNING. YYYYMMDD (ISO 8601 basic format) reads the same on "
        "PostgreSQL and SQL Server and is reported only on DuckDB. A literal is judged when it is compared with a "
        "CDM date column, cast to a date, or passed as a date to DATEDIFF / DATEADD; conversions with an explicit "
        "format are not flagged."
    )
    example_bad = "SELECT *\nFROM condition_occurrence\nWHERE condition_start_date = '01/31/2020';"
    example_good = "SELECT *\nFROM condition_occurrence\nWHERE condition_start_date = DATE '2020-01-31';"
    extra_examples = (
        RuleExample(
            sql="SELECT person_id\nFROM drug_exposure\nWHERE drug_exposure_start_date >= '01-JAN-2020';",
            severity=Severity.WARNING,
            note="DD-MON-YYYY: SQL Server and Oracle read the month name in the session language; DuckDB rejects it.",
        ),
    )
    references = (
        Reference(
            "PostgreSQL documentation: Date/Time Types, Date input",
            "https://www.postgresql.org/docs/current/datatype-datetime.html#DATATYPE-DATETIME-INPUT-DATES",
        ),
        Reference(
            "SQL Server documentation: SET DATEFORMAT",
            "https://learn.microsoft.com/en-us/sql/t-sql/statements/set-dateformat-transact-sql",
        ),
        Reference(
            "Oracle Database 19c Reference: NLS_DATE_LANGUAGE",
            "https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/NLS_DATE_LANGUAGE.html",
        ),
        Reference("DuckDB documentation: Date Types", "https://duckdb.org/docs/stable/sql/data_types/date"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            seen: Set[str] = set()
            for literal in _date_literals(tree):
                value = str(literal.this)
                found = _format(value)
                if found is None or value in seen:
                    continue
                kind, description = found
                if kind == "basic" and dialect not in _DUCKDB:
                    continue
                seen.add(value)
                duckdb_date = _duckdb_reading(value, kind) if dialect in _DUCKDB else None
                if dialect in _DUCKDB and duckdb_date is None:
                    severity = Severity.ERROR
                    consequence = "DuckDB cannot read it as a year-first date and rejects it, so the query fails."
                elif dialect in _DUCKDB:
                    severity = Severity.WARNING
                    consequence = (
                        f"DuckDB reads dates year first, so it reads this as {duckdb_date}: the query runs, but it "
                        "compares with that date, not with the day and year that were probably meant."
                    )
                elif kind in ("slash", "dot"):
                    severity = Severity.WARNING
                    consequence = (
                        "PostgreSQL reads it by DateStyle and SQL Server by DATEFORMAT, so its day and month depend on "
                        "server settings."
                    )
                elif kind == "mon2":
                    severity = Severity.WARNING
                    consequence = "The two-digit year is ambiguous (PostgreSQL rejects it in YMD mode)."
                else:
                    severity = Severity.WARNING
                    consequence = (
                        "SQL Server and Oracle read month names in the session language, and DuckDB rejects the "
                        "format, so the query is not portable."
                    )
                patch = None
                iso = _try_canonicalize_to_iso(value)
                if iso is not None:
                    span = locate(sql, f"'{value}'")
                    if span is not None:
                        patch = patch_replace(span, f"'{iso}'")
                violations.append(
                    self.create_violation(
                        message=f"Non-standard date literal '{value}' ({description}). {consequence} Use ISO 8601 (YYYY-MM-DD).",
                        severity=severity,
                        suggested_fix_patch=patch,
                        details={"literal": value, "format": kind},
                    )
                )
        return violations


__all__ = ["NonStandardDateLiteralFormatRule"]

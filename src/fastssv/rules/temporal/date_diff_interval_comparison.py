"""DATE − DATE compared with an INTERVAL.

PostgreSQL defines ``date - date`` as an integer number of days, and has no comparison operator between an integer
and an interval, so ``(end_date - start_date) <= INTERVAL '30 days'`` is rejected with ``operator does not exist:
integer <= interval``. DuckDB types the difference as BIGINT and rejects the comparison in the same way.

Violation patterns::

    (MAX(condition_start_date) - MIN(condition_start_date)) <= INTERVAL '1000 days'
    ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= INTERVAL '30 days'
    (CURRENT_DATE - drug_exposure_start_date) <= 30 * INTERVAL '1 day'
    (b.visit_start_date - a.visit_end_date) BETWEEN INTERVAL '0 days' AND INTERVAL '30 days'

Correct patterns::

    ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= 30               -- integer days
    (a.condition_start_datetime - b.condition_start_datetime) <= INTERVAL '30 days'  -- timestamps yield intervals

Operand types are settled by lineage, not by name: a column is followed through CTEs and derived tables to the CDM
column (typed by the CDM v5.4 field-level specification) or to the expression that produces it, and the operator
results follow the PostgreSQL date/time operator table. A column whose type cannot be settled is not judged.

The rule runs under the postgres and duckdb dialects, where the failure was reproduced. Other dialects are not
checked: the result type of DATE − DATE differs between engines (BigQuery returns an INTERVAL).
"""

from typing import List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register
from fastssv.rules.temporal._typing import DATE, INT, INTERVAL, sql_type, unwrap

_VERIFIED_DIALECTS = {"postgres", "postgresql", "duckdb"}
_SYMBOL = {exp.EQ: "=", exp.NEQ: "<>", exp.LT: "<", exp.LTE: "<=", exp.GT: ">", exp.GTE: ">="}


def _date_difference(node: Optional[exp.Expression]) -> Optional[exp.Sub]:
    """The ``date - date`` subtraction that makes ``node`` an integer day count, or None."""
    node = unwrap(node)
    if node is None or sql_type(node) != INT:
        return None
    for sub in node.find_all(exp.Sub):
        if sql_type(sub.this) == DATE and sql_type(sub.expression) == DATE:
            return sub
    return None


def _mismatches(tree: exp.Expression) -> List[Tuple[exp.Sub, exp.Expression, exp.Expression, str]]:
    """(date difference, interval operand, predicate, operator text as the engine reports it) per finding."""
    found = []
    for node in tree.find_all(*_SYMBOL, exp.Between):
        if isinstance(node, exp.Between):
            diff = _date_difference(node.this)
            bounds = [b for b in (node.args.get("low"), node.args.get("high")) if b is not None]
            interval = next((b for b in bounds if sql_type(b) == INTERVAL), None)
            if diff is not None and interval is not None:
                found.append((diff, interval, node, "integer >= interval"))
            continue
        symbol = _SYMBOL[type(node)]
        for days, other, text in (
            (node.this, node.expression, f"integer {symbol} interval"),
            (node.expression, node.this, f"interval {symbol} integer"),
        ):
            diff = _date_difference(days)
            if diff is not None and sql_type(other) == INTERVAL:
                found.append((diff, unwrap(other), node, text))
    return found


@register
class DateDiffIntervalComparisonRule(Rule):
    rule_id = "temporal.date_diff_interval_comparison"
    name = "DATE difference compared with INTERVAL"
    description = (
        "On PostgreSQL and DuckDB, DATE − DATE is an integer number of days; comparing it with an INTERVAL is a "
        "type error and the query fails."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the INTERVAL WITH an integer number of days (e.g. `<= 30`), OR subtract *_datetime / TIMESTAMP "
        "values so the difference is an interval."
    )
    long_description = (
        "PostgreSQL defines date - date as an integer number of days and timestamp - timestamp as an interval. "
        "There is no comparison operator between an integer and an interval, so a day difference compared with "
        "INTERVAL '30 days', with a cast or computed interval, or tested BETWEEN two intervals is rejected before "
        "any row is read; DuckDB rejects it in the same way. Operand types are settled by lineage: a column is "
        "followed through CTEs and derived tables to its CDM column or to the expression that produces it, so a "
        "timestamp that happens to be called first_date is not reported and a date that was renamed is. The "
        "rule runs under the postgres and duckdb dialects only; other engines type DATE − DATE differently."
    )
    example_bad = (
        "SELECT COUNT(*)\n"
        "FROM drug_exposure a\n"
        "JOIN drug_exposure b ON a.person_id = b.person_id AND a.drug_exposure_id <> b.drug_exposure_id\n"
        "WHERE ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= INTERVAL '30 days';"
    )
    example_good = (
        "SELECT COUNT(*)\n"
        "FROM drug_exposure a\n"
        "JOIN drug_exposure b ON a.person_id = b.person_id AND a.drug_exposure_id <> b.drug_exposure_id\n"
        "WHERE ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= 30;"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT COUNT(*)\n"
                "FROM drug_exposure\n"
                "WHERE (CURRENT_DATE - drug_exposure_start_date) BETWEEN INTERVAL '0 days' AND INTERVAL '30 days';"
            ),
            severity=Severity.ERROR,
            note="Day difference from CURRENT_DATE tested BETWEEN two intervals",
        ),
    )
    references = (
        Reference(
            "PostgreSQL documentation: Date/Time Functions and Operators, Date/Time Operators",
            "https://www.postgresql.org/docs/current/functions-datetime.html#OPERATORS-DATETIME-TABLE",
        ),
        Reference(
            "DuckDB documentation: Date Functions, Date Operators",
            "https://duckdb.org/docs/stable/sql/functions/date.html",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        name = (dialect or "postgres").lower()
        if name not in _VERIFIED_DIALECTS:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        seen = set()
        for tree in trees:
            if not tree:
                continue
            for diff, interval, predicate, operator in _mismatches(tree):
                key = predicate.sql()
                if key in seen:
                    continue
                seen.add(key)
                engine = "DuckDB" if name == "duckdb" else "PostgreSQL"
                outcome = (
                    "DuckDB rejects the query: it cannot compare BIGINT with INTERVAL."
                    if name == "duckdb"
                    else f"PostgreSQL rejects the query with 'operator does not exist: {operator}'."
                )
                violations.append(
                    self.create_violation(
                        message=(
                            f"`{diff.sql(dialect=dialect)}` is an integer number of days (DATE − DATE) but is "
                            f"compared with the interval `{interval.sql(dialect=dialect)}`. {outcome} Compare with "
                            "an integer day count instead."
                        ),
                        severity=self.severity,
                        location=predicate.sql(dialect=dialect)[:120],
                        details={"engine": engine, "date_difference": diff.sql(), "interval": interval.sql()},
                    )
                )
        return violations


__all__ = ["DateDiffIntervalComparisonRule"]

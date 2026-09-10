"""DATE − DATE compared with an INTERVAL.

On PostgreSQL, Redshift and DuckDB, subtracting two DATE values yields an INTEGER number of days, so comparing
that difference with an INTERVAL literal (``... <= INTERVAL '30 days'``) is a type error and the query fails
(``operator does not exist: integer <= interval``). LLM-written OMOP SQL produces this shape often.

Violation pattern::

    (MAX(condition_start_date) - MIN(condition_start_date)) <= INTERVAL '1000 days'
    ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= INTERVAL '30 days'

Correct patterns::

    (MAX(condition_start_date) - MIN(condition_start_date)) <= 1000            -- integer days
    ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= 30
    (a.condition_start_datetime - b.condition_start_datetime) <= INTERVAL '30 days'  -- timestamps yield intervals

Only DATE-typed operands are flagged: CDM ``*_date`` columns and explicit ``CAST(... AS DATE)``; ``*_datetime``
columns and timestamps legitimately produce intervals. Dialects where DATE − DATE is not an integer (BigQuery,
Snowflake, T-SQL, Oracle) are not flagged.
"""

from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register

_INTEGER_DAY_DIALECTS = {"postgres", "postgresql", "redshift", "duckdb"}
_COMPARISONS = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)


def _is_date_operand(node: exp.Expression) -> bool:
    """A DATE-typed operand: a ``*_date`` column (not ``*_datetime``), a CAST/:: to DATE, or an aggregate of one."""
    node = node.unnest() if isinstance(node, exp.Paren) else node
    if isinstance(node, exp.Cast):
        return isinstance(node.to, exp.DataType) and node.to.this == exp.DataType.Type.DATE
    if isinstance(node, exp.AggFunc) and node.this is not None:
        return _is_date_operand(node.this)
    if isinstance(node, (exp.Greatest, exp.Least)):  # GREATEST(a_date, b_date) - LEAST(...) is still DATE - DATE
        args = [node.this, *node.expressions]
        return bool(args) and all(_is_date_operand(a) for a in args)
    if isinstance(node, exp.Column):
        name = normalize_name(node.name) or ""
        return name.endswith("_date") and not name.endswith("_datetime")
    return False


def _date_minus_date(node: exp.Expression) -> Optional[exp.Sub]:
    """Return the ``date - date`` subtraction inside ``node`` (possibly wrapped in ABS()/parentheses)."""
    node = node.unnest() if isinstance(node, exp.Paren) else node
    if isinstance(node, exp.Abs):
        return _date_minus_date(node.this)
    if isinstance(node, exp.Sub) and _is_date_operand(node.this) and _is_date_operand(node.expression):
        return node
    return None


@register
class DateDiffIntervalComparisonRule(Rule):
    rule_id = "temporal.date_diff_interval_comparison"
    name = "DATE difference compared with INTERVAL"
    description = (
        "On PostgreSQL/Redshift/DuckDB, DATE − DATE is an integer number of days; comparing it with an INTERVAL "
        "literal is a type error and the query fails."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the INTERVAL literal WITH an integer number of days (e.g. `<= 30`), OR subtract *_datetime / "
        "TIMESTAMP values so the difference is an interval."
    )
    example_bad = "SELECT COUNT(*) FROM drug_exposure a JOIN drug_exposure b ON a.person_id = b.person_id\nWHERE ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= INTERVAL '30 days';"
    example_good = "SELECT COUNT(*) FROM drug_exposure a JOIN drug_exposure b ON a.person_id = b.person_id\nWHERE ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= 30;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if (dialect or "postgres").lower() not in _INTEGER_DAY_DIALECTS:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        seen = set()
        for tree in trees:
            if not tree:
                continue
            for cmp in tree.find_all(*_COMPARISONS):
                for lhs, rhs in ((cmp.this, cmp.expression), (cmp.expression, cmp.this)):
                    sub = _date_minus_date(lhs)
                    rhs_u = rhs.unnest() if isinstance(rhs, exp.Paren) else rhs
                    if sub is None or not isinstance(rhs_u, exp.Interval):
                        continue
                    key = sub.sql()
                    if key in seen:
                        continue
                    seen.add(key)
                    violations.append(
                        self.create_violation(
                            message=(
                                f"`{sub.sql()}` is an integer number of days (DATE − DATE) but is compared with "
                                f"`{rhs_u.sql()}`; PostgreSQL raises 'operator does not exist: integer "
                                f"{type(cmp).__name__.lower()} interval'. Compare with an integer day count instead."
                            ),
                            severity=self.severity,
                            location=cmp.sql()[:120],
                        )
                    )
        return violations


__all__ = ["DateDiffIntervalComparisonRule"]

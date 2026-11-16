"""Measurement Range Low/High Validation Rule.

CDM v5.4 stores a measurement's reference range in ``range_low`` and ``range_high``: "If reference ranges for
upper and lower limit of normal as provided (typically by a laboratory) these are stored in the RANGE_HIGH and
RANGE_LOW fields." A row whose lower limit is above its upper limit is therefore a data error. A filter that keeps
only such rows runs and returns exactly them, which is what a data-quality audit wants and what a clinical query
does not. The CDM does not forbid the rows, so this is a WARNING.

Fires when a row filter (a conjunct of WHERE, HAVING or an inner join's ON, outside NOT / NOT EXISTS / EXCEPT) can
hold only for rows with ``range_low > range_high``:

* a strict comparison of the two columns of the same row: ``range_low > range_high``, ``range_high < range_low``,
  ``range_low - range_high > 0``;
* literal bounds on the two columns of the same row that leave no room: ``range_low >= 150 AND range_high < 100``.

Columns are followed through casts, CTEs and derived tables, and bounds are collected per measurement row, so a
self-join, UNION branches or a subquery that constrain different rows are not combined. ``range_low >=
range_high`` is not reported: a source can give a single reference value as both limits.

Violation:
    SELECT * FROM measurement WHERE range_low > range_high AND value_as_number > range_high

Correct:
    SELECT * FROM measurement WHERE value_as_number < range_low OR value_as_number > range_high
    SELECT * FROM measurement WHERE range_low >= 50 AND range_high <= 200
"""

from typing import List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import cdm_table
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import (
    RowPath,
    group_by_row,
    in_negated_scope,
    number_literal,
    peel,
    read_of,
    restricts_rows,
    same_row,
)

TABLE_NAME = "measurement"
RANGE_LOW = "range_low"
RANGE_HIGH = "range_high"

_ORDERINGS = (exp.GT, exp.GTE, exp.LT, exp.LTE)
_MIRROR = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE, exp.EQ: exp.EQ}


def _range_read(node: Optional[exp.Expression]) -> Optional[Tuple[RowPath, str]]:
    """(row, column) when ``node`` reads measurement.range_low or range_high."""
    read = read_of(node)
    if read is None or read[1] != TABLE_NAME or read[2] not in (RANGE_LOW, RANGE_HIGH):
        return None
    return read[0], read[2]


def _low_minus_high(node: Optional[exp.Expression]) -> Optional[int]:
    """+1 for ``range_low - range_high`` of one row, -1 for the reverse, else None."""
    node = peel(node)
    if not isinstance(node, exp.Sub):
        return None
    left, right = _range_read(node.this), _range_read(node.expression)
    if left is None or right is None or not same_row(left[0], right[0]) or left[1] == right[1]:
        return None
    return 1 if left[1] == RANGE_LOW else -1


def _requires_inverted_range(node: exp.Expression) -> bool:
    """``node`` is a strict ordering that holds only when range_low > range_high on one row."""
    left, right = _range_read(node.this), _range_read(node.expression)
    if left is not None and right is not None:
        if not same_row(left[0], right[0]):
            return False
        return (isinstance(node, exp.GT) and (left[1], right[1]) == (RANGE_LOW, RANGE_HIGH)) or (
            isinstance(node, exp.LT) and (left[1], right[1]) == (RANGE_HIGH, RANGE_LOW)
        )
    # range_low - range_high <op> literal, either operand order
    for diff_side, other, op in ((node.this, node.expression, type(node)), (node.expression, node.this, None)):
        sign = _low_minus_high(diff_side)
        bound = number_literal(other)
        if sign is None or bound is None:
            continue
        op = op or _MIRROR[type(node)]
        if sign < 0:
            op, bound = _MIRROR[op], -bound
        # now: (range_low - range_high) <op> bound
        return (op is exp.GT and bound >= 0) or (op is exp.GTE and bound > 0)
    return False


def _literal_bounds(node: exp.Expression) -> List[Tuple[RowPath, Tuple[str, float, bool]]]:
    """Bounds that ``node`` puts on one range column: ``("low_min" | "high_max", value, strict)`` per row."""
    out: List[Tuple[RowPath, Tuple[str, float, bool]]] = []
    if isinstance(node, exp.Between):
        col = _range_read(node.this)
        low, high = number_literal(node.args.get("low")), number_literal(node.args.get("high"))
        if col is not None and low is not None and high is not None:
            out.append((col[0], ("low_min", low, False) if col[1] == RANGE_LOW else ("high_max", high, False)))
        return out
    for col_side, lit_side, op in ((node.this, node.expression, type(node)), (node.expression, node.this, None)):
        col, value = _range_read(col_side), number_literal(lit_side)
        if col is None or value is None:
            continue
        op = op or _MIRROR[type(node)]
        if col[1] == RANGE_LOW and op in (exp.GT, exp.GTE, exp.EQ):
            out.append((col[0], ("low_min", value, op is exp.GT)))
        elif col[1] == RANGE_HIGH and op in (exp.LT, exp.LTE, exp.EQ):
            out.append((col[0], ("high_max", value, op is exp.LT)))
        break
    return out


def _find_violations(tree: exp.Expression) -> List[str]:
    """Texts describing each filter that only inverted reference ranges satisfy."""
    found: List[str] = []
    bounds: List[Tuple[RowPath, Tuple[str, float, bool]]] = []
    for node in tree.find_all(*_ORDERINGS, exp.EQ, exp.Between):
        if not restricts_rows(node) or in_negated_scope(node):
            continue
        if isinstance(node, _ORDERINGS) and _requires_inverted_range(node):
            text = f"`{node.sql()}`"
            if text not in found:
                found.append(text)
            continue
        bounds.extend(_literal_bounds(node))

    for _, row_bounds in group_by_row(bounds):
        lows = [(v, strict) for kind, v, strict in row_bounds if kind == "low_min"]
        highs = [(v, strict) for kind, v, strict in row_bounds if kind == "high_max"]
        if not lows or not highs:
            continue
        low_min, low_strict = max(lows, key=lambda b: (b[0], b[1]))
        high_max, high_strict = min(highs, key=lambda b: (b[0], not b[1]))
        if low_min > high_max or (low_min == high_max and (low_strict or high_strict)):
            text = (
                f"the literal filters on range_low ({'>' if low_strict else '>='} {low_min:g}) and "
                f"range_high ({'<' if high_strict else '<='} {high_max:g}) together"
            )
            if text not in found:
                found.append(text)
    return found


@register
class MeasurementRangeLowHighValidationRule(Rule):
    """A row filter that only measurement rows with range_low above range_high can satisfy."""

    rule_id = "domain_specific.measurement_range_low_high_validation"
    name = "Measurement Range Low/High Validation"
    description = (
        "Flags a row filter that holds only when measurement.range_low is above range_high. range_low and "
        "range_high are the lower and upper limit of normal, so the filter keeps only rows with an inverted "
        "reference range: useful as a data-quality audit, not as a clinical criterion."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REMOVE: the filter, unless the query audits reference ranges on purpose. To find abnormal results compare "
        "the value with the range: `value_as_number < range_low OR value_as_number > range_high`."
    )
    long_description = (
        "CDM v5.4 stores the lower and upper limit of normal provided by the source in measurement.range_low and "
        "range_high. The specification does not forbid a row whose lower limit exceeds its upper limit, but such a "
        "row is a data error. A filter that requires range_low > range_high (directly, or through literal bounds "
        "that leave no other possibility) returns only those rows. Bounds are evaluated per measurement row, and "
        "`range_low >= range_high` is accepted because both limits may hold the same reference value."
    )
    example_bad = (
        "SELECT person_id, value_as_number FROM measurement\n"
        "WHERE measurement_concept_id = 3004410\n"
        "  AND range_low > range_high;"
    )
    example_good = (
        "SELECT person_id, value_as_number FROM measurement\n"
        "WHERE measurement_concept_id = 3004410\n"
        "  AND value_as_number > range_high;"
    )
    extra_examples = (
        RuleExample(
            sql=("SELECT person_id FROM measurement\nWHERE range_low >= 150\n  AND range_high < 100;"),
            severity=Severity.WARNING,
            note="literal bounds on range_low and range_high of one row that together require range_low > range_high",
        ),
    )
    references = (cdm_table("measurement"),)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []
        lowered = sql.lower()
        if RANGE_LOW not in lowered or RANGE_HIGH not in lowered:
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            for text in _find_violations(tree):
                violations.append(
                    self.create_violation(
                        message=(
                            f"{text[0].upper()}{text[1:]} can hold only for measurement rows whose range_low is above "
                            "range_high. Those are inverted reference ranges, so the query keeps only data errors; "
                            "that is what a data-quality audit wants, and not a clinical filter."
                        ),
                    )
                )
        return violations


__all__ = ["MeasurementRangeLowHighValidationRule"]

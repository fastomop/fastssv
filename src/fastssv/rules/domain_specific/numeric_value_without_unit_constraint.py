"""Numeric Value Without Unit Constraint Rule.

``measurement.value_as_number``, ``observation.value_as_number`` and ``dose_era.dose_value`` are only meaningful
together with the row's ``unit_concept_id``. CDM v5.4: "At present, there isn't a prescribed unit for individual
measurements" and "There is currently no recommended unit for individual observation concepts", so the same concept
can arrive in different units (HbA1c as % or mmol/mol, glucose as mg/dL or mmol/L). Two shapes then mix
incompatible scales:

* aggregating the value (AVG, SUM, MIN, MAX, STDDEV, VARIANCE, MEDIAN, PERCENTILE) across rows, and
* comparing it with a numeric threshold (``> 7``, ``BETWEEN 70 AND 180``) in a filter or a CASE condition.

Either is fine once the unit of the same row is constrained: ``unit_concept_id = <id>``, an ``IN`` list or subquery
of unit concepts (the Unit domain has several concepts for one scale: 8848, 8961, 9444 and 8816 all denote 10^9/L),
``unit_concept_id IS NULL`` (the CDM keeps the unit NULL for a quantity that has none), ``GROUP BY`` / ``PARTITION
BY unit_concept_id``, a join of ``unit_concept_id`` to a ``concept`` row that is itself filtered or grouped, or a
unit test in the same CASE condition or aggregate (``CASE WHEN unit_concept_id = 8840 THEN value_as_number … END``).
Rows are identified by lineage through casts, CTEs and derived tables, so a constraint on another alias, another
UNION branch or another table's unit does not count, and equating two rows' units does not fix either unit.
Comparisons with 0 test the sign or presence of a result and are left alone.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.references import cdm_table
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import RowPath, in_negated_scope, number_literal, peel, read_of, same_row

VALUE_COLUMNS: Dict[str, str] = {
    "measurement": "value_as_number",
    "observation": "value_as_number",
    "dose_era": "dose_value",
}
UNIT_COLUMN = "unit_concept_id"

_AGGREGATES = (
    exp.Avg,
    exp.Sum,
    exp.Min,
    exp.Max,
    exp.Stddev,
    exp.StddevPop,
    exp.StddevSamp,
    exp.Variance,
    exp.VariancePop,
    exp.Median,
    exp.PercentileCont,
    exp.PercentileDisc,
    exp.Quantile,
)
_THRESHOLD_OPS = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)
_VALUE_OPERANDS = (exp.Literal, exp.Neg, exp.Placeholder, exp.Parameter, exp.Subquery, exp.Select, exp.Null)

# A unit constraint: on a row (row path) or, for an unresolved unit column, on every row of one SELECT.
Pin = Tuple[str, object]


def _value_read(node: Optional[exp.Expression]) -> Optional[Tuple[RowPath, str]]:
    """(row, table) when ``node`` reads one of the numeric value columns."""
    read = read_of(node)
    if read is None or VALUE_COLUMNS.get(read[1]) != read[2]:
        return None
    return read[0], read[1]


def _unit_column(node: Optional[exp.Expression]) -> Optional[exp.Column]:
    node = peel(node)
    if isinstance(node, exp.Column) and normalize_name(node.name) == UNIT_COLUMN:
        return node
    return None


def _pin_of(col: exp.Column) -> Optional[Pin]:
    read = read_of(col)
    if read is not None:
        return ("row", read[0]) if read[1] in VALUE_COLUMNS else None
    select = col.find_ancestor(exp.Select)
    return ("select", id(select)) if select is not None else None


def _is_concept_id(node: Optional[exp.Expression]) -> Optional[RowPath]:
    read = read_of(node)
    return read[0] if read is not None and read[1:] == ("concept", "concept_id") else None


def _unit_pins(scope: exp.Expression, concept_links: Dict[RowPath, List[Pin]]) -> List[Pin]:
    """Unit constraints that the predicates under ``scope`` put on rows."""
    pins: List[Pin] = []
    for node in scope.find_all(exp.EQ, exp.In, exp.Is):
        if in_negated_scope(node) and not isinstance(node, exp.Is):
            continue
        if isinstance(node, exp.Is):
            col = _unit_column(node.this)
            if col is not None and isinstance(node.expression, exp.Null) and not isinstance(node.parent, exp.Not):
                pin = _pin_of(col)
                if pin:
                    pins.append(pin)
            continue
        if isinstance(node, exp.In):
            col = _unit_column(node.this)
            values = node.expressions or []
            if col is not None and (
                node.args.get("query") is not None
                or (values and all(isinstance(peel(v), _VALUE_OPERANDS) for v in values))
            ):
                pin = _pin_of(col)
                if pin:
                    pins.append(pin)
            continue
        for side, other in ((node.this, node.expression), (node.expression, node.this)):
            col = _unit_column(side)
            if col is None:
                continue
            pin = _pin_of(col)
            if pin is None:
                break
            concept_row = _is_concept_id(other)
            if concept_row is not None:
                concept_links.setdefault(concept_row, []).append(pin)
            elif isinstance(peel(other), _VALUE_OPERANDS):
                pins.append(pin)
            elif isinstance(peel(other), exp.Column):
                other_read = read_of(other)
                # Equating two value rows' units fixes neither unit.
                if not (other_read is not None and other_read[1] in VALUE_COLUMNS and other_read[2] == UNIT_COLUMN):
                    pins.append(pin)
            break
    return pins


def _grouping_columns(tree: exp.Expression) -> List[exp.Column]:
    cols: List[exp.Column] = []
    for group in tree.find_all(exp.Group):
        cols.extend(group.find_all(exp.Column))
    for window in tree.find_all(exp.Window):
        for part in window.args.get("partition_by") or []:
            cols.extend(part.find_all(exp.Column))
    return cols


def _filter_scopes(tree: exp.Expression) -> List[exp.Expression]:
    scopes: List[exp.Expression] = [w for w in tree.find_all(exp.Where, exp.Having)]
    scopes += [j.args["on"] for j in tree.find_all(exp.Join) if j.args.get("on") is not None]
    return scopes


def _statement_pins(tree: exp.Expression) -> List[Pin]:
    concept_links: Dict[RowPath, List[Pin]] = {}
    pins: List[Pin] = []
    for scope in _filter_scopes(tree):
        pins += _unit_pins(scope, concept_links)
    for col in _grouping_columns(tree):
        if normalize_name(col.name) == UNIT_COLUMN:
            pin = _pin_of(col)
            if pin:
                pins.append(pin)
    if concept_links:
        # A joined unit concept narrows the unit only if that concept row is filtered or grouped on.
        used: Set[RowPath] = set()
        for scope in _filter_scopes(tree):
            for node in scope.find_all(exp.EQ, exp.In, exp.Like, exp.ILike):
                operands = (
                    [node.this] if isinstance(node, (exp.In, exp.Like, exp.ILike)) else [node.this, node.expression]
                )
                for operand in operands:
                    read = read_of(operand)
                    if read is not None and read[1] == "concept" and read[2] != "concept_id":
                        used.add(read[0])
        for col in _grouping_columns(tree):
            read = read_of(col)
            if read is not None and read[1] == "concept":
                used.add(read[0])
        for concept_row, linked in concept_links.items():
            if any(same_row(concept_row, u) for u in used):
                pins += linked
    return pins


def _covered(row: RowPath, pins: List[Pin]) -> bool:
    for kind, value in pins:
        if kind == "row" and same_row(row, value):
            return True
        if kind == "select" and row[0][0] == value:
            return True
    return False


def _inside_predicate(col: exp.Column, stop: exp.Expression) -> bool:
    """The value is only tested (``IS NULL``, ``> range_high``) on its way up to ``stop``, not aggregated."""
    node = col.parent
    while node is not None and node is not stop:
        if isinstance(node, (exp.Predicate, exp.Select)):
            return True
        node = node.parent
    return False


def _aggregated(tree: exp.Expression) -> List[Tuple[RowPath, str]]:
    found: List[Tuple[RowPath, str]] = []
    aggregates: List[exp.Expression] = list(tree.find_all(*_AGGREGATES))
    # PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY value): the value sits in the ordering, outside the call.
    aggregates += [w for w in tree.find_all(exp.WithinGroup) if isinstance(w.this, _AGGREGATES)]
    for agg in aggregates:
        local = _unit_pins(agg, {}) + [
            p for c in agg.find_all(exp.Column) if _unit_column(c) for p in [_pin_of(c)] if p
        ]
        for col in agg.find_all(exp.Column):
            value = _value_read(col)
            if value is None or _inside_predicate(col, agg) or _covered(value[0], local):
                continue
            found.append(value)
    return found


def _thresholds(tree: exp.Expression) -> List[Tuple[RowPath, str, str]]:
    """``(row, table, predicate)`` for value columns compared with a non-zero numeric literal."""
    out: List[Tuple[RowPath, str, str]] = []
    for node in tree.find_all(*_THRESHOLD_OPS, exp.Between):
        if isinstance(node, exp.Between):
            pairs = [(node.this, [node.args.get("low"), node.args.get("high")])]
        else:
            pairs = [(node.this, [node.expression]), (node.expression, [node.this])]
        for col, bounds in pairs:
            value = _value_read(col)
            if value is None:
                continue
            literals = [number_literal(b) for b in bounds]
            if any(v is None for v in literals) or all(v == 0 for v in literals):
                continue
            condition = node.find_ancestor(exp.If)
            if condition is not None and _covered(value[0], _unit_pins(condition.this, {})):
                continue
            out.append((value[0], value[1], node.sql()))
    return out


@register
class NumericValueWithoutUnitConstraintRule(Rule):
    """Aggregating or thresholding a numeric CDM value without constraining the unit of the same row."""

    rule_id = "domain_specific.numeric_value_without_unit_constraint"
    name = "Numeric Value Without Unit Constraint"
    description = (
        "Aggregating or thresholding measurement.value_as_number, observation.value_as_number or "
        "dose_era.dose_value without constraining the same row's unit_concept_id mixes values recorded in different "
        "units (glucose in mg/dL and mmol/L, doses in mg and mcg)."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: `AND m.unit_concept_id = <unit_concept_id>` (or IN (...) of concepts for the same scale), or "
        "`AND m.unit_concept_id IS NULL` when the quantity has no unit; OR GROUP BY m.unit_concept_id so each "
        "aggregate is in one unit; OR convert explicitly with CASE WHEN m.unit_concept_id = ... THEN ... END before "
        "aggregating."
    )
    long_description = (
        "The CDM v5.4 does not prescribe a unit for individual measurement or observation concepts; the ETL maps "
        "whatever unit the source used. The same concept can therefore carry values on different scales, and an "
        "average, a percentile or a fixed numeric cut-off computed across them is meaningless. dose_era.dose_value "
        "has the same structure, with its unit in dose_era.unit_concept_id. A unit constraint counts only for the row "
        "it is written on, followed through CTEs and derived tables; `unit_concept_id IS NULL` counts, because the "
        "CDM leaves the unit NULL for a quantity without one."
    )
    example_bad = "SELECT AVG(m.value_as_number) FROM measurement m\nWHERE m.measurement_concept_id = 3004410;"
    example_good = (
        "SELECT AVG(m.value_as_number) FROM measurement m\n"
        "WHERE m.measurement_concept_id = 3004410\n"
        "  AND m.unit_concept_id = 8554;"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT DISTINCT m.person_id FROM measurement m\n"
                "WHERE m.measurement_concept_id = 3004410\n"
                "  AND m.value_as_number > 7;"
            ),
            severity=Severity.WARNING,
            note="numeric threshold on value_as_number with no unit constraint",
        ),
    )
    references = (
        cdm_table("measurement"),
        cdm_table("observation"),
        cdm_table("dose_era"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if "value_as_number" not in lowered and "dose_value" not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            aggregated = _aggregated(tree)
            thresholds = _thresholds(tree)
            if not aggregated and not thresholds:
                continue
            pins = _statement_pins(tree)
            reported: Set[str] = set()
            for row, table in aggregated:
                if _covered(row, pins) or table in reported:
                    continue
                reported.add(table)
                column = f"{table}.{VALUE_COLUMNS[table]}"
                violations.append(
                    self.create_violation(
                        message=(
                            f"Aggregation of {column} without a unit_concept_id constraint on the same row. The same "
                            "concept can be recorded in different units, so the aggregate mixes incompatible scales."
                        ),
                        details={"column": column, "shape": "aggregate"},
                    )
                )
            for row, table, predicate in thresholds:
                if _covered(row, pins) or table in reported:
                    continue
                reported.add(table)
                column = f"{table}.{VALUE_COLUMNS[table]}"
                violations.append(
                    self.create_violation(
                        message=(
                            f"Numeric threshold on {column} ({predicate}) without a unit_concept_id constraint on the "
                            "same row. The cut-off only means something in one unit; rows recorded in other units are "
                            "compared against the same number."
                        ),
                        details={"column": column, "shape": "threshold", "predicate": predicate},
                    )
                )
        return violations


__all__ = ["NumericValueWithoutUnitConstraintRule"]

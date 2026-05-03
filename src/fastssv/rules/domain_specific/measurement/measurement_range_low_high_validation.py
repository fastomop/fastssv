"""Measurement Range Low/High Validation Rule.

OMOP semantic rule CLIN_027:
Detects logically impossible range constraints where range_low > range_high.

CLIN_027 (measurement_range_low_greater_than_range_high):
If a query filters measurement records where range_low > range_high, this is
logically impossible for valid reference ranges and indicates a data quality
issue or a WHERE clause error.

The Problem:
    In the measurement table, range_low and range_high represent the normal
    reference range for a measurement. By definition, range_low must be ≤ range_high.
    A query that filters for range_low > range_high is logically impossible
    unless it's a data quality check.

    Common mistakes:
    - Direct comparison: WHERE range_low > range_high (as business logic filter)
    - Static contradictions: WHERE range_low > 100 AND range_high < 50
    - Swapped column usage in filters

Violation patterns:
    SELECT * FROM measurement
    WHERE range_low > range_high
      AND value_as_number > range_high
    -- ERROR: Filtering business data where range_low > range_high is impossible

    SELECT * FROM measurement
    WHERE range_low >= 150
      AND range_high < 100
    -- ERROR: Static bounds make it impossible (150 > 100)

Correct patterns:
    SELECT * FROM measurement
    WHERE range_low > range_high
    -- OK: This is a data quality check query (no other business logic)

    SELECT * FROM measurement
    WHERE value_as_number < range_low
       OR value_as_number > range_high
    -- OK: Detecting out-of-range values (valid business logic)

    SELECT * FROM measurement
    WHERE range_low >= 50
      AND range_high <= 200
    -- OK: Overlapping range is valid
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


TABLE_NAME = "measurement"
RANGE_LOW = "range_low"
RANGE_HIGH = "range_high"


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_in_or(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Or):
            return True
        parent = parent.parent
    return False


def _extract_numeric(node: exp.Expression) -> Optional[float]:
    if isinstance(node, exp.Neg):
        inner = node.this
        if isinstance(inner, exp.Literal) and inner.is_number:
            try:
                return -float(inner.this)
            except Exception:
                return None
        return None

    if isinstance(node, exp.Literal) and node.is_number:
        try:
            return float(node.this)
        except Exception:
            return None

    return None


class NumericBounds:
    def __init__(self):
        self.min_value: Optional[float] = None
        self.max_value: Optional[float] = None
        self.min_inclusive: bool = True
        self.max_inclusive: bool = True
        self.has_constraints = False

    def add_gt(self, value: float, inclusive: bool):
        self.has_constraints = True
        if self.min_value is None or value > self.min_value:
            self.min_value = value
            self.min_inclusive = inclusive
        elif value == self.min_value:
            self.min_inclusive = self.min_inclusive and inclusive

    def add_lt(self, value: float, inclusive: bool):
        self.has_constraints = True
        if self.max_value is None or value < self.max_value:
            self.max_value = value
            self.max_inclusive = inclusive
        elif value == self.max_value:
            self.max_inclusive = self.max_inclusive and inclusive

    def add_eq(self, value: float):
        self.has_constraints = True
        self.min_value = value
        self.max_value = value
        self.min_inclusive = True
        self.max_inclusive = True

    def impossible_with(self, other: "NumericBounds") -> bool:
        if not self.has_constraints or not other.has_constraints:
            return False

        if self.min_value is not None and other.max_value is not None:
            if self.min_value > other.max_value:
                return True
            if self.min_value == other.max_value:
                if not self.min_inclusive or not other.max_inclusive:
                    return True
        return False


def _is_measurement_column(col: exp.Column, aliases: Dict[str, str], name: str) -> bool:
    table, column = resolve_table_col(col, aliases)

    if _norm(column) != name:
        return False

    if table:
        return _norm(table) == TABLE_NAME

    return TABLE_NAME in {_norm(t) for t in aliases.values()}


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[Tuple[str, Optional[exp.Expression]]]:
    """Return list of (message, offending_node).

    ``offending_node`` is the AST node whose source span we'll target with a
    REPLACE patch (only set for the direct ``range_low > range_high``
    comparison; the static-contradiction case is left as FREEFORM since the
    fix depends on which side the analyst intended to relax).
    """
    violations: List[Tuple[str, Optional[exp.Expression]]] = []
    seen: Set[str] = set()

    bounds = {
        RANGE_LOW: NumericBounds(),
        RANGE_HIGH: NumericBounds(),
    }

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_or(node):
            continue

        # --- Column vs column ---
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            left, right = node.this, node.expression

            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                t1, c1 = resolve_table_col(left, aliases)
                t2, c2 = resolve_table_col(right, aliases)

                # Check both are from measurement (handle qualified and unqualified)
                t1_is_measurement = (
                    _norm(t1) == TABLE_NAME if t1 else TABLE_NAME in {_norm(t) for t in aliases.values()}
                )
                t2_is_measurement = (
                    _norm(t2) == TABLE_NAME if t2 else TABLE_NAME in {_norm(t) for t in aliases.values()}
                )

                if t1_is_measurement and t2_is_measurement:
                    c1n, c2n = _norm(c1), _norm(c2)

                    if (c1n == RANGE_LOW and c2n == RANGE_HIGH and isinstance(node, (exp.GT, exp.GTE))) or (
                        c1n == RANGE_HIGH and c2n == RANGE_LOW and isinstance(node, (exp.LT, exp.LTE))
                    ):
                        key = "direct_comparison"
                        if key not in seen:
                            seen.add(key)
                            violations.append(
                                (
                                    f"Comparison implies {RANGE_LOW} > {RANGE_HIGH}, which is invalid.",
                                    node,
                                )
                            )

        # --- Column vs literal ---
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                numeric_val = _extract_numeric(val_node)
                if numeric_val is None:
                    continue

                for col_name in (RANGE_LOW, RANGE_HIGH):
                    if not _is_measurement_column(col_node, aliases, col_name):
                        continue

                    if isinstance(node, exp.GT):
                        bounds[col_name].add_gt(numeric_val, False)
                    elif isinstance(node, exp.GTE):
                        bounds[col_name].add_gt(numeric_val, True)
                    elif isinstance(node, exp.LT):
                        bounds[col_name].add_lt(numeric_val, False)
                    elif isinstance(node, exp.LTE):
                        bounds[col_name].add_lt(numeric_val, True)
                    elif isinstance(node, exp.EQ):
                        bounds[col_name].add_eq(numeric_val)

        # --- BETWEEN ---
        if isinstance(node, exp.Between):
            if isinstance(node.this, exp.Column):
                low = _extract_numeric(node.args.get("low"))
                high = _extract_numeric(node.args.get("high"))

                if low is None or high is None:
                    continue

                for col_name in (RANGE_LOW, RANGE_HIGH):
                    if not _is_measurement_column(node.this, aliases, col_name):
                        continue

                    bounds[col_name].add_gt(low, True)
                    bounds[col_name].add_lt(high, True)

    if bounds[RANGE_LOW].impossible_with(bounds[RANGE_HIGH]):
        key = "static_contradiction"
        if key not in seen:
            seen.add(key)
            # Static contradictions span multiple predicates; no single
            # mechanical edit cleans them up.  Leave patch to FREEFORM.
            violations.append(
                (
                    f"Static filters imply {RANGE_LOW} > {RANGE_HIGH}, which is logically impossible.",
                    None,
                )
            )

    return violations


@register
class MeasurementRangeLowHighValidationRule(Rule):
    rule_id = "domain_specific.measurement_range_low_high_validation"
    name = "Measurement Range Low/High Validation"

    description = "Detects logically impossible constraints where range_low > range_high."

    severity = Severity.ERROR
    suggested_fix = "REPLACE: `range_low > range_high` WITH `range_low <= range_high` (or remove the predicate — range_low must be no greater than range_high by definition)."
    example_bad = "SELECT person_id FROM measurement\nWHERE range_low > range_high;"
    example_good = "SELECT person_id FROM measurement\nWHERE range_low <= range_high;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, TABLE_NAME):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg, offending_node in issues:
                # When the violation is the direct-comparison case, build a
                # REPLACE patch that flips the inequality so the predicate
                # becomes valid (range_low <= range_high). Reverse-orientation
                # GT/LT pairs are handled symmetrically.
                patch = None
                if offending_node is not None:
                    flipped_op = {
                        exp.GT: "<=",
                        exp.GTE: "<",
                        exp.LT: ">=",
                        exp.LTE: ">",
                    }.get(type(offending_node))
                    if flipped_op is not None:
                        left = offending_node.this
                        right = offending_node.expression
                        if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                            new_text = f"{left.sql()} {flipped_op} {right.sql()}"
                            span = locate(sql, offending_node.sql())
                            if span is not None:
                                patch = patch_replace(span, new_text)

                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        suggested_fix_patch=patch,
                    )
                )

        return violations


__all__ = ["MeasurementRangeLowHighValidationRule"]

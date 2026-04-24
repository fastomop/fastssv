"""Measurement Cross-Unit Comparison Rule.

OMOP semantic rule OMOP_232:
Aggregations or arithmetic operations on measurement.value_as_number across
different units produce meaningless results without unit conversion.

The Problem:
    The measurement table can store the same clinical concept (e.g., blood glucose,
    HbA1c) in different units across sites or time periods:

    Blood glucose: 5.5 (mmol/L) vs 100 (mg/dL)
    HbA1c: 7.0 (%) vs 53 (mmol/mol)

    Performing aggregations (AVG, SUM, MIN, MAX) or arithmetic operations across
    measurements with different units produces meaningless results:

    AVG(5.5 mmol/L, 100 mg/dL) = 52.75 (meaningless!)

Violation patterns:
    -- WRONG: Aggregating without constraining unit
    SELECT AVG(value_as_number) AS avg_glucose
    FROM measurement
    WHERE measurement_concept_id = 3004410
    -- Mixes mmol/L and mg/dL values!

    -- WRONG: Explicitly allowing multiple units
    SELECT person_id, AVG(value_as_number)
    FROM measurement
    WHERE measurement_concept_id = 3004410
      AND unit_concept_id IN (8753, 8840)  -- mmol/L and mg/dL
    GROUP BY person_id
    -- Intentionally mixing units!

Correct patterns:
    -- CORRECT: Constrain to single unit
    SELECT AVG(value_as_number) AS avg_glucose_mmol
    FROM measurement
    WHERE measurement_concept_id = 3004410
      AND unit_concept_id = 8753  -- mmol/L only

    -- CORRECT: Separate aggregations per unit
    SELECT
      unit_concept_id,
      AVG(value_as_number) AS avg_value
    FROM measurement
    WHERE measurement_concept_id = 3004410
    GROUP BY unit_concept_id
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

_AGGREGATION_TYPES = (exp.Avg, exp.Sum, exp.Min, exp.Max)


# --- Helpers ---------------------------------------------------------------

def _norm(val: Optional[str]) -> Optional[str]:
    return normalize_name(val) if val else None


def _has_aggregation_on_value_as_number(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    for node in tree.find_all(_AGGREGATION_TYPES):
        for col in node.find_all(exp.Column):
            table, col_name = resolve_table_col(col, aliases)

            # Skip if not value_as_number
            if _norm(col_name) != "value_as_number":
                continue

            # Accept if explicitly from measurement OR unqualified (when measurement in scope)
            if _norm(table) == "measurement":
                return True
            if not table and "measurement" in aliases.values():
                return True

    return False


def _has_unit_concept_constraint(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    """Only accept strict constraints: =, IN (single value), or GROUP BY."""

    # --- WHERE / JOIN constraints ---
    for node in list(tree.find_all(exp.EQ)) + list(tree.find_all(exp.In)):
        if isinstance(node, exp.EQ):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col, _ in pairs:
                if not isinstance(col, exp.Column):
                    continue

                table, col_name = resolve_table_col(col, aliases)

                # Check if unit_concept_id
                if _norm(col_name) != "unit_concept_id":
                    continue

                # Accept if from measurement OR unqualified (when measurement in scope)
                if _norm(table) == "measurement":
                    return True
                if not table and "measurement" in aliases.values():
                    return True

        elif isinstance(node, exp.In):
            col = node.this
            if not isinstance(col, exp.Column):
                continue

            table, col_name = resolve_table_col(col, aliases)

            # Check if unit_concept_id
            if _norm(col_name) != "unit_concept_id":
                continue

            # Check if from measurement table or unqualified
            is_measurement_col = (
                _norm(table) == "measurement" or
                (not table and "measurement" in aliases.values())
            )
            if not is_measurement_col:
                continue

            # Only safe if exactly 1 value
            values = {
                str(expr.this)
                for expr in node.expressions or []
                if isinstance(expr, exp.Literal)
            }

            if len(values) == 1:
                return True

    # --- GROUP BY ---
    for select in tree.find_all(exp.Select):
        group = select.args.get("group")
        if not group:
            continue

        for expr in group.expressions:
            for col in expr.find_all(exp.Column):
                table, col_name = resolve_table_col(col, aliases)

                # Check if unit_concept_id
                if _norm(col_name) != "unit_concept_id":
                    continue

                # Accept if from measurement OR unqualified (when measurement in scope)
                if _norm(table) == "measurement":
                    return True
                if not table and "measurement" in aliases.values():
                    return True

    return False


def _has_multiple_unit_values_in_clause(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    for node in tree.find_all(exp.In):
        if not isinstance(node.this, exp.Column):
            continue

        table, col_name = resolve_table_col(node.this, aliases)

        # Check if unit_concept_id
        if _norm(col_name) != "unit_concept_id":
            continue

        # Check if from measurement table or unqualified
        is_measurement_col = (
            _norm(table) == "measurement" or
            (not table and "measurement" in aliases.values())
        )
        if not is_measurement_col:
            continue

        values: Set[str] = set()

        for expr in node.expressions or []:
            if isinstance(expr, exp.Literal):
                values.add(str(expr.this))

        if len(values) > 1:
            return True

    return False


# --- Rule ------------------------------------------------------------------

@register
class MeasurementCrossUnitComparisonRule(Rule):
    """Detect aggregations on measurement.value_as_number across different units."""

    rule_id = "domain_specific.measurement_cross_unit_comparison"
    name = "Measurement Cross-Unit Comparison"

    description = (
        "Aggregating measurement.value_as_number without constraining unit_concept_id "
        "mixes incompatible units and produces meaningless results."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Add unit_concept_id constraint (e.g., = <unit>) or group by unit. "
        "Alternatively, convert values to a common unit before aggregation."
    )

    example_bad = (
        "SELECT AVG(m.value_as_number) FROM measurement m\n"
        "WHERE m.measurement_concept_id = 3004249;"
    )
    example_good = (
        "SELECT AVG(m.value_as_number) FROM measurement m\n"
        "WHERE m.measurement_concept_id = 3004249\n"
        "  AND m.unit_concept_id = 8876;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, "measurement"):
                continue

            aliases = extract_aliases(tree)

            if not _has_aggregation_on_value_as_number(tree, aliases):
                continue

            # Case 2: explicit multi-unit IN (check this first - more specific)
            if _has_multiple_unit_values_in_clause(tree, aliases):
                key = "multi_unit_in"
                if key not in seen:
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                "Aggregation with unit_concept_id IN (...) containing "
                                "multiple units mixes incompatible units."
                            ),
                            details={
                                "column": "measurement.value_as_number",
                                "violation_type": "explicit_multi_unit",
                            },
                        )
                    )
                continue

            # Case 1: no valid constraint
            if not _has_unit_concept_constraint(tree, aliases):
                key = "no_unit_constraint"
                if key not in seen:
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                "Aggregation on measurement.value_as_number without "
                                "unit_concept_id constraint mixes incompatible units."
                            ),
                            details={
                                "column": "measurement.value_as_number",
                                "violation_type": "missing_unit_constraint",
                            },
                        )
                    )

        return violations


__all__ = ["MeasurementCrossUnitComparisonRule"]

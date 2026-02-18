"""Measurement Unit Validation Rule.

OMOP semantic rule:
When a query filters measurement.value_as_number against a numeric threshold,
it must also constrain unit_concept_id.

The measurement table stores numeric results alongside their units. The same
clinical concept (e.g. blood glucose, HbA1c) can be recorded in different
units across source systems and ETL pipelines:

    Blood glucose: 5.5 (mmol/L) vs 100 (mg/dL)
    HbA1c: 7.0 (%) vs 53 (mmol/mol)

Filtering on a numeric threshold without specifying the unit means both
representations are tested against the same cutoff, silently including or
excluding patients based on which unit convention was used at their site.

Correct pattern:
    WHERE m.value_as_number > 7.0
      AND m.unit_concept_id = 8554  -- % (UCUM)
"""

from typing import Dict, List, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register

# Comparison operators that indicate a numeric threshold filter
_NUMERIC_COMPARISON_TYPES = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)


def _find_value_as_number_threshold(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    """Return True if query compares value_as_number against a numeric literal.

    Handles both aliased (m.value_as_number > 7) and unqualified
    (value_as_number > 7) column references, and both comparison directions.
    """
    for node in tree.find_all(_NUMERIC_COMPARISON_TYPES):
        if not is_in_where_or_join_clause(node):
            continue

        left, right = node.left, node.right

        # Check both orientations: col OP literal and literal OP col
        for col_side, val_side in ((left, right), (right, left)):
            if not isinstance(col_side, exp.Column):
                continue

            # Accept numeric literals and negated literals (e.g. -1.5)
            if not isinstance(val_side, (exp.Literal, exp.Neg)):
                continue

            # Confirm the literal side is numeric (not a string)
            literal = val_side if isinstance(val_side, exp.Literal) else val_side.this
            if not isinstance(literal, exp.Literal) or not literal.is_number:
                continue

            table, col = resolve_table_col(col_side, aliases)
            if normalize_name(col) != "value_as_number":
                continue

            # Accept if column is unqualified (only measurement in scope)
            # or explicitly from measurement
            if table is None or normalize_name(table) == "measurement":
                return True

    return False


def _has_unit_concept_constraint(tree: exp.Expression) -> bool:
    """Return True if unit_concept_id is referenced anywhere in the query."""
    for col in tree.find_all(exp.Column):
        if normalize_name(col.name) == "unit_concept_id":
            return True
    return False


@register
class MeasurementUnitValidationRule(Rule):
    """Detects numeric measurement threshold filters missing a unit_concept_id constraint."""

    rule_id = "semantic.measurement_unit_validation"
    name = "Measurement Unit Validation"
    description = (
        "Detects queries that filter measurement.value_as_number against a numeric "
        "threshold without also constraining unit_concept_id. The same measurement "
        "concept can be stored in different units across sites (e.g. glucose in "
        "mmol/L vs mg/dL). A numeric threshold applied without a unit filter silently "
        "mixes patients measured in different unit conventions."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Add a unit_concept_id constraint alongside the numeric threshold: "
        "AND m.unit_concept_id = <unit_concept_id>. "
        "Look up the correct UCUM unit concept ID in the OMOP vocabulary "
        "(e.g. SELECT concept_id FROM concept WHERE concept_code = '%' "
        "AND vocabulary_id = 'UCUM')."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if tree is None:
                continue

            # Only examine queries that reference the measurement table
            if not uses_table(tree, "measurement"):
                continue

            aliases = extract_aliases(tree)

            if not _find_value_as_number_threshold(tree, aliases):
                continue

            if _has_unit_concept_constraint(tree):
                continue

            violations.append(self.create_violation(
                message=(
                    "Query filters measurement.value_as_number against a numeric "
                    "threshold without constraining unit_concept_id. The same "
                    "measurement concept can be stored in different units across "
                    "sites (e.g. glucose: 5.5 mmol/L vs 100 mg/dL), making the "
                    "numeric threshold unreliable without a unit filter."
                ),
                details={
                    "column": "measurement.value_as_number",
                    "missing": "unit_concept_id constraint",
                },
            ))

        return violations


__all__ = ["MeasurementUnitValidationRule"]

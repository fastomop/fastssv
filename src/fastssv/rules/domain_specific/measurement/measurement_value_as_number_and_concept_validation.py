"""Measurement Value As Number and Concept Validation Rule.

OMOP semantic rule CLIN_028:
Detects when both value_as_number and value_as_concept_id are filtered with AND.

CLIN_028 (measurement_value_as_number_and_concept_exclusive):
In measurement, value_as_number (quantitative) and value_as_concept_id (qualitative)
often represent the same result in different forms. However, they CAN legitimately
coexist (e.g., a lab with both a numeric titer and a qualitative interpretation).
Filtering both with AND is usually overly restrictive and may return no records.

The Problem:
    value_as_number stores quantitative results (e.g., 6.5 mg/dL)
    value_as_concept_id stores qualitative results (e.g., "Positive", "Negative")

    Both CAN be populated simultaneously for the same measurement, but they typically
    represent different aspects of the result. Filtering both with AND is usually
    a logic error that will be overly restrictive.

    Common mistakes:
    - Filtering both columns with AND: WHERE value_as_number > 6.5 AND value_as_concept_id = 45884084
    - Not understanding that measurements usually have EITHER a numeric OR concept value
    - Overly restrictive filters that return no results

Violation patterns:
    SELECT * FROM measurement
    WHERE value_as_number > 6.5
      AND value_as_concept_id = 45884084
    -- WARNING: Both columns filtered with AND (overly restrictive)

    SELECT * FROM measurement
    WHERE value_as_number BETWEEN 5.0 AND 10.0
      AND value_as_concept_id IN (45884084, 45878583)
    -- WARNING: Both columns filtered with AND

Correct patterns:
    SELECT * FROM measurement
    WHERE value_as_number > 6.5
       OR value_as_concept_id = 45884084
    -- OK: Using OR to check either quantitative or qualitative

    SELECT * FROM measurement
    WHERE value_as_number IS NOT NULL
      AND value_as_concept_id IS NOT NULL
    -- OK: Just checking both are populated (not business logic)

    SELECT * FROM measurement
    WHERE value_as_number > 6.5
    -- OK: Only filtering one column
"""

from typing import Dict, List, Optional, Set

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


TABLE_NAME = "measurement"
VALUE_AS_NUMBER = "value_as_number"
VALUE_AS_CONCEPT_ID = "value_as_concept_id"


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_measurement_column(col: exp.Column, aliases: Dict[str, str], col_name: str) -> bool:
    table, column = resolve_table_col(col, aliases)

    if _norm(column) != col_name:
        return False

    if table:
        return _norm(table) == TABLE_NAME

    return TABLE_NAME in {_norm(t) for t in aliases.values()}


def _has_business_constraint(expr: exp.Expression, aliases: Dict[str, str], col_name: str) -> bool:
    """
    Detect non-null business logic constraints on a column.
    """
    for node in expr.walk():
        if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.In, exp.Between)):

            if isinstance(node, exp.Between):
                if isinstance(node.this, exp.Column):
                    if _is_measurement_column(node.this, aliases, col_name):
                        return True

            else:
                # Binary ops
                for candidate in [node.this, getattr(node, "expression", None)]:
                    if isinstance(candidate, exp.Column):
                        if _is_measurement_column(candidate, aliases, col_name):
                            return True

    return False


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    violations: List[str] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if not isinstance(node, exp.And):
            continue

        left = node.this
        right = node.expression

        left_has_number = _has_business_constraint(left, aliases, VALUE_AS_NUMBER)
        left_has_concept = _has_business_constraint(left, aliases, VALUE_AS_CONCEPT_ID)

        right_has_number = _has_business_constraint(right, aliases, VALUE_AS_NUMBER)
        right_has_concept = _has_business_constraint(right, aliases, VALUE_AS_CONCEPT_ID)

        # Require constraints on opposite sides of AND
        if (
            (left_has_number and right_has_concept) or
            (left_has_concept and right_has_number)
        ):
            key = "value_number_and_concept"
            if key in seen:
                continue
            seen.add(key)

            violations.append(
                f"Filters on both {VALUE_AS_NUMBER} and {VALUE_AS_CONCEPT_ID} are combined with AND. "
                f"This may be overly restrictive or semantically inconsistent. "
                f"Consider whether OR or separate logic is more appropriate."
            )

    return violations


@register
class MeasurementValueAsNumberAndConceptValidationRule(Rule):
    """Validate inconsistent filtering of measurement value representations."""

    rule_id = "domain_specific.measurement_value_as_number_and_concept_validation"
    name = "Measurement Value Representation Consistency"

    description = (
        "Detects when value_as_number and value_as_concept_id are both filtered with AND, "
        "which may indicate inconsistent use of quantitative and qualitative representations."
    )

    severity = Severity.WARNING
    suggested_fix = "Use OR or separate logic depending on measurement type"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not uses_table(tree, TABLE_NAME):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for message in issues:
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        details={
                            "table": TABLE_NAME,
                            "value_as_number": VALUE_AS_NUMBER,
                            "value_as_concept_id": VALUE_AS_CONCEPT_ID,
                        },
                    )
                )

        return violations


__all__ = ["MeasurementValueAsNumberAndConceptValidationRule"]
"""Integer Division Precision Loss Rule.

OMOP semantic rule:
When performing division operations in analytical queries, integer division
can cause precision loss. For example, dividing by 365 to convert days to years
should use decimal/float division to preserve fractional years.
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register


def _is_likely_integer_literal(node: exp.Expression) -> bool:
    """Check if node is likely an integer literal (not a decimal)."""
    if isinstance(node, exp.Literal):
        # Check if it's an integer (no decimal point)
        value = str(node.this)
        return value.isdigit() or (value.startswith('-') and value[1:].isdigit())
    return False


def _has_explicit_cast_to_decimal(node: exp.Expression) -> bool:
    """Check if the division is wrapped in a CAST to DECIMAL/FLOAT/NUMERIC."""
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Cast):
            # Check if casting to a decimal type
            cast_to = parent.to
            if cast_to:
                type_str = str(cast_to).upper()
                if any(t in type_str for t in ['DECIMAL', 'FLOAT', 'NUMERIC', 'DOUBLE', 'REAL']):
                    return True
        parent = getattr(parent, 'parent', None)
    return False


def _check_division_operands(div: exp.Div) -> bool:
    """Check if any operand has explicit decimal casting."""
    # Check if left operand is cast to decimal
    left = div.left
    if isinstance(left, exp.Cast):
        cast_to = left.to
        if cast_to:
            type_str = str(cast_to).upper()
            if any(t in type_str for t in ['DECIMAL', 'FLOAT', 'NUMERIC', 'DOUBLE', 'REAL']):
                return True

    # Check if right operand is cast to decimal
    right = div.right
    if isinstance(right, exp.Cast):
        cast_to = right.to
        if cast_to:
            type_str = str(cast_to).upper()
            if any(t in type_str for t in ['DECIMAL', 'FLOAT', 'NUMERIC', 'DOUBLE', 'REAL']):
                return True

    # Check if any operand is a decimal literal (has decimal point)
    if isinstance(right, exp.Literal):
        value = str(right.this)
        if '.' in value:
            return True

    return False


@register
class IntegerDivisionPrecisionLossRule(Rule):
    """Warns about potential precision loss from integer division."""

    rule_id = "analytics.integer_division_precision_loss"
    name = "Integer Division Precision Loss"
    description = (
        "Warns when division operations may lose precision due to integer arithmetic. "
        "Common in date calculations like dividing days by 365 for years."
    )
    severity = Severity.WARNING
    suggested_fix = "Cast at least one operand to DECIMAL or use decimal literals (e.g., 365.0 instead of 365)"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if tree is None:
                continue

            # Track reported divisions to avoid duplicates
            # Key: (left_expr_normalized, divisor_value)
            reported = set()

            # Find all division operations
            for div in tree.find_all(exp.Div):
                right = div.right

                # Check if dividing by an integer literal (common case: /365, /12, etc.)
                if _is_likely_integer_literal(right):
                    # Check if there's explicit decimal casting
                    has_decimal_cast = (
                        _has_explicit_cast_to_decimal(div) or
                        _check_division_operands(div)
                    )

                    if not has_decimal_cast:
                        divisor_value = str(right.this) if isinstance(right, exp.Literal) else str(right)

                        # Normalize left expression for deduplication
                        # Remove whitespace and lowercase for comparison
                        left_expr = str(div.left).replace(" ", "").lower()
                        dedup_key = (left_expr, divisor_value)

                        if dedup_key in reported:
                            continue
                        reported.add(dedup_key)

                        # Check if this is a date-related division (365 for years, 12 for months, etc.)
                        is_date_division = divisor_value in ['365', '366', '12', '7', '30']

                        if is_date_division:
                            # For date arithmetic, suggest semantic alternatives
                            if divisor_value in ['365', '366']:
                                message = (
                                    f"Division by {divisor_value} for year calculation may lose precision and "
                                    f"doesn't account for leap years properly."
                                )
                                suggested_fix = (
                                    f"Use date functions like AGE() or EXTRACT(YEAR FROM ...) for accurate "
                                    f"year calculations, or use {divisor_value}.0 for decimal division"
                                )
                            elif divisor_value == '12':
                                message = (
                                    f"Division by 12 may be for month calculation and could lose precision."
                                )
                                suggested_fix = (
                                    f"Use EXTRACT(MONTH FROM ...) for month calculations, or use 12.0 for decimal division"
                                )
                            else:
                                message = (
                                    f"Division by integer literal ({divisor_value}) may cause precision loss."
                                )
                                suggested_fix = (
                                    f"Change {divisor_value} to {divisor_value}.0 or CAST(... AS DECIMAL) / {divisor_value}"
                                )
                        else:
                            message = (
                                f"Division by integer literal ({divisor_value}) may cause precision loss. "
                                f"Use CAST to DECIMAL or decimal literal (e.g., {divisor_value}.0) to preserve precision."
                            )
                            suggested_fix = (
                                f"Change {divisor_value} to {divisor_value}.0 or "
                                f"CAST(... AS DECIMAL) / {divisor_value}"
                            )

                        violations.append(self.create_violation(
                            message=message,
                            suggested_fix=suggested_fix,
                            details={
                                "divisor": divisor_value,
                                "is_date_calculation": is_date_division,
                            }
                        ))

        return violations


__all__ = ["IntegerDivisionPrecisionLossRule"]

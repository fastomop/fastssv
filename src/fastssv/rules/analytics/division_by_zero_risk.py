"""Division by Zero Risk Rule.

OMOP semantic rule:
Warns about potential division by zero in analytical queries.
Common cases include:
- Dividing by window function results (e.g., SUM(...) OVER())
- Dividing by aggregate functions (COUNT, SUM) without safeguards
- Dividing by columns that may be zero
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register


def _is_potentially_zero_expression(node: exp.Expression) -> bool:
    """Check if an expression could evaluate to zero.

    Returns True for:
    - Window functions (especially aggregates like SUM() OVER())
    - Aggregate functions (COUNT, SUM, etc.)
    - Column references (could be zero)
    """
    # Window functions
    if isinstance(node, exp.Window):
        return True

    # Aggregate functions
    if isinstance(node, (exp.Sum, exp.Count, exp.Avg)):
        return True

    # Column references (could be zero)
    if isinstance(node, exp.Column):
        return True

    # Check if wrapped in parentheses
    if isinstance(node, exp.Paren):
        return _is_potentially_zero_expression(node.this)

    return False


def _has_null_or_zero_guard(div: exp.Div) -> bool:
    """Check if division has NULL or zero guards.

    Common patterns:
    - NULLIF(denominator, 0)
    - CASE WHEN denominator = 0 THEN ... ELSE ... END
    - COALESCE with a non-zero default
    """
    divisor = div.right

    # Check for NULLIF
    if isinstance(divisor, exp.Nullif):
        # NULLIF(x, 0) is a guard
        if len(divisor.expressions) > 0:
            second_arg = divisor.expressions[0]
            if isinstance(second_arg, exp.Literal) and str(second_arg.this) == '0':
                return True

    # Check for CASE statement with zero check
    if isinstance(divisor, exp.Case):
        # Look for any WHEN condition that checks for zero
        for when_clause in divisor.args.get('ifs', []):
            if isinstance(when_clause, exp.If):
                condition = when_clause.this
                # Check if condition involves = 0 or IS NULL
                condition_str = str(condition).lower()
                if '= 0' in condition_str or 'is null' in condition_str or '= null' in condition_str:
                    return True

    # Check for COALESCE with non-zero default
    if isinstance(divisor, exp.Coalesce):
        # Check last argument (default value)
        expressions = divisor.expressions or []
        if expressions:
            last_expr = expressions[-1]
            if isinstance(last_expr, exp.Literal):
                try:
                    value = float(last_expr.this)
                    if value != 0:
                        return True
                except (ValueError, TypeError):
                    pass

    return False


@register
class DivisionByZeroRiskRule(Rule):
    """Warns about potential division by zero in analytical queries."""

    rule_id = "analytics.division_by_zero_risk"
    name = "Division by Zero Risk"
    description = (
        "Warns when division operations may fail due to zero denominators. "
        "Common with window functions and aggregates that could return zero."
    )
    severity = Severity.WARNING
    suggested_fix = "Use NULLIF(denominator, 0) or add WHERE clause to exclude zero values"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if tree is None:
                continue

            # Find all division operations
            for div in tree.find_all(exp.Div):
                divisor = div.right

                # Check if divisor could be zero
                if _is_potentially_zero_expression(divisor):
                    # Check if there's a guard against zero/NULL
                    if not _has_null_or_zero_guard(div):
                        divisor_str = str(divisor).strip()

                        # Determine the type of risky expression
                        # Look inside Paren expressions
                        inner_divisor = divisor
                        if isinstance(divisor, exp.Paren):
                            inner_divisor = divisor.this

                        risk_type = "expression"
                        if isinstance(inner_divisor, exp.Window):
                            risk_type = "window function"
                        elif isinstance(inner_divisor, (exp.Sum, exp.Count, exp.Avg)):
                            risk_type = "aggregate function"
                        elif isinstance(inner_divisor, exp.Column):
                            risk_type = "column"

                        # Truncate long expressions
                        if len(divisor_str) > 60:
                            divisor_str = divisor_str[:57] + "..."

                        message = (
                            f"Division by {risk_type} '{divisor_str}' may fail if it evaluates to zero. "
                            f"Add NULL/zero guards to prevent runtime errors."
                        )

                        violations.append(self.create_violation(
                            message=message,
                            suggested_fix="Use NULLIF(denominator, 0) or add WHERE/CASE to handle zero values",
                            details={
                                "divisor_type": risk_type,
                            }
                        ))

        return violations


__all__ = ["DivisionByZeroRiskRule"]

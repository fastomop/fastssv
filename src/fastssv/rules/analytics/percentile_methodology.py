"""Percentile Methodology Rule.

OMOP semantic rule:
Custom percentile calculations using window functions and CASE statements
may not match standard statistical definitions. Most modern databases provide
native percentile functions that are more accurate and efficient.
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register


def _has_custom_percentile_calculation(tree: exp.Expression) -> bool:
    """Detect custom percentile calculations.

    Looks for patterns commonly used in manual percentile calculations:
    - Window functions with ROWS UNBOUNDED PRECEDING
    - CASE statements with percentile logic
    - FLOOR/CEILING with division patterns
    - MAX(CASE WHEN (percentile = ...) THEN ...)
    """
    # Check for window functions with ROWS UNBOUNDED PRECEDING (common in percentile calcs)
    has_window_with_rows = False
    for window in tree.find_all(exp.Window):
        spec = window.args.get("spec")
        if spec:
            # Check for ROWS UNBOUNDED PRECEDING pattern
            if "rows" in str(spec).lower() and "unbounded" in str(spec).lower():
                has_window_with_rows = True
                break

    # Check for CASE with percentile-related logic
    has_percentile_case = False
    for case in tree.find_all(exp.Case):
        case_str = str(case).lower()
        if "percentile" in case_str:
            has_percentile_case = True
            break

    # Check for FLOOR/CEILING with division (common in percentile bucket logic)
    has_floor_division = False
    for floor_fn in tree.find_all(exp.Floor):
        # Check if it contains division
        for div in floor_fn.find_all(exp.Div):
            has_floor_division = True
            break

    # Also check for CEIL function
    for ceil_fn in tree.find_all(exp.Ceil):
        # Check if it contains division
        for div in ceil_fn.find_all(exp.Div):
            has_floor_division = True
            break

    # Custom percentile calculation typically has all three patterns
    return has_window_with_rows and has_percentile_case and has_floor_division


def _has_native_percentile_function(tree: exp.Expression) -> bool:
    """Check if query uses native percentile functions.

    Common native percentile functions:
    - PERCENTILE_CONT
    - PERCENTILE_DISC
    - APPROX_PERCENTILE
    - MEDIAN (in some databases)
    """
    for func in tree.find_all(exp.Anonymous):
        func_name = func.name.lower() if hasattr(func, 'name') else ''
        if 'percentile' in func_name or func_name == 'median':
            return True

    return False


@register
class PercentileMethodologyRule(Rule):
    """Warns about custom percentile calculations that may not match standard definitions."""

    rule_id = "analytics.percentile_methodology"
    name = "Percentile Methodology"
    description = (
        "Warns when custom percentile calculations are used instead of "
        "native database percentile functions"
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Use database-native percentile functions if available "
        "(e.g., PERCENTILE_CONT, APPROX_PERCENTILE)"
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

            # Check if query has custom percentile calculation
            if _has_custom_percentile_calculation(tree):
                # Only warn if not using native functions
                if not _has_native_percentile_function(tree):
                    message = (
                        "Custom percentile calculation may not match standard statistical definitions."
                    )

                    violations.append(self.create_violation(
                        message=message,
                        suggested_fix=(
                            "Use database-native percentile functions if available "
                            "(e.g., PERCENTILE_CONT, APPROX_PERCENTILE)."
                        ),
                        details={}
                    ))

        return violations


__all__ = ["PercentileMethodologyRule"]

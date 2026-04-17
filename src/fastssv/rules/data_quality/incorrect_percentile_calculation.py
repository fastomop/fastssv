"""Incorrect Percentile Calculation Rule.

Detects common mistakes in percentile calculations:
- Using ROW_NUMBER() with max_value comparisons instead of proper percentile functions
- Incorrect threshold logic (e.g., order_nr < .25 * population_size)
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


def _has_incorrect_percentile_pattern(tree: exp.Expression) -> List[str]:
    """Detect incorrect percentile calculation patterns.

    Returns list of issue descriptions found.
    """
    issues = []

    # Pattern 1: ROW_NUMBER/DENSE_RANK in same query with threshold
    has_rank_function = False
    has_threshold_column = False
    has_suspicious_case_when = False

    # Check for ROW_NUMBER, DENSE_RANK, RANK anywhere in query (including CTEs)
    for func in tree.find_all(exp.Anonymous):
        func_name = ""
        if hasattr(func, 'name'):
            func_name = normalize_name(func.name)
        elif hasattr(func, 'this') and isinstance(func.this, str):
            func_name = normalize_name(func.this)

        if func_name in {'row_number', 'dense_rank', 'rank'}:
            has_rank_function = True

    # Check for order_nr, max_value, population_size columns
    for col in tree.find_all(exp.Column):
        col_name = normalize_name(col.name)
        if col_name in {'order_nr', 'max_value', 'population_size'}:
            has_threshold_column = True

    # Check for CASE WHEN order_nr < .XX * max_value pattern
    for case in tree.find_all(exp.Case):
        case_sql = case.sql().lower()
        # Look for patterns like "order_nr < .25 * max_value" or "order_nr < .50 * population_size"
        if 'order_nr' in case_sql and any(threshold in case_sql for threshold in ['.25', '.50', '.75', '0.25', '0.50', '0.75']):
            if any(col in case_sql for col in ['max_value', 'population_size']):
                has_suspicious_case_when = True

    # Trigger if we have the suspicious CASE pattern with order_nr and max_value/population_size
    # Even if DENSE_RANK is in a CTE, the presence of order_nr + max_value + percentile thresholds is a red flag
    if has_threshold_column and has_suspicious_case_when:
        issues.append(
            "Incorrect percentile calculation detected: using 'order_nr < .25 * max_value' logic. "
            "This is statistically incorrect (should use <= with population_size, not max_value). "
            "Use NTILE(4) for quartiles or PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY column) "
            "for accurate percentiles."
        )

    return issues


@register
class IncorrectPercentileCalculationRule(Rule):
    """Detects incorrect or error-prone percentile calculations."""

    rule_id = "data_quality.incorrect_percentile_calculation"
    name = "Incorrect Percentile Calculation"
    description = (
        "Detects manual percentile calculations using ROW_NUMBER() with incorrect threshold logic "
        "(e.g., < instead of <=, max_value instead of population_size). "
        "While syntactically valid SQL, this produces statistically incorrect results. "
        "Use NTILE() or PERCENTILE_CONT() for accurate percentiles."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Use NTILE(4) for quartiles, or PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY column) for medians. "
        "Avoid manual calculations like: CASE WHEN order_nr < .25 * max_value"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            issues = _has_incorrect_percentile_pattern(tree)

            for issue in issues:
                violations.append(self.create_violation(
                    message=issue,
                    details={
                        "pattern": "manual_percentile_with_row_number",
                    }
                ))

        return violations


__all__ = ["IncorrectPercentileCalculationRule"]

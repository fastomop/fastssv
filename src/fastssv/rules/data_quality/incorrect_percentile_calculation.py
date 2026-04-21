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


def _has_incorrect_percentile_pattern(tree: exp.Expression) -> List[tuple]:
    """Detect incorrect percentile calculation patterns.

    Returns list of (issue_description, severity) tuples.
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

    # NEW: Check for duplicate threshold bug (all percentiles using same value)
    percentile_case_statements = []
    for select in tree.find_all(exp.Select):
        for expr in select.expressions or []:
            alias = None

            if isinstance(expr, exp.Alias):
                alias = normalize_name(expr.alias)
                # Look for CASE expressions anywhere in the aliased expression
                # (could be wrapped in MIN, MAX, etc.)
                for case_expr in expr.find_all(exp.Case):
                    case_sql = case_expr.sql().lower()
                    if 'order_nr' in case_sql and any(col in case_sql for col in ['max_value', 'population_size']):
                        # Extract the threshold value
                        import re
                        threshold_match = re.search(r'order_nr\s*<\s*([.0-9]+)\s*\*', case_sql)
                        if threshold_match:
                            threshold = threshold_match.group(1)
                            percentile_case_statements.append((alias or 'unknown', threshold, case_sql))
                            break  # Only process first CASE per alias

    # Check if multiple percentile columns use the SAME threshold
    has_critical_threshold_bug = False
    if len(percentile_case_statements) >= 2:
        thresholds = [t for _, t, _ in percentile_case_statements]
        aliases = [a for a, _, _ in percentile_case_statements]

        # If all thresholds are identical but aliases suggest different percentiles
        if len(set(thresholds)) == 1 and len(set(aliases)) > 1:
            # Check if aliases suggest different percentiles
            # Look for patterns like: percentile_25, percentile_75, median, p25, p75, etc.
            percentile_alias_patterns = []
            for alias in aliases:
                alias_lower = str(alias).lower()
                if any(marker in alias_lower for marker in ['25', 'quartile', 'p25']):
                    percentile_alias_patterns.append('25th')
                elif any(marker in alias_lower for marker in ['50', 'median', 'p50']):
                    percentile_alias_patterns.append('50th')
                elif any(marker in alias_lower for marker in ['75', 'p75']):
                    percentile_alias_patterns.append('75th')
                elif 'percentile' in alias_lower:
                    percentile_alias_patterns.append('percentile')

            # If we have at least 2 different percentile indicators, this is the critical bug
            if len(set(percentile_alias_patterns)) >= 2:
                has_critical_threshold_bug = True
                issues.append((
                    f"CRITICAL BUG: All percentile columns ({', '.join(aliases)}) use identical threshold ({thresholds[0]}). "
                    f"This will produce identical values at runtime, corrupting analytical results. "
                    f"Each percentile must use a different threshold: "
                    f"percentile_25 should use 0.25, median should use 0.50, percentile_75 should use 0.75.",
                    Severity.ERROR
                ))

    # Check for CASE WHEN order_nr < .XX * max_value pattern
    for case in tree.find_all(exp.Case):
        case_sql = case.sql().lower()
        # Look for patterns like "order_nr < .25 * max_value" or "order_nr < .50 * population_size"
        if 'order_nr' in case_sql and any(threshold in case_sql for threshold in ['.25', '.50', '.75', '0.25', '0.50', '0.75']):
            if any(col in case_sql for col in ['max_value', 'population_size']):
                has_suspicious_case_when = True

    # Trigger if we have the suspicious CASE pattern with order_nr and max_value/population_size
    # Even if DENSE_RANK is in a CTE, the presence of order_nr + max_value + percentile thresholds is a red flag
    # BUT: Don't add generic warning if we've already flagged the critical threshold bug
    if has_threshold_column and has_suspicious_case_when and not has_critical_threshold_bug:
        issues.append((
            "Incorrect percentile calculation detected: using 'order_nr < .25 * max_value' logic. "
            "This is statistically incorrect (should use <= with population_size, not max_value). "
            "Use NTILE(4) for quartiles or PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY column) "
            "for accurate percentiles.",
            Severity.ERROR  # Changed from WARNING to ERROR - produces incorrect results
        ))

    return issues


@register
class IncorrectPercentileCalculationRule(Rule):
    """Detects incorrect or error-prone percentile calculations."""

    rule_id = "data_quality.incorrect_percentile_calculation"
    name = "Incorrect Percentile Calculation"
    description = (
        "Detects manual percentile calculations using ROW_NUMBER() with incorrect threshold logic "
        "(e.g., < instead of <=, max_value instead of population_size). "
        "This produces statistically incorrect results and should be treated as an error. "
        "Use NTILE() or PERCENTILE_CONT() for accurate percentiles."
    )
    severity = Severity.ERROR  # Changed from WARNING - produces incorrect results
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

            for issue, severity in issues:
                violations.append(self.create_violation(
                    message=issue,
                    severity=severity,
                    details={
                        "pattern": "manual_percentile_with_row_number",
                    }
                ))

        return violations


__all__ = ["IncorrectPercentileCalculationRule"]

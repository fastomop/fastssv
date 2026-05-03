"""Incorrect Percentile Calculation Rule.

Detects the copy-paste bug where multiple percentile-named columns
(percentile_25, median, percentile_75) reuse the same threshold value,
silently producing identical results across all three columns.

Generic manual percentile calculations using ROW_NUMBER() + CASE WHEN
with different thresholds per column (e.g., the OHDSI Achilles quartile
idiom) are intentionally NOT flagged — statistically imprecise but
widely used and not a silent failure.
"""

import re
from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


def _has_incorrect_percentile_pattern(tree: exp.Expression) -> List[tuple]:
    """Detect the identical-threshold percentile bug.

    Returns list of (issue_description, severity) tuples.
    """
    issues = []

    # Collect (alias, threshold) for each aliased CASE expression that looks
    # like a manual percentile calculation using order_nr + population_size
    # / max_value.
    percentile_case_statements = []
    for select in tree.find_all(exp.Select):
        for expr in select.expressions or []:
            if not isinstance(expr, exp.Alias):
                continue
            alias = normalize_name(expr.alias)
            for case_expr in expr.find_all(exp.Case):
                case_sql = case_expr.sql().lower()
                if "order_nr" not in case_sql:
                    continue
                if not any(col in case_sql for col in ["max_value", "population_size"]):
                    continue
                threshold_match = re.search(r"order_nr\s*<\s*([.0-9]+)\s*\*", case_sql)
                if threshold_match:
                    threshold = threshold_match.group(1)
                    percentile_case_statements.append((alias, threshold))
                    break  # Only process first CASE per alias

    if len(percentile_case_statements) < 2:
        return issues

    thresholds = [t for _, t in percentile_case_statements]
    aliases = [a for a, _ in percentile_case_statements]

    # All aliases must share the exact same threshold AND the aliases must
    # suggest they were intended to represent different percentiles.
    if len(set(thresholds)) != 1 or len(set(aliases)) < 2:
        return issues

    percentile_alias_patterns = set()
    for alias in aliases:
        alias_lower = str(alias).lower()
        if any(marker in alias_lower for marker in ["25", "quartile", "p25"]):
            percentile_alias_patterns.add("25th")
        elif any(marker in alias_lower for marker in ["50", "median", "p50"]):
            percentile_alias_patterns.add("50th")
        elif any(marker in alias_lower for marker in ["75", "p75"]):
            percentile_alias_patterns.add("75th")
        elif "percentile" in alias_lower:
            percentile_alias_patterns.add("percentile")

    if len(percentile_alias_patterns) < 2:
        return issues

    issues.append(
        (
            f"CRITICAL BUG: All percentile columns ({', '.join(aliases)}) use identical threshold ({thresholds[0]}). "
            f"This will produce identical values at runtime, corrupting analytical results. "
            f"Each percentile must use a different threshold: "
            f"percentile_25 should use 0.25, median should use 0.50, percentile_75 should use 0.75.",
            Severity.ERROR,
        )
    )

    return issues


@register
class IncorrectPercentileCalculationRule(Rule):
    """Detects incorrect or error-prone percentile calculations."""

    rule_id = "data_quality.incorrect_percentile_calculation"
    name = "Incorrect Percentile Calculation"
    description = (
        "Detects the copy-paste bug where percentile_25, median, and percentile_75 "
        "(or similarly-named percentile columns) all reuse the same threshold value in a "
        "CASE WHEN order_nr < threshold * population_size pattern, silently producing "
        "identical results across all percentile columns."
    )
    severity = Severity.ERROR
    suggested_fix = "REPLACE: hard-coded percentile thresholds WITH PERCENTILE_CONT(0.25) / PERCENTILE_CONT(0.5) / PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY <value_col>) — the SQL standard percentile aggregate."
    long_description = (
        "Hand-rolled percentile calculations using ROW_NUMBER() + "
        "population_size + CASE WHEN are easy to copy-paste and easy to "
        "get wrong: the classic bug is percentile_25, median, and "
        "percentile_75 all re-using the 0.25 threshold, so three columns "
        "report the same number. If you spot three aliased percentile "
        "columns sharing a threshold, either set them to 0.25/0.50/0.75 "
        "respectively or replace the manual logic with NTILE(4) or "
        "PERCENTILE_CONT() from the SQL standard."
    )
    example_bad = (
        "SELECT\n"
        "  MAX(CASE WHEN order_nr < 25 * population_size THEN value END) AS percentile_25,\n"
        "  MAX(CASE WHEN order_nr < 25 * population_size THEN value END) AS median,\n"
        "  MAX(CASE WHEN order_nr < 25 * population_size THEN value END) AS percentile_75\n"
        "FROM (\n"
        "  SELECT value,\n"
        "         ROW_NUMBER() OVER (ORDER BY value) AS order_nr,\n"
        "         COUNT(*) OVER () AS population_size\n"
        "  FROM measurement\n"
        ") t;"
    )
    example_good = (
        "SELECT\n"
        "  MAX(CASE WHEN order_nr < 0.25 * population_size THEN value END) AS percentile_25,\n"
        "  MAX(CASE WHEN order_nr < 0.50 * population_size THEN value END) AS median,\n"
        "  MAX(CASE WHEN order_nr < 0.75 * population_size THEN value END) AS percentile_75\n"
        "FROM (\n"
        "  SELECT value,\n"
        "         ROW_NUMBER() OVER (ORDER BY value) AS order_nr,\n"
        "         COUNT(*) OVER () AS population_size\n"
        "  FROM measurement\n"
        ") t;"
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
                violations.append(
                    self.create_violation(
                        message=issue,
                        severity=severity,
                        details={
                            "pattern": "manual_percentile_with_row_number",
                        },
                    )
                )

        return violations


__all__ = ["IncorrectPercentileCalculationRule"]

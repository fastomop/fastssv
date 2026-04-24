"""Non-Standard Date Literal Format Rule.

Detects date literals in non-standard or ambiguous formats that may cause
portability issues or incorrect interpretations across different databases.

The Problem:
    Date literals like '01-jan-2011', '31-dec-2011', '01/15/2020' are:
    1. Database-specific and may not work across all SQL dialects
    2. Ambiguous (is '01/02/2020' Jan 2 or Feb 1?)
    3. Dependent on database locale settings

    ISO 8601 format (YYYY-MM-DD) is the international standard and is:
    - Unambiguous
    - Portable across all SQL databases
    - Locale-independent

Violation patterns:
    -- WRONG: Ambiguous format
    WHERE observation_period_start_date <= '01-jan-2011'
    WHERE visit_date = '12/31/2020'

    -- CORRECT: ISO 8601 format
    WHERE observation_period_start_date <= '2011-01-01'
    WHERE visit_date = '2020-12-31'
"""

import re
from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


# Patterns for non-standard date formats
NON_STANDARD_DATE_PATTERNS = [
    (r'\d{2}-[a-zA-Z]{3}-\d{4}', 'DD-MMM-YYYY (e.g., 01-jan-2011)'),  # 01-jan-2011
    (r'\d{2}-[a-zA-Z]{3}-\d{2}', 'DD-MMM-YY (e.g., 01-jan-11)'),      # 01-jan-11
    (r'\d{1,2}/\d{1,2}/\d{2,4}', 'M/D/YYYY or D/M/YYYY (ambiguous)'),  # 01/15/2020 or 15/01/2020
    (r'\d{8}', 'YYYYMMDD (no separators)'),                            # 20110101
]

# ISO 8601 pattern (YYYY-MM-DD)
ISO_DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _is_non_standard_date_literal(literal_str: str) -> tuple:
    """Check if a string literal is a non-standard date format.

    Returns (is_non_standard, format_description)
    """
    literal_str = literal_str.strip().strip("'\"")

    # Check if it's already ISO format (good!)
    if ISO_DATE_PATTERN.match(literal_str):
        return False, None

    # Check for non-standard patterns
    for pattern, description in NON_STANDARD_DATE_PATTERNS:
        if re.match(pattern, literal_str):
            return True, description

    return False, None


def _is_date_column(col_name: str) -> bool:
    """Check if a column name suggests it's a date/datetime column."""
    col_lower = normalize_name(col_name)
    # Common date column patterns in OMOP and general SQL
    date_indicators = [
        '_date', '_datetime', '_time',
        'date_', 'datetime_', 'time_',
        'start_date', 'end_date', 'birth_date', 'death_date'
    ]
    return any(indicator in col_lower for indicator in date_indicators)


def _find_non_standard_date_literals(tree: exp.Expression) -> List[tuple]:
    """Find all non-standard date literals in the query.

    Only flags literals that are being compared to date/datetime columns.
    This avoids false positives on concept codes, IDs, etc.

    Returns list of (literal_value, format_description) tuples.
    """
    issues = []

    # Find comparisons (=, <, >, <=, >=, BETWEEN, etc.)
    comparison_types = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)

    for comparison in tree.find_all(comparison_types):
        # Find columns and literals in the comparison
        columns_in_comparison = []
        literals_in_comparison = []

        for col in comparison.find_all(exp.Column):
            columns_in_comparison.append(normalize_name(col.name))

        for lit in comparison.find_all(exp.Literal):
            if lit.is_string:
                literals_in_comparison.append(str(lit.this))

        # Only check literals if they're being compared to a date column
        has_date_column = any(_is_date_column(col) for col in columns_in_comparison)

        if has_date_column:
            for literal_str in literals_in_comparison:
                is_non_standard, format_desc = _is_non_standard_date_literal(literal_str)
                if is_non_standard:
                    issues.append((literal_str, format_desc))

    return issues


@register
class NonStandardDateLiteralFormatRule(Rule):
    """Detects non-standard or ambiguous date literal formats."""

    rule_id = "data_quality.non_standard_date_literal_format"
    name = "Non-Standard Date Literal Format"

    description = (
        "Detects date literals in non-standard formats (e.g., '01-jan-2011', '12/31/2020') "
        "that may be ambiguous or not portable across databases. ISO 8601 format (YYYY-MM-DD) "
        "is recommended for clarity and portability."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use ISO 8601 date format (YYYY-MM-DD) for date literals. "
        "Example: '2011-01-01' instead of '01-jan-2011'"
    )
    long_description = (
        "Date literals like '01/31/2020', '31-Jan-2020', or '01-jan-2011' "
        "are ambiguous: '01/02/2020' means January 2 in the US and "
        "February 1 in Europe, and engines disagree about which to pick. "
        "ISO 8601 (YYYY-MM-DD) is unambiguous and portable across every "
        "supported dialect. Rewrite literals to the ISO form, optionally "
        "wrapping in DATE '...' to make the type explicit."
    )
    example_bad = (
        "SELECT *\n"
        "FROM condition_occurrence\n"
        "WHERE condition_start_date = '01/31/2020';"
    )
    example_good = (
        "SELECT *\n"
        "FROM condition_occurrence\n"
        "WHERE condition_start_date = DATE '2020-01-31';"
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

            issues = _find_non_standard_date_literals(tree)

            for literal_value, format_desc in issues:
                violations.append(self.create_violation(
                    message=(
                        f"Non-standard date literal format detected: '{literal_value}' ({format_desc}). "
                        f"Use ISO 8601 format (YYYY-MM-DD) for portability and clarity."
                    ),
                    details={
                        "literal": literal_value,
                        "format": format_desc,
                        "recommendation": "Use ISO 8601 format: YYYY-MM-DD"
                    }
                ))

        return violations


__all__ = ["NonStandardDateLiteralFormatRule"]

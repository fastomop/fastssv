"""Duplicate Column Alias Rule.

Detects when the same expression is selected multiple times with different aliases,
which is usually a copy-paste error and provides no analytical value.

The Problem:
    When multiple columns in a SELECT statement have identical expressions but different
    aliases, it's typically a mistake. This creates:
    1. Redundant data in the result set
    2. Confusion for query consumers
    3. Potential for using the wrong column name
    4. Wasted computation and memory

Violation patterns:
    -- WRONG: Same calculation with different aliases
    SELECT
        ROUND(STDEV(age), 1) AS stdev_age,
        ROUND(STDEV(age), 1) AS STDEV_value  -- Duplicate!
    FROM person;

    -- WRONG: Same column selected multiple times
    SELECT
        person_id,
        person_id AS patient_id  -- Duplicate!
    FROM person;

Correct patterns:
    -- CORRECT: Different calculations
    SELECT
        ROUND(STDEV(age), 1) AS stdev_age,
        ROUND(AVG(age), 1) AS avg_age
    FROM person;

    -- CORRECT: Single alias for each unique expression
    SELECT
        ROUND(STDEV(age), 1) AS stdev_age
    FROM person;
"""

from typing import Dict, List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


def _normalize_expression(expr: exp.Expression) -> str:
    """Normalize an expression for comparison.

    This converts the expression to SQL and normalizes it to detect duplicates.
    """
    # Convert to SQL string and normalize
    sql_str = expr.sql().lower().strip()

    # Remove extra whitespace
    import re
    sql_str = re.sub(r'\s+', ' ', sql_str)

    return sql_str


def _find_duplicate_column_aliases(tree: exp.Expression) -> List[tuple]:
    """Find duplicate column expressions with different aliases.

    Returns list of (alias1, alias2, expression) tuples for duplicates.
    """
    issues = []

    for select in tree.find_all(exp.Select):
        # Build a map of normalized expressions to their aliases
        expr_to_aliases: Dict[str, List[str]] = {}

        for expr in select.expressions or []:
            # Get the actual expression (unwrap alias)
            actual_expr = expr
            alias = None

            if isinstance(expr, exp.Alias):
                alias = expr.alias
                actual_expr = expr.this
            else:
                # For non-aliased expressions, use the SQL representation as the "alias"
                alias = actual_expr.sql()

            # Normalize the expression for comparison
            normalized = _normalize_expression(actual_expr)

            if normalized not in expr_to_aliases:
                expr_to_aliases[normalized] = []

            expr_to_aliases[normalized].append(alias)

        # Find expressions with multiple different aliases
        for normalized_expr, aliases in expr_to_aliases.items():
            if len(aliases) > 1:
                # Check if aliases are actually different (not just case)
                normalized_aliases = [normalize_name(a) for a in aliases]
                if len(set(normalized_aliases)) > 1:
                    # We have duplicate expressions with different aliases
                    issues.append((aliases, normalized_expr))

    return issues


@register
class DuplicateColumnAliasRule(Rule):
    """Detects duplicate column expressions with different aliases."""

    rule_id = "anti_patterns.duplicate_column_alias"
    name = "Duplicate Column Alias"

    description = (
        "Detects when the same expression is selected multiple times with different aliases. "
        "This is usually a copy-paste error and creates redundant data in the result set."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Remove duplicate columns or ensure each column represents a unique calculation. "
        "If you need the same value with different names, consider using views or derived tables."
    )
    long_description = (
        "Selecting the same expression twice with different aliases is "
        "almost always the result of copy-paste during iteration. It "
        "produces result-set columns that carry the same value row-for-row "
        "but different names, doubling the output width without adding "
        "information. Remove the duplicate, or if you genuinely need the "
        "same value projected twice, make the second projection "
        "compute something different (or name them differently via a view)."
    )
    example_bad = (
        "SELECT person_id AS pid_a, person_id AS pid_b\n"
        "FROM person;"
    )
    example_good = (
        "SELECT person_id\n"
        "FROM person;"
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

            issues = _find_duplicate_column_aliases(tree)

            for aliases, expression in issues:
                # Truncate long expressions for readability
                display_expr = expression
                if len(display_expr) > 50:
                    display_expr = display_expr[:50] + "..."

                violations.append(self.create_violation(
                    message=(
                        f"Duplicate column expression detected: '{display_expr}' "
                        f"is selected with multiple aliases: {', '.join(repr(a) for a in aliases)}. "
                        f"This is likely a copy-paste error."
                    ),
                    details={
                        "aliases": aliases,
                        "expression": expression,
                        "recommendation": "Remove duplicate columns or ensure each represents a unique calculation."
                    }
                ))

        return violations


__all__ = ["DuplicateColumnAliasRule"]

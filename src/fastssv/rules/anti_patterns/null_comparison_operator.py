"""NULL Comparison Operator Rule.

SQL semantic correctness rule:
Using comparison operators (=, <>, !=, >, <, >=, <=) with NULL is always incorrect.
NULL comparisons always return UNKNOWN (neither true nor false), causing logic errors.

The Problem:
    In SQL, NULL represents "unknown" and has special comparison semantics:
    - NULL = NULL → UNKNOWN (not TRUE!)
    - NULL <> NULL → UNKNOWN (not TRUE!)
    - NULL > 5 → UNKNOWN
    - x = NULL → UNKNOWN (even if x is NULL!)

    This causes:
    - WHERE clauses to silently filter out all rows
    - CASE/IF conditions to behave unexpectedly
    - Joins to produce incorrect results

Violation patterns:
    -- WRONG: Always false/unknown
    SELECT * FROM person WHERE person_id = NULL
    SELECT * FROM person WHERE person_id <> NULL
    CASE WHEN column_id <> NULL THEN ...

    -- WRONG: Meaningless condition
    IF @variable = NULL
    WHERE column != NULL

Correct patterns:
    -- CORRECT: Use IS NULL / IS NOT NULL
    SELECT * FROM person WHERE person_id IS NULL
    SELECT * FROM person WHERE person_id IS NOT NULL
    CASE WHEN column_id IS NOT NULL THEN ...

Why this matters:
    This is a fundamental SQL correctness issue. Queries with NULL comparison
    operators will execute but produce incorrect results, making it a critical
    error that must be caught.
"""

from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _find_null_comparisons(tree: exp.Expression) -> List[tuple]:
    """Find comparison operators used with NULL literals.

    Returns list of (operator, context) tuples.
    """
    violations: List[tuple] = []

    for node in tree.walk():
        # Check for comparison operators
        if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            left = node.this if hasattr(node, 'this') else None
            right = node.expression if hasattr(node, 'expression') else None

            # Check if either side is NULL
            is_left_null = isinstance(left, exp.Null) or (
                isinstance(left, exp.Literal) and
                left.this and
                str(left.this).upper() == 'NULL'
            )
            is_right_null = isinstance(right, exp.Null) or (
                isinstance(right, exp.Literal) and
                right.this and
                str(right.this).upper() == 'NULL'
            )

            if is_left_null or is_right_null:
                # Get operator name
                op_name = {
                    exp.EQ: '=',
                    exp.NEQ: '<>' or '!=',
                    exp.GT: '>',
                    exp.GTE: '>=',
                    exp.LT: '<',
                    exp.LTE: '<=',
                }.get(type(node), '?')

                # Get context (WHERE, CASE, etc.)
                context = _get_context(node)

                # Get the SQL fragment
                sql_fragment = node.sql()[:100]

                violations.append((op_name, context, sql_fragment))

    return violations


def _get_context(node: exp.Expression) -> str:
    """Determine the context where the NULL comparison appears."""
    parent = node.parent if hasattr(node, 'parent') else None

    while parent:
        if isinstance(parent, exp.Where):
            return "WHERE clause"
        elif isinstance(parent, exp.Case):
            return "CASE expression"
        elif isinstance(parent, exp.If):
            return "IF statement"
        elif isinstance(parent, exp.Join):
            return "JOIN ON clause"
        elif isinstance(parent, exp.Having):
            return "HAVING clause"

        parent = parent.parent if hasattr(parent, 'parent') else None

    return "expression"


@register
class NullComparisonOperatorRule(Rule):
    """Detects use of comparison operators (=, <>, !=, >, <, >=, <=) with NULL.

    This is a SQL correctness error - NULL comparisons must use IS NULL / IS NOT NULL.
    """

    rule_id = "anti_patterns.null_comparison_operator"
    name = "NULL Comparison Must Use IS NULL / IS NOT NULL"

    description = (
        "Using comparison operators (=, <>, !=, >, <, >=, <=) with NULL is incorrect SQL. "
        "NULL comparisons always return UNKNOWN, causing logic errors. "
        "Use IS NULL or IS NOT NULL instead."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Replace '= NULL' with 'IS NULL' and '<> NULL' or '!= NULL' with 'IS NOT NULL'. "
        "For example: WHERE column IS NULL instead of WHERE column = NULL"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if not tree:
                continue

            null_comparisons = _find_null_comparisons(tree)

            for operator, context, sql_fragment in null_comparisons:
                message = (
                    f"Incorrect NULL comparison using '{operator}' operator in {context}. "
                    f"NULL comparisons with =, <>, !=, >, <, >=, <= always return UNKNOWN (neither true nor false), "
                    f"causing incorrect query behavior. Use 'IS NULL' or 'IS NOT NULL' instead."
                )

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details={
                            "operator": operator,
                            "context": context,
                            "sql_fragment": sql_fragment[:80],
                        },
                    )
                )

        return violations


__all__ = ["NullComparisonOperatorRule"]

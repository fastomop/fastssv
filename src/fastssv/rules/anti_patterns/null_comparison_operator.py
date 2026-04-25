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
from fastssv.core.patch import freeform, locate, replace
from fastssv.core.registry import register


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _find_null_comparisons(tree: exp.Expression) -> List[tuple]:
    """Find comparison operators used with NULL literals.

    Returns list of (op_name, context, sql_fragment, node_type, non_null_side_sql).
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

                # Get the SQL fragment and the non-NULL side (for patch construction).
                sql_fragment = node.sql()[:100]
                non_null_side = right if is_left_null else left
                non_null_sql = non_null_side.sql() if non_null_side is not None else None

                violations.append((op_name, context, sql_fragment, type(node), non_null_sql))

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

    suggested_fix = "REPLACE: `<col> = NULL` WITH `<col> IS NULL`. REPLACE: `<col> <> NULL` or `<col> != NULL` WITH `<col> IS NOT NULL`."
    long_description = (
        "Comparing a column to NULL with `=`, `<>`, `!=`, `<`, `>`, `<=`, "
        "or `>=` always evaluates to UNKNOWN, which behaves as FALSE in "
        "WHERE and HAVING. A predicate like `WHERE death_date = NULL` "
        "returns zero rows regardless of the data, silently. Use the "
        "dedicated predicates `IS NULL` and `IS NOT NULL` — they are the "
        "only correct way to test nullability in SQL."
    )
    example_bad = (
        "SELECT person_id\n"
        "FROM death\n"
        "WHERE death_date = NULL;"
    )
    example_good = (
        "SELECT person_id\n"
        "FROM death\n"
        "WHERE death_date IS NULL;"
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

            for operator, context, sql_fragment, node_type, non_null_sql in null_comparisons:
                message = (
                    f"Incorrect NULL comparison using '{operator}' operator in {context}. "
                    f"NULL comparisons with =, <>, !=, >, <, >=, <= always return UNKNOWN (neither true nor false), "
                    f"causing incorrect query behavior. Use 'IS NULL' or 'IS NOT NULL' instead."
                )

                # Build a structured patch when intent is unambiguous:
                # `=` → IS NULL, `<>`/`!=` → IS NOT NULL. Range operators
                # against NULL are nonsense; emit FREEFORM since the user's
                # intent isn't recoverable.
                patch = None
                span = locate(sql, sql_fragment)
                if span and non_null_sql:
                    if node_type is exp.EQ:
                        patch = replace(span, f"{non_null_sql} IS NULL")
                    elif node_type is exp.NEQ:
                        patch = replace(span, f"{non_null_sql} IS NOT NULL")
                if patch is None:
                    patch = freeform(self.suggested_fix)

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=patch,
                        details={
                            "operator": operator,
                            "context": context,
                            "sql_fragment": sql_fragment[:80],
                        },
                    )
                )

        return violations


__all__ = ["NullComparisonOperatorRule"]

"""Observation Value As String Numeric Comparison Rule.

OMOP semantic rule OMOP_111:
observation.value_as_string is a free-text VARCHAR. Numeric comparisons
(>, <, >=, <=, BETWEEN) on this column require explicit casting and may
fail on non-numeric strings. Use value_as_number for numeric comparisons.

The Problem:
    value_as_string is a VARCHAR field. Applying numeric comparison
    operators directly is semantically incorrect:
    - The database may silently coerce strings to numbers, returning
      wrong results or errors when non-numeric strings are encountered.
    - The proper column for numeric comparisons is value_as_number.

    Common mistakes:
    - WHERE value_as_string > 100
    - WHERE value_as_string <= 7.5
    - WHERE value_as_string BETWEEN 50 AND 200

Violation patterns:
    SELECT * FROM observation
    WHERE observation_concept_id = 3038553
      AND value_as_string > 100
    -- ERROR: Numeric comparison on VARCHAR column

    SELECT * FROM observation
    WHERE value_as_string BETWEEN 50 AND 200
    -- ERROR: BETWEEN on VARCHAR column with numeric bounds

Correct patterns:
    SELECT * FROM observation
    WHERE observation_concept_id = 3038553
      AND value_as_number > 100
    -- OK: Numeric comparison on the numeric column

    SELECT * FROM observation
    WHERE CAST(value_as_string AS FLOAT) > 100
    -- OK: Explicit cast before comparison
"""

from typing import Dict, List, Optional, Set

import logging
from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

TABLE_NAME = "observation"
TARGET_COLUMN = "value_as_string"

NUMERIC_COMPARISON_TYPES = (
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Between,
)

SAFE_CAST_FUNCTIONS = {
    "cast",
    "try_cast",
    "safe_cast",
    "convert",
    "try_convert",
}

# --- Helpers -----------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _unwrap_expression(expr: exp.Expression) -> exp.Expression:
    """
    Unwrap only transparent wrappers (parentheses, aliases) to reach the base
    expression.  We intentionally do NOT unwrap Cast/TryCast/Func here so that
    _is_safely_casted() can still detect them via the parent-chain walk.
    """
    current = expr
    while True:
        if isinstance(current, exp.Paren):
            current = current.this
        elif isinstance(current, exp.Alias):
            current = current.this
        else:
            break
    return current


def _is_value_as_string(col: exp.Expression, aliases: Dict[str, str]) -> bool:
    """
    Strict check: ensure column resolves to observation.value_as_string.
    Avoid ambiguous unqualified columns when multiple tables exist.
    """
    col = _unwrap_expression(col)

    if not isinstance(col, exp.Column):
        return False

    table, column = resolve_table_col(col, aliases)

    if _norm(column) != TARGET_COLUMN:
        return False

    if table:
        return _norm(table) == TABLE_NAME

    # Unqualified column: accept if observation (or an alias of it) appears
    # anywhere as a *key* in the alias map. extract_aliases always writes
    # aliases[real_name] = real_name, so this covers direct references,
    # aliased tables, and CTEs that reference the observation table.
    return TABLE_NAME in aliases


def _is_safely_casted(expr: exp.Expression) -> bool:
    """
    Detect if value_as_string is explicitly cast to numeric.
    Only allow safe numeric casts.
    """
    parent = expr.parent

    while parent:
        if isinstance(parent, (exp.Cast, exp.TryCast)):
            return True

        if isinstance(parent, exp.Func):
            try:
                name = _norm(parent.sql_name())
            except Exception:
                name = None

            if name in SAFE_CAST_FUNCTIONS:
                return True

        if isinstance(parent, NUMERIC_COMPARISON_TYPES):
            break

        parent = parent.parent

    return False


def _is_numeric_literal(node: Optional[exp.Expression]) -> bool:
    if node is None:
        return False

    if isinstance(node, exp.Literal) and not node.is_string:
        return True

    if isinstance(node, exp.Neg):
        inner = node.this
        return isinstance(inner, exp.Literal) and not inner.is_string

    return False


def _get_operator_name(node: exp.Expression) -> str:
    return {
        exp.GT: ">",
        exp.GTE: ">=",
        exp.LT: "<",
        exp.LTE: "<=",
        exp.Between: "BETWEEN",
    }.get(type(node), type(node).__name__)


# --- Detection ---------------------------------------------------------------


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues: List[str] = []
    seen: Set[str] = set()

    for node in tree.find_all(NUMERIC_COMPARISON_TYPES):
        try:
            if isinstance(node, exp.Between):
                col_expr = node.this
                low = node.args.get("low")
                high = node.args.get("high")

                if not _is_value_as_string(col_expr, aliases):
                    continue

                if _is_safely_casted(col_expr):
                    continue

                # BOTH bounds must be numeric
                if not (_is_numeric_literal(low) and _is_numeric_literal(high)):
                    continue

                key = node.sql()
                if key in seen:
                    continue
                seen.add(key)

                issues.append(
                    "Numeric BETWEEN comparison on observation.value_as_string. "
                    "Use value_as_number or CAST(value_as_string AS NUMERIC)."
                )

            else:
                left, right = node.left, node.right

                # Left side
                if (
                    _is_value_as_string(left, aliases)
                    and not _is_safely_casted(left)
                    and _is_numeric_literal(right)
                ):
                    key = node.sql()
                    if key not in seen:
                        seen.add(key)
                        issues.append(
                            f"Numeric comparison ({_get_operator_name(node)}) on "
                            "observation.value_as_string. Use value_as_number or CAST."
                        )

                # Right side
                elif (
                    _is_numeric_literal(left)
                    and _is_value_as_string(right, aliases)
                    and not _is_safely_casted(right)
                ):
                    key = node.sql()
                    if key not in seen:
                        seen.add(key)
                        issues.append(
                            f"Numeric comparison ({_get_operator_name(node)}) on "
                            "observation.value_as_string. Use value_as_number or CAST."
                        )

        except Exception as e:
            logger.exception("Error while analyzing SQL node", exc_info=e)

    return issues


# --- Rule --------------------------------------------------------------------


@register
class ObservationValueAsStringNumericComparisonRule(Rule):
    """
    Production-grade rule:
    Detect unsafe numeric comparisons on observation.value_as_string.
    """

    rule_id = "domain_specific.observation_value_as_string_numeric_comparison"

    name = "Observation Value As String Numeric Comparison"

    description = (
        "Detects numeric comparisons on observation.value_as_string "
        "(VARCHAR). This can lead to incorrect results."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Replace with value_as_number or explicitly CAST(value_as_string AS NUMERIC)."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter
        if "value_as_string" not in sql_lower:
            return []
        if TABLE_NAME not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)

        if parse_error:
            logger.warning(
                "SQL parsing failed",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not uses_table(tree, TABLE_NAME):
                continue

            aliases = extract_aliases(tree)

            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["ObservationValueAsStringNumericComparisonRule"]
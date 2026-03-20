"""Condition End Date NULL Handling Rule.

OMOP semantic rule OMOP_062:
condition_occurrence.condition_end_date is optional and often NULL.
Queries computing duration must handle NULLs or results will silently drop rows.

The Problem:
    condition_end_date is NULL for:
    - Ongoing/chronic conditions
    - Conditions without documented resolution
    - Point-in-time diagnoses

    NULL in date arithmetic returns NULL, silently excluding rows from aggregations.

Example impact:
    SELECT AVG(DATEDIFF(day, condition_start_date, condition_end_date))
    FROM condition_occurrence
    -- Returns NULL for rows with NULL end_date
    -- Aggregation excludes these rows → biased results

Violation pattern:
    SELECT DATEDIFF(day, condition_start_date, condition_end_date) AS duration
    FROM condition_occurrence
    -- No NULL handling!

Correct patterns:
    -- Option 1: COALESCE with fallback
    SELECT DATEDIFF(day, condition_start_date,
                    COALESCE(condition_end_date, CURRENT_DATE))
    FROM condition_occurrence

    -- Option 2: Filter out NULLs
    SELECT DATEDIFF(day, condition_start_date, condition_end_date)
    FROM condition_occurrence
    WHERE condition_end_date IS NOT NULL

    -- Option 3: CASE statement
    SELECT CASE WHEN condition_end_date IS NULL THEN NULL
                ELSE DATEDIFF(day, condition_start_date, condition_end_date)
           END
    FROM condition_occurrence
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONDITION_OCCURRENCE = "condition_occurrence"
CONDITION_END_DATE = "condition_end_date"

DATE_FUNCTIONS = {
    "datediff",
    "date_diff",
    "timestampdiff",
    "dateadd",
    "date_add",
    "adddate",
    "date_sub",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_condition_end_date(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    if isinstance(node, exp.Column):
        table, col = resolve_table_col(node, aliases)
        if _norm(table) == CONDITION_OCCURRENCE and _norm(col) == CONDITION_END_DATE:
            return True
        if not table and _norm(col) == CONDITION_END_DATE:
            return any(_norm(t) == CONDITION_OCCURRENCE for t in aliases.values())

    if isinstance(node, exp.Var):
        if _norm(str(node.this)) == CONDITION_END_DATE:
            return any(_norm(t) == CONDITION_OCCURRENCE for t in aliases.values())

    return False


def _is_null_check(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Detect condition_end_date IS NOT NULL (robust)."""
    # IS NOT NULL pattern: exp.Not(this=exp.Is(this=Column, expression=exp.Null()))
    if isinstance(node, exp.Not):
        if isinstance(node.this, exp.Is):
            is_node = node.this
            if isinstance(is_node.expression, exp.Null):
                if isinstance(is_node.this, exp.Column):
                    return _is_condition_end_date(is_node.this, aliases)

    return False


def _is_wrapped_with_null_handling(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if node is protected by COALESCE/CASE/etc."""
    parent = node.parent

    while parent:
        # COALESCE / NULLIF
        if isinstance(parent, (exp.Coalesce, exp.Nullif)):
            return True

        # Vendor-specific
        if isinstance(parent, exp.Func):
            name = _norm(parent.sql_name() if hasattr(parent, "sql_name") else str(parent.key))
            if name in {"isnull", "ifnull", "nvl"}:
                return True

        # CASE WHEN
        if isinstance(parent, exp.Case):
            return True

        parent = parent.parent

    return False


def _is_protected(node: exp.Expression, tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if this specific usage is protected."""
    if _is_wrapped_with_null_handling(node, aliases):
        return True

    # Check local WHERE/JOIN conditions for IS NOT NULL on same column
    for cond in tree.find_all(exp.Not):
        if _is_null_check(cond, aliases):
            return True

    return False


def _collect_relevant_expressions(tree: exp.Expression) -> List[exp.Expression]:
    """Collect expressions where NULL propagation matters."""
    exprs = []

    # SELECT expressions
    for select in tree.find_all(exp.Select):
        exprs.extend(select.expressions or [])

    # WHERE / HAVING / JOIN
    for node in tree.walk():
        if is_in_where_or_join_clause(node):
            exprs.append(node)

    return exprs


# --- Detection -------------------------------------------------------------

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues = []
    seen: Set[str] = set()

    exprs = _collect_relevant_expressions(tree)

    for expr_node in exprs:

        # --- 1. Date functions ---
        for func in expr_node.find_all(exp.Func):
            name = _norm(
                func.sql_name() if hasattr(func, "sql_name") else str(func.key)
            )

            if name not in DATE_FUNCTIONS:
                continue

            # Check both Column and Var nodes (DATEDIFF args are Var)
            for col in func.find_all((exp.Column, exp.Var)):
                if not _is_condition_end_date(col, aliases):
                    continue

                if _is_protected(col, tree, aliases):
                    continue

                key = func.sql()
                if key not in seen:
                    seen.add(key)
                    issues.append(
                        f"condition_end_date used in {name.upper()}() without NULL handling. "
                        f"Use COALESCE(condition_end_date, fallback) or add IS NOT NULL filter."
                    )

        # --- 2. Arithmetic ---
        for node in expr_node.find_all((exp.Add, exp.Sub)):
            for col in node.find_all(exp.Column):
                if not _is_condition_end_date(col, aliases):
                    continue

                if _is_protected(col, tree, aliases):
                    continue

                key = node.sql()
                if key not in seen:
                    seen.add(key)
                    issues.append(
                        "condition_end_date used in date arithmetic without NULL handling. "
                        "Use COALESCE or add IS NOT NULL filter."
                    )

        # --- 3. BETWEEN ---
        for between in expr_node.find_all(exp.Between):
            if _is_condition_end_date(between.this, aliases):
                if not _is_protected(between.this, tree, aliases):
                    key = between.sql()
                    if key not in seen:
                        seen.add(key)
                        issues.append(
                            "condition_end_date used in BETWEEN without NULL handling. "
                            "Use COALESCE or add IS NOT NULL filter."
                        )

        # --- 4. Comparisons ---
        for comp in expr_node.find_all((exp.GT, exp.GTE, exp.LT, exp.LTE)):
            for side in [comp.this, comp.expression]:
                if isinstance(side, exp.Column) and _is_condition_end_date(side, aliases):
                    if _is_protected(side, tree, aliases):
                        continue

                    key = comp.sql()
                    if key not in seen:
                        seen.add(key)
                        issues.append(
                            "condition_end_date used in comparison without NULL handling. "
                            "Use COALESCE or add IS NOT NULL filter."
                        )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class ConditionEndDateNullHandlingRule(Rule):
    """Production-grade NULL handling validation."""

    rule_id = "semantic.condition_end_date_null_handling"
    name = "Condition End Date NULL Handling"
    description = (
        "condition_end_date is frequently NULL. Unprotected usage in date logic "
        "causes silent row loss or NULL propagation."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Use COALESCE(condition_end_date, fallback_date) or filter IS NOT NULL."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "condition_end_date" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not uses_table(tree, CONDITION_OCCURRENCE):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["ConditionEndDateNullHandlingRule"]
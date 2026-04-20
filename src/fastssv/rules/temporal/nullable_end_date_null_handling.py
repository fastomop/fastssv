"""Nullable End Date NULL Handling Rule.

OMOP semantic rules OMOP_062, OMOP_159, CLIN_022, CLIN_039:
Validates that nullable *_end_date columns have proper NULL handling when used in
date functions, arithmetic, or comparisons.

Covered Rules:
- OMOP_062: condition_occurrence.condition_end_date
- OMOP_159: drug_exposure.drug_exposure_end_date
- CLIN_022: procedure_occurrence.procedure_end_date
- CLIN_039: visit_occurrence.visit_end_date

The Problem:
    End date columns are frequently NULL because:
    - Ongoing/chronic conditions without resolution
    - Drug exposures with unknown end dates
    - Single-point-in-time procedures
    - Incomplete/ongoing visits

    NULL in date arithmetic returns NULL, silently excluding rows from aggregations.

Example impact:
    SELECT AVG(DATEDIFF(day, start_date, end_date))
    FROM condition_occurrence
    -- Returns NULL for rows with NULL end_date
    -- Aggregation excludes these rows → biased results

Violation pattern:
    SELECT DATEDIFF(day, procedure_date, procedure_end_date) AS duration
    FROM procedure_occurrence
    -- No NULL handling!

Correct patterns:
    -- Option 1: COALESCE with fallback
    SELECT DATEDIFF(day, drug_exposure_start_date,
                    COALESCE(drug_exposure_end_date, CURRENT_DATE))
    FROM drug_exposure

    -- Option 2: Filter out NULLs
    SELECT DATEDIFF(day, visit_start_date, visit_end_date)
    FROM visit_occurrence
    WHERE visit_end_date IS NOT NULL

    -- Option 3: CASE statement
    SELECT CASE WHEN condition_end_date IS NULL THEN NULL
                ELSE DATEDIFF(day, condition_start_date, condition_end_date)
           END
    FROM condition_occurrence
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Configuration ---------------------------------------------------------

class EndDateConfig:
    """Configuration for nullable end_date column."""

    def __init__(self, table: str, column: str, description: str):
        self.table = table
        self.column = column
        self.description = description


NULLABLE_END_DATE_CONFIGS = {
    "condition_occurrence": EndDateConfig(
        table="condition_occurrence",
        column="condition_end_date",
        description="often NULL for ongoing/chronic conditions",
    ),
    "drug_exposure": EndDateConfig(
        table="drug_exposure",
        column="drug_exposure_end_date",
        description="NULL when end of exposure is unknown",
    ),
    "procedure_occurrence": EndDateConfig(
        table="procedure_occurrence",
        column="procedure_end_date",
        description="NULL for single-point-in-time procedures",
    ),
    "visit_occurrence": EndDateConfig(
        table="visit_occurrence",
        column="visit_end_date",
        description="NULL for ongoing/incomplete visits",
    ),
}


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


def _is_nullable_end_date(
    node: exp.Expression,
    aliases: Dict[str, str],
) -> Optional[EndDateConfig]:
    table_name = None
    col_name = None

    if isinstance(node, exp.Column):
        table_name, col_name = resolve_table_col(node, aliases)

    elif isinstance(node, exp.Var):
        col_name = str(node.this)

    if not col_name:
        return None

    col_norm = _norm(col_name)

    for config in NULLABLE_END_DATE_CONFIGS.values():
        if _norm(config.column) != col_norm:
            continue

        if table_name:
            if _norm(table_name) == _norm(config.table):
                return config
        else:
            if config.table in {_norm(t) for t in aliases.values()}:
                return config

    return None


def _is_null_check_for_column(
    node: exp.Expression,
    aliases: Dict[str, str],
    config: EndDateConfig,
) -> bool:
    if not isinstance(node, exp.Not):
        return False

    if not isinstance(node.this, exp.Is):
        return False

    is_node = node.this

    if not isinstance(is_node.expression, exp.Null):
        return False

    if not isinstance(is_node.this, exp.Column):
        return False

    matched = _is_nullable_end_date(is_node.this, aliases)
    if not matched:
        return False

    return (
        _norm(matched.table) == _norm(config.table)
        and _norm(matched.column) == _norm(config.column)
    )


def _has_local_null_filter(
    tree: exp.Expression,
    aliases: Dict[str, str],
    config: EndDateConfig,
) -> bool:
    for where in tree.find_all(exp.Where):
        for cond in where.find_all(exp.Not):
            if _is_null_check_for_column(cond, aliases, config):
                return True
    return False


def _is_wrapped_with_null_handling(node: exp.Expression) -> bool:
    parent = node.parent

    while parent:
        if isinstance(parent, (exp.Coalesce, exp.Nullif)):
            return True

        if isinstance(parent, exp.Func):
            name = _norm(
                parent.sql_name() if hasattr(parent, "sql_name") else str(parent.key)
            )
            if name in {"isnull", "ifnull", "nvl"}:
                return True

        # CASE WHEN protection
        if isinstance(parent, exp.Case):
            return True

        parent = parent.parent

    return False


def _is_protected(
    node: exp.Expression,
    tree: exp.Expression,
    aliases: Dict[str, str],
    config: EndDateConfig,
) -> bool:
    if _is_wrapped_with_null_handling(node):
        return True

    if _has_local_null_filter(tree, aliases, config):
        return True

    return False


def _collect_relevant_expressions(tree: exp.Expression) -> List[exp.Expression]:
    exprs: List[exp.Expression] = []

    for select in tree.find_all(exp.Select):
        exprs.extend(select.expressions or [])

    for node in tree.walk():
        if is_in_where_or_join_clause(node):
            exprs.append(node)

    return exprs


# --- Detection -------------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[EndDateConfig, str]]:
    issues: List[Tuple[EndDateConfig, str]] = []
    seen: Set[str] = set()

    exprs = _collect_relevant_expressions(tree)

    for expr_node in exprs:

        # --- Date functions ---
        for func in expr_node.find_all(exp.Func):
            name = _norm(
                func.sql_name() if hasattr(func, "sql_name") else str(func.key)
            )

            if name not in DATE_FUNCTIONS:
                continue

            for col in func.find_all((exp.Column, exp.Var)):
                config = _is_nullable_end_date(col, aliases)
                if not config:
                    continue

                if _is_protected(col, tree, aliases, config):
                    continue

                key = f"{config.table}|{config.column}|func|{func.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                issues.append((
                    config,
                    f"{config.column} used in {name.upper()}() without NULL handling. "
                    f"Use COALESCE({config.column}, fallback) or filter IS NOT NULL."
                ))

        # --- Arithmetic ---
        for node in expr_node.find_all((exp.Add, exp.Sub)):
            for col in node.find_all(exp.Column):
                config = _is_nullable_end_date(col, aliases)
                if not config:
                    continue

                if _is_protected(col, tree, aliases, config):
                    continue

                key = f"{config.table}|{config.column}|arith|{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                issues.append((
                    config,
                    f"{config.column} used in arithmetic without NULL handling. "
                    "Use COALESCE or filter IS NOT NULL."
                ))

        # --- BETWEEN ---
        for between in expr_node.find_all(exp.Between):
            col_node = between.this
            if not isinstance(col_node, exp.Column):
                continue

            config = _is_nullable_end_date(col_node, aliases)
            if not config:
                continue

            if _is_protected(col_node, tree, aliases, config):
                continue

            key = f"{config.table}|{config.column}|between|{between.sql()}"
            if key in seen:
                continue
            seen.add(key)

            issues.append((
                config,
                f"{config.column} used in BETWEEN without NULL handling. "
                "Use COALESCE or filter IS NOT NULL."
            ))

        # --- Comparisons ---
        for comp in expr_node.find_all((exp.GT, exp.GTE, exp.LT, exp.LTE)):
            for side in [comp.this, comp.expression]:
                if not isinstance(side, exp.Column):
                    continue

                config = _is_nullable_end_date(side, aliases)
                if not config:
                    continue

                if _is_protected(side, tree, aliases, config):
                    continue

                key = f"{config.table}|{config.column}|comp|{comp.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                issues.append((
                    config,
                    f"{config.column} used in comparison without NULL handling. "
                    "Use COALESCE or filter IS NOT NULL."
                ))

    return issues


# --- Rule ------------------------------------------------------------------

@register
class NullableEndDateNullHandlingRule(Rule):
    """Validate NULL handling for nullable end_date columns."""

    rule_id = "temporal.nullable_end_date_null_handling"
    name = "Nullable End Date NULL Handling"

    description = (
        "Ensures nullable end_date columns are properly handled when used in "
        "functions, arithmetic, or comparisons to avoid NULL propagation issues."
    )

    severity = Severity.WARNING
    suggested_fix = "Use COALESCE(end_date, fallback) or filter IS NOT NULL"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            # Skip if no relevant tables
            if not any(
                has_table_reference(tree, config.table)
                for config in NULLABLE_END_DATE_CONFIGS.values()
            ):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for config, msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        details={
                            "table": config.table,
                            "column": config.column,
                            "reason": config.description,
                        },
                    )
                )

        return violations


__all__ = ["NullableEndDateNullHandlingRule"]
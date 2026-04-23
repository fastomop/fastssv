"""Visit Outpatient Same-Day Validation Rule.

OMOP semantic rule CLIN_040: visit_occurrence_outpatient_same_day

Outpatient visits (visit_concept_id = 9202) are typically same-day visits where
visit_start_date = visit_end_date. Queries that filter outpatient visits with
multi-day date ranges (e.g., DATEDIFF > 1 or > 30) may indicate confusion with
inpatient visit logic.

The Problem:
    - Outpatient visits: same-day (9202)
    - Inpatient visits: multi-day stays (9201)
    - Emergency Room: same-day (9203)
    - ER+Inpatient: multi-day (262)

Violation pattern:
    SELECT * FROM visit_occurrence
    WHERE visit_concept_id = 9202
      AND DATEDIFF(day, visit_start_date, visit_end_date) > 30
    -- Outpatient visits rarely span 30+ days!

Correct patterns:
    -- Use inpatient for multi-day stays
    SELECT * FROM visit_occurrence
    WHERE visit_concept_id = 9201
      AND DATEDIFF(day, visit_start_date, visit_end_date) > 30

    -- For outpatient, expect same-day or very short duration
    SELECT * FROM visit_occurrence
    WHERE visit_concept_id = 9202
      AND DATEDIFF(day, visit_start_date, visit_end_date) <= 1
"""

from typing import Dict, List, Optional

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

TABLE_NAME = "visit_occurrence"
VISIT_CONCEPT_ID = "visit_concept_id"
VISIT_START_DATE = "visit_start_date"
VISIT_END_DATE = "visit_end_date"

# Standard visit type concepts
OUTPATIENT_CONCEPT_ID = 9202
INPATIENT_CONCEPT_ID = 9201

DATE_FUNCTIONS = {
    "datediff",
    "date_diff",
    "timestampdiff",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_visit_column(
    node: exp.Column,
    aliases: Dict[str, str],
    col_name: str,
) -> bool:
    """Check if column is from visit_occurrence table."""
    table, col = resolve_table_col(node, aliases)

    if not col or _norm(col) != _norm(col_name):
        return False

    if table:
        return _norm(table) == _norm(TABLE_NAME)
    else:
        # Unqualified column - check if visit_occurrence is in aliases
        return TABLE_NAME in {_norm(t) for t in aliases.values()}


def _is_visit_var(
    node: exp.Var,
    aliases: Dict[str, str],
    col_name: str,
) -> bool:
    """Check if Var node represents a visit_occurrence column.

    Some SQL dialects parse certain function arguments as Var nodes instead of Column nodes.
    """
    var_name = str(node.this)
    if not var_name or _norm(var_name) != _norm(col_name):
        return False

    # For Var nodes, we assume they refer to the table if it's in aliases
    return TABLE_NAME in {_norm(t) for t in aliases.values()}


def _extract_int(node: exp.Expression) -> Optional[int]:
    """Extract integer value from expression."""
    if isinstance(node, exp.Literal):
        try:
            return int(node.this)
        except (ValueError, TypeError):
            return None
    return None


def _has_outpatient_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if query filters for outpatient visits (visit_concept_id = 9202)."""
    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        # Check for EQ: visit_concept_id = 9202
        if isinstance(node, exp.EQ):
            pairs = [(node.this, node.expression), (node.expression, node.this)]
            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue
                if not _is_visit_column(col_node, aliases, VISIT_CONCEPT_ID):
                    continue

                val = _extract_int(val_node)
                if val == OUTPATIENT_CONCEPT_ID:
                    return True

        # Check for IN: visit_concept_id IN (9202, ...)
        elif isinstance(node, exp.In):
            col_node = node.this
            if not isinstance(col_node, exp.Column):
                continue
            if not _is_visit_column(col_node, aliases, VISIT_CONCEPT_ID):
                continue

            for val in node.expressions or []:
                v = _extract_int(val)
                if v == OUTPATIENT_CONCEPT_ID:
                    return True

    return False


def _has_multiday_datediff_filter(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Optional[str]:
    """Check if query has DATEDIFF(visit_start_date, visit_end_date) > threshold.

    Returns the violating SQL fragment if found, None otherwise.
    """
    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        # Check for comparisons: >, >=, <, <=
        # Note: 30 < DATEDIFF is parsed as LT, which is equivalent to DATEDIFF > 30
        if not isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            continue

        # Try both sides: datediff_func > threshold OR threshold < datediff_func
        for left, right in [(node.this, node.expression), (node.expression, node.this)]:
            # Check if left side is DATEDIFF function
            if not isinstance(left, exp.Func):
                continue

            func_name = _norm(
                left.sql_name() if hasattr(left, "sql_name") else str(left.key)
            )
            if func_name not in DATE_FUNCTIONS:
                continue

            # Check if DATEDIFF uses visit_start_date and visit_end_date
            # Note: Some arguments may be parsed as Var nodes instead of Column nodes
            has_start = False
            has_end = False

            for col in left.find_all(exp.Column):
                if _is_visit_column(col, aliases, VISIT_START_DATE):
                    has_start = True
                elif _is_visit_column(col, aliases, VISIT_END_DATE):
                    has_end = True

            for var in left.find_all(exp.Var):
                if _is_visit_var(var, aliases, VISIT_START_DATE):
                    has_start = True
                elif _is_visit_var(var, aliases, VISIT_END_DATE):
                    has_end = True

            if not (has_start and has_end):
                continue

            # Check if right side is a numeric threshold > 1
            threshold = _extract_int(right)
            if threshold is not None and threshold > 1:
                return node.sql()

    return None


# --- Detection -------------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[str]:
    """Find outpatient visits filtered with multi-day date range logic."""
    violations: List[str] = []

    # Check if query filters for outpatient visits
    has_outpatient = _has_outpatient_filter(tree, aliases)
    if not has_outpatient:
        return violations

    # Check if query also has multi-day DATEDIFF filter
    datediff_fragment = _has_multiday_datediff_filter(tree, aliases)
    if datediff_fragment:
        violations.append(
            f"Query filters outpatient visits (visit_concept_id = {OUTPATIENT_CONCEPT_ID}) "
            f"with multi-day date range: {datediff_fragment}. "
            f"Outpatient visits are typically same-day (start_date = end_date). "
            f"Consider using inpatient visits (visit_concept_id = {INPATIENT_CONCEPT_ID}) "
            f"for multi-day stay analysis."
        )

    return violations


# --- Rule ------------------------------------------------------------------

@register
class VisitOutpatientSameDayValidationRule(Rule):
    """Validate outpatient visits are not filtered with multi-day date logic."""

    rule_id = "domain_specific.visit_outpatient_same_day_validation"
    name = "Visit Outpatient Same-Day Validation"

    description = (
        "Detects queries that filter outpatient visits (visit_concept_id = 9202) "
        "with multi-day date range logic, which may indicate confusion with inpatient logic."
    )

    severity = Severity.WARNING
    suggested_fix = (
        "Use visit_concept_id = 9201 (inpatient) for multi-day stays, "
        "or adjust date range for outpatient visits"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            # Skip if no visit_occurrence table
            if not has_table_reference(tree, TABLE_NAME):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        details={
                            "table": TABLE_NAME,
                            "outpatient_concept_id": OUTPATIENT_CONCEPT_ID,
                            "inpatient_concept_id": INPATIENT_CONCEPT_ID,
                        },
                    )
                )

        return violations


__all__ = ["VisitOutpatientSameDayValidationRule"]

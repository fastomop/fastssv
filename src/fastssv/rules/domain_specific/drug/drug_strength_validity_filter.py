"""Drug Strength Validity Filter Rule.

OMOP semantic rule OMOP_064:
drug_strength records have valid_start_date and valid_end_date.
Queries should filter for current validity when looking up strength information,
as formulations change over time.

The Problem:
    drug_strength is a vocabulary table with temporal validity:
    - Same drug_concept_id may have multiple strength records over time
    - Formulations change (new strengths, discontinued strengths)
    - invalid_reason IS NULL indicates currently valid records

    Querying without validity filters returns BOTH current AND historical strengths
    → incorrect calculations, duplicate results

Example impact:
    -- Returns 3 rows: old formulation (100mg), current (150mg), deprecated (200mg)
    SELECT drug_concept_id, amount_value, amount_unit_concept_id
    FROM drug_strength
    WHERE drug_concept_id = 19078461
    -- Which strength is correct? Calculation uses wrong value!

Violation pattern:
    SELECT amount_value
    FROM drug_strength
    WHERE drug_concept_id = 123
    -- No validity filter!

Correct patterns:
    -- Option 1: Filter invalid_reason (most common)
    SELECT amount_value
    FROM drug_strength
    WHERE drug_concept_id = 123
      AND invalid_reason IS NULL

    -- Option 2: Date range check
    SELECT amount_value
    FROM drug_strength
    WHERE drug_concept_id = 123
      AND CURRENT_DATE BETWEEN valid_start_date AND valid_end_date

    -- Option 3: Check valid_end_date
    SELECT amount_value
    FROM drug_strength
    WHERE drug_concept_id = 123
      AND valid_end_date >= CURRENT_DATE
"""

from typing import Dict, List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

DRUG_STRENGTH = "drug_strength"

INVALID_REASON = "invalid_reason"
VALID_START_DATE = "valid_start_date"
VALID_END_DATE = "valid_end_date"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_ds_column(col: exp.Column, aliases: Dict[str, str], target: str) -> bool:
    """Check if column is from drug_strength table."""
    table, col_name = resolve_table_col(col, aliases)

    # Column name must match
    if _norm(col_name) != target:
        return False

    # If qualified, table must be drug_strength
    if table:
        return _norm(table) == DRUG_STRENGTH

    # If unqualified, check if drug_strength is in the query
    return DRUG_STRENGTH in [_norm(t) for t in aliases.values()]


def _is_current_date(node: exp.Expression) -> bool:
    """Detect CURRENT_DATE (dialect-safe)."""
    if isinstance(node, exp.CurrentDate):
        return True
    if isinstance(node, exp.Func):
        name = _norm(node.sql_name() if hasattr(node, "sql_name") else str(node.key))
        return name == "current_date"
    return False


# --- Validity Patterns -----------------------------------------------------

def _has_invalid_reason_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Detect any filter on invalid_reason (IS NULL, equality, etc)."""
    # Check for IS NULL
    for node in tree.find_all(exp.Is):
        if isinstance(node.expression, exp.Null):
            if isinstance(node.this, exp.Column):
                if _is_ds_column(node.this, aliases, INVALID_REASON):
                    return True

    # Check for any other comparison (=, !=, IN, etc)
    for node in tree.walk():
        if isinstance(node, (exp.EQ, exp.NEQ, exp.In)):
            if isinstance(node.this, exp.Column):
                if _is_ds_column(node.this, aliases, INVALID_REASON):
                    return True

    return False


def _has_between_validity(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Detect CURRENT_DATE BETWEEN valid_start_date AND valid_end_date."""
    for between in tree.find_all(exp.Between):
        value = between.this
        low = between.args.get("low")
        high = between.args.get("high")

        if not (_is_current_date(value)):
            continue

        if isinstance(low, exp.Column) and isinstance(high, exp.Column):
            if (
                _is_ds_column(low, aliases, VALID_START_DATE)
                and _is_ds_column(high, aliases, VALID_END_DATE)
            ):
                return True

        # also allow reversed order
        if isinstance(low, exp.Column) and isinstance(high, exp.Column):
            if (
                _is_ds_column(low, aliases, VALID_END_DATE)
                and _is_ds_column(high, aliases, VALID_START_DATE)
            ):
                return True

    return False


def _has_date_validity(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """
    Detect any validity date filter:
        - valid_start_date <= CURRENT_DATE (alone or with end date)
        - valid_end_date >= CURRENT_DATE (alone or with start date)
    """
    has_start = False
    has_end = False

    for node in tree.walk():

        # valid_start_date <= CURRENT_DATE
        if isinstance(node, (exp.LTE, exp.LT)):
            left, right = node.this, node.expression

            if isinstance(left, exp.Column) and _is_ds_column(left, aliases, VALID_START_DATE):
                if _is_current_date(right):
                    has_start = True

            if isinstance(right, exp.Column) and _is_ds_column(right, aliases, VALID_START_DATE):
                if _is_current_date(left):
                    has_start = True

        # valid_end_date >= CURRENT_DATE
        if isinstance(node, (exp.GTE, exp.GT)):
            left, right = node.this, node.expression

            if isinstance(left, exp.Column) and _is_ds_column(left, aliases, VALID_END_DATE):
                if _is_current_date(right):
                    has_end = True

            if isinstance(right, exp.Column) and _is_ds_column(right, aliases, VALID_END_DATE):
                if _is_current_date(left):
                    has_end = True

    # Accept either start OR end date check, or both
    return has_start or has_end


def _has_validity_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Strict validity detection."""
    return (
        _has_invalid_reason_filter(tree, aliases)
        or _has_between_validity(tree, aliases)
        or _has_date_validity(tree, aliases)
    )


# --- Detection -------------------------------------------------------------

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    if not has_table_reference(tree, DRUG_STRENGTH):
        return []

    if _has_validity_filter(tree, aliases):
        return []

    return [
        "drug_strength queried without proper validity filter. "
        "Use 'invalid_reason IS NULL' or "
        "'CURRENT_DATE BETWEEN valid_start_date AND valid_end_date'."
    ]


# --- Rule ------------------------------------------------------------------

@register
class DrugStrengthValidityFilterRule(Rule):
    """Production-grade validation of drug_strength temporal validity."""

    rule_id = "domain_specific.drug_strength_validity_filter"
    name = "Drug Strength Validity Filter"
    description = (
        "drug_strength is time-versioned. Queries must filter for currently valid records."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Add 'invalid_reason IS NULL' OR "
        "'CURRENT_DATE BETWEEN valid_start_date AND valid_end_date'."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "drug_strength" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["DrugStrengthValidityFilterRule"]
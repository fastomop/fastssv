"""Drug Days Supply Validation Rule.

OMOP semantic rule CLIN_016:
Validates that drug_exposure.days_supply contains plausible values.

CLIN_016 (days_supply): Should be a positive integer in a plausible range (typically 1-365)

The Problem:
    days_supply in the drug_exposure table should contain plausible values.
    Values <= 0 or > 365 indicate data quality issues or query logic errors.

    Common mistakes:
    - days_supply = -30 (negative value)
    - days_supply = 0 (zero value)
    - days_supply = 500 (unrealistically long supply)

Violation patterns:
    SELECT * FROM drug_exposure WHERE days_supply = -30
    -- Negative values are invalid

    SELECT * FROM drug_exposure WHERE days_supply = 0
    -- Zero is not a plausible supply duration

    SELECT * FROM drug_exposure WHERE days_supply > 400
    -- Values over 365 days are implausible (more than 1 year)

Correct patterns:
    SELECT * FROM drug_exposure WHERE days_supply = 30
    SELECT * FROM drug_exposure WHERE days_supply BETWEEN 1 AND 90
    SELECT * FROM drug_exposure WHERE days_supply IN (7, 14, 30, 90)
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
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

MIN_DAYS_SUPPLY = 1
MAX_DAYS_SUPPLY = 365

TABLE_NAME = "drug_exposure"
COLUMN_NAME = "days_supply"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_days_supply_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    """Check if column is drug_exposure.days_supply."""
    table, col_name = resolve_table_col(col, aliases)
    table_norm = _norm(table) if table else None
    col_norm = _norm(col_name)

    if col_norm != COLUMN_NAME:
        return False

    # If table is specified, must be drug_exposure
    if table_norm:
        return table_norm == TABLE_NAME

    # If no table specified, only allow in single-table queries
    tables = list(aliases.values())
    return len(tables) == 1 and _norm(tables[0]) == TABLE_NAME


def _extract_int(node: exp.Expression) -> Optional[int]:
    """Extract integer value from literal (including negative numbers)."""
    # Handle negative numbers
    if isinstance(node, exp.Neg):
        inner = node.this
        if isinstance(inner, exp.Literal) and inner.is_int:
            try:
                return -int(inner.this)
            except Exception:
                return None
        return None

    # Handle positive numbers
    if isinstance(node, exp.Literal) and node.is_int:
        try:
            return int(node.this)
        except Exception:
            return None

    return None


def _validate_value(value: int) -> Optional[str]:
    """Check if days_supply value is in plausible range."""
    if value < MIN_DAYS_SUPPLY:
        return (
            f"days_supply = {value} is below minimum plausible value {MIN_DAYS_SUPPLY}. "
            f"days_supply should be a positive integer representing the number of days "
            f"of medication supplied."
        )
    if value > MAX_DAYS_SUPPLY:
        return (
            f"days_supply = {value} is above maximum plausible value {MAX_DAYS_SUPPLY}. "
            f"Values over 1 year (365 days) are typically unrealistic for a single "
            f"drug dispense."
        )
    return None


# --- Detection -------------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[str]:
    issues = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        # --- Binary comparisons ---
        if isinstance(node, (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                if not _is_days_supply_column(col_node, aliases):
                    continue

                value = _extract_int(val_node)
                if value is None:
                    continue

                error_msg = _validate_value(value)
                if error_msg:
                    key = f"{value}|{type(node).__name__}"
                    if key in seen:
                        continue
                    seen.add(key)
                    issues.append(error_msg)

        # --- BETWEEN ---
        elif isinstance(node, exp.Between):
            col_node = node.this

            if not isinstance(col_node, exp.Column):
                continue

            if not _is_days_supply_column(col_node, aliases):
                continue

            low = _extract_int(node.args.get("low"))
            high = _extract_int(node.args.get("high"))

            for bound_val in [low, high]:
                if bound_val is None:
                    continue

                error_msg = _validate_value(bound_val)
                if error_msg:
                    key = f"between|{bound_val}"
                    if key in seen:
                        continue
                    seen.add(key)
                    issues.append(error_msg)

        # --- IN clause ---
        elif isinstance(node, exp.In):
            col_node = node.this

            if not isinstance(col_node, exp.Column):
                continue

            if not _is_days_supply_column(col_node, aliases):
                continue

            invalid_values = []

            for val in node.expressions or []:
                v = _extract_int(val)
                if v is None:
                    continue

                error_msg = _validate_value(v)
                if error_msg:
                    invalid_values.append(v)

            if invalid_values:
                key = f"in|{tuple(sorted(invalid_values))}"
                if key in seen:
                    continue
                seen.add(key)

                msg = (
                    f"days_supply IN clause contains implausible values: {invalid_values}. "
                    f"Valid range is {MIN_DAYS_SUPPLY} to {MAX_DAYS_SUPPLY} days."
                )
                issues.append(msg)

    return issues


# --- Rule ------------------------------------------------------------------

@register
class DrugDaysSupplyValidationRule(Rule):
    """Validates that drug_exposure.days_supply contains plausible values."""

    rule_id = "domain_specific.drug_days_supply_validation"
    name = "Drug Days Supply Validation"
    description = (
        "Validates that drug_exposure.days_supply is in a plausible range "
        f"({MIN_DAYS_SUPPLY} to {MAX_DAYS_SUPPLY} days). Values outside this range "
        "indicate data quality issues or query logic errors."
    )
    severity = Severity.WARNING
    suggested_fix = (
        f"Ensure days_supply values are between {MIN_DAYS_SUPPLY} and {MAX_DAYS_SUPPLY}"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for message in issues:
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=Severity.WARNING,
                    )
                )

        return violations


__all__ = ["DrugDaysSupplyValidationRule"]

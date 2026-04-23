"""Drug Quantity Validation Rule.

OMOP semantic rule CLIN_019:
Validates that drug_exposure.quantity contains non-negative values.

CLIN_019 (quantity): Should be a non-negative number representing the amount dispensed

The Problem:
    quantity in the drug_exposure table represents the amount dispensed (e.g., 30 tablets).
    Negative values are never valid and indicate data quality issues or query logic errors.

    Common mistakes:
    - quantity = -10 (negative value)
    - quantity < 0 (filtering for negatives)
    - Using negative as sentinel/null value

Violation patterns:
    SELECT * FROM drug_exposure WHERE quantity = -10
    -- Negative quantities are invalid

    SELECT * FROM drug_exposure WHERE quantity < 0
    -- Filtering for negative quantities

Correct patterns:
    SELECT * FROM drug_exposure WHERE quantity = 30
    SELECT * FROM drug_exposure WHERE quantity > 0
    SELECT * FROM drug_exposure WHERE quantity BETWEEN 1 AND 100
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
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

TABLE_NAME = "drug_exposure"
COLUMN_NAME = "quantity"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_quantity_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != COLUMN_NAME:
        return False

    if table:
        return _norm(table) == TABLE_NAME

    # Allow unqualified column if drug_exposure is present anywhere
    return TABLE_NAME in {_norm(t) for t in aliases.values()}


def _extract_numeric(node: exp.Expression) -> Optional[float]:
    """Extract numeric value (int or float), including negatives."""
    if isinstance(node, exp.Neg):
        inner = node.this
        if isinstance(inner, exp.Literal) and inner.is_number:
            try:
                return -float(inner.this)
            except Exception:
                return None
        return None

    if isinstance(node, exp.Literal) and node.is_number:
        try:
            return float(node.this)
        except Exception:
            return None

    return None


# --- Detection -------------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[str]:
    issues: List[str] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        # --- Binary comparisons ---
        if isinstance(node, (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)):
            left, right = node.this, node.expression

            pairs = [
                (left, right, False),   # normal
                (right, left, True),    # reversed
            ]

            for col_node, val_node, reversed_op in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                if not _is_quantity_column(col_node, aliases):
                    continue

                value = _extract_numeric(val_node)
                if value is None:
                    continue

                key = f"{type(node).__name__}|{value}|{reversed_op}"
                if key in seen:
                    continue
                seen.add(key)

                # Case 1: explicit negative value
                if value < 0:
                    issues.append(
                        f"quantity compared to negative value ({value}). "
                        f"quantity should be non-negative."
                    )
                    continue

                # Case 2: filtering for negatives
                if not reversed_op:
                    if isinstance(node, (exp.LT, exp.LTE)) and value <= 0:
                        op_str = "<=" if isinstance(node, exp.LTE) else "<"
                        issues.append(
                            f"quantity {op_str} {value} filters for negative values. "
                            f"quantity should be non-negative."
                        )
                else:
                    if isinstance(node, (exp.GT, exp.GTE)) and value <= 0:
                        op_str = ">=" if isinstance(node, exp.GTE) else ">"
                        issues.append(
                            f"{value} {op_str} quantity filters for negative values. "
                            f"quantity should be non-negative."
                        )

        # --- BETWEEN ---
        elif isinstance(node, exp.Between):
            col_node = node.this
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_quantity_column(col_node, aliases):
                continue

            low = _extract_numeric(node.args.get("low"))
            high = _extract_numeric(node.args.get("high"))

            for bound in [low, high]:
                if bound is not None and bound < 0:
                    key = f"between|{bound}"
                    if key in seen:
                        continue
                    seen.add(key)

                    issues.append(
                        f"quantity BETWEEN contains negative bound ({bound}). "
                        f"quantity should be non-negative."
                    )

        # --- IN / NOT IN ---
        elif isinstance(node, exp.In):
            col_node = node.this
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_quantity_column(col_node, aliases):
                continue

            negatives = []
            for val in node.expressions or []:
                v = _extract_numeric(val)
                if v is not None and v < 0:
                    negatives.append(v)

            if negatives:
                key = f"in|{tuple(sorted(negatives))}"
                if key in seen:
                    continue
                seen.add(key)

                issues.append(
                    f"quantity IN clause contains negative values {negatives}. "
                    f"quantity should be non-negative."
                )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class DrugQuantityValidationRule(Rule):
    """Validate that drug_exposure.quantity is non-negative."""

    rule_id = "domain_specific.drug_quantity_validation"
    name = "Drug Quantity Validation"

    description = (
        "Ensures drug_exposure.quantity is non-negative. "
        "Negative values indicate data quality or query logic issues."
    )

    severity = Severity.WARNING
    suggested_fix = "Ensure quantity >= 0"

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
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["DrugQuantityValidationRule"]

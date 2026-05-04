"""Cost Paid Ingredient Cost Drug-Specific Rule.

OMOP semantic rule GAP_020:
cost.paid_ingredient_cost and cost.paid_dispensing_fee are specific to pharmacy/drug
claims. Querying these columns for non-drug cost records (e.g., procedure or visit costs)
returns NULL or meaningless values. Filter cost_domain_id = 'Drug' when using these columns.

The Problem:
    The cost table is polymorphic and stores costs for multiple domains (Drug, Procedure,
    Visit, Device, etc.). However, certain columns are domain-specific:

    Drug-specific columns:
    - paid_ingredient_cost: Cost of the drug ingredient (pharmacy claims only)
    - paid_dispensing_fee: Pharmacy dispensing fee (pharmacy claims only)

    These columns are NULL or meaningless for:
    - Procedure costs (surgeries, medical procedures)
    - Visit costs (hospital stays, ER visits)
    - Device costs (medical devices)
    - Specimen, Measurement, Observation costs

    Querying without domain filtering causes:
    - Incorrect aggregations (NULLs mixed with drug costs)
    - Misleading cost calculations (zeros or NULLs skew averages)
    - Wrong totals (missing domain filter includes irrelevant records)

Common mistakes:
    1. SELECT SUM(paid_ingredient_cost) without domain filter
    2. Aggregating paid_dispensing_fee across all domains
    3. Using drug columns in non-drug cost analysis

Violation pattern:
    SELECT SUM(paid_ingredient_cost)
    FROM cost
    -- WRONG: Includes NULLs from procedure/visit costs, incorrect total!

Correct pattern:
    SELECT SUM(paid_ingredient_cost)
    FROM cost
    WHERE cost_domain_id = 'Drug'
    -- Correct: Only drug costs where paid_ingredient_cost is meaningful
"""

from typing import Dict, List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

COST = "cost"
COST_DOMAIN_ID = "cost_domain_id"
DRUG_DOMAIN = "drug"

DRUG_SPECIFIC_COLUMNS = {
    "paid_ingredient_cost",
    "paid_dispensing_fee",
}


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_cost(table: Optional[str]) -> bool:
    return _norm(table) == COST if table else False


def _is_drug_specific_column(col: Optional[str]) -> bool:
    return _norm(col) in DRUG_SPECIFIC_COLUMNS


def _has_drug_specific_columns(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    """Check if query uses any drug-specific cost columns."""
    for col in tree.find_all(exp.Column):
        table, column = resolve_table_col(col, aliases)

        if not column:
            continue

        # Check if it's a drug-specific column
        if not _is_drug_specific_column(column):
            continue

        # If qualified, must be from cost table
        if table and not _is_cost(table):
            continue

        # If unqualified, check if cost table is in query
        if not table and not has_table_reference(tree, COST):
            continue

        return True

    return False


def _has_drug_domain_filter(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    """Check if query filters cost_domain_id = 'Drug' in WHERE or JOIN."""

    # Check WHERE clauses
    for where in tree.find_all(exp.Where):
        if _check_domain_filter_in_node(where, aliases):
            return True

    # Check JOIN ON clauses
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause and _check_domain_filter_in_node(on_clause, aliases):
            return True

    return False


def _check_domain_filter_in_node(
    node: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    """Check if node contains cost_domain_id = 'Drug' filter."""

    # Check equality: cost_domain_id = 'Drug'
    for eq in node.find_all(exp.EQ):
        left, right = eq.this, eq.expression

        # Normalize direction
        if is_string_literal(left) and isinstance(right, exp.Column):
            left, right = right, left

        if not isinstance(left, exp.Column):
            continue

        table, column = resolve_table_col(left, aliases)

        # Must be cost_domain_id
        if _norm(column) != COST_DOMAIN_ID:
            continue

        # If qualified, must be cost table
        if table and not _is_cost(table):
            continue

        # Check if value is 'Drug'
        if is_string_literal(right):
            value = _norm(right.this)
            if value == DRUG_DOMAIN:
                return True

    # Check IN: cost_domain_id IN ('Drug', ...)
    for in_expr in node.find_all(exp.In):
        col = in_expr.this

        if not isinstance(col, exp.Column):
            continue

        table, column = resolve_table_col(col, aliases)

        # Must be cost_domain_id
        if _norm(column) != COST_DOMAIN_ID:
            continue

        # If qualified, must be cost table
        if table and not _is_cost(table):
            continue

        # Check if 'Drug' is in the IN list
        for val in in_expr.expressions:
            if is_string_literal(val) and _norm(val.this) == DRUG_DOMAIN:
                return True

    return False


# --- Rule ------------------------------------------------------------------


@register
class CostPaidIngredientCostDrugSpecificRule(Rule):
    """Ensure drug-specific cost columns are only used with Drug domain filter."""

    rule_id = "domain_specific.cost_paid_ingredient_cost_drug_specific"
    name = "Cost Drug-Specific Columns Require Domain Filter"

    description = (
        "cost.paid_ingredient_cost and cost.paid_dispensing_fee are pharmacy-specific "
        "columns that are NULL or meaningless for non-drug costs. Filter by "
        "cost_domain_id = 'Drug' when using these columns."
    )

    severity = Severity.WARNING

    suggested_fix = "ADD: `WHERE c.cost_domain_id = 'Drug'` before reading paid_ingredient_cost / paid_dispensing_fee. These columns are NULL or meaningless for non-pharmacy cost rows."
    example_bad = "SELECT cost_id, paid_ingredient_cost FROM cost;"
    example_good = "SELECT cost_id, paid_ingredient_cost FROM cost\nWHERE cost_domain_id = 'Drug';"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "cost" not in sql.lower():
            return []

        # Quick check for drug-specific columns
        sql_lower = sql.lower()
        if not any(col in sql_lower for col in DRUG_SPECIFIC_COLUMNS):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, COST):
                continue

            aliases = extract_aliases(tree)

            # Check if query uses drug-specific columns
            if not _has_drug_specific_columns(tree, aliases):
                continue

            # Check if query has Drug domain filter
            if _has_drug_domain_filter(tree, aliases):
                continue

            # Violation: drug-specific columns without domain filter
            violations.append(
                self.create_violation(
                    message=(
                        "Drug-specific cost columns (paid_ingredient_cost, paid_dispensing_fee) "
                        "used without cost_domain_id = 'Drug' filter. These columns are NULL "
                        "or meaningless for non-drug costs."
                    ),
                    severity=self.severity,
                    suggested_fix=self.suggested_fix,
                    details={
                        "issue": "missing_drug_domain_filter",
                        "columns": list(DRUG_SPECIFIC_COLUMNS),
                    },
                )
            )

        return violations


__all__ = ["CostPaidIngredientCostDrugSpecificRule"]

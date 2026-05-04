"""Cost Currency Concept ID For Multi-Currency Rule.

OMOP semantic rule OMOP_112:
In multi-site studies, cost.currency_concept_id identifies the currency of
financial amounts. Aggregating cost amounts across records without filtering
or grouping by currency_concept_id mixes currencies, producing meaningless
totals (e.g., adding USD and EUR).

The Problem:
    The cost table stores amounts in whatever currency the source data used.
    Without constraining currency_concept_id, SUM/AVG across records may add
    dollars and euros together, which is financially and analytically incorrect.

    Affected columns:
        total_charge   - billed amount
        total_cost     - actual cost
        total_paid     - total amount paid
        paid_by_payer  - portion paid by payer
        paid_by_patient - portion paid by patient
        paid_patient_copay        - patient copay portion
        paid_patient_coinsurance  - patient coinsurance portion
        paid_patient_deductible   - patient deductible portion

Violation patterns:
    SELECT SUM(total_paid) FROM cost
    -- WARNING: Sums across all currencies

    SELECT AVG(total_charge) FROM cost WHERE cost_type_concept_id = 32
    -- WARNING: type filter doesn't help — still mixes currencies

Correct patterns:
    SELECT currency_concept_id, SUM(total_paid)
    FROM cost
    GROUP BY currency_concept_id
    -- OK: grouped by currency

    SELECT SUM(total_paid)
    FROM cost
    WHERE currency_concept_id = 44818668 -- USD
    -- OK: filtered to single currency
"""

import logging
from typing import Dict, List, Optional, Set

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


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

TABLE_NAME = "cost"
CURRENCY_COLUMN = "currency_concept_id"

# Financial amount columns subject to currency-mixing risk
AMOUNT_COLUMNS: Set[str] = {
    "total_charge",
    "total_cost",
    "total_paid",
    "paid_by_payer",
    "paid_by_patient",
    "paid_patient_copay",
    "paid_patient_coinsurance",
    "paid_patient_deductible",
}

# Aggregation function types that produce a single value across rows
AGGREGATION_TYPES = (exp.Sum, exp.Avg, exp.Min, exp.Max)


# --- Helpers -----------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_cost_column(col: exp.Column, aliases: Dict[str, str], col_name: str) -> bool:
    """Return True if col resolves to cost.<col_name>."""
    table, column = resolve_table_col(col, aliases)
    if _norm(column) != col_name:
        return False
    if table:
        return _norm(table) == TABLE_NAME
    # Unqualified: accept if cost table is referenced
    return TABLE_NAME in aliases


def _direct_aggs_in_select(select: exp.Select) -> List[exp.Expression]:
    """
    Return aggregation nodes that are directly within this SELECT's projection list.
    We do NOT recurse into subqueries/CTEs nested inside this SELECT, so each
    aggregation is only attributed to the SELECT that directly owns it.
    """
    result = []
    for expr in select.expressions:  # iterate over SELECT columns / aliases
        for node in expr.walk():
            if isinstance(node, AGGREGATION_TYPES):
                # Make sure we haven't crossed into a nested subquery
                parent = node.parent
                crossed_subquery = False
                while parent is not None and parent is not select:
                    if isinstance(parent, exp.Select):
                        crossed_subquery = True
                        break
                    parent = parent.parent
                if not crossed_subquery:
                    result.append(node)
    return result


def _select_has_aggregation_on_amount(
    select: exp.Select,
    aliases: Dict[str, str],
) -> Optional[str]:
    """
    Return the first amount column found inside a *direct* aggregation in this
    SELECT's projection, or None if no such aggregation exists.
    """
    for agg in _direct_aggs_in_select(select):
        for col in agg.find_all(exp.Column):
            _, col_name = resolve_table_col(col, aliases)
            if _norm(col_name) in AMOUNT_COLUMNS:
                if _is_cost_column(col, aliases, _norm(col_name)):
                    return _norm(col_name)
    return None


def _select_has_currency_filter(
    select: exp.Select,
    aliases: Dict[str, str],
) -> bool:
    """
    Return True if this SELECT (or its WHERE / JOIN ON) has a currency_concept_id
    equality filter to a single value.

    Accepts:
      - currency_concept_id = 44818668
      - currency_concept_id IN (44818668)   ← single element only

    Rejects:
      - currency_concept_id IN (44818668, 44818669)  ← multiple currencies
    """
    where = select.args.get("where")
    if not where:
        return False

    for node in where.find_all((exp.EQ, exp.In)):
        if isinstance(node, exp.EQ):
            for side in (node.this, node.expression):
                if isinstance(side, exp.Column) and _is_cost_column(side, aliases, CURRENCY_COLUMN):
                    return True  # any equality to a single value is fine

        elif isinstance(node, exp.In):
            col = node.this
            if not isinstance(col, exp.Column):
                continue
            if not _is_cost_column(col, aliases, CURRENCY_COLUMN):
                continue
            # Only safe if exactly one literal value
            literals = [v for v in (node.expressions or []) if isinstance(v, exp.Literal)]
            if len(literals) == 1:
                return True
            # Multiple values → not safe (falls through to violation)

    return False


def _select_has_currency_group_by(
    select: exp.Select,
    aliases: Dict[str, str],
) -> bool:
    """
    Return True if GROUP BY includes currency_concept_id.
    """
    group = select.args.get("group")
    if not group:
        return False

    for expr in group.expressions:
        for col in expr.find_all(exp.Column):
            if _is_cost_column(col, aliases, CURRENCY_COLUMN):
                return True
    return False


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues: List[str] = []
    seen: Set[str] = set()

    for select in tree.find_all(exp.Select):
        try:
            agg_col = _select_has_aggregation_on_amount(select, aliases)
            if agg_col is None:
                # Also check if aggregating a column from a subquery that uses cost columns
                if not _select_aggregates_cost_derived_column(select, aliases):
                    continue
                agg_col = "derived cost column"

            if _select_has_currency_filter(select, aliases):
                continue

            if _select_has_currency_group_by(select, aliases):
                continue

            key = select.sql()
            if key in seen:
                continue
            seen.add(key)

            if agg_col == "derived cost column":
                issues.append(
                    "Aggregation on cost-derived column without currency_concept_id "
                    "constraint or GROUP BY. In multi-currency datasets this mixes "
                    "amounts across different currencies (e.g., USD + EUR). "
                    "Add WHERE currency_concept_id = <value> or "
                    "GROUP BY currency_concept_id."
                )
            else:
                issues.append(
                    f"Aggregation on cost.{agg_col} without currency_concept_id "
                    f"constraint or GROUP BY. In multi-currency datasets this mixes "
                    f"amounts across different currencies (e.g., USD + EUR). "
                    f"Add WHERE currency_concept_id = <value> or "
                    f"GROUP BY currency_concept_id."
                )

        except Exception:
            logger.exception("Error while analyzing SELECT node for OMOP_112")

    return issues


def _select_aggregates_cost_derived_column(
    select: exp.Select,
    aliases: Dict[str, str],
) -> bool:
    """
    Check if this SELECT aggregates a column that comes from a subquery
    which uses cost table amount columns.
    """
    # Check if there's aggregation in this SELECT
    aggs = _direct_aggs_in_select(select)
    if not aggs:
        return False

    # Check if FROM clause contains a subquery
    from_clause = select.args.get("from") or select.args.get("from_")
    if not from_clause:
        return False

    for subquery in from_clause.find_all(exp.Subquery):
        # Check if the subquery uses cost table amount columns
        if _subquery_uses_cost_columns(subquery, aliases):
            return True

    return False


def _subquery_uses_cost_columns(subquery: exp.Subquery, aliases: Dict[str, str]) -> bool:
    """Check if a subquery references cost table amount columns."""
    # Extract aliases from within the subquery
    from fastssv.core.helpers import extract_aliases

    subquery_aliases = extract_aliases(subquery)

    for col in subquery.find_all(exp.Column):
        table, col_name = resolve_table_col(col, subquery_aliases)
        if _norm(col_name) in AMOUNT_COLUMNS:
            if table and _norm(table) == TABLE_NAME:
                return True
            # Unqualified: check if cost table is in subquery
            if not table:
                for node in subquery.find_all(exp.Table):
                    if _norm(node.name) == TABLE_NAME:
                        return True
    return False


# --- Rule --------------------------------------------------------------------


@register
class CostCurrencyConceptIdRule(Rule):
    """
    OMOP_112: Aggregating cost amounts without currency_concept_id
    constraint mixes records from different currencies.
    """

    rule_id = "domain_specific.cost_currency_concept_id"
    name = "Cost Currency Concept ID For Multi-Currency"

    description = (
        "Aggregating cost amount columns (total_paid, total_charge, etc.) "
        "without filtering or grouping by currency_concept_id mixes records "
        "from different currencies, producing incorrect financial totals."
    )

    severity = Severity.WARNING

    suggested_fix = "ADD: `WHERE c.currency_concept_id = <currency_id>` (or IN(...)) before aggregating cost amount columns, OR GROUP BY c.currency_concept_id so each aggregate row is in one currency."
    long_description = (
        "Records in the cost table carry a currency_concept_id indicating "
        "which currency their amounts are denominated in. Summing total_paid "
        "or total_charge across mixed currencies, without filtering or "
        "grouping by currency_concept_id, produces a meaningless total "
        "(literally GBP + USD + EUR). Either restrict the query to a single "
        "currency, or aggregate per currency so downstream code can convert "
        "before rolling the figures up further."
    )
    example_bad = "SELECT SUM(total_paid) AS paid_total\nFROM cost;"
    example_good = (
        "SELECT SUM(total_paid) AS paid_total\nFROM cost\nWHERE currency_concept_id = 44818668;  -- US Dollar"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter: must reference the cost table and an amount column
        if TABLE_NAME not in sql_lower:
            return []
        if not any(col in sql_lower for col in AMOUNT_COLUMNS):
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_112",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, TABLE_NAME):
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


__all__ = ["CostCurrencyConceptIdRule"]

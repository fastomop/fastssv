"""Cost Payer Plan Period ID Join Rule.

OMOP semantic rule OMOP_135:
cost.payer_plan_period_id is a FK to payer_plan_period.payer_plan_period_id.
Joining cost to payer_plan_period on person_id or cost_event_id is incorrect.

The Problem:
    The cost table has a payer_plan_period_id column (INTEGER FK) that references
    payer_plan_period.payer_plan_period_id (INTEGER PK). This is the correct join key.

    Common mistakes:
    1. Joining cost.person_id = payer_plan_period.person_id
       - Both tables have person_id, but this will match ALL payer periods for that person
       - A person can have multiple insurance periods over time
       - This produces incorrect many-to-many joins

    2. Joining cost.cost_event_id = payer_plan_period.payer_plan_period_id
       - Type matches (both INTEGER) but semantics are completely wrong
       - cost_event_id is a polymorphic FK to clinical events, not payer periods

    3. Other incorrect column pairs
       - Any join not using payer_plan_period_id is wrong

Why this is wrong:
    The payer_plan_period_id FK exists specifically to link costs to the correct
    insurance period. Using other columns:
    - Returns incorrect cost-to-payer associations
    - Produces duplicate/missing records
    - Breaks financial analytics and reimbursement calculations

Violation patterns:
    SELECT * FROM cost c
    JOIN payer_plan_period pp ON c.cost_event_id = pp.payer_plan_period_id
    -- ERROR: cost_event_id is not the payer period reference

    SELECT * FROM cost c
    JOIN payer_plan_period pp ON c.person_id = pp.person_id
    -- ERROR: person_id will match all payer periods for the patient

    SELECT * FROM cost c
    JOIN payer_plan_period pp ON c.payer_plan_period_id = pp.person_id
    -- ERROR: Wrong column on payer_plan_period side

Correct patterns:
    SELECT * FROM cost c
    JOIN payer_plan_period pp ON c.payer_plan_period_id = pp.payer_plan_period_id
    -- OK: Correct FK to PK join

    SELECT * FROM cost c
    JOIN payer_plan_period pp
      ON c.payer_plan_period_id = pp.payer_plan_period_id
      AND c.person_id = pp.person_id
    -- OK: Correct join key with additional validation

    SELECT * FROM cost c
    WHERE c.payer_plan_period_id IN (SELECT payer_plan_period_id FROM payer_plan_period)
    -- OK: Not a join, just subquery validation

Note:
    This is an ERROR, not a WARNING. The cost table schema requires joining
    via payer_plan_period_id for correct cost-to-payer associations.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

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

COST_TABLE = "cost"
PAYER_PLAN_PERIOD_TABLE = "payer_plan_period"
PAYER_PLAN_PERIOD_ID_COL = "payer_plan_period_id"


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_cost(table: Optional[str]) -> bool:
    return table == COST_TABLE


def _is_payer_plan_period(table: Optional[str]) -> bool:
    return table == PAYER_PLAN_PERIOD_TABLE


def _is_ppp_id(col: Optional[str]) -> bool:
    return col == PAYER_PLAN_PERIOD_ID_COL


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    """Collect CTE names to avoid shadowing real tables."""
    return {
        _norm(cte.alias_or_name)
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _resolve_column(
    column: exp.Column,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve table + column safely, excluding CTE shadowing."""
    table, col = resolve_table_col(column, aliases)
    table = _norm(table)
    col = _norm(col)

    if table in cte_names:
        return None, None

    return table, col


def _is_cost_ppp_pair(t1: str, t2: str) -> bool:
    return (
        (_is_cost(t1) and _is_payer_plan_period(t2))
        or (_is_payer_plan_period(t1) and _is_cost(t2))
    )


def _is_valid_join(
    t1: str, c1: str,
    t2: str, c2: str,
) -> bool:
    return (
        _is_cost_ppp_pair(t1, t2)
        and _is_ppp_id(c1)
        and _is_ppp_id(c2)
    )


def _analyze_conditions(
    condition: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> Tuple[bool, bool]:
    """
    Returns:
        (is_cost_ppp_join, has_correct_join)
    """
    is_relevant = False
    has_correct = False

    for eq in condition.find_all(exp.EQ):
        left, right = eq.this, eq.expression

        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        t1, c1 = _resolve_column(left, aliases, cte_names)
        t2, c2 = _resolve_column(right, aliases, cte_names)

        if not t1 or not t2:
            continue

        if _is_cost_ppp_pair(t1, t2):
            is_relevant = True

            if _is_valid_join(t1, c1, t2, c2):
                has_correct = True

    return is_relevant, has_correct


def _check_joins(tree: exp.Expression) -> List[str]:
    issues: List[str] = []

    if not has_table_reference(tree, COST_TABLE) or not has_table_reference(tree, PAYER_PLAN_PERIOD_TABLE):
        return issues

    aliases = extract_aliases(tree)
    cte_names = _extract_cte_names(tree)

    # --- Explicit JOINs ---
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        is_relevant, has_correct = _analyze_conditions(on_clause, aliases, cte_names)

        if is_relevant and not has_correct:
            issues.append(
                "JOIN between cost and payer_plan_period missing correct join key. "
                "Expected: cost.payer_plan_period_id = payer_plan_period.payer_plan_period_id"
            )

    # --- Implicit JOINs via WHERE ---
    for where in tree.find_all(exp.Where):
        is_relevant, has_correct = _analyze_conditions(where.this, aliases, cte_names)

        if is_relevant and not has_correct:
            issues.append(
                "Implicit JOIN between cost and payer_plan_period missing correct join key. "
                "Expected: cost.payer_plan_period_id = payer_plan_period.payer_plan_period_id"
            )

    # Deduplicate
    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class CostPayerPlanPeriodIdJoinRule(Rule):
    """
    OMOP_135: Ensure cost joins to payer_plan_period via payer_plan_period_id.
    """

    rule_id = "domain_specific.cost_payer_plan_period_id_join"
    name = "Cost Payer Plan Period ID Join"

    description = (
        "cost.payer_plan_period_id is a FK to payer_plan_period.payer_plan_period_id. "
        "Joins must use this column pair."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: the join condition WITH `c.payer_plan_period_id = ppp.payer_plan_period_id`. cost.payer_plan_period_id is a FK only to payer_plan_period.payer_plan_period_id."
    example_bad = (
        "SELECT c.cost_id FROM cost c\n"
        "JOIN payer_plan_period p ON c.payer_plan_period_id = p.person_id;"
    )
    example_good = (
        "SELECT c.cost_id FROM cost c\n"
        "JOIN payer_plan_period p ON c.payer_plan_period_id = p.payer_plan_period_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if COST_TABLE not in sql_lower or PAYER_PLAN_PERIOD_TABLE not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_135",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            issues = _check_joins(tree)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["CostPayerPlanPeriodIdJoinRule"]

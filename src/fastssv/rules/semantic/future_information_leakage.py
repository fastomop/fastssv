"""Future Information Leakage Rule.

OMOP semantic rule:
When a query compares dates across two different clinical event tables
(e.g. co.condition_start_date > de.drug_exposure_start_date), it must also
bound the future event against observation_period_end_date.

Without this bound, the query implicitly uses information from beyond the
patient's observable follow-up window, introducing temporal bias. In
cohort studies this manifests as immortal time bias or future information
leakage: patients are selected or excluded based on what happens after
the study period ends for them individually.

Correct pattern:
    co.condition_start_date > de.drug_exposure_start_date
    AND co.condition_start_date <= op.observation_period_end_date
"""

from typing import Dict, List, Tuple

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
from .temporal_constraint_mapping import (
    CLINICAL_TABLES_WITH_DATES,
    is_date_column,
)


def _find_cross_table_date_comparisons(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """Find temporal ordering comparisons between date columns from different clinical tables.

    Handles both GT/GTE (a > b) and LT/LTE (a < b, normalized so the later
    event is always in the left position of the returned tuple).

    Returns list of (later_table, later_col, earlier_table, earlier_col) tuples.
    """
    results: List[Tuple[str, str, str, str]] = []
    seen: set = set()

    for node in tree.find_all((exp.GT, exp.GTE, exp.LT, exp.LTE)):
        if not is_in_where_or_join_clause(node):
            continue

        left, right = node.left, node.right

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        left_table, left_col = resolve_table_col(left, aliases)
        right_table, right_col = resolve_table_col(right, aliases)

        if not left_table or not right_table:
            continue

        if not (is_date_column(left_col) and is_date_column(right_col)):
            continue

        # Must be from different clinical tables
        if left_table == right_table:
            continue

        if left_table not in CLINICAL_TABLES_WITH_DATES:
            continue
        if right_table not in CLINICAL_TABLES_WITH_DATES:
            continue

        # Normalize so the "later" event is always first in the tuple.
        # GT/GTE: left > right  → left is later
        # LT/LTE: left < right  → right is later (swap)
        if isinstance(node, (exp.LT, exp.LTE)):
            later_table, later_col = right_table, right_col
            earlier_table, earlier_col = left_table, left_col
        else:
            later_table, later_col = left_table, left_col
            earlier_table, earlier_col = right_table, right_col

        key = (later_table, later_col, earlier_table, earlier_col)
        if key not in seen:
            seen.add(key)
            results.append(key)

    return results


def _has_observation_period_end_bound(tree: exp.Expression) -> bool:
    """Check if the query has an actual upper-bound predicate using observation_period_end_date.

    A bare reference in the SELECT list or an unrelated expression does not count.
    Accepted patterns (must appear in WHERE or JOIN ON):
        col <= op.observation_period_end_date
        col <  op.observation_period_end_date
        op.observation_period_end_date >= col
        op.observation_period_end_date >  col
        col BETWEEN x AND op.observation_period_end_date
    """
    # LT / LTE / GT / GTE with observation_period_end_date on either side
    for node in tree.find_all((exp.LTE, exp.LT, exp.GTE, exp.GT)):
        if not is_in_where_or_join_clause(node):
            continue
        for side in (node.left, node.right):
            if isinstance(side, exp.Column) and normalize_name(side.name) == "observation_period_end_date":
                return True

    # BETWEEN: col BETWEEN x AND observation_period_end_date
    for between in tree.find_all(exp.Between):
        if not is_in_where_or_join_clause(between):
            continue
        high = between.args.get("high")
        if high is not None and isinstance(high, exp.Column):
            if normalize_name(high.name) == "observation_period_end_date":
                return True

    return False


@register
class FutureInformationLeakageRule(Rule):
    """Detects cross-table date comparisons not bounded by observation_period_end_date."""

    rule_id = "semantic.future_information_leakage"
    name = "Future Information Leakage"
    description = (
        "Detects queries that compare dates across different clinical event tables "
        "(e.g. condition_start_date > drug_exposure_start_date) without bounding the "
        "future event against observation_period_end_date. This introduces temporal "
        "bias: patients are implicitly selected based on events beyond their "
        "individual follow-up window."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Add an upper bound using observation_period_end_date: "
        "AND future_event.date <= op.observation_period_end_date, "
        "where op is joined via JOIN observation_period op ON table.person_id = op.person_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)
            cross_comparisons = _find_cross_table_date_comparisons(tree, aliases)

            if not cross_comparisons:
                continue

            if _has_observation_period_end_bound(tree):
                continue

            for later_table, later_col, earlier_table, earlier_col in cross_comparisons:
                violations.append(self.create_violation(
                    message=(
                        f"Query compares {later_table}.{later_col} against "
                        f"{earlier_table}.{earlier_col} without bounding the later event "
                        f"by observation_period_end_date. This uses future information "
                        f"beyond the patient's observable follow-up window, introducing "
                        f"temporal bias."
                    ),
                    details={
                        "later_event": f"{later_table}.{later_col}",
                        "index_event": f"{earlier_table}.{earlier_col}",
                        "missing": "observation_period_end_date upper bound",
                    },
                ))

        return violations


__all__ = ["FutureInformationLeakageRule"]

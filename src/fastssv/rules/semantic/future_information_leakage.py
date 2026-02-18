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
from fastssv.rules.semantic.temporal_constraint_mapping import (
    CLINICAL_TABLES_WITH_DATES,
    _is_date_column,
)


def _find_cross_table_date_comparisons(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """Find GT/GTE comparisons between date columns from different clinical tables.

    Returns list of (left_table, left_col, right_table, right_col) tuples.
    """
    results: List[Tuple[str, str, str, str]] = []
    seen: set = set()

    for node in tree.find_all((exp.GT, exp.GTE)):
        if not is_in_where_or_join_clause(node):
            continue

        left, right = node.left, node.right

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        left_table, left_col = resolve_table_col(left, aliases)
        right_table, right_col = resolve_table_col(right, aliases)

        if not left_table or not right_table:
            continue

        if not (_is_date_column(left_col) and _is_date_column(right_col)):
            continue

        # Must be from different clinical tables
        if left_table == right_table:
            continue

        if left_table not in CLINICAL_TABLES_WITH_DATES:
            continue
        if right_table not in CLINICAL_TABLES_WITH_DATES:
            continue

        key = (left_table, left_col, right_table, right_col)
        if key not in seen:
            seen.add(key)
            results.append(key)

    return results


def _has_observation_period_end_bound(tree: exp.Expression) -> bool:
    """Check if the query references observation_period_end_date anywhere."""
    for col in tree.find_all(exp.Column):
        if normalize_name(col.name) == "observation_period_end_date":
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

            for left_table, left_col, right_table, right_col in cross_comparisons:
                violations.append(self.create_violation(
                    message=(
                        f"Query compares {left_table}.{left_col} against "
                        f"{right_table}.{right_col} without bounding the later event "
                        f"by observation_period_end_date. This uses future information "
                        f"beyond the patient's observable follow-up window, introducing "
                        f"temporal bias."
                    ),
                    details={
                        "later_event": f"{left_table}.{left_col}",
                        "index_event": f"{right_table}.{right_col}",
                        "missing": "observation_period_end_date upper bound",
                    },
                ))

        return violations


__all__ = ["FutureInformationLeakageRule"]

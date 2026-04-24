"""Observation Period Date Range Logic Rule.

OMOP semantic rules OMOP_033, OMOP_512:
When using observation_period to validate patient enrollment, the clinical event
date must fall BETWEEN observation_period_start_date AND observation_period_end_date.

Reversing the logic (testing if period dates fall within event dates) is incorrect
and produces semantically wrong results.

Correct pattern:
    WHERE event_date BETWEEN op.observation_period_start_date
                         AND op.observation_period_end_date

Incorrect pattern (reversed):
    WHERE op.observation_period_start_date BETWEEN event_start_date
                                               AND event_end_date
"""

from typing import Dict, List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


OBSERVATION_PERIOD_TABLE = "observation_period"
OP_START = "observation_period_start_date"
OP_END = "observation_period_end_date"

CLINICAL_TABLES = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "visit_occurrence",
    "visit_detail",
    "device_exposure",
    "episode",
}


# --- Helpers ---------------------------------------------------------------

def _is_op_date(table: str, col: str) -> bool:
    return (
        normalize_name(table) == OBSERVATION_PERIOD_TABLE
        and normalize_name(col) in {OP_START, OP_END}
    )


def _is_event_date(table: str, col: str) -> bool:
    table = normalize_name(table)
    col = normalize_name(col)

    if table not in CLINICAL_TABLES:
        return False

    return (
        col.endswith("_start_date")
        or col.endswith("_end_date")
        or col.endswith("_date")
        or col.endswith("_datetime")
    )


# --- BETWEEN detection -----------------------------------------------------

def _find_between_reversed(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[str]:
    issues = []
    seen: Set[str] = set()

    for node in tree.find_all(exp.Between):
        value = node.this
        low = node.args.get("low")
        high = node.args.get("high")

        if not all(isinstance(x, exp.Column) for x in [value, low, high]):
            continue

        vt, vc = resolve_table_col(value, aliases)
        lt, lc = resolve_table_col(low, aliases)
        ht, hc = resolve_table_col(high, aliases)

        if not all([vt, vc, lt, lc, ht, hc]):
            continue

        # --- reversed logic ---
        if (
            _is_op_date(vt, vc)
            and _is_event_date(lt, lc)
            and _is_event_date(ht, hc)
            and normalize_name(lt) == normalize_name(ht)  # same table
        ):
            key = f"{value.sql()}|{low.sql()}|{high.sql()}"
            if key in seen:
                continue
            seen.add(key)

            issues.append(
                f"Reversed BETWEEN logic: {value.sql()} BETWEEN {low.sql()} AND {high.sql()}. "
                f"Observation period date used as value instead of bounds."
            )

    return issues


# --- >= AND <= detection ---------------------------------------------------

def _find_comparison_reversed(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[str]:
    issues = []
    seen: Set[str] = set()

    comparisons = list(tree.find_all(exp.GTE)) + list(tree.find_all(exp.LTE))

    for node in comparisons:
        left = node.this
        right = node.expression

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not all([lt, lc, rt, rc]):
            continue

        # Look for pattern: op_date >= event_start AND op_date <= event_end
        if _is_op_date(lt, lc) and _is_event_date(rt, rc):
            key = f"{left.sql()}|{right.sql()}"
            if key in seen:
                continue
            seen.add(key)

            issues.append(
                f"Potential reversed date logic: {left.sql()} compared to {right.sql()}. "
                f"Observation period date should not be constrained by event dates."
            )

    return issues


# --- RULE ------------------------------------------------------------------

@register
class ObservationPeriodDateRangeLogicRule(Rule):
    """Robust validation for observation_period date range logic."""

    rule_id = "temporal.observation_period_date_range_logic"
    name = "Observation Period Date Range Logic"
    description = (
        "Ensures clinical event dates are tested within observation_period bounds. "
        "Detects reversed logic where observation_period dates are incorrectly used as values."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Use: event_date BETWEEN op.observation_period_start_date "
        "AND op.observation_period_end_date."
    )
    long_description = (
        "The conventional OMOP invariant is "
        "event_date BETWEEN observation_period_start_date AND "
        "observation_period_end_date: the event is the value being tested, "
        "the observation window is the range. Reversed forms like "
        "observation_period_start_date BETWEEN event_start_date AND "
        "event_end_date produce rows only when the observation window "
        "happens to fall inside a single event, which is almost never what "
        "was intended. It is a classic copy-paste bug from swapping the "
        "BETWEEN operands."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN observation_period op ON co.person_id = op.person_id\n"
        "WHERE op.observation_period_start_date\n"
        "      BETWEEN co.condition_start_date AND co.condition_end_date;"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN observation_period op ON co.person_id = op.person_id\n"
        "WHERE co.condition_start_date\n"
        "      BETWEEN op.observation_period_start_date AND op.observation_period_end_date;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            # BETWEEN issues
            between_issues = _find_between_reversed(tree, aliases)

            # Comparison issues
            comparison_issues = _find_comparison_reversed(tree, aliases)

            for msg in between_issues + comparison_issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["ObservationPeriodDateRangeLogicRule"]

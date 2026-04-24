"""Condition Occurrence Stop Reason Is Free Text Rule.

OMOP semantic rule OMOP_107:
condition_occurrence.stop_reason is a free-text VARCHAR column, not a concept_id.
It should not be joined to the concept table or used in numeric comparisons.

The Problem:
    The condition_occurrence table has a stop_reason column that stores free-text
    explanations for why a condition ended:
    - stop_reason: VARCHAR field (e.g., 'Patient Improved', 'Treatment Completed')

    Developers might mistakenly:
    1. Join stop_reason to the concept table as if it were a concept_id
    2. Use numeric comparisons with stop_reason (treating it like an integer)

    Both patterns are incorrect and will produce unexpected results or errors.

Violation patterns:
    -- WRONG: Joining stop_reason to concept
    SELECT * FROM condition_occurrence co
    JOIN concept c ON co.stop_reason = c.concept_id;

    -- WRONG: Joining to concept_code
    SELECT * FROM condition_occurrence co
    JOIN concept c ON co.stop_reason = c.concept_code;

    -- WRONG: Numeric comparison
    SELECT * FROM condition_occurrence
    WHERE stop_reason = 12345;

Correct patterns:
    -- CORRECT: Using as free text
    SELECT * FROM condition_occurrence
    WHERE stop_reason = 'Patient Improved';

    -- CORRECT: Text filtering
    SELECT * FROM condition_occurrence
    WHERE stop_reason LIKE '%Improved%';

    -- CORRECT: NULL check
    SELECT * FROM condition_occurrence
    WHERE stop_reason IS NOT NULL;
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
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONDITION_OCCURRENCE = "condition_occurrence"
CONCEPT = "concept"
STOP_REASON = "stop_reason"


# --- Normalized Constants --------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


NORM_CONDITION_OCCURRENCE = _norm(CONDITION_OCCURRENCE)
NORM_CONCEPT = _norm(CONCEPT)
NORM_STOP_REASON = _norm(STOP_REASON)


# --- Helpers ---------------------------------------------------------------

def _normalize_aliases(aliases: Dict[str, str]) -> Dict[str, str]:
    return {_norm(k): _norm(v) for k, v in aliases.items()}


def _get_table_aliases(
    aliases: Dict[str, str],
    table_name: str,
) -> Set[str]:
    return {k for k, v in aliases.items() if v == table_name}


def _resolve_stop_reason_column(
    col: exp.Column,
    aliases: Dict[str, str],
    condition_aliases: Set[str],
) -> Optional[Tuple[str, str]]:
    table, col_name = resolve_table_col(col, aliases)

    if not col_name:
        return None

    col_norm = _norm(col_name)
    if col_norm != NORM_STOP_REASON:
        return None

    if table:
        table_norm = _norm(table)
        if table_norm in condition_aliases:
            return table_norm, col_norm
        return None

    if len(condition_aliases) == 1:
        return next(iter(condition_aliases)), col_norm

    return None


def _resolve_concept_column(
    col: exp.Column,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> Optional[Tuple[str, str]]:
    table, col_name = resolve_table_col(col, aliases)

    if not col_name:
        return None

    if table:
        table_norm = _norm(table)
        if table_norm in concept_aliases:
            return table_norm, _norm(col_name)
        return None

    if len(concept_aliases) == 1:
        return next(iter(concept_aliases)), _norm(col_name)

    return None


def _is_numeric_literal(node: exp.Expression) -> bool:
    """Robust numeric literal detection (int, float, negative)."""
    if not isinstance(node, exp.Literal):
        return False
    try:
        float(node.this)
        return True
    except (ValueError, TypeError):
        return False


def _detect_stop_reason_concept_joins(
    select: exp.Select,
    aliases: Dict[str, str],
) -> Set[str]:
    violations: Set[str] = set()

    condition_aliases = _get_table_aliases(aliases, NORM_CONDITION_OCCURRENCE)
    concept_aliases = _get_table_aliases(aliases, NORM_CONCEPT)

    if not condition_aliases or not concept_aliases:
        return violations

    for node in select.walk():
        if not isinstance(node, exp.EQ):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        right = node.expression

        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        left_stop = _resolve_stop_reason_column(left, aliases, condition_aliases)
        right_stop = _resolve_stop_reason_column(right, aliases, condition_aliases)

        left_con = _resolve_concept_column(left, aliases, concept_aliases)
        right_con = _resolve_concept_column(right, aliases, concept_aliases)

        if left_stop and right_con:
            stop_alias, stop_col = left_stop
            con_alias, con_col = right_con
            violations.add(f"{stop_alias}.{stop_col} = {con_alias}.{con_col}")

        elif right_stop and left_con:
            stop_alias, stop_col = right_stop
            con_alias, con_col = left_con
            violations.add(f"{stop_alias}.{stop_col} = {con_alias}.{con_col}")

    return violations


def _detect_stop_reason_numeric_comparisons(
    select: exp.Select,
    aliases: Dict[str, str],
) -> Set[str]:
    violations: Set[str] = set()

    condition_aliases = _get_table_aliases(aliases, NORM_CONDITION_OCCURRENCE)

    if not condition_aliases:
        return violations

    for node in select.walk():
        if not isinstance(node, (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        right = node.expression

        stop_side = None
        numeric_node = None

        if isinstance(left, exp.Column):
            left_stop = _resolve_stop_reason_column(left, aliases, condition_aliases)
            if left_stop and _is_numeric_literal(right):
                stop_side = left_stop
                numeric_node = right

        if isinstance(right, exp.Column) and not stop_side:
            right_stop = _resolve_stop_reason_column(right, aliases, condition_aliases)
            if right_stop and _is_numeric_literal(left):
                stop_side = right_stop
                numeric_node = left

        if stop_side and numeric_node:
            stop_alias, stop_col = stop_side
            violations.add(f"{stop_alias}.{stop_col} = {numeric_node.this}")

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ConditionOccurrenceStopReasonIsFreeTextRule(Rule):
    """Detects incorrect usage of condition_occurrence.stop_reason."""

    rule_id = "data_quality.condition_occurrence_stop_reason_is_free_text"
    name = "Condition Occurrence Stop Reason Is Free Text"

    description = (
        "Ensures that condition_occurrence.stop_reason (free-text VARCHAR field) "
        "is not joined to the concept table or used in numeric comparisons."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Remove joins between condition_occurrence.stop_reason and concept table. "
        "Avoid numeric comparisons with stop_reason. Treat stop_reason as a "
        "free-text field."
    )
    long_description = (
        "condition_occurrence.stop_reason is a free-text VARCHAR field — "
        "clinician-entered notes about why a condition was considered "
        "resolved. It has no mapping into the concept table and no "
        "numeric semantics. Joining it to concept (on concept_name or "
        "any concept column) or doing numeric comparisons on it produces "
        "zero or meaningless rows. Treat it purely as free text."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.stop_reason = c.concept_name;"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if CONDITION_OCCURRENCE not in sql_lower:
            return []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            if not has_table_reference(tree, CONDITION_OCCURRENCE):
                continue

            aliases = _normalize_aliases(extract_aliases(tree))

            seen_patterns: Set[str] = set()

            for select in tree.find_all(exp.Select):
                concept_joins = _detect_stop_reason_concept_joins(select, aliases)
                numeric_comps = _detect_stop_reason_numeric_comparisons(select, aliases)

                all_detected = concept_joins | numeric_comps

                if not all_detected:
                    continue

                for pattern in all_detected:
                    if pattern in seen_patterns:
                        continue

                    seen_patterns.add(pattern)

                    message = (
                        f"Invalid usage detected: {pattern}. "
                        f"condition_occurrence.stop_reason is a free-text field and "
                        f"must not be joined to concept table or used in numeric comparisons."
                    )

                    violations.append(
                        self.create_violation(
                            message=message,
                            suggested_fix=self.suggested_fix,
                            details={
                                "pattern": pattern,
                                "recommendation": (
                                    "Use stop_reason only for text filtering/display. "
                                    "Do not join to concept or compare numerically."
                                ),
                            },
                        )
                    )

        return violations


__all__ = ["ConditionOccurrenceStopReasonIsFreeTextRule"]

"""Concept Relationship Valid Date Range Check Rule.

OMOP semantic rule VOCAB_040:
concept_relationship has valid_start_date and valid_end_date. For current mappings,
the row should have valid_end_date >= CURRENT_DATE (or the far-future sentinel
'2099-12-31'). Relying solely on invalid_reason IS NULL without checking dates may
include relationships that are technically scheduled to expire.

The Problem:
    The concept_relationship table has three temporal validity fields:
    1. valid_start_date: When the relationship became active
    2. valid_end_date: When the relationship becomes/became invalid (default: '2099-12-31')
    3. invalid_reason: NULL (valid), 'D' (deleted), or 'U' (updated)

    Common misconception: Checking invalid_reason IS NULL is sufficient for finding
    current/valid mappings.

    Reality: A relationship can have:
    - invalid_reason = NULL (technically "valid")
    - valid_end_date = '2024-06-30' (expired in the past!)

    This happens when:
    - Relationships are marked for deprecation but haven't been formally invalidated
    - Temporal validity is set proactively for future transitions
    - Vocabulary updates are staged but not yet marked as invalid

    Issues with incomplete temporal checks:
    1. May include expired relationships in current mappings
    2. Historical queries may use relationships that didn't exist at the time
    3. Subtle data quality issues that are hard to detect
    4. Incorrect prevalence estimates and mapping statistics

Violation patterns:
    -- WRONG: Only checks invalid_reason
    SELECT concept_id_2
    FROM concept_relationship
    WHERE concept_id_1 = 44836914
      AND relationship_id = 'Maps to'
      AND invalid_reason IS NULL
    -- May include relationships with valid_end_date in the past!

    -- WRONG: No temporal check at all
    SELECT cr.concept_id_2 AS target_concept
    FROM concept_relationship cr
    WHERE cr.concept_id_1 = 1234
      AND cr.relationship_id = 'Maps to'
    -- Includes everything, even expired relationships!

    -- WRONG: Checks valid_start_date but not valid_end_date
    SELECT concept_id_2
    FROM concept_relationship
    WHERE concept_id_1 = 5678
      AND relationship_id = 'Subsumes'
      AND valid_start_date <= CURRENT_DATE
      AND invalid_reason IS NULL
    -- Still missing valid_end_date check!

Correct patterns:
    -- CORRECT: Full temporal validity check
    SELECT concept_id_2
    FROM concept_relationship
    WHERE concept_id_1 = 44836914
      AND relationship_id = 'Maps to'
      AND invalid_reason IS NULL
      AND valid_end_date >= CURRENT_DATE

    -- CORRECT: Using BETWEEN for temporal validity
    SELECT concept_id_2
    FROM concept_relationship
    WHERE concept_id_1 = 1234
      AND relationship_id = 'Maps to'
      AND invalid_reason IS NULL
      AND CURRENT_DATE BETWEEN valid_start_date AND valid_end_date

    -- CORRECT: Far-future sentinel check
    SELECT concept_id_2
    FROM concept_relationship
    WHERE concept_id_1 = 5678
      AND relationship_id = 'Subsumes'
      AND invalid_reason IS NULL
      AND (valid_end_date = '2099-12-31' OR valid_end_date >= CURRENT_DATE)

    -- ACCEPTABLE: Historical/retrospective query with explicit date
    SELECT concept_id_2
    FROM concept_relationship
    WHERE concept_id_1 = 1234
      AND relationship_id = 'Maps to'
      AND invalid_reason IS NULL
      AND '2015-06-01' BETWEEN valid_start_date AND valid_end_date
    -- Clear historical context
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_RELATIONSHIP = "concept_relationship"

CURRENT_DATE_FUNCTIONS = {
    "current_date",
    "current_timestamp",
    "now",
    "sysdate",
    "getdate",
    "curdate",
}

FAR_FUTURE_SENTINEL = "2099-12-31"


# --- Helpers ---------------------------------------------------------------

def _norm(val: Optional[str]) -> Optional[str]:
    return normalize_name(val) if val else None


def _has_concept_relationship_table(tree: exp.Expression) -> bool:
    return any(_norm(t.name) == _norm(CONCEPT_RELATIONSHIP) for t in tree.find_all(exp.Table))


def _is_current_date_expr(node: exp.Expression) -> bool:
    """Detect CURRENT_DATE / NOW() / etc."""
    if isinstance(node, (exp.CurrentDate, exp.CurrentTimestamp)):
        return True

    if isinstance(node, exp.Anonymous):
        return _norm(node.this) in CURRENT_DATE_FUNCTIONS

    if isinstance(node, exp.Column):
        return _norm(node.name) in CURRENT_DATE_FUNCTIONS

    return False


def _has_invalid_reason_null_check(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    for node in tree.find_all(exp.Is):
        if not isinstance(node.expression, exp.Null):
            continue

        if isinstance(node.parent, exp.Not):
            continue  # IS NOT NULL

        col = node.this
        if not isinstance(col, exp.Column):
            continue

        table, col_name = resolve_table_col(col, aliases)

        if table and _norm(table) != _norm(CONCEPT_RELATIONSHIP):
            continue

        if _norm(col_name) != "invalid_reason":
            continue

        return True

    return False


def _has_valid_date_check(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    for col in tree.find_all(exp.Column):
        table, col_name = resolve_table_col(col, aliases)

        if table and _norm(table) != _norm(CONCEPT_RELATIONSHIP):
            continue

        parent = col.parent

        # --- valid_end_date >= CURRENT_DATE ---
        if _norm(col_name) == "valid_end_date":
            if isinstance(parent, (exp.GTE, exp.GT)):
                for expr in parent.find_all(exp.Expression):
                    if _is_current_date_expr(expr):
                        return True

            # valid_end_date = '2099-12-31'
            if isinstance(parent, exp.EQ):
                for expr in parent.find_all(exp.Literal):
                    if expr.is_string and FAR_FUTURE_SENTINEL in str(expr.this):
                        return True

        # --- BETWEEN (temporal) ---
        if isinstance(parent, exp.Between):
            for expr in parent.find_all(exp.Expression):
                if _is_current_date_expr(expr):
                    return True
            # Also accept historical queries where pivot is a date string literal
            if isinstance(parent.this, exp.Literal) and parent.this.is_string:
                return True

    return False


# --- Rule ------------------------------------------------------------------

@register
class ConceptRelationshipValidDateRangeCheckRule(Rule):
    """Detect incomplete temporal validity checks on concept_relationship."""

    rule_id = "concept_standardization.concept_relationship_valid_date_range_check"
    name = "Concept Relationship Valid Date Range Check"

    description = (
        "concept_relationship has temporal validity fields. Checking only "
        "invalid_reason IS NULL may include expired relationships."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Add temporal validity check: "
        "valid_end_date >= CURRENT_DATE or "
        "CURRENT_DATE BETWEEN valid_start_date AND valid_end_date"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if "concept_relationship" not in sql_lower:
            return []
        if "invalid_reason" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            if not _has_concept_relationship_table(tree):
                continue

            aliases = extract_aliases(tree)

            if not _has_invalid_reason_null_check(tree, aliases):
                continue

            if _has_valid_date_check(tree, aliases):
                continue

            key = "invalid_reason_without_valid_date"
            if key in seen:
                continue
            seen.add(key)

            violations.append(
                self.create_violation(
                    message=(
                        "Query filters concept_relationship by invalid_reason IS NULL "
                        "but lacks temporal validity filtering (valid_end_date / valid_start_date)."
                    ),
                    severity=Severity.WARNING,
                    suggested_fix=(
                        "Add: valid_end_date >= CURRENT_DATE OR "
                        "CURRENT_DATE BETWEEN valid_start_date AND valid_end_date"
                    ),
                    details={
                        "has_invalid_reason_check": True,
                        "has_valid_date_check": False,
                    },
                )
            )

        return violations


__all__ = ["ConceptRelationshipValidDateRangeCheckRule"]
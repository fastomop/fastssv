"""UNION vs UNION ALL for Clinical Events Rule.

OMOP semantic rule GAP_036:
UNION removes duplicates, which is rarely appropriate for clinical event data
because two identical-looking rows may represent legitimately separate events
(e.g., two ER visits on the same day). Use UNION ALL to preserve all records
unless deduplication is explicitly intended.

The Problem:
    UNION removes duplicates by sorting and deduplicating results. For clinical
    event data, this is almost always wrong because:

    1. Legitimate duplicates: Two events that look identical are still separate
       - Two ER visits on same day (morning: chest pain, evening: injury)
       - Two drug prescriptions on same day (different physicians/episodes)
       - Multiple measurements on same day (repeated tests, different encounters)
       - Two procedures on same day (staged surgeries, emergency + planned)

    2. Silent data loss: UNION drops events without warning or error

    3. Incorrect counts: Event counting becomes inaccurate
       - Patient had 5 visits, but UNION shows 3
       - Cost analysis missing events

    4. Performance: UNION is slower (must sort and deduplicate)

    Clinical events have unique primary keys (condition_occurrence_id,
    drug_exposure_id, etc.) that make them distinct even if other columns
    appear identical. UNION operates on selected columns only, not primary keys.

Common mistakes:
    1. Using UNION by default (learned from non-temporal data)
    2. Assuming duplicates are data quality issues (they're not for events)
    3. Not understanding that identical appearance ≠ same event
    4. Trying to "clean" data that doesn't need cleaning

Violation pattern:
    SELECT person_id, condition_start_date AS event_date
    FROM condition_occurrence
    WHERE condition_concept_id = 201826
    UNION
    SELECT person_id, drug_exposure_start_date
    FROM drug_exposure
    WHERE drug_concept_id = 1125315
    -- WRONG: Two ER visits same day are dropped to one!

    SELECT person_id, visit_start_date
    FROM visit_occurrence
    WHERE visit_concept_id = 9203
    UNION
    SELECT person_id, visit_start_date
    FROM visit_detail
    WHERE visit_detail_concept_id = 9201
    -- WRONG: Multiple visits/details on same day are deduplicated!

Correct pattern:
    SELECT person_id, condition_start_date AS event_date
    FROM condition_occurrence
    WHERE condition_concept_id = 201826
    UNION ALL
    SELECT person_id, drug_exposure_start_date
    FROM drug_exposure
    WHERE drug_concept_id = 1125315
    -- CORRECT: Preserves all events, even if they look identical

Note: UNION without ALL is acceptable for:
    - Vocabulary tables (concept, vocabulary, domain, etc.)
    - Deduplication is explicitly intended and documented
    - Non-event data where duplicates are truly errors
"""

from typing import List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CLINICAL_EVENT_TABLES: Set[str] = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "device_exposure",
    "visit_occurrence",
    "visit_detail",
    "specimen",
    "note",
    "episode",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_clinical_event_table(table: Optional[str]) -> bool:
    return _norm(table) in CLINICAL_EVENT_TABLES if table else False


def _has_clinical_event_table(node: exp.Expression) -> bool:
    """Check if any part of a query branch references clinical event tables."""
    return any(
        _is_clinical_event_table(t.name)
        for t in node.find_all(exp.Table)
    )


def _is_union_without_all(union: exp.Union) -> bool:
    """
    Detect UNION (deduplicating) vs UNION ALL.

    sqlglot uses:
    - distinct=True  -> UNION (removes duplicates)
    - distinct=False -> UNION ALL (keeps all rows)
    """
    return union.args.get("distinct", False) is True


def _is_safely_deduplicated(union: exp.Union) -> bool:
    """
    Suppress warning if parent SELECT explicitly uses DISTINCT,
    indicating intentional deduplication.
    """
    parent = union.parent

    while parent:
        if isinstance(parent, exp.Select):
            if parent.args.get("distinct"):
                return True
            return False
        parent = parent.parent

    return False


# --- Rule ------------------------------------------------------------------

@register
class UnionVsUnionAllClinicalEventsRule(Rule):
    """Detect UNION without ALL for clinical event tables."""

    rule_id = "data_quality.union_vs_union_all_clinical_events"
    name = "UNION vs UNION ALL for Clinical Events"

    description = (
        "Detects UNION (without ALL) when combining clinical event data. "
        "UNION removes duplicates, but identical-looking rows may represent "
        "distinct clinical events. Use UNION ALL to preserve all events."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Replace UNION with UNION ALL to preserve all clinical events. "
        "If deduplication is intentional, document it explicitly."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        # --- Fast pre-check ---
        if "union" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            for union in tree.find_all(exp.Union):
                # Skip UNION ALL
                if not _is_union_without_all(union):
                    continue

                # Skip safe intentional deduplication
                if _is_safely_deduplicated(union):
                    continue

                # Check both branches (robust to nesting)
                if not (
                    (union.this and _has_clinical_event_table(union.this)) or
                    (union.expression and _has_clinical_event_table(union.expression))
                ):
                    continue

                # Deduplicate violations
                key = union.sql()
                if key in seen:
                    continue
                seen.add(key)

                violations.append(
                    self.create_violation(
                        message=(
                            "UNION (without ALL) detected when combining clinical event data. "
                            "This removes duplicate rows, but identical rows may represent "
                            "distinct clinical events (e.g., multiple visits on the same day)."
                        ),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details={
                            "issue": "union_without_all_clinical_events",
                            "union_sql": union.sql(),
                        },
                    )
                )

        return violations


__all__ = ["UnionVsUnionAllClinicalEventsRule"]
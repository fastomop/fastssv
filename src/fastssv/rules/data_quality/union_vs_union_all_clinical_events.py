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

import re
from typing import Iterator, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.patch import replace as patch_replace
from fastssv.core.registry import register


# Match `UNION` as a standalone keyword that is *not* immediately followed
# by `ALL` (so we don't false-match the already-correct form). Word
# boundaries on either side prevent matching e.g. `UNIONS`.
_UNION_KEYWORD_RE = re.compile(
    r"\bUNION\b(?!\s+ALL\b)",
    re.IGNORECASE,
)


def _find_unique_union_keyword_span(sql: str) -> Optional[Tuple[int, int]]:
    """If the source contains exactly one `UNION` (not followed by ALL),
    return its [start, end) byte span. Otherwise return None.
    """
    matches = list(_UNION_KEYWORD_RE.finditer(sql))
    if len(matches) != 1:
        return None
    m = matches[0]
    return (m.start(), m.end())


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
    return any(_is_clinical_event_table(t.name) for t in node.find_all(exp.Table))


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


def _iter_union_arms(union: exp.Union) -> Iterator[exp.Expression]:
    """Yield every SELECT arm in a (possibly chained) UNION tree."""
    for side in (union.this, union.expression):
        if side is None:
            continue
        if isinstance(side, exp.Union):
            yield from _iter_union_arms(side)
        else:
            yield side


def _is_person_id_only_select(node: exp.Expression) -> bool:
    """
    True if `node` is a SELECT whose output is a single `person_id` column.

    Matches `SELECT person_id FROM ...`, `SELECT DISTINCT person_id FROM ...`,
    `SELECT co.person_id FROM ...`, and `SELECT foo AS person_id FROM ...`
    — anything whose sole output column is named ``person_id``.
    """
    if isinstance(node, exp.Subquery):
        node = node.this
    if not isinstance(node, exp.Select):
        return False
    exprs = node.expressions or []
    if len(exprs) != 1:
        return False
    return _norm(exprs[0].alias_or_name) == "person_id"


def _is_cohort_id_only_union(union: exp.Union) -> bool:
    """
    Suppress when every arm of the UNION selects only ``person_id``.

    In that case the UNION is doing a cohort set-union over patient IDs,
    not combining clinical events. There are no event-level columns that
    UNION (vs UNION ALL) could silently collapse, so the warning does not
    apply — UNION is the semantically correct choice.
    """
    arms = list(_iter_union_arms(union))
    if not arms:
        return False
    return all(_is_person_id_only_select(arm) for arm in arms)


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

    suggested_fix = "REPLACE: `UNION` WITH `UNION ALL` when combining clinical event rows. Identical-looking rows can represent distinct events; UNION silently dedupes them."
    long_description = (
        "UNION de-duplicates rows; UNION ALL preserves them. Two distinct "
        "clinical events can share identical values on the selected "
        "columns (same person, same date, different visit) — "
        "de-duplicating them via UNION silently drops legitimate events "
        "and under-counts the cohort. Use UNION ALL for clinical-event "
        "combinations; use UNION only when you genuinely want to "
        "collapse duplicates and can justify why."
    )
    example_bad = (
        "SELECT person_id, condition_start_date AS event_date\n"
        "FROM condition_occurrence\n"
        "UNION\n"
        "SELECT person_id, drug_exposure_start_date\n"
        "FROM drug_exposure;"
    )
    example_good = (
        "SELECT person_id, condition_start_date AS event_date\n"
        "FROM condition_occurrence\n"
        "UNION ALL\n"
        "SELECT person_id, drug_exposure_start_date\n"
        "FROM drug_exposure;"
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

                # Skip cohort-ID-only unions (every arm selects only person_id)
                if _is_cohort_id_only_union(union):
                    continue

                # Check both branches (robust to nesting)
                if not (
                    (union.this and _has_clinical_event_table(union.this))
                    or (union.expression and _has_clinical_event_table(union.expression))
                ):
                    continue

                # Deduplicate violations
                key = union.sql()
                if key in seen:
                    continue
                seen.add(key)

                # Structured patch: REPLACE the bare `UNION` keyword with
                # `UNION ALL`. Only applied when the source contains a
                # single `UNION` (without ALL) — otherwise the violation
                # falls back to FREEFORM auto-default. The applier just
                # appends ` ALL` after the keyword, preserving casing.
                patch = None
                span = _find_unique_union_keyword_span(sql)
                if span is not None:
                    keyword_text = sql[span[0] : span[1]]
                    patch = patch_replace(span, f"{keyword_text} ALL")

                violations.append(
                    self.create_violation(
                        message=(
                            "UNION (without ALL) detected when combining clinical event data. "
                            "This removes duplicate rows, but identical rows may represent "
                            "distinct clinical events (e.g., multiple visits on the same day)."
                        ),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=patch,
                        details={
                            "issue": "union_without_all_clinical_events",
                            "union_sql": union.sql(),
                        },
                    )
                )

        return violations


__all__ = ["UnionVsUnionAllClinicalEventsRule"]

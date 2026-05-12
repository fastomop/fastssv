"""Visit Occurrence INNER JOIN Validation Rule.

OMOP semantic rule OMOP_043:
Many clinical events have NULL visit_occurrence_id (20-60% in real datasets).
Using INNER JOIN to visit_occurrence silently drops these records, causing
significant data loss.

The Problem:
    INNER JOIN filters out rows where visit_occurrence_id IS NULL.
    This silently excludes:
    - Outpatient prescriptions
    - External lab results
    - Historical diagnoses
    - Telemedicine events
    - Claims data without encounter mapping

Violation pattern:
    SELECT co.*
    FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    -- Silently drops 20-60% of conditions!

Correct pattern:
    SELECT co.*
    FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    -- Preserves all conditions, visit info NULL when not linked
"""

import re
from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.patch import replace as patch_replace
from fastssv.core.registry import register


# Match a JOIN keyword span before `visit_occurrence` that isn't already a
# LEFT / RIGHT / FULL / OUTER join. INNER / CROSS prefix optional.
_JOIN_VO_RE = re.compile(
    r"(?<!LEFT\s)(?<!RIGHT\s)(?<!FULL\s)(?<!OUTER\s)"
    r"(?:(?:INNER|CROSS)\s+)?\bJOIN\s+visit_occurrence\b",
    re.IGNORECASE,
)


def _build_left_join_patch(sql: str) -> Optional[dict]:
    """Build a REPLACE patch swapping the inner JOIN keyword for LEFT JOIN.

    Returns ``None`` if the JOIN keyword cannot be uniquely located (e.g.
    multiple visit_occurrence joins in the same SQL).
    """
    matches = _JOIN_VO_RE.findall(sql)
    if len(matches) != 1:
        return None
    m = _JOIN_VO_RE.search(sql)
    if m is None:
        return None
    span = m.span()
    return patch_replace(span, "LEFT JOIN visit_occurrence")


# --- Constants -------------------------------------------------------------

VISIT_OCCURRENCE = "visit_occurrence"
VISIT_OCCURRENCE_ID = "visit_occurrence_id"

CLINICAL_EVENT_TABLES = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "device_exposure",
    "specimen",
    "visit_detail",
}


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_vo(table: Optional[str]) -> bool:
    return _norm(table) == VISIT_OCCURRENCE


def _is_inner_join(join: exp.Join) -> bool:
    """Robust INNER JOIN detection."""
    kind = join.args.get("kind")
    side = join.args.get("side")

    # INNER JOIN explicitly or implicit JOIN
    return (kind is None or normalize_name(kind) == "inner") and side is None


def _get_join_tables(join: exp.Join, aliases: Dict[str, str]) -> Set[str]:
    """Get all tables involved in a join."""
    tables = set()

    # Right side
    if join.this:
        alias = join.this.alias_or_name
        table = aliases.get(alias, alias)
        tables.add(_norm(table))

    # From ON clause
    on = join.args.get("on")
    if on:
        for col in on.find_all(exp.Column):
            if col.table:
                alias = str(col.table)
                table = aliases.get(alias, alias)
                tables.add(_norm(table))

    return tables


def _is_vo_join(join: exp.Join, aliases: Dict[str, str]) -> bool:
    """Check if join involves visit_occurrence."""
    tables = _get_join_tables(join, aliases)
    return VISIT_OCCURRENCE in tables


def _join_on_uses_visit_occurrence_id(join: exp.Join) -> bool:
    """True if the JOIN's ON clause references ``visit_occurrence_id`` on
    at least one side of an **equality** — i.e. the linkage is actually
    via the visit-id key.

    The rule's premise is "INNER JOIN on visit_occurrence_id drops rows
    where the event's visit_occurrence_id is NULL." That premise only
    applies when the JOIN key is ``visit_occurrence_id``; joining on
    ``person_id`` (or any other column) cannot trigger NULL-driven row
    loss because those keys aren't nullable on event tables.

    The check is restricted to ``exp.EQ`` nodes so a non-equality
    predicate that merely *mentions* ``visit_occurrence_id`` —
    ``ON c.person_id = vo.person_id AND vo.visit_occurrence_id IS NOT NULL``
    is the canonical FP shape — does not retrigger the warning. An
    earlier draft walked every Column in the ON clause and reintroduced
    that false positive class (caught in code review).
    """
    on = join.args.get("on")
    if on is None:
        return False
    for eq in on.find_all(exp.EQ):
        for side in (eq.this, eq.expression):
            if isinstance(side, exp.Column) and _norm(side.name) == VISIT_OCCURRENCE_ID:
                return True
    return False


def _has_intentional_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """
    Detect intentional filtering of visit linkage.
    Stronger signal than just any VO column usage.
    """
    for where in tree.find_all(exp.Where):
        for node in where.walk():
            if isinstance(node, (exp.Is, exp.NEQ, exp.EQ)):
                left = node.this
                right = getattr(node, "expression", None)

                for col in [left, right]:
                    if isinstance(col, exp.Column):
                        table, column = resolve_table_col(col, aliases)
                        if _is_vo(table) and _norm(column) == VISIT_OCCURRENCE_ID:
                            return True

    return False


def _has_implicit_join(tree: exp.Expression) -> bool:
    """Detect comma joins (implicit INNER JOIN)."""
    for from_node in tree.find_all(exp.From):
        tables = list(from_node.find_all(exp.Table))
        if len(tables) > 1:
            return True
    return False


# --- Core ------------------------------------------------------------------


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[dict]:
    issues = []
    seen: Set[str] = set()

    intentional = _has_intentional_filter(tree, aliases)

    # --- Explicit JOINs ---
    for join in tree.find_all(exp.Join):
        if not _is_inner_join(join):
            continue

        if not _is_vo_join(join, aliases):
            continue

        # Only warn when the JOIN key is actually visit_occurrence_id. A
        # join on person_id (or any non-VOID key) cannot drop rows due to
        # NULL visit linkage, so the rule's premise doesn't apply.
        if not _join_on_uses_visit_occurrence_id(join):
            continue

        key = join.sql()
        if key in seen:
            continue
        seen.add(key)

        if intentional:
            issues.append(
                {
                    "message": (
                        "INNER JOIN to visit_occurrence with explicit filtering. "
                        "This restricts results to visit-linked events only."
                    ),
                    "severity": Severity.WARNING,
                    "fixable": False,
                }
            )
        else:
            issues.append(
                {
                    "message": (
                        "INNER JOIN to visit_occurrence may drop events with NULL "
                        "visit_occurrence_id (often 20–60% of records). "
                        "Consider LEFT JOIN unless filtering is intentional."
                    ),
                    "severity": Severity.WARNING,
                    "fixable": True,
                }
            )

    # --- Implicit JOINs ---
    if _has_implicit_join(tree):
        # Check if both VO and event tables are present
        tables = {_norm(t.name) for t in tree.find_all(exp.Table)}

        if VISIT_OCCURRENCE in tables and any(t in CLINICAL_EVENT_TABLES for t in tables):
            key = "implicit_join_vo"
            if key not in seen:
                seen.add(key)

                issues.append(
                    {
                        "message": (
                            "Implicit INNER JOIN involving visit_occurrence detected. "
                            "This may unintentionally drop records with NULL visit_occurrence_id."
                        ),
                        "severity": Severity.WARNING,
                        "fixable": False,
                    }
                )

    return issues


# --- Rule ------------------------------------------------------------------


@register
class VisitOccurrenceInnerJoinValidationRule(Rule):
    """Detects INNER JOINs to visit_occurrence that may drop data."""

    rule_id = "joins.visit_occurrence_inner_join_validation"
    name = "Visit Occurrence INNER JOIN Validation"
    description = "Detects INNER JOINs to visit_occurrence that may exclude events with NULL visit_occurrence_id."
    severity = Severity.WARNING
    suggested_fix = "REPLACE: `INNER JOIN visit_occurrence` WITH `LEFT JOIN visit_occurrence` if events without a recorded visit should be preserved (visit_occurrence_id is nullable on event tables)."
    example_bad = (
        "SELECT co.person_id FROM condition_occurrence co\n"
        "JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id;"
    )
    example_good = (
        "SELECT co.person_id FROM condition_occurrence co\n"
        "LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id;"
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

            issues = _find_violations(tree, aliases)

            for issue in issues:
                patch = None
                if issue.get("fixable"):
                    patch = _build_left_join_patch(sql)

                violations.append(
                    self.create_violation(
                        message=issue["message"],
                        severity=issue["severity"],
                        suggested_fix_patch=patch,
                    )
                )

        return violations


__all__ = ["VisitOccurrenceInnerJoinValidationRule"]

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

        key = join.sql()
        if key in seen:
            continue
        seen.add(key)

        if intentional:
            issues.append({
                "message": (
                    f"INNER JOIN to visit_occurrence with explicit filtering. "
                    f"This restricts results to visit-linked events only."
                ),
                "severity": Severity.WARNING,
            })
        else:
            issues.append({
                "message": (
                    f"INNER JOIN to visit_occurrence may drop events with NULL "
                    f"visit_occurrence_id (often 20–60% of records). "
                    f"Consider LEFT JOIN unless filtering is intentional."
                ),
                "severity": Severity.WARNING,
            })

    # --- Implicit JOINs ---
    if _has_implicit_join(tree):
        # Check if both VO and event tables are present
        tables = {
            _norm(t.name)
            for t in tree.find_all(exp.Table)
        }

        if VISIT_OCCURRENCE in tables and any(t in CLINICAL_EVENT_TABLES for t in tables):
            key = "implicit_join_vo"
            if key not in seen:
                seen.add(key)

                issues.append({
                    "message": (
                        f"Implicit INNER JOIN involving visit_occurrence detected. "
                        f"This may unintentionally drop records with NULL visit_occurrence_id."
                    ),
                    "severity": Severity.WARNING,
                })

    return issues


# --- Rule ------------------------------------------------------------------

@register
class VisitOccurrenceInnerJoinValidationRule(Rule):
    """Detects INNER JOINs to visit_occurrence that may drop data."""

    rule_id = "joins.visit_occurrence_inner_join_validation"
    name = "Visit Occurrence INNER JOIN Validation"
    description = (
        "Detects INNER JOINs to visit_occurrence that may exclude events "
        "with NULL visit_occurrence_id."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Use LEFT JOIN to preserve all events, or explicitly filter "
        "visit_occurrence_id to indicate intentional restriction."
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
                violations.append(
                    self.create_violation(
                        message=issue["message"],
                        severity=issue["severity"],
                    )
                )

        return violations


__all__ = ["VisitOccurrenceInnerJoinValidationRule"]
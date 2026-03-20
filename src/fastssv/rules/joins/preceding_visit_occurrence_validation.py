"""Preceding Visit Occurrence Validation Rule.

OMOP semantic rule OMOP_059:
visit_occurrence.preceding_visit_occurrence_id references another visit_occurrence_id
in the same table. The self-join must use visit_occurrence on both sides.

The Problem:
    preceding_visit_occurrence_id is a self-referential foreign key that links
    to the previous visit for a patient. It MUST join to visit_occurrence.visit_occurrence_id.

    Patient visit chain example:
    - Visit ID 1: ER visit (preceding_visit_occurrence_id = NULL)
    - Visit ID 2: Inpatient (preceding_visit_occurrence_id = 1)
    - Visit ID 3: Follow-up (preceding_visit_occurrence_id = 2)

Common mistakes:
    - Joining to a different table (visit_detail, person, etc.)
    - Joining to the wrong column in visit_occurrence

Violation pattern:
    SELECT * FROM visit_occurrence vo
    JOIN visit_detail vd ON vo.preceding_visit_occurrence_id = vd.visit_detail_id
    -- WRONG: Joining to different table!

    SELECT * FROM visit_occurrence v1
    JOIN visit_occurrence v2 ON v1.preceding_visit_occurrence_id = v2.person_id
    -- WRONG: Joining to wrong column!

Correct pattern:
    SELECT v1.*, v2.visit_start_date AS prior_visit_date
    FROM visit_occurrence v1
    JOIN visit_occurrence v2
      ON v1.preceding_visit_occurrence_id = v2.visit_occurrence_id
    -- CORRECT: Self-join to visit_occurrence_id
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

VISIT_OCCURRENCE = "visit_occurrence"
PRECEDING = "preceding_visit_occurrence_id"
VISIT_ID = "visit_occurrence_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_visit_occurrence(table: Optional[str]) -> bool:
    return _norm(table) == VISIT_OCCURRENCE


def _extract_join_conditions(tree: exp.Expression) -> List[exp.Expression]:
    """Extract all column-to-column equality conditions from JOIN and WHERE."""
    conditions = []

    # JOIN ON clauses
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause:
            conditions.extend(on_clause.find_all(exp.EQ))

    # WHERE implicit joins
    for where in tree.find_all(exp.Where):
        conditions.extend(where.find_all(exp.EQ))

    return conditions


# --- Detection -------------------------------------------------------------

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues = []
    seen: Set[str] = set()

    conditions = _extract_join_conditions(tree)

    for eq in conditions:
        left = eq.this
        right = eq.expression

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        lt, lc = _norm(lt), _norm(lc)
        rt, rc = _norm(rt), _norm(rc)

        # --- Identify preceding side ---
        if lc == PRECEDING:
            source_table = lt
            target_table = rt
            target_col = rc
            source_alias = _norm(str(left.table)) if left.table else None
            target_alias = _norm(str(right.table)) if right.table else None

        elif rc == PRECEDING:
            source_table = rt
            target_table = lt
            target_col = lc
            source_alias = _norm(str(right.table)) if right.table else None
            target_alias = _norm(str(left.table)) if left.table else None

        else:
            continue

        key = eq.sql()
        if key in seen:
            continue
        seen.add(key)

        # --- Check 1: source must be visit_occurrence ---
        if not _is_visit_occurrence(source_table):
            issues.append(
                f"{PRECEDING} used from non-visit_occurrence table '{source_table}'. "
                f"It must originate from visit_occurrence."
            )
            continue

        # --- Check 2: must join to visit_occurrence ---
        if not _is_visit_occurrence(target_table):
            issues.append(
                f"{PRECEDING} joined to '{target_table or 'unknown'}' table. "
                f"Must join to visit_occurrence."
            )
            continue

        # --- Check 3: must join to visit_occurrence_id ---
        if target_col != VISIT_ID:
            issues.append(
                f"{PRECEDING} joined to '{target_col}'. "
                f"Must join to visit_occurrence_id."
            )
            continue

        # --- Optional strictness: ensure different aliases (true self-join) ---
        if source_alias and target_alias and source_alias == target_alias:
            issues.append(
                f"{PRECEDING} self-joined using same alias '{source_alias}'. "
                f"Use two aliases for proper self-join."
            )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class PrecedingVisitOccurrenceValidationRule(Rule):
    """Validates correct usage of preceding_visit_occurrence_id."""

    rule_id = "semantic.preceding_visit_occurrence_validation"
    name = "Preceding Visit Occurrence Validation"
    description = (
        "preceding_visit_occurrence_id must reference visit_occurrence.visit_occurrence_id "
        "via a proper self-join."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Join visit_occurrence to itself using "
        "preceding_visit_occurrence_id = visit_occurrence_id with separate aliases."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        if "preceding_visit_occurrence_id" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            # Only relevant if visit_occurrence is used
            if not uses_table(tree, VISIT_OCCURRENCE):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["PrecedingVisitOccurrenceValidationRule"]

"""Preceding Visit Occurrence Validation Rule.

OMOP semantic rules OMOP_059, OMOP_404:
visit_occurrence.preceding_visit_occurrence_id references another visit_occurrence_id
in the same table. The self-join must use visit_occurrence on both sides, ensure
both visits belong to the same person, and maintain chronological ordering.

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
    - Not enforcing person_id match (OMOP_404)
    - Not enforcing chronological ordering (OMOP_404)

Violation patterns:
    -- ERROR: Joining to different table!
    SELECT * FROM visit_occurrence vo
    JOIN visit_detail vd ON vo.preceding_visit_occurrence_id = vd.visit_detail_id

    -- ERROR: Joining to wrong column!
    SELECT * FROM visit_occurrence v1
    JOIN visit_occurrence v2 ON v1.preceding_visit_occurrence_id = v2.person_id

    -- WARNING: Missing person_id constraint (OMOP_404)
    SELECT v1.*, v2.visit_start_date
    FROM visit_occurrence v1
    JOIN visit_occurrence v2 ON v1.preceding_visit_occurrence_id = v2.visit_occurrence_id
    -- Could join visits from different patients!

    -- WARNING: Missing temporal ordering constraint (OMOP_404)
    SELECT v1.*, v2.visit_start_date
    FROM visit_occurrence v1
    JOIN visit_occurrence v2 ON v1.preceding_visit_occurrence_id = v2.visit_occurrence_id
      AND v1.person_id = v2.person_id
    -- Could allow preceding visit to end after current visit starts!

Correct pattern:
    SELECT v1.*, v2.visit_start_date AS prior_visit_date
    FROM visit_occurrence v1
    JOIN visit_occurrence v2
      ON v1.preceding_visit_occurrence_id = v2.visit_occurrence_id
      AND v1.person_id = v2.person_id
      AND v2.visit_end_date <= v1.visit_start_date
    -- CORRECT: Proper self-join with person_id and temporal constraints
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

VISIT_OCCURRENCE = "visit_occurrence"
PRECEDING = "preceding_visit_occurrence_id"
VISIT_ID = "visit_occurrence_id"
PERSON_ID = "person_id"
VISIT_START_DATE = "visit_start_date"
VISIT_END_DATE = "visit_end_date"


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


def _has_person_id_constraint(
    conditions: List[exp.Expression],
    source_alias: Optional[str],
    target_alias: Optional[str],
    aliases: Dict[str, str],
) -> bool:
    """Check if person_id equality constraint exists between the two visit aliases."""
    if not source_alias or not target_alias:
        return False

    for eq in conditions:
        if not isinstance(eq.this, exp.Column) or not isinstance(eq.expression, exp.Column):
            continue

        # Get column names
        _, lc = resolve_table_col(eq.this, aliases)
        _, rc = resolve_table_col(eq.expression, aliases)

        lc, rc = _norm(lc), _norm(rc)

        # Check if both columns are person_id
        if lc != PERSON_ID or rc != PERSON_ID:
            continue

        # Get aliases directly from the column objects
        left_alias = _norm(str(eq.this.table)) if eq.this.table else None
        right_alias = _norm(str(eq.expression.table)) if eq.expression.table else None

        # Check if they reference our two visit aliases
        if (left_alias == source_alias and right_alias == target_alias) or (left_alias == target_alias and right_alias == source_alias):
            return True

    return False


def _has_temporal_constraint(
    tree: exp.Expression,
    source_alias: Optional[str],
    target_alias: Optional[str],
    aliases: Dict[str, str],
) -> bool:
    """Check if temporal ordering constraint exists (prior.visit_end_date <= current.visit_start_date)."""
    if not source_alias or not target_alias:
        return False

    # Look for LTE, LT, GTE, GT constraints in JOIN ON and WHERE
    for node in tree.walk():
        if not isinstance(node, (exp.LTE, exp.LT, exp.GTE, exp.GT)):
            continue

        # Must be in JOIN or WHERE clause
        parent = node.parent
        in_join_or_where = False
        while parent:
            if isinstance(parent, (exp.Join, exp.Where)):
                in_join_or_where = True
                break
            parent = parent.parent

        if not in_join_or_where:
            continue

        left = node.this
        right = node.expression

        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        # Get column names
        _, lc = resolve_table_col(left, aliases)
        _, rc = resolve_table_col(right, aliases)

        lc, rc = _norm(lc), _norm(rc)

        # Get aliases directly from the column objects
        left_alias = _norm(str(left.table)) if left.table else None
        right_alias = _norm(str(right.table)) if right.table else None

        # Check for: prior.visit_end_date <= current.visit_start_date
        if isinstance(node, (exp.LTE, exp.LT)):
            if (
                left_alias == target_alias and lc == VISIT_END_DATE
                and right_alias == source_alias and rc == VISIT_START_DATE
            ):
                return True

        # Check for: current.visit_start_date >= prior.visit_end_date
        if isinstance(node, (exp.GTE, exp.GT)):
            if (
                left_alias == source_alias and lc == VISIT_START_DATE
                and right_alias == target_alias and rc == VISIT_END_DATE
            ):
                return True

    return False


# --- Detection -------------------------------------------------------------

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> Dict[str, List[str]]:
    """
    Returns a dict with:
    - 'errors': structural violations (wrong table/column)
    - 'warnings': semantic violations (missing person_id or temporal constraints)
    """
    errors = []
    warnings = []
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
            errors.append(
                f"{PRECEDING} used from non-visit_occurrence table '{source_table}'. "
                f"It must originate from visit_occurrence."
            )
            continue

        # --- Check 2: must join to visit_occurrence ---
        if not _is_visit_occurrence(target_table):
            errors.append(
                f"{PRECEDING} joined to '{target_table or 'unknown'}' table. "
                f"Must join to visit_occurrence."
            )
            continue

        # --- Check 3: must join to visit_occurrence_id ---
        if target_col != VISIT_ID:
            errors.append(
                f"{PRECEDING} joined to '{target_col}'. "
                f"Must join to visit_occurrence_id."
            )
            continue

        # --- Check 4: ensure different aliases (true self-join) ---
        if source_alias and target_alias and source_alias == target_alias:
            errors.append(
                f"{PRECEDING} self-joined using same alias '{source_alias}'. "
                f"Use two aliases for proper self-join."
            )
            continue

        # --- OMOP_404 Checks (warnings for missing semantic constraints) ---

        # Check 5: person_id constraint (OMOP_404)
        if not _has_person_id_constraint(conditions, source_alias, target_alias, aliases):
            warnings.append(
                f"Missing person_id equality constraint in preceding_visit_occurrence_id join. "
                f"Add constraint: {source_alias}.person_id = {target_alias}.person_id to ensure "
                f"visits belong to the same patient (OMOP_404)."
            )

        # Check 6: temporal ordering constraint (OMOP_404)
        if not _has_temporal_constraint(tree, source_alias, target_alias, aliases):
            warnings.append(
                f"Missing temporal ordering constraint in preceding_visit_occurrence_id join. "
                f"Add constraint: {target_alias}.visit_end_date <= {source_alias}.visit_start_date "
                f"to ensure chronological ordering (OMOP_404)."
            )

    return {"errors": errors, "warnings": warnings}


# --- Rule ------------------------------------------------------------------

@register
class PrecedingVisitOccurrenceValidationRule(Rule):
    """Validates correct usage of preceding_visit_occurrence_id."""

    rule_id = "joins.preceding_visit_occurrence_validation"
    name = "Preceding Visit Occurrence Validation"
    description = (
        "preceding_visit_occurrence_id must reference visit_occurrence.visit_occurrence_id "
        "via a proper self-join with person_id and temporal constraints."
    )
    severity = Severity.ERROR
    suggested_fix = "REPLACE: the join condition WITH `vo.preceding_visit_occurrence_id = prev.visit_occurrence_id AND vo.person_id = prev.person_id AND prev.visit_end_date <= vo.visit_start_date`. Use distinct aliases (vo, prev) for the two visit_occurrence rows."
    example_bad = (
        "SELECT vo.visit_occurrence_id FROM visit_occurrence vo\n"
        "JOIN visit_occurrence prev ON vo.preceding_visit_occurrence_id = prev.person_id;"
    )
    example_good = (
        "SELECT vo.visit_occurrence_id, vo.preceding_visit_occurrence_id\n"
        "FROM visit_occurrence vo\n"
        "JOIN visit_occurrence prev\n"
        "  ON vo.preceding_visit_occurrence_id = prev.visit_occurrence_id\n"
        "  AND vo.person_id = prev.person_id\n"
        "  AND prev.visit_end_date <= vo.visit_start_date;"
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
            if not has_table_reference(tree, VISIT_OCCURRENCE):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            # Add ERROR violations
            for msg in issues["errors"]:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=Severity.ERROR,
                    )
                )

            # Add WARNING violations (OMOP_404 semantic checks)
            for msg in issues["warnings"]:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=Severity.WARNING,
                    )
                )

        return violations


__all__ = ["PrecedingVisitOccurrenceValidationRule"]

"""Condition Occurrence Visit Hierarchy Validation Rule.

OMOP semantic rule CLIN_013:
When condition_occurrence.visit_detail_id is used to link to visit_detail, the query
should be aware that visit_detail nests inside visit_occurrence. If the query needs
visit-level attributes, it must join through visit_occurrence, not just reference
visit_occurrence columns without a proper join.

The Problem:
    visit_detail records are nested within visit_occurrence records. A condition can
    be linked to a visit_detail, and that visit_detail belongs to a visit_occurrence.

    If a query joins condition_occurrence to visit_detail but then tries to access
    visit_occurrence columns without properly joining to visit_occurrence, it will
    either fail (if the table isn't in FROM/JOIN) or produce incorrect results.

Example violation:
    -- BAD: References vo.visit_start_date without joining visit_occurrence
    SELECT co.*, vo.visit_start_date
    FROM condition_occurrence co
    JOIN visit_detail vd ON co.visit_detail_id = vd.visit_detail_id
    -- ERROR: vo referenced but not joined

Correct pattern:
    -- GOOD: Properly joins through visit_occurrence
    SELECT co.*, vo.visit_start_date
    FROM condition_occurrence co
    JOIN visit_detail vd ON co.visit_detail_id = vd.visit_detail_id
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
"""

from typing import Dict, List

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

CONDITION_TABLE = "condition_occurrence"
VISIT_DETAIL_TABLE = "visit_detail"
VISIT_OCCURRENCE_TABLE = "visit_occurrence"

VISIT_DETAIL_ID_COL = "visit_detail_id"
VISIT_OCCURRENCE_ID_COL = "visit_occurrence_id"


# --- Helpers ---------------------------------------------------------------

def _norm(name: str) -> str:
    return normalize_name(name)


def _is_pure_column_equality(eq: exp.EQ) -> bool:
    return isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)


# --- Join Detection --------------------------------------------------------

def _has_condition_visit_detail_join(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            if not _is_pure_column_equality(eq):
                continue

            lt, lc = resolve_table_col(eq.this, aliases)
            rt, rc = resolve_table_col(eq.expression, aliases)

            if not (lt and lc and rt and rc):
                continue

            if _norm(lc) != VISIT_DETAIL_ID_COL or _norm(rc) != VISIT_DETAIL_ID_COL:
                continue

            if (
                (_norm(lt) == CONDITION_TABLE and _norm(rt) == VISIT_DETAIL_TABLE)
                or (_norm(rt) == CONDITION_TABLE and _norm(lt) == VISIT_DETAIL_TABLE)
            ):
                return True

    return False


def _has_valid_visit_occurrence_join(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    """
    Ensure visit_occurrence is joined through visit_detail using visit_occurrence_id.
    """
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            if not _is_pure_column_equality(eq):
                continue

            lt, lc = resolve_table_col(eq.this, aliases)
            rt, rc = resolve_table_col(eq.expression, aliases)

            if not (lt and lc and rt and rc):
                continue

            if _norm(lc) != VISIT_OCCURRENCE_ID_COL or _norm(rc) != VISIT_OCCURRENCE_ID_COL:
                continue

            if (
                (_norm(lt) == VISIT_DETAIL_TABLE and _norm(rt) == VISIT_OCCURRENCE_TABLE)
                or (_norm(rt) == VISIT_DETAIL_TABLE and _norm(lt) == VISIT_OCCURRENCE_TABLE)
            ):
                return True

    return False


# --- Column Usage Detection ------------------------------------------------

def _references_visit_occurrence_columns(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    for col in tree.find_all(exp.Column):
        table, _ = resolve_table_col(col, aliases)
        if table and _norm(table) == VISIT_OCCURRENCE_TABLE:
            return True
    return False


# --- Core Detection --------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[str]:
    issues: List[str] = []

    if not _has_condition_visit_detail_join(tree, aliases):
        return issues

    if not _references_visit_occurrence_columns(tree, aliases):
        return issues

    if _has_valid_visit_occurrence_join(tree, aliases):
        return issues

    issues.append(
        f"Query joins {CONDITION_TABLE} to {VISIT_DETAIL_TABLE} and references "
        f"{VISIT_OCCURRENCE_TABLE} columns, but does not properly join "
        f"{VISIT_OCCURRENCE_TABLE} via {VISIT_DETAIL_TABLE}. "
        f"Add a join using {VISIT_DETAIL_TABLE}.{VISIT_OCCURRENCE_ID_COL}."
    )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class ConditionVisitHierarchyValidationRule(Rule):
    """
    Enforces correct OMOP visit hierarchy usage:

    condition_occurrence → visit_detail → visit_occurrence

    If visit_occurrence fields are referenced, a proper join through
    visit_detail must be present.
    """

    rule_id = "domain_specific.condition_visit_hierarchy_validation"
    name = "Condition Occurrence Visit Hierarchy Validation"

    description = (
        "When condition_occurrence joins to visit_detail, any reference to "
        "visit_occurrence columns requires a proper join to visit_occurrence "
        "through visit_detail using visit_occurrence_id."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Add: JOIN visit_occurrence vo "
        "ON vd.visit_occurrence_id = vo.visit_occurrence_id"
    )

    def validate(
        self,
        sql: str,
        dialect: str = "postgres",
    ) -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for message in issues:
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["ConditionVisitHierarchyValidationRule"]

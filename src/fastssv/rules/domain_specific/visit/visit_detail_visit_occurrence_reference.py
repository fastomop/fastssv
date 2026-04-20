"""Visit Detail Visit Occurrence Reference Rule.

OMOP semantic rule CLIN_044: visit_detail_must_reference_visit_occurrence

Every visit_detail record is nested within a parent visit_occurrence. Queries
analyzing visit_detail should reference visit_occurrence to access visit-level
context such as visit type, overall dates, and admission/discharge information.

The Problem:
    visit_detail provides granular sub-visit information (ICU stay, ward transfer,
    operating room), but critical context is stored in visit_occurrence:
    - Overall visit type (inpatient, outpatient, ER)
    - Visit-level dates (visit_start_date, visit_end_date)
    - Visit-level provider and care site
    - Admission source and discharge destination

    Analyzing visit_detail without referencing visit_occurrence loses this context.

Violation pattern:
    SELECT person_id, visit_detail_start_date
    FROM visit_detail
    WHERE visit_detail_concept_id = 32037  -- ICU
    -- Missing: What type of visits had ICU stays?

Correct patterns:
    -- Option 1: JOIN to visit_occurrence
    SELECT vd.*, vo.visit_concept_id, vo.visit_start_date
    FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vd.visit_detail_concept_id = 32037

    -- Option 2: Subquery with visit_occurrence
    SELECT * FROM visit_detail
    WHERE visit_occurrence_id IN (
        SELECT visit_occurrence_id FROM visit_occurrence
        WHERE visit_concept_id = 9201  -- Inpatient visits
    )
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    has_table_reference,
    resolve_table_col,
)
from fastssv.core.registry import register


VISIT_DETAIL = "visit_detail"
VISIT_OCCURRENCE = "visit_occurrence"
VISIT_OCCURRENCE_ID = "visit_occurrence_id"


def _norm(x: str) -> str:
    return normalize_name(x) if x else ""


def _check_visit_occurrence_id_linkage(node: exp.Expression, aliases: dict) -> bool:
    """Check if an EQ node links visit_detail and visit_occurrence via visit_occurrence_id."""
    if not isinstance(node, exp.EQ):
        return False

    left, right = node.this, node.expression

    if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
        return False

    lt, lc = resolve_table_col(left, aliases)
    rt, rc = resolve_table_col(right, aliases)

    if not (lt and lc and rt and rc):
        return False

    if _norm(lc) != VISIT_OCCURRENCE_ID or _norm(rc) != VISIT_OCCURRENCE_ID:
        return False

    return (
        (_norm(lt) == VISIT_DETAIL and _norm(rt) == VISIT_OCCURRENCE)
        or (_norm(rt) == VISIT_DETAIL and _norm(lt) == VISIT_OCCURRENCE)
    )


def _has_valid_join(tree: exp.Expression, aliases: dict) -> bool:
    """
    Check if visit_detail is properly linked to visit_occurrence
    via visit_occurrence_id in JOIN ON clauses, WHERE clauses, or subqueries.
    """
    # Check JOIN ON clauses
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            if _check_visit_occurrence_id_linkage(eq, aliases):
                return True

    # Check WHERE clauses (for cross joins with WHERE filters)
    for where in tree.find_all(exp.Where):
        for eq in where.find_all(exp.EQ):
            if _check_visit_occurrence_id_linkage(eq, aliases):
                return True

    # Check subqueries (IN, EXISTS, etc.)
    # If visit_occurrence is in a subquery and references visit_occurrence_id,
    # consider it linked
    for subquery in tree.find_all(exp.Subquery):
        # Check if subquery contains visit_occurrence
        subquery_aliases = extract_aliases(subquery)
        has_vo = any(_norm(t) == VISIT_OCCURRENCE for t in subquery_aliases.values())

        if has_vo:
            # Check if subquery references visit_occurrence_id
            for col in subquery.find_all(exp.Column):
                _, col_name = resolve_table_col(col, subquery_aliases)
                if _norm(col_name) == VISIT_OCCURRENCE_ID:
                    return True

    return False


def _find_violations(tree: exp.Expression, aliases: dict) -> List[str]:
    violations: List[str] = []

    if not has_table_reference(tree, VISIT_DETAIL):
        return violations

    # Case 1: visit_occurrence not referenced at all
    if not has_table_reference(tree, VISIT_OCCURRENCE):
        violations.append(
            "Query uses visit_detail without referencing visit_occurrence. "
            "This may omit visit-level context (visit type, admission/discharge, overall dates). "
            "Consider whether visit_occurrence should be included."
        )
        return violations

    # Case 2: visit_occurrence present but not properly joined
    if not _has_valid_join(tree, aliases):
        violations.append(
            "visit_detail and visit_occurrence are both used but not properly joined "
            "via visit_occurrence_id. This may indicate incorrect visit linkage."
        )

    return violations


@register
class VisitDetailVisitOccurrenceReferenceRule(Rule):
    rule_id = "domain_specific.visit_detail_visit_occurrence_reference"
    name = "Visit Detail Visit Occurrence Reference"

    description = (
        "Detects potential issues when using visit_detail without proper "
        "visit_occurrence context or join."
    )

    severity = Severity.ERROR
    suggested_fix = (
        "Ensure visit_detail is correctly linked to visit_occurrence "
        "via visit_occurrence_id when visit-level context is needed"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, VISIT_DETAIL):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        details={
                            "visit_detail_table": VISIT_DETAIL,
                            "visit_occurrence_table": VISIT_OCCURRENCE,
                        },
                    )
                )

        return violations


__all__ = ["VisitDetailVisitOccurrenceReferenceRule"]
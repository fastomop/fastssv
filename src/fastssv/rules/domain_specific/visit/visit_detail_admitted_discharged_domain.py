"""Visit Detail Admitted From / Discharged To Domain Rule.

OMOP semantic rule GAP_039:
visit_detail.admitted_from_concept_id and visit_detail.discharged_to_concept_id
must reference concepts from the Visit or Place of Service domains. Using concepts
from clinical domains (Condition, Drug, Procedure) is incorrect.

The Problem:
    The admission source and discharge destination columns represent WHERE the
    patient came from and WHERE they went, not WHAT condition they had or WHAT
    was done to them.

    Correct domains:
    - Visit: Emergency Room Visit, Inpatient Visit, Outpatient Visit
    - Place of Service: Home, Skilled Nursing Facility, Hospice, Rehabilitation

    Incorrect domains:
    - Condition: Diabetes, Myocardial Infarction (these are clinical diagnoses)
    - Drug: Aspirin, Metformin (these are medications)
    - Procedure: Appendectomy, Chemotherapy (these are treatments)

    When queries use hardcoded concept IDs in these columns without verifying
    the domain, they risk using clinical concepts as location concepts.

Common mistakes:
    1. Using condition concept IDs for admission source
    2. Using procedure concept IDs for discharge destination
    3. Hardcoding concept IDs without domain validation
    4. Assuming any concept ID works for these columns

Violation pattern:
    -- WRONG: Hardcoded concept IDs without domain verification
    SELECT * FROM visit_detail
    WHERE admitted_from_concept_id = 201826
    -- Risk: 201826 might be a Condition concept, not a Visit/Place concept

    SELECT * FROM visit_detail
    WHERE discharged_to_concept_id IN (12345, 67890)
    -- Risk: These IDs might be from wrong domains

Correct patterns:
    -- CORRECT: Join to concept with domain validation
    SELECT vd.*
    FROM visit_detail vd
    JOIN concept c ON vd.admitted_from_concept_id = c.concept_id
    WHERE c.domain_id IN ('Visit', 'Place of Service')

    -- CORRECT: Known valid concept IDs with comment
    SELECT * FROM visit_detail
    WHERE admitted_from_concept_id = 8870  -- Emergency Room (Visit domain)

    -- CORRECT: No hardcoded filtering (dynamic joins)
    SELECT vd.*
    FROM visit_detail vd
    JOIN concept c ON vd.discharged_to_concept_id = c.concept_id
    WHERE c.concept_name LIKE '%Home%'
      AND c.domain_id = 'Place of Service'

Note: This rule uses heuristic detection. It warns when hardcoded concept IDs
are used without domain validation, but cannot verify the actual domain without
database access. The warning encourages best practices.
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

VISIT_DETAIL = "visit_detail"
CONCEPT = "concept"

ADMITTED_FROM_CONCEPT_ID = "admitted_from_concept_id"
DISCHARGED_TO_CONCEPT_ID = "discharged_to_concept_id"

TARGET_COLUMNS = {
    ADMITTED_FROM_CONCEPT_ID,
    DISCHARGED_TO_CONCEPT_ID,
}

VALID_DOMAINS = {"Visit", "Place of Service"}

# Pre-normalized sets (performance + consistency)
NORM_TARGET_COLUMNS = {normalize_name(c) for c in TARGET_COLUMNS}
NORM_VALID_DOMAINS = {normalize_name(d) for d in VALID_DOMAINS}


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_target_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    # Must belong to visit_detail (if table is resolvable)
    if table and _norm(table) != VISIT_DETAIL:
        return False

    return _norm(col_name) in NORM_TARGET_COLUMNS


def _extract_literal_value(node: exp.Expression) -> Optional[int]:
    """Strict extraction: only allow numeric literals."""
    if isinstance(node, exp.Literal) and node.is_int:
        try:
            return int(node.this)
        except (ValueError, TypeError):
            return None
    return None


def _has_domain_validation(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """
    Check for domain validation on concept.domain_id.
    Supports:
    - WHERE c.domain_id = 'Visit'
    - WHERE c.domain_id IN (...)
    - JOIN concept ... AND c.domain_id = 'Visit'
    """

    # Check equality conditions
    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        if not isinstance(eq.this, exp.Column):
            continue

        table, col_name = resolve_table_col(eq.this, aliases)

        if _norm(col_name) != "domain_id":
            continue

        # Ensure it belongs to concept table
        if table and _norm(table) != CONCEPT:
            continue

        right = eq.expression
        if isinstance(right, exp.Literal):
            value = _norm(str(right.this).strip("'\""))
            if value in NORM_VALID_DOMAINS:
                return True

    # Check IN conditions
    for in_node in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_node):
            continue

        if not isinstance(in_node.this, exp.Column):
            continue

        table, col_name = resolve_table_col(in_node.this, aliases)

        if _norm(col_name) != "domain_id":
            continue

        if table and _norm(table) != CONCEPT:
            continue

        for expr in in_node.expressions or []:
            if isinstance(expr, exp.Literal):
                value = _norm(str(expr.this).strip("'\""))
                if value in NORM_VALID_DOMAINS:
                    return True

    return False


def _find_hardcoded_concept_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, Tuple[int, ...]]]:
    """
    Detect hardcoded concept filters on target columns.
    """

    violations: List[Tuple[str, Tuple[int, ...]]] = []

    # EQ conditions
    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        left, right = eq.this, eq.expression

        for col_node, val_node in [(left, right), (right, left)]:
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_target_column(col_node, aliases):
                continue

            concept_id = _extract_literal_value(val_node)
            if concept_id is not None:
                _, col_name = resolve_table_col(col_node, aliases)
                violations.append((col_name, (concept_id,)))

    # IN conditions
    for in_node in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_node):
            continue

        if not isinstance(in_node.this, exp.Column):
            continue

        if not _is_target_column(in_node.this, aliases):
            continue

        concept_ids = []
        for expr in in_node.expressions or []:
            concept_id = _extract_literal_value(expr)
            if concept_id is not None:
                concept_ids.append(concept_id)

        if concept_ids:
            _, col_name = resolve_table_col(in_node.this, aliases)
            violations.append((col_name, tuple(concept_ids)))

    return violations


# --- Rule ------------------------------------------------------------------


@register
class VisitDetailAdmittedDischargedDomainRule(Rule):
    """
    Detect hardcoded concept IDs in admitted/discharged columns
    without domain validation against concept.domain_id.
    """

    rule_id = "domain_specific.visit_detail_admitted_discharged_domain"
    name = "Visit Detail Admitted/Discharged Domain Validation"

    description = (
        "visit_detail.admitted_from_concept_id and discharged_to_concept_id must "
        "reference concepts from Visit or Place of Service domains. Hardcoded "
        "concept IDs should always be validated using concept.domain_id."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: hard-coded admitted_from_concept_id / discharged_to_concept_id literals WITH a JOIN to concept and filter `WHERE c.domain_id IN ('Visit', 'Place of Service')` to validate the domain of the concept you're filtering by."
    example_bad = "SELECT visit_detail_id FROM visit_detail\nWHERE admitted_from_concept_id = 8870;"
    example_good = (
        "SELECT vd.visit_detail_id FROM visit_detail vd\n"
        "JOIN concept c ON vd.admitted_from_concept_id = c.concept_id\n"
        "WHERE c.domain_id IN ('Visit', 'Place of Service');"
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

            # Skip if domain validation already present
            if _has_domain_validation(tree, aliases):
                continue

            hardcoded_filters = _find_hardcoded_concept_filters(tree, aliases)

            seen: Set[Tuple[str, Tuple[int, ...]]] = set()

            for col_name, concept_ids in hardcoded_filters:
                key = (_norm(col_name), concept_ids)
                if key in seen:
                    continue
                seen.add(key)

                concept_ids_str = ", ".join(map(str, concept_ids[:3]))
                if len(concept_ids) > 3:
                    concept_ids_str += f", ... ({len(concept_ids)} total)"

                violations.append(
                    self.create_violation(
                        message=(
                            f"Hardcoded concept ID filter on '{col_name}' ({concept_ids_str}) "
                            f"without domain validation. These must map to Visit or Place of "
                            f"Service domains via concept.domain_id."
                        ),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details={
                            "column": col_name,
                            "concept_ids": list(concept_ids),
                            "expected_domains": list(VALID_DOMAINS),
                        },
                    )
                )

        return violations


__all__ = ["VisitDetailAdmittedDischargedDomainRule"]

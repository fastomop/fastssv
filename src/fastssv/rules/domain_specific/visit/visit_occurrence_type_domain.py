"""Visit Occurrence Type Domain Validation Rule.

OMOP semantic rule OMOP_524:
visit_type_concept_id must reference a concept belonging to the Type Concept domain.

The Problem:
    The visit_type_concept_id column in visit_occurrence represents the provenance or
    type of the visit record (e.g., "EHR record", "Insurance claim", "Patient reported").

    These type concepts belong to the 'Type Concept' domain, not clinical domains like
    'Visit', 'Condition', or 'Procedure'. When queries join visit_type_concept_id to
    the concept table, they must filter by domain_id = 'Type Concept' or risk including
    concepts from incorrect domains.

Examples:
    Common type concepts:
    - 44818517: Visit derived from EHR record (Type Concept domain)
    - 44818518: Inpatient claim (Type Concept domain)
    - 44818519: Outpatient claim (Type Concept domain)

Violation patterns:
    -- WRONG: No domain validation on type concept join
    SELECT vo.*, c.concept_name
    FROM visit_occurrence vo
    JOIN concept c ON vo.visit_type_concept_id = c.concept_id
    -- Risk: Could incorrectly join to Visit or other domain concepts

    -- WRONG: Using wrong domain filter
    SELECT vo.*, c.concept_name
    FROM visit_occurrence vo
    JOIN concept c ON vo.visit_type_concept_id = c.concept_id
    WHERE c.domain_id = 'Visit'
    -- Returns nothing! Type concepts have domain_id = 'Type Concept'

Correct patterns:
    -- CORRECT: Proper domain validation
    SELECT vo.*, c.concept_name
    FROM visit_occurrence vo
    JOIN concept c ON vo.visit_type_concept_id = c.concept_id
    WHERE c.domain_id = 'Type Concept'

    -- CORRECT: Domain validation in JOIN clause
    SELECT vo.*, c.concept_name
    FROM visit_occurrence vo
    JOIN concept c ON vo.visit_type_concept_id = c.concept_id
        AND c.domain_id = 'Type Concept'
"""

import logging
from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants -------------------------------------------------------------

VISIT_OCCURRENCE = "visit_occurrence"
CONCEPT = "concept"
CONCEPT_ID = "concept_id"
VISIT_TYPE_CONCEPT_ID = "visit_type_concept_id"
DOMAIN_ID = "domain_id"
EXPECTED_DOMAIN = "Type Concept"

# Normalized constants
VISIT_OCCURRENCE_NORM = normalize_name(VISIT_OCCURRENCE)
CONCEPT_NORM = normalize_name(CONCEPT)
CONCEPT_ID_NORM = normalize_name(CONCEPT_ID)
VISIT_TYPE_CONCEPT_ID_NORM = normalize_name(VISIT_TYPE_CONCEPT_ID)
DOMAIN_ID_NORM = normalize_name(DOMAIN_ID)
EXPECTED_DOMAIN_NORM = normalize_name(EXPECTED_DOMAIN)


# --- Helpers ---------------------------------------------------------------

def _norm(val: Optional[str]) -> Optional[str]:
    return normalize_name(val) if val else None


def _find_visit_type_concept_joins(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Set[str]:
    """
    Find joins between visit_occurrence.visit_type_concept_id and concept.concept_id.
    Returns set of concept table aliases.
    """
    concept_aliases: Set[str] = set()

    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        # Ensure inside JOIN
        parent = eq.parent
        in_join = False
        while parent:
            if isinstance(parent, exp.Join):
                in_join = True
                break
            parent = parent.parent
        if not in_join:
            continue

        left, right = eq.left, eq.right

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        lt_norm, lc_norm = _norm(lt), _norm(lc)
        rt_norm, rc_norm = _norm(rt), _norm(rc)

        left_alias = _norm(left.table) if left.table else None
        right_alias = _norm(right.table) if right.table else None

        # visit_occurrence -> concept
        if (
            lt_norm == VISIT_OCCURRENCE_NORM
            and lc_norm == VISIT_TYPE_CONCEPT_ID_NORM
            and rt_norm == CONCEPT_NORM
            and rc_norm == CONCEPT_ID_NORM
        ):
            if right_alias and _norm(aliases.get(right_alias)) == CONCEPT_NORM:
                concept_aliases.add(right_alias)

        # concept -> visit_occurrence
        elif (
            rt_norm == VISIT_OCCURRENCE_NORM
            and rc_norm == VISIT_TYPE_CONCEPT_ID_NORM
            and lt_norm == CONCEPT_NORM
            and lc_norm == CONCEPT_ID_NORM
        ):
            if left_alias and _norm(aliases.get(left_alias)) == CONCEPT_NORM:
                concept_aliases.add(left_alias)

    return concept_aliases


def _extract_domain_values(
    tree: exp.Expression,
    aliases: Dict[str, str],
    concept_alias: str,
) -> Set[str]:
    """
    Extract all domain_id values applied to a specific concept alias.
    Only considers explicitly qualified columns (strict alias match).
    """
    values: Set[str] = set()
    concept_alias_norm = _norm(concept_alias)

    def _extract_column(expr):
        """Handle column or simple function wrapping."""
        if isinstance(expr, exp.Column):
            return expr
        if isinstance(expr, exp.Func):
            return expr.this if isinstance(expr.this, exp.Column) else None
        return None

    # EQ conditions
    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        col = _extract_column(eq.this)
        if not col:
            continue

        table, col_name = resolve_table_col(col, aliases)

        if _norm(col_name) != DOMAIN_ID_NORM:
            continue

        col_table = _norm(col.table) if col.table else None

        # STRICT alias enforcement (critical fix)
        if col_table != concept_alias_norm:
            continue

        if table and _norm(table) != CONCEPT_NORM:
            continue

        if is_string_literal(eq.expression):
            value = str(eq.expression.this).strip("'\"")
            values.add(value)

    # IN conditions
    for in_node in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_node):
            continue

        col = _extract_column(in_node.this)
        if not col:
            continue

        table, col_name = resolve_table_col(col, aliases)

        if _norm(col_name) != DOMAIN_ID_NORM:
            continue

        col_table = _norm(col.table) if col.table else None

        # STRICT alias enforcement
        if col_table != concept_alias_norm:
            continue

        if table and _norm(table) != CONCEPT_NORM:
            continue

        for expr in in_node.expressions or []:
            if is_string_literal(expr):
                value = str(expr.this).strip("'\"")
                values.add(value)

    return values


# --- Rule ------------------------------------------------------------------

@register
class VisitOccurrenceTypeDomainRule(Rule):
    """
    Validates that visit_type_concept_id joins to concept with domain_id = 'Type Concept'.
    """

    rule_id = "domain_specific.visit_occurrence_type_domain"
    name = "Visit Occurrence Type Concept Domain Validation"

    description = (
        "visit_type_concept_id must reference a concept belonging to the Type Concept "
        "domain. Queries joining visit_type_concept_id to concept must filter by "
        "domain_id = 'Type Concept'."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Add domain filter: c.domain_id = 'Type Concept' in WHERE or JOIN clause."
    )

    example_bad = (
        "SELECT vo.visit_occurrence_id FROM visit_occurrence vo\n"
        "JOIN concept c ON vo.visit_type_concept_id = c.concept_id;"
    )
    example_good = (
        "SELECT vo.visit_occurrence_id FROM visit_occurrence vo\n"
        "JOIN concept c ON vo.visit_type_concept_id = c.concept_id\n"
        "WHERE c.domain_id = 'Type Concept';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            logger.warning(f"[{self.rule_id}] SQL parse error: {err}")
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, VISIT_OCCURRENCE):
                continue

            if not has_table_reference(tree, CONCEPT):
                continue

            aliases = extract_aliases(tree)

            concept_aliases = _find_visit_type_concept_joins(tree, aliases)

            for concept_alias in concept_aliases:
                values = _extract_domain_values(tree, aliases, concept_alias)

                normalized_values = {_norm(v) for v in values}

                # Case 1: correct filter present
                if EXPECTED_DOMAIN_NORM in normalized_values:
                    continue

                # Case 2: wrong domain(s)
                if values:
                    violations.append(
                        self.create_violation(
                            message=(
                                f"visit_type_concept_id joined to concept '{concept_alias}' "
                                f"with incorrect domain_id(s) = {sorted(values)}. "
                                f"Expected '{EXPECTED_DOMAIN}'."
                            ),
                            severity=Severity.ERROR,
                            suggested_fix=(
                                f"Use: {concept_alias}.domain_id = '{EXPECTED_DOMAIN}'"
                            ),
                            details={
                                "column": VISIT_TYPE_CONCEPT_ID,
                                "concept_alias": concept_alias,
                                "found_domains": sorted(values),
                                "expected_domain": EXPECTED_DOMAIN,
                            },
                        )
                    )
                else:
                    # Case 3: missing filter
                    violations.append(
                        self.create_violation(
                            message=(
                                f"visit_type_concept_id joined to concept '{concept_alias}' "
                                f"without domain_id filter. Expected '{EXPECTED_DOMAIN}'."
                            ),
                            severity=Severity.ERROR,
                            suggested_fix=(
                                f"Add: {concept_alias}.domain_id = '{EXPECTED_DOMAIN}'"
                            ),
                            details={
                                "column": VISIT_TYPE_CONCEPT_ID,
                                "concept_alias": concept_alias,
                                "expected_domain": EXPECTED_DOMAIN,
                            },
                        )
                    )

        return violations


__all__ = ["VisitOccurrenceTypeDomainRule"]

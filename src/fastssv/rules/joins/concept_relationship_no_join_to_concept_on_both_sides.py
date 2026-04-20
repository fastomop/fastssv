"""Concept Relationship Incomplete Join Validation Rule.

OMOP semantic rule VOCAB_015:
When using concept_relationship to traverse concept relationships, both concept_id_1
and concept_id_2 should typically be joined to the concept table to access concept
details (name, vocabulary_id, domain_id) for both sides of the relationship.

The Problem:
    The concept_relationship table stores only concept IDs:
    - concept_id_1: The source/origin concept ID
    - concept_id_2: The target/destination concept ID

    To get actual concept information (names, vocabularies, domains), you need
    to join to the concept table. When working with relationships, you typically
    want details about BOTH concepts, not just one.

    Joining only one side leaves the other as just a number, which is rarely
    what users intend when exploring concept relationships.

Common mistake scenarios:
    1. Joining concept only on concept_id_1 to get source names
       (but concept_id_2 remains just an ID)

    2. Joining concept only on concept_id_2 to get target names
       (but concept_id_1 remains just an ID)

    3. Forgetting that relationships need two-sided resolution

Violation pattern:
    SELECT c1.concept_name, cr.concept_id_2
    FROM concept c1
    JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
    WHERE cr.relationship_id = 'Maps to'
    -- INCOMPLETE: concept_id_2 is just a number, not a name!

Correct pattern:
    SELECT c1.concept_name AS source_name, c2.concept_name AS target_name
    FROM concept c1
    JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
    JOIN concept c2 ON c2.concept_id = cr.concept_id_2
    WHERE cr.relationship_id = 'Maps to'
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_RELATIONSHIP = "concept_relationship"
CONCEPT = "concept"
CONCEPT_ID_1 = "concept_id_1"
CONCEPT_ID_2 = "concept_id_2"
CONCEPT_ID = "concept_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _get_cr_aliases(aliases: Dict[str, str]) -> Set[str]:
    """Return all aliases that correspond to concept_relationship."""
    return {
        alias for alias, table in aliases.items()
        if _normalize_table(table) == CONCEPT_RELATIONSHIP
    }


def _extract_joined_sides_per_alias(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Dict[str, Set[str]]:
    """
    Returns:
        Dict[cr_alias -> set(concept_id_1 / concept_id_2)]
    """
    cr_aliases = _get_cr_aliases(aliases)
    results: Dict[str, Set[str]] = {a: set() for a in cr_aliases}

    for eq in tree.find_all(exp.EQ):
        # Only consider JOIN / WHERE clauses (avoid projections)
        if not is_in_where_or_join_clause(eq):
            continue

        if not (isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)):
            continue

        lt, lc = resolve_table_col(eq.this, aliases)
        rt, rc = resolve_table_col(eq.expression, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt_norm = _normalize_table(lt)
        rt_norm = _normalize_table(rt)

        lc_norm = _norm(lc)
        rc_norm = _norm(rc)

        # LEFT: concept_relationship → concept
        if lt_norm == CONCEPT_RELATIONSHIP and rt_norm == CONCEPT:
            if lc_norm in {CONCEPT_ID_1, CONCEPT_ID_2} and rc_norm == CONCEPT_ID:
                # Use original alias from Column node, not resolved table name
                cr_alias = eq.this.table if eq.this.table else lt
                cr_alias_norm = _norm(cr_alias)
                if cr_alias_norm in results:
                    results[cr_alias_norm].add(lc_norm)

        # RIGHT: concept → concept_relationship
        elif rt_norm == CONCEPT_RELATIONSHIP and lt_norm == CONCEPT:
            if rc_norm in {CONCEPT_ID_1, CONCEPT_ID_2} and lc_norm == CONCEPT_ID:
                # Use original alias from Column node, not resolved table name
                cr_alias = eq.expression.table if eq.expression.table else rt
                cr_alias_norm = _norm(cr_alias)
                if cr_alias_norm in results:
                    results[cr_alias_norm].add(rc_norm)

    return results


def _detect_incomplete_joins(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, Dict[str, str]]]:
    """
    Returns:
        List of (cr_alias, violation_info)
    """
    joined = _extract_joined_sides_per_alias(tree, aliases)

    violations = []

    for cr_alias, sides in joined.items():
        if not sides:
            continue

        if CONCEPT_ID_1 in sides and CONCEPT_ID_2 in sides:
            continue

        if CONCEPT_ID_1 in sides:
            violations.append((
                cr_alias,
                {
                    "joined_side": CONCEPT_ID_1,
                    "missing_side": CONCEPT_ID_2,
                    "message": (
                        "concept_relationship is joined on concept_id_1 but not concept_id_2. "
                        "Target concept details are missing."
                    ),
                }
            ))

        elif CONCEPT_ID_2 in sides:
            violations.append((
                cr_alias,
                {
                    "joined_side": CONCEPT_ID_2,
                    "missing_side": CONCEPT_ID_1,
                    "message": (
                        "concept_relationship is joined on concept_id_2 but not concept_id_1. "
                        "Source concept details are missing."
                    ),
                }
            ))

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ConceptRelationshipIncompleteJoinRule(Rule):
    """Validate concept_relationship joins to concept on both sides."""

    rule_id = "joins.concept_relationship_incomplete_join"
    name = "Concept Relationship Incomplete Join"

    description = (
        "Ensures concept_relationship is joined to concept on both concept_id_1 "
        "and concept_id_2 to access complete concept details."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Join the concept table twice using different aliases to resolve both "
        "concept_id_1 and concept_id_2."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if CONCEPT_RELATIONSHIP not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not uses_table(tree, CONCEPT_RELATIONSHIP):
                continue

            if not uses_table(tree, CONCEPT):
                continue

            aliases = extract_aliases(tree)
            issues = _detect_incomplete_joins(tree, aliases)

            for cr_alias, issue in issues:
                violations.append(
                    self.create_violation(
                        message=issue["message"],
                        severity=self.severity,
                        suggested_fix=(
                            f"Add missing join for {issue['missing_side']}: "
                            f"JOIN concept c_{issue['missing_side']} "
                            f"ON c_{issue['missing_side']}.concept_id = "
                            f"{cr_alias}.{issue['missing_side']}"
                        ),
                        details={
                            "type": "incomplete_join",
                            "cr_alias": cr_alias,
                            "joined_side": issue["joined_side"],
                            "missing_side": issue["missing_side"],
                        },
                    )
                )

        return violations


__all__ = ["ConceptRelationshipIncompleteJoinRule"]
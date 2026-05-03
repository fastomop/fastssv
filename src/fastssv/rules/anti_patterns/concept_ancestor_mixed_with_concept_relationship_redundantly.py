"""Concept Ancestor Mixed with Concept Relationship Redundantly Rule.

OMOP semantic rule VOCAB_038:
concept_ancestor already includes all transitive hierarchical paths. Joining
concept_ancestor AND concept_relationship with 'Is a' in the same query for
the same hierarchy traversal is redundant and may produce duplicated or
incorrect row counts.

The Problem:
    The concept_ancestor table is a pre-computed transitive closure table
    that contains ALL hierarchical relationships across all levels:
    - Direct parent-child relationships (1 hop)
    - Grandparent-grandchild relationships (2 hops)
    - All deeper ancestor-descendant relationships (N hops)

    This table is automatically built by traversing the concept_relationship
    table and following all hierarchical relationships where:
    - relationship_id = 'Is a', 'Subsumes', or other relationships
    - relationship.defines_ancestry = 1

    When a query already uses concept_ancestor for hierarchy traversal,
    also joining concept_relationship with hierarchical relationship_id
    filters is redundant because:
    1. concept_ancestor already contains this information
    2. Mixing both tables may cause duplicate rows
    3. It may produce incorrect counts or aggregations
    4. It adds unnecessary complexity and performance overhead

Violation patterns:
    -- WRONG: Redundant use of both tables
    SELECT DISTINCT ca.descendant_concept_id
    FROM concept_ancestor ca
    JOIN concept_relationship cr
      ON ca.descendant_concept_id = cr.concept_id_1
    WHERE ca.ancestor_concept_id = 201820
      AND cr.relationship_id = 'Is a'
    -- concept_ancestor already includes all 'Is a' relationships

    -- WRONG: Both tables for same hierarchy traversal
    SELECT c.concept_name
    FROM concept c
    JOIN concept_ancestor ca ON c.concept_id = ca.descendant_concept_id
    JOIN concept_relationship cr ON c.concept_id = cr.concept_id_1
    WHERE ca.ancestor_concept_id = 4329847
      AND cr.relationship_id = 'Subsumes'
    -- Redundant - 'Subsumes' is already in concept_ancestor

    -- WRONG: Mixing both in subquery
    SELECT DISTINCT concept_id
    FROM (
      SELECT ca.descendant_concept_id AS concept_id
      FROM concept_ancestor ca
      WHERE ca.ancestor_concept_id = 201820
    ) ancestors
    JOIN concept_relationship cr
      ON ancestors.concept_id = cr.concept_id_1
    WHERE cr.relationship_id = 'Is a'

Correct patterns:
    -- CORRECT: Use concept_ancestor only
    SELECT DISTINCT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
    -- Efficient, includes all levels

    -- CORRECT: Use concept_relationship for non-hierarchical relationships
    SELECT cr.concept_id_2
    FROM concept_ancestor ca
    JOIN concept c ON ca.descendant_concept_id = c.concept_id
    JOIN concept_relationship cr ON c.concept_id = cr.concept_id_1
    WHERE ca.ancestor_concept_id = 201820
      AND cr.relationship_id = 'RxNorm has dose form'
    -- Not hierarchical (lateral relationship), so legitimate

    -- CORRECT: Use concept_relationship alone for direct parent only
    SELECT cr.concept_id_2 AS parent_id
    FROM concept_relationship cr
    WHERE cr.concept_id_1 = 1234
      AND cr.relationship_id = 'Is a'
      AND cr.invalid_reason IS NULL
    -- Explicit direct parent lookup (not using concept_ancestor)

    -- CORRECT: Use both for different purposes
    SELECT c.concept_id
    FROM concept c
    JOIN concept_ancestor ca
      ON c.concept_id = ca.descendant_concept_id
    JOIN concept_relationship cr
      ON c.concept_id = cr.concept_id_1
    WHERE ca.ancestor_concept_id = 201820
      AND cr.relationship_id = 'Maps to'
    -- concept_ancestor for hierarchy, concept_relationship for mapping
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

CONCEPT_ANCESTOR = "concept_ancestor"
CONCEPT_RELATIONSHIP = "concept_relationship"

HIERARCHICAL_RELATIONSHIPS = {
    "Is a",
    "Subsumes",
    "Has ancestor",
    "Has descendant",
}

HIERARCHICAL_RELATIONSHIPS_NORM = {normalize_name(r) for r in HIERARCHICAL_RELATIONSHIPS}


# --- Helpers ---------------------------------------------------------------


def _norm(val: Optional[str]) -> Optional[str]:
    return normalize_name(val) if val else None


def _extract_table_names(tree: exp.Expression) -> Set[str]:
    table_names = set()
    for table in tree.find_all(exp.Table):
        table_names.add(_norm(table.name))
    return table_names


def _has_hierarchical_relationship_filter(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> bool:
    """Check if concept_relationship is filtered by hierarchical relationship_id."""

    target_nodes = list(tree.find_all(exp.EQ)) + list(tree.find_all(exp.In))

    for node in target_nodes:
        # --- EQ ---
        if isinstance(node, exp.EQ):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                # Resolve actual table
                table, col_name = resolve_table_col(col_node, aliases)

                if _norm(table) != _norm(CONCEPT_RELATIONSHIP):
                    continue

                if _norm(col_name) != "relationship_id":
                    continue

                if isinstance(val_node, exp.Literal) and val_node.is_string:
                    val = _norm(str(val_node.this))
                    if val in HIERARCHICAL_RELATIONSHIPS_NORM:
                        return True

        # --- IN ---
        elif isinstance(node, exp.In):
            col = node.this
            if not isinstance(col, exp.Column):
                continue

            table, col_name = resolve_table_col(col, aliases)

            if _norm(table) != _norm(CONCEPT_RELATIONSHIP):
                continue

            if _norm(col_name) != "relationship_id":
                continue

            for val in node.expressions or []:
                if isinstance(val, exp.Literal) and val.is_string:
                    norm_val = _norm(str(val.this))
                    if norm_val in HIERARCHICAL_RELATIONSHIPS_NORM:
                        return True

    return False


# --- Rule ------------------------------------------------------------------


@register
class ConceptAncestorMixedWithConceptRelationshipRedundantlyRule(Rule):
    """Detect redundant use of both concept_ancestor and concept_relationship."""

    rule_id = "anti_patterns.concept_ancestor_mixed_with_concept_relationship_redundantly"
    name = "Concept Ancestor Mixed with Concept Relationship Redundantly"

    description = (
        "concept_ancestor already includes all transitive hierarchical paths. "
        "Using concept_relationship with hierarchical filters in the same query is redundant."
    )

    severity = Severity.WARNING

    suggested_fix = "REMOVE: the concept_relationship hierarchical filter. Use concept_ancestor alone for hierarchical traversal. Reserve concept_relationship for non-hierarchical relationships (Maps to, Has_RxNorm, etc.)."
    long_description = (
        "concept_ancestor already encodes every transitive hierarchical "
        "path (ancestor → descendant, including indirect ones). Joining "
        "concept_relationship to it for hierarchical relationships like "
        "'Subsumes' or 'Is a' is redundant and typically changes the "
        "semantics — the combined filter returns the intersection rather "
        "than the hierarchy you intended. Use concept_ancestor for "
        "hierarchy and reserve concept_relationship for non-hierarchical "
        "links like 'Maps to', 'Has indication', 'Contains'."
    )
    example_bad = (
        "SELECT ca.descendant_concept_id\n"
        "FROM concept_ancestor ca\n"
        "JOIN concept_relationship cr\n"
        "  ON ca.descendant_concept_id = cr.concept_id_1\n"
        "WHERE ca.ancestor_concept_id = 201820\n"
        "  AND cr.relationship_id = 'Subsumes';"
    )
    example_good = "SELECT ca.descendant_concept_id\nFROM concept_ancestor ca\nWHERE ca.ancestor_concept_id = 201820;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if "concept_ancestor" not in sql_lower or "concept_relationship" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            table_names = _extract_table_names(tree)

            has_ca = _norm(CONCEPT_ANCESTOR) in table_names
            has_cr = _norm(CONCEPT_RELATIONSHIP) in table_names

            if not (has_ca and has_cr):
                continue

            if not _has_hierarchical_relationship_filter(tree, aliases):
                continue

            key = "ca+cr+hierarchical"
            if key in seen:
                continue
            seen.add(key)

            violations.append(
                self.create_violation(
                    message=(
                        "Query uses both concept_ancestor and concept_relationship with "
                        "hierarchical relationship_id filters (e.g., 'Is a', 'Subsumes'). "
                        "This is redundant since concept_ancestor already contains all "
                        "transitive hierarchical paths."
                    ),
                    severity=Severity.WARNING,
                    suggested_fix=(
                        "REMOVE: the concept_relationship join when concept_ancestor is "
                        "already in scope. concept_ancestor pre-computes the transitive "
                        "'Is a' closure; reserve concept_relationship for non-hierarchical "
                        "relationships (Maps to, Has_RxNorm, etc.)."
                    ),
                    details={
                        "has_concept_ancestor": True,
                        "has_concept_relationship": True,
                        "has_hierarchical_filter": True,
                    },
                )
            )

        return violations


__all__ = ["ConceptAncestorMixedWithConceptRelationshipRedundantlyRule"]

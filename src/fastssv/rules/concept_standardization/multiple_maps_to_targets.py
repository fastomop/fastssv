"""Multiple Maps To Targets Not Handled Validation Rule.

OMOP semantic rule VOCAB_013:
A single source concept can map to multiple standard concepts via 'Maps to'.
Queries that assume a 1:1 mapping may silently duplicate records or miss mappings.

The Problem:
    The concept_relationship table with relationship_id = 'Maps to' is a
    one-to-many relationship:
    - A source concept can map to multiple standard concepts
    - Assuming only one mapping exists leads to:
      * Duplicate rows (when joining without DISTINCT)
      * Arbitrary/incomplete results (when using scalar subqueries)
      * Missing mappings (when using LIMIT 1)

    Common mistakes:
    1. Scalar subquery assuming single result
    2. JOIN without DISTINCT or GROUP BY
    3. Using LIMIT 1 to force single result

Violation patterns:
    -- WRONG: Scalar subquery (returns arbitrary value if multiple)
    SELECT (
      SELECT concept_id_2
      FROM concept_relationship
      WHERE concept_id_1 = c.concept_id
        AND relationship_id = 'Maps to'
    ) AS standard_id
    FROM concept c

    -- WRONG: JOIN without DISTINCT (duplicates rows)
    SELECT de.*, cr.concept_id_2
    FROM drug_exposure de
    JOIN concept_relationship cr ON de.drug_concept_id = cr.concept_id_1
    WHERE cr.relationship_id = 'Maps to'

    -- WRONG: LIMIT 1 (arbitrary selection)
    SELECT concept_id_2
    FROM concept_relationship
    WHERE concept_id_1 = 12345
      AND relationship_id = 'Maps to'
    LIMIT 1

Correct patterns:
    -- CORRECT: Use DISTINCT
    SELECT DISTINCT de.drug_exposure_id, cr.concept_id_2
    FROM drug_exposure de
    JOIN concept_relationship cr ON de.drug_concept_id = cr.concept_id_1
    WHERE cr.relationship_id = 'Maps to'

    -- CORRECT: Use aggregation
    SELECT de.drug_exposure_id,
           ARRAY_AGG(cr.concept_id_2) AS mapped_concepts
    FROM drug_exposure de
    JOIN concept_relationship cr ON de.drug_concept_id = cr.concept_id_1
    WHERE cr.relationship_id = 'Maps to'
    GROUP BY de.drug_exposure_id
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
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_RELATIONSHIP = "concept_relationship"
RELATIONSHIP_ID = "relationship_id"

MAPS_TO_VALUES = {"maps to", "mapped from"}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


def _is_maps_to_filter(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if expression filters relationship_id to Maps to / Mapped from."""
    if not isinstance(node, exp.EQ):
        return False

    if not is_in_where_or_join_clause(node):
        return False

    pairs = [(node.this, node.expression), (node.expression, node.this)]

    for col_node, val_node in pairs:
        if not isinstance(col_node, exp.Column):
            continue

        table, col_name = resolve_table_col(col_node, aliases)

        # relationship_id is unique to concept_relationship, so table check is optional
        if _norm(table) and _norm(table) != CONCEPT_RELATIONSHIP:
            continue

        if _norm(col_name) != RELATIONSHIP_ID:
            continue

        value = _extract_string_literal(val_node)
        if value and value.strip().lower() in MAPS_TO_VALUES:
            return True

    return False


def _uses_maps_to(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    return any(_is_maps_to_filter(node, aliases) for node in tree.find_all(exp.EQ))


def _has_distinct(tree: exp.Expression) -> bool:
    return any(sel.args.get("distinct") for sel in tree.find_all(exp.Select))


def _has_group_by(tree: exp.Expression) -> bool:
    return any(sel.args.get("group") for sel in tree.find_all(exp.Select))


def _has_limit_one(tree: exp.Expression) -> bool:
    for sel in tree.find_all(exp.Select):
        limit = sel.args.get("limit")
        if not limit:
            continue

        expr = limit.expression
        if isinstance(expr, exp.Literal):
            try:
                if int(expr.this) == 1:
                    return True
            except Exception:
                continue
    return False


# --- Detection -------------------------------------------------------------

def _detect_scalar_subquery(tree: exp.Expression) -> List[str]:
    """Detect scalar subqueries anywhere in AST."""
    contexts = []
    for subquery in tree.find_all(exp.Subquery):
        contexts.append(subquery.sql())
    return contexts


# --- Rule ------------------------------------------------------------------

@register
class MultipleMapsToTargetsRule(Rule):
    """Detect incorrect assumptions of 1:1 mapping in concept_relationship."""

    rule_id = "concept_standardization.multiple_maps_to_targets"
    name = "Multiple Maps To Targets Not Handled"

    description = (
        "A source concept can map to multiple standard concepts via 'Maps to'. "
        "Queries assuming 1:1 mapping may produce incorrect results."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use DISTINCT, GROUP BY with aggregation (e.g., ARRAY_AGG), "
        "or explicitly handle multiple mappings."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "concept_relationship" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, CONCEPT_RELATIONSHIP):
                continue

            aliases = extract_aliases(tree)

            if not _uses_maps_to(tree, aliases):
                continue

            # --- Scalar subqueries ---
            for subquery in tree.find_all(exp.Subquery):
                sub_tree = subquery.this
                if not sub_tree:
                    continue

                sub_aliases = extract_aliases(sub_tree)

                if has_table_reference(sub_tree, CONCEPT_RELATIONSHIP) and _uses_maps_to(sub_tree, sub_aliases):
                    key = f"scalar_{subquery.sql()}"
                    if key in seen:
                        continue
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                "Scalar subquery with 'Maps to' assumes a single mapping. "
                                "Multiple mappings may exist, causing incomplete results."
                            ),
                            severity=Severity.WARNING,
                            suggested_fix=(
                                "Replace scalar subquery with JOIN + DISTINCT or aggregation."
                            ),
                            details={"context": subquery.sql()},
                        )
                    )

            # --- JOIN without dedup ---
            if not (_has_distinct(tree) or _has_group_by(tree)):
                key = "join_no_dedup"
                if key not in seen:
                    seen.add(key)
                    violations.append(
                        self.create_violation(
                            message=(
                                "Query uses 'Maps to' relationships without DISTINCT or GROUP BY. "
                                "A single source concept can map to multiple standard concepts, "
                                "which may cause duplicate rows or incomplete results."
                            ),
                            severity=Severity.WARNING,
                            suggested_fix=(
                                "Add DISTINCT to SELECT or use GROUP BY with aggregation "
                                "to explicitly handle multiple mappings."
                            ),
                        )
                    )

            # --- LIMIT 1 misuse ---
            if _has_limit_one(tree):
                key = "limit_one"
                if key not in seen:
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                "LIMIT 1 with 'Maps to' assumes a single mapping. "
                                "This may return arbitrary results."
                            ),
                            severity=Severity.WARNING,
                            suggested_fix=(
                                "Remove LIMIT or use aggregation to explicitly select one mapping."
                            ),
                            details={"context": tree.sql()},
                        )
                    )

        return violations


__all__ = ["MultipleMapsToTargetsRule"]
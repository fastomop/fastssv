"""Concept Relationship to Concept Join Validation Rule.

OMOP semantic rule JOIN_017:
When joining concept_relationship to concept table for name resolution, the join
columns must match the semantic direction indicated by aliases.

The Problem:
    concept_relationship has two concept_id columns:
    - concept_id_1: The source/origin concept (what you're mapping FROM)
    - concept_id_2: The target/destination concept (what you're mapping TO)

    When joining to the concept table twice to retrieve names for both concepts,
    developers often swap the join columns, causing:
    - The "source" alias to actually show the target concept's name
    - The "target" alias to actually show the source concept's name
    - Completely reversed mapping semantics

    Common mistakes:
    1. Aliasing as "c_source" but joining on concept_id_2
       - Returns the target concept's name, not the source
    2. Aliasing as "c_target" but joining on concept_id_1
       - Returns the source concept's name, not the target
    3. Using numbered aliases (c1, c2) but swapping which joins to which

Violation pattern:
    SELECT
      c_source.concept_name AS source_name,
      c_target.concept_name AS target_name
    FROM concept_relationship cr
    JOIN concept c_source ON cr.concept_id_2 = c_source.concept_id
    JOIN concept c_target ON cr.concept_id_1 = c_target.concept_id
    -- WRONG: aliases are swapped!

Correct pattern:
    SELECT
      c_source.concept_name AS source_name,
      c_target.concept_name AS target_name
    FROM concept_relationship cr
    JOIN concept c_source ON cr.concept_id_1 = c_source.concept_id
    JOIN concept c_target ON cr.concept_id_2 = c_target.concept_id
"""

from typing import Dict, List, Optional, Set, Tuple
import re

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

CONCEPT_RELATIONSHIP = "concept_relationship"
CONCEPT = "concept"

CONCEPT_ID_1 = "concept_id_1"
CONCEPT_ID_2 = "concept_id_2"
CONCEPT_ID = "concept_id"

SOURCE_KEYWORDS = {"source", "src", "from", "origin", "one", "first"}
TARGET_KEYWORDS = {"target", "tgt", "to", "dest", "destination", "two", "second", "standard"}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _tokenize(name: str) -> List[str]:
    """Tokenize alias safely."""
    tokens = name.lower().replace("-", "_").split("_")
    expanded = []

    for token in tokens:
        match = re.match(r"^([a-z]+)(\d)$", token)  # only single digit
        if match:
            expanded.append(match.group(1))
            expanded.append(match.group(2))
        else:
            expanded.append(token)

    return expanded


def _infer_intent(alias: Optional[str]) -> Optional[str]:
    """Infer source vs target intent."""
    if not alias:
        return None

    tokens = _tokenize(alias)

    # Exact match for numeric tokens
    if "1" in tokens:
        return "source"
    if "2" in tokens:
        return "target"

    if any(t in SOURCE_KEYWORDS for t in tokens):
        return "source"

    if any(t in TARGET_KEYWORDS for t in tokens):
        return "target"

    return None


def _extract_cr_concept_joins(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str]]:
    """
    Extract CR → concept joins.

    Returns:
        List of (concept_alias, cr_column)
    """
    results = []

    for eq in tree.find_all(exp.EQ):
        if not (isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)):
            continue

        lt, lc = resolve_table_col(eq.this, aliases)
        rt, rc = resolve_table_col(eq.expression, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt_norm = _normalize_table(lt)
        rt_norm = _normalize_table(rt)

        # CR → concept
        if lt_norm == CONCEPT_RELATIONSHIP and rt_norm == CONCEPT:
            if _norm(rc) == CONCEPT_ID and _norm(lc) in {CONCEPT_ID_1, CONCEPT_ID_2}:
                # Use the alias from the Column node, not the resolved table name
                concept_alias = eq.expression.table if eq.expression.table else rt
                results.append((concept_alias, lc))

        # concept → CR
        elif rt_norm == CONCEPT_RELATIONSHIP and lt_norm == CONCEPT:
            if _norm(lc) == CONCEPT_ID and _norm(rc) in {CONCEPT_ID_1, CONCEPT_ID_2}:
                # Use the alias from the Column node, not the resolved table name
                concept_alias = eq.this.table if eq.this.table else lt
                results.append((concept_alias, rc))

    return results


# --- Detection -------------------------------------------------------------

def _detect(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    """
    Detect semantic mismatches.

    Returns:
        List of (concept_alias, cr_column, intent)
    """
    errors = []
    seen: Set[Tuple[str, str, str]] = set()

    joins = _extract_cr_concept_joins(tree, aliases)

    for concept_alias, cr_col in joins:
        concept_alias_norm = _normalize_table(concept_alias)
        intent = _infer_intent(concept_alias_norm)

        if not intent:
            continue  # conservative: avoid noise

        cr_col_norm = _norm(cr_col)

        mismatch = (
            intent == "source" and cr_col_norm == CONCEPT_ID_2
        ) or (
            intent == "target" and cr_col_norm == CONCEPT_ID_1
        )

        if mismatch:
            key = (concept_alias_norm, cr_col_norm, intent)
            if key not in seen:
                errors.append(key)
                seen.add(key)

    return errors


# --- Rule ------------------------------------------------------------------

@register
class ConceptRelationshipConceptJoinValidationRule(Rule):
    """Validate CR → concept joins match semantic intent."""

    rule_id = "joins.concept_relationship_concept_join_validation"
    name = "Concept Relationship to Concept Join Validation"

    description = (
        "Ensures concept_relationship joins to concept align with "
        "semantic intent (source vs target) inferred from aliases."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use concept_id_1 for source concepts and concept_id_2 for target concepts."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if "concept_relationship" not in sql_lower or "concept" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            if not (has_table_reference(tree, CONCEPT_RELATIONSHIP) and has_table_reference(tree, CONCEPT)):
                continue

            aliases = extract_aliases(tree)
            errors = _detect(tree, aliases)

            for concept_alias, actual_col, intent in errors:
                expected_col = CONCEPT_ID_1 if intent == "source" else CONCEPT_ID_2

                violations.append(
                    self.create_violation(
                        message=(
                            f"Semantic mismatch: alias '{concept_alias}' implies {intent} concept "
                            f"(expected {expected_col}) but join uses {actual_col}."
                        ),
                        suggested_fix=(
                            f"Use: concept_relationship.{expected_col} = "
                            f"{concept_alias}.concept_id"
                        ),
                        details={
                            "type": "semantic_intent_mismatch",
                            "alias": concept_alias,
                            "intent": intent,
                            "actual_column": actual_col,
                            "expected_column": expected_col,
                        },
                    )
                )

        return violations


__all__ = ["ConceptRelationshipConceptJoinValidationRule"]
"""Join Key Validation Rule.

Validates that JOIN conditions use correct foreign key relationships in OMOP queries.
Detects incompatible or suspicious JOIN keys that would produce incorrect results.

The Problem:
    Joining tables on incompatible keys creates meaningless results:

    SELECT * FROM person p
    JOIN concept c ON p.person_id = c.concept_id
    -- WRONG: person_id and concept_id are different entity types!

    Common mistakes:
    1. Joining person_id to concept_id (person IDs vs concept IDs)
    2. Joining visit_occurrence_id to concept_id (visit IDs vs concept IDs)
    3. Joining provider_id to concept_id (provider IDs vs concept IDs)
    4. Mixing up different _id columns (person_id = provider_id)

    This causes:
    - Incorrect query results (accidental matches on numeric IDs)
    - Data quality issues (meaningless joins)
    - Silent bugs (query runs but returns wrong data)

Violation patterns:
    SELECT * FROM person p
    JOIN concept c ON p.person_id = c.concept_id
    -- ERROR: Incompatible keys (person_id ≠ concept_id)

    SELECT * FROM condition_occurrence co
    JOIN provider pr ON co.person_id = pr.provider_id
    -- WARNING: Suspicious (different _id types)

Correct patterns:
    SELECT * FROM person p
    JOIN condition_occurrence co ON p.person_id = co.person_id
    -- CORRECT: Same entity type (person_id ↔ person_id)

    SELECT * FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    -- CORRECT: Foreign key relationship (*_concept_id ↔ concept_id)
"""

from typing import List, Optional, Set, Tuple

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

# Known valid OMOP join keys (safe joins)
VALID_JOIN_KEYS: Set[Tuple[str, str]] = {
    ("person_id", "person_id"),
    ("visit_occurrence_id", "visit_occurrence_id"),
    ("visit_detail_id", "visit_detail_id"),
    ("provider_id", "provider_id"),
    ("care_site_id", "care_site_id"),
    ("drug_concept_id", "concept_id"),
    ("condition_concept_id", "concept_id"),
    ("procedure_concept_id", "concept_id"),
    ("measurement_concept_id", "concept_id"),
    ("observation_concept_id", "concept_id"),
    ("device_concept_id", "concept_id"),
    ("concept_id", "concept_id"),
    # Canonical OMOP concept_ancestor patterns for hierarchy expansion
    ("descendant_concept_id", "condition_concept_id"),
    ("descendant_concept_id", "drug_concept_id"),
    ("descendant_concept_id", "procedure_concept_id"),
    ("descendant_concept_id", "measurement_concept_id"),
    ("descendant_concept_id", "observation_concept_id"),
    ("ancestor_concept_id", "concept_id"),
    ("descendant_concept_id", "concept_id"),
    # Gender/race/ethnicity lookups
    ("gender_concept_id", "concept_id"),
    ("race_concept_id", "concept_id"),
    ("ethnicity_concept_id", "concept_id"),
    # Cost table joins (cost_event_id is a polymorphic key)
    ("drug_exposure_id", "cost_event_id"),
    ("procedure_occurrence_id", "cost_event_id"),
    ("condition_occurrence_id", "cost_event_id"),
    ("measurement_id", "cost_event_id"),
    ("observation_id", "cost_event_id"),
    ("device_exposure_id", "cost_event_id"),
    ("visit_occurrence_id", "cost_event_id"),
    ("specimen_id", "cost_event_id"),
}

# Columns that are definitely incompatible
# These are entity IDs that should never be joined to concept_id
INCOMPATIBLE_COLUMN_GROUPS: List[Set[str]] = [
    {"person_id", "concept_id"},
    {"visit_occurrence_id", "concept_id"},
    {"provider_id", "concept_id"},
    {"care_site_id", "concept_id"},  # care_site is a physical entity, not a concept
    {"location_id", "concept_id"},  # location is a physical entity, not a concept
]


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_valid_join_pair(col1: str, col2: str) -> bool:
    pair = (_norm(col1), _norm(col2))
    reverse = (_norm(col2), _norm(col1))
    return pair in VALID_JOIN_KEYS or reverse in VALID_JOIN_KEYS


def _is_incompatible(col1: str, col2: str) -> bool:
    c1 = _norm(col1)
    c2 = _norm(col2)

    for group in INCOMPATIBLE_COLUMN_GROUPS:
        if c1 in group and c2 in group and c1 != c2:
            return True
    return False


def _is_suspicious(col1: str, col2: str) -> bool:
    """
    Heuristic: same suffix (_id) but different semantic prefix.

    OMOP-aware: If one column ends with '_concept_id' and the other is
    any other '*_id' column, this is likely a valid concept join pattern.
    """
    if not col1 or not col2:
        return False

    c1 = _norm(col1)
    c2 = _norm(col2)

    # Both end with _id but are different
    if c1.endswith("_id") and c2.endswith("_id") and c1 != c2:
        # OMOP pattern: any column with name ending in '_concept_id' can join to another
        # This includes CTE aliases like 'snomed_diabetes_id' joining to 'condition_concept_id'
        if c1.endswith("_concept_id") and c2.endswith("_concept_id"):
            return False  # Both are concept_id columns, valid join

        # Allow any *_id to join to *_concept_id (CTE pattern)
        # This handles cases like snomed_diabetes_id = condition_concept_id
        if c1.endswith("_concept_id") or c2.endswith("_concept_id"):
            return False  # One is a concept_id, likely valid

        return True

    return False


# --- Detection -------------------------------------------------------------


def _extract_join_conditions(tree: exp.Expression) -> List[Tuple[exp.Column, exp.Column, str]]:
    """Extract column = column join conditions."""
    joins = []

    for eq in tree.find_all(exp.EQ):
        left = eq.this
        right = eq.expression

        if isinstance(left, exp.Column) and isinstance(right, exp.Column):
            joins.append((left, right, eq.sql()))

    return joins


# --- Rule ------------------------------------------------------------------


@register
class JoinKeyValidationRule(Rule):
    """Validate correctness of JOIN keys in OMOP queries."""

    rule_id = "anti_patterns.join_key_validation"
    name = "Join Key Validation"

    description = (
        "Detects incorrect or suspicious JOIN conditions. "
        "Joining incompatible keys (e.g., person_id = concept_id) "
        "produces invalid results."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: incompatible join keys with the canonical OMOP FK pair. Patterns: person_id = person_id across clinical tables; <event>.<x>_concept_id = concept.concept_id; <clinical>.visit_occurrence_id = visit_occurrence.visit_occurrence_id."
    long_description = (
        "OMOP uses distinct ID namespaces for different entities: person_id, "
        "visit_occurrence_id, provider_id, care_site_id, concept_id, and "
        "so on. Joining incompatible ID columns (e.g. "
        "`person.person_id = concept.concept_id`) rarely errors, because "
        "they're both integers — but every match is a numeric coincidence, "
        "not a real relationship. The result is garbage rows. Pair keys "
        "with their canonical foreign-key counterparts."
    )
    example_bad = "SELECT *\nFROM person p\nJOIN concept c ON p.person_id = c.concept_id;"
    example_good = (
        "SELECT p.person_id, c.concept_name AS gender\n"
        "FROM person p\n"
        "JOIN concept c ON p.gender_concept_id = c.concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        # --- Fast pre-check ---
        if "join" not in sql.lower():
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

            for left, right, context in _extract_join_conditions(tree):
                left_table, left_col = resolve_table_col(left, aliases)
                right_table, right_col = resolve_table_col(right, aliases)

                if not left_col or not right_col:
                    continue

                l = _norm(left_col)
                r = _norm(right_col)

                key = f"{l}|{r}|{context}"
                if key in seen:
                    continue
                seen.add(key)

                # --- Case 1: Incompatible (HIGH severity) ---
                if _is_incompatible(l, r):
                    violations.append(
                        self.create_violation(
                            message=(
                                f"Incompatible JOIN keys: '{left_col}' = '{right_col}'. "
                                "These columns represent different entities and should not be joined."
                            ),
                            severity=Severity.ERROR,
                            suggested_fix=self.suggested_fix,
                            details={
                                "type": "incompatible_join",
                                "left_column": left_col,
                                "right_column": right_col,
                                "context": context,
                            },
                        )
                    )
                    continue

                # --- Case 2: Valid joins ---
                if _is_valid_join_pair(l, r):
                    continue

                # --- Case 3: Suspicious joins ---
                if _is_suspicious(l, r):
                    violations.append(
                        self.create_violation(
                            message=(
                                f"Suspicious JOIN condition: '{left_col}' = '{right_col}'. "
                                "Columns share '_id' suffix but represent different entities."
                            ),
                            severity=Severity.WARNING,
                            suggested_fix=self.suggested_fix,
                            details={
                                "type": "suspicious_join",
                                "left_column": left_col,
                                "right_column": right_col,
                                "context": context,
                            },
                        )
                    )

        return violations


__all__ = ["JoinKeyValidationRule"]

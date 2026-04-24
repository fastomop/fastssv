"""Concept Class ID Case Sensitivity Validation Rule.

OMOP semantic rule VOCAB_007:
concept_class_id values are case-sensitive strings with canonical casing.

The Problem:
    OMOP concept_class_id values have specific canonical casing:
    - 'Ingredient' (not 'ingredient', 'INGREDIENT')
    - 'Clinical Drug' (not 'clinical drug', 'CLINICAL DRUG')
    - 'Branded Drug' (not 'branded drug', 'BRANDED DRUG')
    - 'Clinical Finding' (not 'clinical finding', 'CLINICAL FINDING')
    - 'Procedure' (not 'procedure', 'PROCEDURE')
    - 'Lab Test' (not 'lab test', 'LAB TEST')

    String comparisons are case-sensitive, so using wrong casing
    will cause the query to return zero results.

Common mistakes:
    - 'ingredient' instead of 'Ingredient'
    - 'CLINICAL DRUG' instead of 'Clinical Drug'
    - 'branded drug' instead of 'Branded Drug'
    - 'procedure' instead of 'Procedure'

Violation pattern:
    SELECT * FROM concept
    WHERE concept_class_id = 'ingredient'  -- WRONG: should be 'Ingredient'

Correct pattern:
    SELECT * FROM concept
    WHERE concept_class_id = 'Ingredient'  -- Correct casing
"""

"""Concept Class ID Case Sensitivity Validation Rule.

OMOP semantic rule VOCAB_007:
concept_class_id values are case-sensitive strings with canonical casing.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register
from fastssv.schemas.concept_class_id_canonical import (
    get_canonical_concept_class,
)


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        value = str(node.this).strip()
        return value if value else None
    return None


def _is_concept_class_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    _, col_name = resolve_table_col(col, aliases)
    return _norm(col_name) == "concept_class_id"


# --- Extraction ------------------------------------------------------------

def _extract_concept_class_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str]]:
    """Extract concept_class_id filters from SQL."""
    filters: List[Tuple[str, str]] = []

    # --- EQ ---
    for node in tree.find_all(exp.EQ):
        pairs = [(node.this, node.expression), (node.expression, node.this)]

        for col_node, val_node in pairs:
            if isinstance(col_node, exp.Column) and _is_concept_class_column(col_node, aliases):
                value = _extract_string_literal(val_node)
                if value:
                    filters.append((value, node.sql()))

    # --- IN ---
    for node in tree.find_all(exp.In):
        if isinstance(node.this, exp.Column) and _is_concept_class_column(node.this, aliases):
            for expr in node.expressions or []:
                value = _extract_string_literal(expr)
                if value:
                    filters.append((value, node.sql()))

    # --- LIKE / ILIKE (skip wildcards) ---
    for node in tree.find_all(exp.Like, exp.ILike):
        if isinstance(node.this, exp.Column) and _is_concept_class_column(node.this, aliases):
            value = _extract_string_literal(node.expression)
            if value and "%" not in value:
                filters.append((value, node.sql()))

    return filters


# --- Validation ------------------------------------------------------------

def _check_concept_class(value: str) -> Optional[Dict[str, str]]:
    """Validate concept_class_id casing."""
    canonical = get_canonical_concept_class(value)

    # Unknown → do not flag (avoids false positives)
    if canonical is None:
        return None

    # Correct
    if value == canonical:
        return None

    return {
        "provided": value,
        "expected": canonical,
    }


# --- Rule ------------------------------------------------------------------

@register
class ConceptClassIdCaseSensitivityRule(Rule):
    """Validate concept_class_id casing."""

    rule_id = "data_quality.concept_class_id_case_sensitivity"
    name = "Concept Class ID Case Sensitivity"

    description = (
        "Ensures concept_class_id values use correct canonical casing. "
        "Incorrect casing may return zero results due to case-sensitive matching."
    )

    severity = Severity.ERROR

    suggested_fix = "Use canonical OMOP concept_class_id values."
    long_description = (
        "concept_class_id values in the OMOP vocabulary are case-sensitive "
        "and follow a canonical casing: 'Ingredient', 'Clinical Drug', "
        "'Branded Drug', 'Clinical Finding' — never 'ingredient' or "
        "'CLINICAL DRUG'. Most database engines compare strings case-"
        "sensitively by default, so a filter with the wrong casing quietly "
        "returns zero rows. Match the canonical form exactly."
    )
    example_bad = (
        "SELECT concept_id\n"
        "FROM concept\n"
        "WHERE concept_class_id = 'ingredient';"
    )
    example_good = (
        "SELECT concept_id\n"
        "FROM concept\n"
        "WHERE concept_class_id = 'Ingredient';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        # Fast pre-check
        if "concept_class_id" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            filters = _extract_concept_class_filters(tree, aliases)

            seen: Set[str] = set()

            for value, context in filters:
                error = _check_concept_class(value)
                if not error:
                    continue

                key = error["provided"]
                if key in seen:
                    continue
                seen.add(key)

                violations.append(
                    self.create_violation(
                        message=(
                            f"Incorrect concept_class_id casing: '{value}'. "
                            f"Expected '{error['expected']}'. "
                            f"Case-sensitive comparison may fail."
                        ),
                        severity=Severity.ERROR,
                        suggested_fix=(
                            f"Replace '{error['provided']}' with '{error['expected']}'"
                        ),
                        details={
                            "provided": error["provided"],
                            "expected": error["expected"],
                            "context": context,
                        },
                    )
                )

        return violations


__all__ = ["ConceptClassIdCaseSensitivityRule"]

"""Concept Class ID Ingredient for Drug Grouping Rule.

OMOP semantic rule VOCAB_039:
When grouping drug data by active ingredient, filter concept_class_id = 'Ingredient'
on the concept table. Using 'Clinical Drug', 'Branded Drug', or 'Clinical Drug Form'
groups at the product/formulation level, not the ingredient level.

The Problem:
    OMOP drug concepts exist in a specificity hierarchy:

    Ingredient (most general - active substance)
        ↓
    Clinical Drug Form (dose form + ingredient)
        ↓
    Clinical Drug (formulation without brand)
        ↓
    Branded Drug (brand name + formulation)
        ↓
    Marketed Product (most specific - commercial package)

    When analysts want to group by active ingredient (e.g., "all Metformin prescriptions"),
    but filter by concept_class_id = 'Clinical Drug' or 'Branded Drug', they're grouping
    at the wrong level of granularity.

    Issues with wrong concept_class_id:
    1. Clinical Drug: Groups by formulation (500mg tablet vs 850mg tablet counted separately)
    2. Branded Drug: Groups by brand (Glucophage vs Fortamet vs generic counted separately)
    3. Missing data: Fails to capture all forms of the active ingredient

    This leads to:
    - Undercounting drug exposure (split across formulations/brands)
    - Incorrect prevalence estimates
    - Misleading comparative effectiveness analyses
    - Fragmented ingredient-level statistics

Violation patterns:
    -- WRONG: Aliased as "ingredient" but filtering by Clinical Drug
    SELECT c.concept_name AS ingredient, COUNT(*) AS patient_count
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Clinical Drug'
    GROUP BY c.concept_name
    -- Groups by formulation, not ingredient!

    -- WRONG: Trying to find "active substances" but using Branded Drug
    SELECT c.concept_name AS active_substance,
           COUNT(DISTINCT de.person_id) AS patients
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Branded Drug'
    GROUP BY c.concept_name
    -- Glucophage and Fortamet counted separately!

    -- WRONG: Using IN clause with non-ingredient classes
    SELECT c.concept_name AS active_ingredient,
           COUNT(*) AS exposures
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id IN ('Clinical Drug', 'Branded Drug')
    GROUP BY c.concept_name

Correct patterns:
    -- CORRECT: Use concept_class_id = 'Ingredient'
    SELECT c.concept_name AS ingredient, COUNT(*) AS patient_count
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Ingredient'
    GROUP BY c.concept_name

    -- CORRECT: Use concept_ancestor to roll up to ingredient
    SELECT ing.concept_name AS ingredient,
           COUNT(DISTINCT de.person_id) AS patients
    FROM drug_exposure de
    JOIN concept_ancestor ca
      ON de.drug_concept_id = ca.descendant_concept_id
    JOIN concept ing
      ON ca.ancestor_concept_id = ing.concept_id
    WHERE ing.concept_class_id = 'Ingredient'
    GROUP BY ing.concept_name

    -- CORRECT: Explicit formulation-level grouping (clear intent)
    SELECT c.concept_name AS drug_formulation, COUNT(*)
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Clinical Drug'
    GROUP BY c.concept_name
    -- Clear alias shows formulation-level intent
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

INGREDIENT_ALIASES = {
    "ingredient",
    "active_ingredient",
    "active_substance",
    "drug_ingredient",
    "active_component",
    "substance",
}

NON_INGREDIENT_CLASSES = {
    "Clinical Drug",
    "Branded Drug",
    "Clinical Drug Form",
    "Branded Drug Form",
    "Clinical Drug Component",
    "Branded Drug Component",
    "Marketed Product",
    "Branded Pack",
    "Clinical Pack",
}

NON_INGREDIENT_CLASSES_NORM = {
    normalize_name(c) for c in NON_INGREDIENT_CLASSES
}


# --- Helpers ---------------------------------------------------------------

def _norm(val: Optional[str]) -> Optional[str]:
    return normalize_name(val) if val else None


def _has_drug_exposure_table(tree: exp.Expression) -> bool:
    return any(_norm(t.name) == "drug_exposure" for t in tree.find_all(exp.Table))


def _has_group_by(tree: exp.Expression) -> bool:
    return tree.find(exp.Group) is not None


def _extract_ingredient_aliases(tree: exp.Expression) -> Set[str]:
    """Robust alias detection using substring matching."""
    found: Set[str] = set()

    for select in tree.find_all(exp.Select):
        for proj in select.expressions:
            alias_expr = proj.args.get("alias")
            if not alias_expr:
                continue

            alias_name = _norm(alias_expr.name)
            if not alias_name:
                continue

            if any(k in alias_name for k in INGREDIENT_ALIASES):
                found.add(alias_expr.name)

    return found


def _has_non_ingredient_concept_class_filter(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Optional[str]:
    """Strict detection of concept.concept_class_id misuse."""

    nodes = (
        list(tree.find_all(exp.EQ)) +
        list(tree.find_all(exp.In))
    )

    for node in nodes:

        # --- EQ ---
        if isinstance(node, exp.EQ):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                table, col_name = resolve_table_col(col_node, aliases)

                # STRICT: must be concept.concept_class_id
                if _norm(table) != "concept":
                    continue
                if _norm(col_name) != "concept_class_id":
                    continue

                if isinstance(val_node, exp.Literal) and val_node.is_string:
                    val = _norm(str(val_node.this))
                    if val in NON_INGREDIENT_CLASSES_NORM:
                        return str(val_node.this)

        # --- IN ---
        elif isinstance(node, exp.In):
            col = node.this
            if not isinstance(col, exp.Column):
                continue

            table, col_name = resolve_table_col(col, aliases)

            if _norm(table) != "concept":
                continue
            if _norm(col_name) != "concept_class_id":
                continue

            for val in node.expressions or []:
                if isinstance(val, exp.Literal) and val.is_string:
                    norm_val = _norm(str(val.this))
                    if norm_val in NON_INGREDIENT_CLASSES_NORM:
                        return str(val.this)

    return None


# --- Rule ------------------------------------------------------------------

@register
class ConceptClassIdIngredientForDrugGroupingRule(Rule):
    """Detect incorrect concept_class_id when grouping drugs by ingredient."""

    rule_id = "concept_standardization.concept_class_id_ingredient_for_drug_grouping"
    name = "Concept Class ID Ingredient for Drug Grouping"

    description = (
        "Grouping drug data by ingredient requires concept_class_id = 'Ingredient'. "
        "Using product-level classes groups at formulation level instead."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use concept_class_id = 'Ingredient' or use concept_ancestor to roll up "
        "drug products to ingredients."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if "drug_exposure" not in sql_lower:
            return []
        if "concept_class_id" not in sql_lower:
            return []
        if "group by" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            if not _has_drug_exposure_table(tree):
                continue

            if not _has_group_by(tree):
                continue

            aliases = extract_aliases(tree)

            ingredient_aliases = _extract_ingredient_aliases(tree)
            if not ingredient_aliases:
                continue

            violating_class = _has_non_ingredient_concept_class_filter(tree, aliases)
            if not violating_class:
                continue

            key = f"{violating_class}|{','.join(sorted(ingredient_aliases))}"
            if key in seen:
                continue
            seen.add(key)

            violations.append(
                self.create_violation(
                    message=(
                        f"Query groups drug data using ingredient-like aliases "
                        f"({', '.join(sorted(ingredient_aliases))}) but filters "
                        f"concept_class_id = '{violating_class}'. This operates at "
                        f"product/formulation level, not ingredient level."
                    ),
                    severity=Severity.WARNING,
                    suggested_fix=(
                        "Use concept_class_id = 'Ingredient' or use concept_ancestor "
                        "to aggregate drugs to ingredient level."
                    ),
                    details={
                        "ingredient_aliases": list(ingredient_aliases),
                        "violating_concept_class": violating_class,
                    },
                )
            )

        return violations


__all__ = ["ConceptClassIdIngredientForDrugGroupingRule"]

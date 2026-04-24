"""Drug Strength Numerator/Denominator for Concentration Rule.

OMOP semantic rule GAP_016:
drug_strength stores concentration-based drugs using numerator_value/numerator_unit_concept_id
and denominator_value/denominator_unit_concept_id (e.g., 500mg/5mL). Queries that only check
amount_value miss these liquid/injectable formulations entirely.

The Problem:
    The drug_strength table has two different models for representing strength:

    1. Simple amount model (solid formulations):
       - amount_value + amount_unit_concept_id
       - Example: 500mg tablet → amount_value = 500, amount_unit_concept_id = mg

    2. Concentration model (liquid/injectable formulations):
       - numerator_value + numerator_unit_concept_id (active ingredient)
       - denominator_value + denominator_unit_concept_id (solution volume)
       - Example: 500mg/5mL injection → numerator_value = 500, denominator_value = 5
       - **amount_value is NULL** for these drugs

    Critical issue: Queries that only check amount_value completely miss
    liquid/injectable formulations, which use the numerator/denominator model.

Common mistakes:
    1. SELECT amount_value without numerator_value
    2. WHERE amount_value > X (excludes all concentration drugs)
    3. WHERE amount_value IS NOT NULL (excludes all concentration drugs)
    4. Calculations using only amount_value

Violation pattern:
    SELECT ds.amount_value, ds.amount_unit_concept_id
    FROM drug_strength ds
    WHERE ds.drug_concept_id = 19078461
    -- WRONG: Returns NULL for liquid/injectable formulations!

Correct pattern:
    SELECT
      COALESCE(ds.amount_value, ds.numerator_value) AS dose_value,
      COALESCE(ds.amount_unit_concept_id, ds.numerator_unit_concept_id) AS dose_unit,
      ds.denominator_value,
      ds.denominator_unit_concept_id
    FROM drug_strength ds
    WHERE ds.drug_concept_id = 19078461
    -- Correct: Handles both solid AND liquid formulations
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
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

DRUG_STRENGTH = "drug_strength"

AMOUNT_VALUE = "amount_value"
NUMERATOR_VALUE = "numerator_value"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    return _norm(name.split(".")[-1]) if name else None


def _is_drug_strength(table: Optional[str]) -> bool:
    return _normalize_table(table) == DRUG_STRENGTH


def _is_target_column(
    col: exp.Column,
    aliases: Dict[str, str],
    target: str,
    has_ds: bool,
) -> bool:
    table, column = resolve_table_col(col, aliases)

    if _norm(column) != _norm(target):
        return False

    if table:
        return _is_drug_strength(table)

    # Unqualified fallback only if drug_strength is present
    return has_ds


def _collect_columns(
    node: exp.Expression,
    aliases: Dict[str, str],
    has_ds: bool,
) -> Set[str]:
    """Collect drug_strength-related column names used in node."""
    cols: Set[str] = set()

    for col in node.find_all(exp.Column):
        table, column = resolve_table_col(col, aliases)

        if not column:
            continue

        if table and _is_drug_strength(table):
            cols.add(_norm(column))
        elif not table and has_ds:
            cols.add(_norm(column))

    return cols


def _has_coalesce_amount_numerator(
    node: exp.Expression,
    aliases: Dict[str, str],
    has_ds: bool,
) -> bool:
    """Detect COALESCE(amount_value, numerator_value) even if nested."""
    for coalesce in node.find_all(exp.Coalesce):
        cols = _collect_columns(coalesce, aliases, has_ds)

        if AMOUNT_VALUE in cols and NUMERATOR_VALUE in cols:
            return True

    return False


def _has_numerator_usage(
    node: exp.Expression,
    aliases: Dict[str, str],
    has_ds: bool,
) -> bool:
    """Check if numerator_value is used anywhere meaningfully."""
    if _has_coalesce_amount_numerator(node, aliases, has_ds):
        return True

    for col in node.find_all(exp.Column):
        if _is_target_column(col, aliases, NUMERATOR_VALUE, has_ds):
            return True

    return False


def _has_amount_usage(
    node: exp.Expression,
    aliases: Dict[str, str],
    has_ds: bool,
) -> bool:
    for col in node.find_all(exp.Column):
        if _is_target_column(col, aliases, AMOUNT_VALUE, has_ds):
            return True
    return False


def _is_in_aggregate(node: exp.Expression) -> bool:
    """Check if node is inside an aggregation function."""
    parent = node.parent
    while parent:
        if isinstance(parent, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
            return True
        if isinstance(parent, exp.Select):
            return False
        parent = parent.parent
    return False


# --- Detection -------------------------------------------------------------

def _detect_select_violation(tree, aliases, has_ds) -> bool:
    """Detect SELECT using amount_value without numerator_value."""
    if not _has_amount_usage(tree, aliases, has_ds):
        return False

    if _has_numerator_usage(tree, aliases, has_ds):
        return False

    for select in tree.find_all(exp.Select):
        for expr in select.expressions:
            for col in expr.find_all(exp.Column):
                if _is_in_aggregate(col):
                    continue

                if _is_target_column(col, aliases, AMOUNT_VALUE, has_ds):
                    return True

    return False


def _detect_where_violation(tree, aliases, has_ds) -> bool:
    """Detect WHERE filtering on amount_value without numerator alternative."""
    comparison_types = (
        exp.GT, exp.GTE, exp.LT, exp.LTE,
        exp.EQ, exp.NEQ, exp.Is, exp.In, exp.Between
    )

    has_amount = False
    has_numerator = False

    for where in tree.find_all(exp.Where):
        if _has_coalesce_amount_numerator(where, aliases, has_ds):
            return False  # safe usage

        for node in where.find_all(*comparison_types):
            for col in node.find_all(exp.Column):
                if _is_target_column(col, aliases, AMOUNT_VALUE, has_ds):
                    has_amount = True
                if _is_target_column(col, aliases, NUMERATOR_VALUE, has_ds):
                    has_numerator = True

    return has_amount and not has_numerator


# --- Rule ------------------------------------------------------------------

@register
class DrugStrengthNumeratorDenominatorForConcentrationRule(Rule):
    """Ensure both amount-based and concentration-based drugs are handled."""

    rule_id = "domain_specific.drug_strength_numerator_denominator_for_concentration"
    name = "Drug Strength Completeness (Amount vs Concentration)"

    description = (
        "drug_strength stores solid formulations in amount_value and liquid/injectable "
        "formulations in numerator_value/denominator_value. Queries using only amount_value "
        "exclude concentration-based drugs."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use COALESCE(amount_value, numerator_value) to include both formulations. "
        "Include denominator_value for concentration context when relevant."
    )

    example_bad = (
        "SELECT drug_concept_id, amount_value FROM drug_strength\n"
        "WHERE amount_value > 500;"
    )
    example_good = (
        "SELECT drug_concept_id,\n"
        "       COALESCE(amount_value, numerator_value / denominator_value) AS dose\n"
        "FROM drug_strength;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "drug_strength" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, DRUG_STRENGTH):
                continue

            aliases = extract_aliases(tree)
            has_ds = True  # already confirmed

            if _detect_select_violation(tree, aliases, has_ds):
                key = "select"
                if key not in seen:
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                "amount_value selected without numerator_value. "
                                "This excludes concentration-based drugs (liquids/injectables)."
                            ),
                            severity=self.severity,
                            suggested_fix=self.suggested_fix,
                            details={"type": "select"},
                        )
                    )

            if _detect_where_violation(tree, aliases, has_ds):
                key = "where"
                if key not in seen:
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                "amount_value filtered without numerator_value alternative. "
                                "This excludes concentration-based drugs (liquids/injectables)."
                            ),
                            severity=self.severity,
                            suggested_fix=self.suggested_fix,
                            details={"type": "where"},
                        )
                    )

        return violations


__all__ = ["DrugStrengthNumeratorDenominatorForConcentrationRule"]

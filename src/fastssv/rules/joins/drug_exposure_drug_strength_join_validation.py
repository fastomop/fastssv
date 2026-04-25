"""Drug Exposure to Drug Strength Join Validation Rule.

OMOP semantic rule JOIN_018:
drug_exposure joins to drug_strength via drug_exposure.drug_concept_id =
drug_strength.drug_concept_id. Joining on any other columns is incorrect.

The Problem:
    The drug_strength table is a vocabulary table that contains strength information
    for drug formulations. It has NO clinical event columns like drug_exposure_id,
    person_id, or visit_occurrence_id.

    The ONLY valid join is:
    drug_exposure.drug_concept_id = drug_strength.drug_concept_id

    Common mistakes:
    1. Joining drug_exposure_id to drug_concept_id
       - drug_strength has no drug_exposure_id column
    2. Joining person_id to drug_concept_id
       - drug_strength has no person_id column
    3. Joining route_concept_id or other *_concept_id columns
       - Wrong semantic - must use drug_concept_id

Violation pattern:
    SELECT *
    FROM drug_exposure de
    JOIN drug_strength ds ON de.drug_exposure_id = ds.drug_concept_id
    -- WRONG: drug_strength has no drug_exposure_id!

Correct pattern:
    SELECT
      de.drug_exposure_id,
      ds.amount_value,
      ds.amount_unit_concept_id
    FROM drug_exposure de
    JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id
    WHERE ds.invalid_reason IS NULL
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.patch import build_join_replace_patch
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

DRUG_EXPOSURE = "drug_exposure"
DRUG_STRENGTH = "drug_strength"
DRUG_CONCEPT_ID = "drug_concept_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_de(table: Optional[str]) -> bool:
    return _normalize_table(table) == DRUG_EXPOSURE


def _is_ds(table: Optional[str]) -> bool:
    return _normalize_table(table) == DRUG_STRENGTH


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_eq_conditions(tree: exp.Expression) -> List[exp.EQ]:
    """Extract column-to-column equality conditions."""
    eqs = []

    for eq in tree.find_all(exp.EQ):
        if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
            eqs.append(eq)

    return eqs


# --- Detection -------------------------------------------------------------

def _detect(
    tree: exp.Expression,
    aliases: Dict[str, str],
):
    errors = []

    seen_e: Set[Tuple[str, str, str, str]] = set()

    found_any_de_ds_relation = False
    found_valid_fk = False

    for eq in _extract_eq_conditions(tree):
        left, right = eq.this, eq.expression

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt_norm = _normalize_table(lt)
        rt_norm = _normalize_table(rt)

        if not ((_is_de(lt_norm) and _is_ds(rt_norm)) or
                (_is_de(rt_norm) and _is_ds(lt_norm))):
            continue

        found_any_de_ds_relation = True

        # normalize direction
        if _is_de(lt_norm):
            de_col, ds_col = lc, rc
        else:
            de_col, ds_col = rc, lc

        de_col_norm = _norm(de_col)
        ds_col_norm = _norm(ds_col)

        # --- correct FK ---
        if de_col_norm == DRUG_CONCEPT_ID and ds_col_norm == DRUG_CONCEPT_ID:
            found_valid_fk = True
            continue

        # --- incorrect join (error) ---
        key = (DRUG_EXPOSURE, de_col_norm, DRUG_STRENGTH, ds_col_norm)
        if key not in seen_e:
            errors.append(key)
            seen_e.add(key)

    # --- missing FK join ---
    if has_table_reference(tree, DRUG_EXPOSURE) and has_table_reference(tree, DRUG_STRENGTH):
        if found_any_de_ds_relation and not found_valid_fk and not errors:
            key = (DRUG_EXPOSURE, "UNKNOWN", DRUG_STRENGTH, "UNKNOWN")
            if key not in seen_e:
                errors.append(key)
                seen_e.add(key)

        elif not found_any_de_ds_relation:
            key = (DRUG_EXPOSURE, "NONE", DRUG_STRENGTH, "NONE")
            if key not in seen_e:
                errors.append(key)
                seen_e.add(key)

    return errors


# --- Rule ------------------------------------------------------------------

@register
class DrugExposureDrugStrengthJoinValidationRule(Rule):
    """Validate drug_exposure ↔ drug_strength joins."""

    rule_id = "joins.drug_exposure_drug_strength_join_validation"
    name = "Drug Exposure to Drug Strength Join Validation"

    description = (
        "Ensures drug_exposure joins to drug_strength using drug_concept_id. "
        "Flags missing or non-standard joins."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: the join target WITH `drug_exposure.drug_concept_id = drug_strength.drug_concept_id`, AND add `AND drug_strength.invalid_reason IS NULL`."
    example_bad = (
        "SELECT * FROM drug_exposure de\n"
        "JOIN drug_strength ds ON de.drug_source_concept_id = ds.drug_concept_id;"
    )
    example_good = (
        "SELECT * FROM drug_exposure de\n"
        "JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if "drug_exposure" not in sql_lower or "drug_strength" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            if not (has_table_reference(tree, DRUG_EXPOSURE) and has_table_reference(tree, DRUG_STRENGTH)):
                continue

            aliases = extract_aliases(tree)
            errors = _detect(tree, aliases)

            # --- ERRORS ---
            for de, de_col, ds, ds_col in errors:
                patch = None
                if de_col == "NONE":
                    msg = (
                        "drug_exposure and drug_strength are used but not joined. "
                        "Missing join condition."
                    )
                elif de_col == "UNKNOWN":
                    msg = (
                        "Invalid join between drug_exposure and drug_strength. "
                        "Expected drug_concept_id = drug_concept_id."
                    )
                else:
                    msg = (
                        f"Invalid join: {de}.{de_col} = {ds}.{ds_col}. "
                        f"Expected drug_concept_id = drug_concept_id."
                    )
                    fix_text = (
                        f"REPLACE: `{de}.{de_col} = {ds}.{ds_col}` "
                        f"WITH `{de}.drug_concept_id = {ds}.drug_concept_id`."
                    )
                    patch = build_join_replace_patch(
                        sql, de, de_col, ds, ds_col,
                        DRUG_CONCEPT_ID, DRUG_CONCEPT_ID,
                        fix_text,
                        aliases=aliases,
                    )

                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=patch,
                        details={
                            "type": "invalid_join",
                            "drug_exposure_column": de_col,
                            "drug_strength_column": ds_col,
                        },
                    )
                )

        return violations


__all__ = ["DrugExposureDrugStrengthJoinValidationRule"]

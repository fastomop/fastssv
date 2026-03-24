"""Clinical Primary Key Cross-Join Validation Rule.

OMOP semantic rule JOIN_028:
Clinical event table primary keys (condition_occurrence_id, drug_exposure_id,
procedure_occurrence_id, measurement_id, observation_id, etc.) are table-specific
and must never be joined to each other. They are independent ID sequences.

The Problem:
    Each clinical event table has its own independent primary key sequence:
    - condition_occurrence_id = 123 → A specific condition event
    - drug_exposure_id = 123 → A specific drug exposure event
    - procedure_occurrence_id = 123 → A specific procedure event

    These are completely unrelated - they just happen to have overlapping numeric
    values. Joining them is always semantically meaningless.

Violation pattern:
    SELECT * FROM condition_occurrence co
    JOIN drug_exposure de ON co.condition_occurrence_id = de.drug_exposure_id
    -- WRONG: These are different event types with independent ID sequences!

Correct pattern:
    SELECT * FROM condition_occurrence co
    JOIN drug_exposure de ON co.person_id = de.person_id
      AND co.visit_occurrence_id = de.visit_occurrence_id
    -- CORRECT: Join via shared foreign keys
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


# --- Constants -------------------------------------------------------------

CLINICAL_EVENT_PKS = {
    "condition_occurrence_id",
    "drug_exposure_id",
    "procedure_occurrence_id",
    "measurement_id",
    "observation_id",
    "device_exposure_id",
    "specimen_id",
    "note_id",
    "visit_detail_id",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_clinical_pk(col: Optional[str]) -> bool:
    return _norm(col) in CLINICAL_EVENT_PKS


def _extract_eq_conditions(tree: exp.Expression) -> List[exp.EQ]:
    """Extract column-to-column equality conditions."""
    eqs: List[exp.EQ] = []

    has_join_on = False

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause:
            has_join_on = True
            for eq in on_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    eqs.append(eq)

    if not has_join_on:
        where_clause = tree.find(exp.Where)
        if where_clause:
            for eq in where_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    eqs.append(eq)

    return eqs


# --- Detection -------------------------------------------------------------

def _detect(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """
    Detect:
    1. PK ↔ PK mismatches (different clinical event PKs)
    2. PK ↔ non-PK joins (invalid usage of PKs)

    Returns:
        List of (left_table, left_column, right_table, right_column)
    """
    violations: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    for eq in _extract_eq_conditions(tree):
        left, right = eq.this, eq.expression

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        lc_norm = _norm(lc)
        rc_norm = _norm(rc)

        # --- Defensive guards ---------------------------------------------

        if not lc_norm or not rc_norm:
            continue

        # Ignore same-table comparisons
        if lt and rt and _norm(lt) == _norm(rt):
            continue

        left_is_pk = _is_clinical_pk(lc_norm)
        right_is_pk = _is_clinical_pk(rc_norm)

        # --- Case 1: PK ↔ PK mismatch -------------------------------------

        if left_is_pk and right_is_pk and lc_norm != rc_norm:
            key = (lt or "unknown", lc_norm, rt or "unknown", rc_norm)

        # --- Case 2: PK ↔ non-PK (strong validation) -----------------------

        elif left_is_pk != right_is_pk:
            key = (lt or "unknown", lc_norm, rt or "unknown", rc_norm)

        else:
            continue

        if key not in seen:
            violations.append(key)
            seen.add(key)

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ClinicalPkCrossJoinValidationRule(Rule):
    """
    Validate that clinical event primary keys are not misused in joins.

    Rules enforced:
    - Clinical PKs cannot join to other clinical PKs (unless identical column)
    - Clinical PKs cannot join to non-PK columns
    """

    rule_id = "joins.clinical_pk_cross_join_validation"
    name = "Clinical Primary Key Join Validation"

    description = (
        "Ensures clinical event primary keys are not incorrectly used in joins. "
        "Each clinical event table has an independent primary key sequence with no "
        "semantic relationship to other tables."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Do not join clinical event primary keys. "
        "Use shared foreign keys such as person_id or visit_occurrence_id instead."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()

        if not any(pk in sql_lower for pk in CLINICAL_EVENT_PKS):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            detected = _detect(tree, aliases)

            for lt, lc, rt, rc in detected:

                lt_disp = lt if lt and lt != "unknown" else ""
                rt_disp = rt if rt and rt != "unknown" else ""

                left_side = f"{lt_disp}.{lc}" if lt_disp else lc
                right_side = f"{rt_disp}.{rc}" if rt_disp else rc

                if _is_clinical_pk(lc) and _is_clinical_pk(rc):
                    msg = (
                        f"Invalid join: {left_side} → {right_side}. "
                        f"Clinical event primary keys are independent and cannot be joined."
                    )
                else:
                    msg = (
                        f"Invalid join: {left_side} → {right_side}. "
                        f"Clinical event primary keys must not be used to join unrelated columns."
                    )

                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "clinical_pk_misuse",
                            "left_table": lt,
                            "left_column": lc,
                            "right_table": rt,
                            "right_column": rc,
                        },
                    )
                )

        return violations


__all__ = ["ClinicalPkCrossJoinValidationRule"]
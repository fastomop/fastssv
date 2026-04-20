"""Cohort to Clinical Table Join Validation Rule.

OMOP semantic rule JOIN_022:
cohort joins to clinical tables via cohort.subject_id = clinical_table.person_id.
Joining on any other columns is structurally incorrect.

The Problem:
    The cohort table is a RESULTS table with unique naming:
    - Uses subject_id (not person_id) to identify patients
    - This is the ONLY table in OMOP CDM that uses subject_id
    - All clinical tables use person_id for patient identity

    The ONLY valid join is:
    cohort.subject_id = clinical_table.person_id

    Common mistakes:
    1. Joining subject_id to primary keys (condition_occurrence_id, etc.)
       - Structurally invalid (patient ID ≠ event ID)
    2. Using person_id from cohort table
       - cohort has no person_id column, only subject_id
    3. Joining cohort_definition_id to person_id
       - cohort_definition_id is not patient identity

Violation pattern:
    SELECT *
    FROM cohort c
    JOIN condition_occurrence co ON c.subject_id = co.condition_occurrence_id
    -- WRONG: Joining patient ID to event ID!

Correct pattern:
    SELECT
      c.subject_id,
      co.condition_occurrence_id,
      co.condition_concept_id
    FROM cohort c
    JOIN condition_occurrence co ON c.subject_id = co.person_id
    WHERE c.cohort_definition_id = 123
      AND co.condition_start_date >= c.cohort_start_date
      AND co.condition_start_date <= c.cohort_end_date
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
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

COHORT = "cohort"
PERSON = "person"

SUBJECT_ID = "subject_id"
PERSON_ID = "person_id"

CLINICAL_TABLES = {
    "observation_period",
    "visit_occurrence",
    "visit_detail",
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "device_exposure",
    "measurement",
    "observation",
    "specimen",
    "death",
    "note",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_cohort(table: Optional[str]) -> bool:
    return table == COHORT


def _is_person(table: Optional[str]) -> bool:
    return table == PERSON


def _is_clinical(table: Optional[str]) -> bool:
    return table in CLINICAL_TABLES


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


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
) -> Dict[str, List[Tuple[str, str, str, str]]]:

    errors_by_table: Dict[str, List[Tuple[str, str, str, str]]] = {}

    clinical_tables: Set[str] = set()
    person_aliases: Set[str] = set()

    # --- Discover tables ---------------------------------------------------
    for table in tree.find_all(exp.Table):
        t = _normalize_table(table.name)

        if _is_clinical(t):
            clinical_tables.add(t)

        if _is_person(t):
            person_aliases.add(t)

    if not clinical_tables or not has_table_reference(tree, COHORT):
        return errors_by_table

    # --- Status tracking ---------------------------------------------------
    status = {
        t: {
            "direct_valid": False,
            "via_person_valid": False,
            "any_relation": False,
            "errors": [],
            "seen": set(),
        }
        for t in clinical_tables
    }

    cohort_to_person = False
    person_to_clinical: Dict[str, bool] = {t: False for t in clinical_tables}

    # --- Analyze joins -----------------------------------------------------
    for eq in _extract_eq_conditions(tree):
        left, right = eq.this, eq.expression

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt = _normalize_table(lt)
        rt = _normalize_table(rt)
        lc = _norm(lc)
        rc = _norm(rc)

        # --- cohort ↔ clinical (direct) ------------------------------------
        if _is_cohort(lt) and _is_clinical(rt):
            s = status[rt]
            s["any_relation"] = True

            if lc == SUBJECT_ID and rc == PERSON_ID:
                s["direct_valid"] = True
            else:
                key = (COHORT, lc, rt, rc)
                if key not in s["seen"]:
                    s["errors"].append(key)
                    s["seen"].add(key)

        elif _is_cohort(rt) and _is_clinical(lt):
            s = status[lt]
            s["any_relation"] = True

            if rc == SUBJECT_ID and lc == PERSON_ID:
                s["direct_valid"] = True
            else:
                key = (COHORT, rc, lt, lc)
                if key not in s["seen"]:
                    s["errors"].append(key)
                    s["seen"].add(key)

        # --- cohort ↔ person -----------------------------------------------
        if (_is_cohort(lt) and _is_person(rt)) or (_is_cohort(rt) and _is_person(lt)):
            if (lc == SUBJECT_ID and rc == PERSON_ID) or (
                rc == SUBJECT_ID and lc == PERSON_ID
            ):
                cohort_to_person = True

        # --- person ↔ clinical ---------------------------------------------
        if _is_person(lt) and _is_clinical(rt):
            if lc == PERSON_ID and rc == PERSON_ID:
                person_to_clinical[rt] = True

        elif _is_person(rt) and _is_clinical(lt):
            if rc == PERSON_ID and lc == PERSON_ID:
                person_to_clinical[lt] = True

    # --- Final evaluation --------------------------------------------------
    for table, s in status.items():

        # valid via PERSON bridge
        if cohort_to_person and person_to_clinical.get(table):
            s["via_person_valid"] = True

        is_valid = s["direct_valid"] or s["via_person_valid"]

        if is_valid:
            continue

        # Only add generic error if no specific errors were found
        if not s["errors"]:
            if s["any_relation"]:
                key = (COHORT, "INVALID", table, "INVALID")
            else:
                key = (COHORT, "NONE", table, "NONE")

            if key not in s["seen"]:
                s["errors"].append(key)

        if s["errors"]:
            errors_by_table[table] = s["errors"]

    return errors_by_table


# --- Rule ------------------------------------------------------------------

@register
class CohortClinicalJoinValidationRule(Rule):
    """
    Validate that cohort joins to clinical tables using:
        cohort.subject_id = clinical.person_id

    Also allows:
        cohort → person → clinical
    """

    rule_id = "joins.cohort_clinical_join_validation"
    name = "Cohort to Clinical Table Join Validation"

    description = (
        "Ensures cohort joins to clinical tables using subject_id = person_id, "
        "either directly or via person table."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use: cohort.subject_id = clinical.person_id "
        "or cohort.subject_id = person.person_id and person.person_id = clinical.person_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        if "cohort" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree or not has_table_reference(tree, COHORT):
                continue

            aliases = extract_aliases(tree)
            errors_by_table = _detect(tree, aliases)

            for clinical_table, errors in errors_by_table.items():
                for cohort_tbl, cohort_col, clin_tbl, clin_col in errors:

                    if cohort_col == "NONE":
                        msg = (
                            f"cohort and {clinical_table} are used but not joined. "
                            f"Missing join condition."
                        )
                    elif cohort_col == "INVALID":
                        msg = (
                            f"Invalid join between cohort and {clinical_table}. "
                            f"Expected subject_id = person_id (directly or via person)."
                        )
                    else:
                        msg = (
                            f"Invalid FK join between cohort and {clinical_table}: "
                            f"{cohort_tbl}.{cohort_col} = {clin_tbl}.{clin_col}. "
                            f"Expected subject_id = person_id."
                        )

                    violations.append(
                        self.create_violation(
                            message=msg,
                            suggested_fix=self.suggested_fix,
                            details={
                                "type": "invalid_fk_join",
                                "cohort_column": cohort_col,
                                "clinical_table": clinical_table,
                                "clinical_column": clin_col,
                            },
                        )
                    )

        return violations


__all__ = ["CohortClinicalJoinValidationRule"]
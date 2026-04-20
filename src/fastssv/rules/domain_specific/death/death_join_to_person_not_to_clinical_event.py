"""Death Join to Person Not to Clinical Event Rule.

OMOP semantic rule OMOP_134:
The death table joins to person on person_id. It does not have visit_occurrence_id
or condition_occurrence_id. To associate death with a clinical event, join both tables
to person independently.

The Problem:
    The death table has a minimal schema:
    - person_id (FK to person)
    - death_date
    - death_datetime
    - death_type_concept_id
    - death_cause_concept_id
    - death_cause_source_value
    - death_cause_source_concept_id

    It does NOT have foreign keys to clinical event tables like:
    - visit_occurrence_id
    - condition_occurrence_id
    - drug_exposure_id
    - procedure_occurrence_id

    The only valid join from death to other tables is via person_id.

Why this is wrong:
    Developers sometimes mistakenly try to join death directly to clinical event
    tables using incorrect column mappings, such as:
    - death.person_id = visit_occurrence.visit_occurrence_id (wrong types)
    - death.death_type_concept_id = condition_occurrence.condition_concept_id (semantically wrong)

    This produces incorrect results or errors.

Violation patterns:
    SELECT * FROM death d
    JOIN visit_occurrence vo ON d.person_id = vo.visit_occurrence_id
    -- ERROR: Joining person_id (INTEGER) to visit_occurrence_id (INTEGER) - wrong semantics

    SELECT * FROM death d
    JOIN condition_occurrence co ON d.death_cause_concept_id = co.condition_occurrence_id
    -- ERROR: Wrong column mapping

    SELECT * FROM death d
    JOIN drug_exposure de ON d.death_type_concept_id = de.drug_concept_id
    -- ERROR: Semantically incorrect join

Correct patterns:
    SELECT * FROM death d
    JOIN visit_occurrence vo ON d.person_id = vo.person_id
    -- OK: Both join on person_id

    SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
    JOIN condition_occurrence co ON p.person_id = co.person_id
    -- OK: Associate death with conditions through person

    SELECT d.*, co.*
    FROM death d
    JOIN condition_occurrence co ON d.person_id = co.person_id
    AND co.condition_start_date <= d.death_date
    -- OK: Join on person_id, filter by date relationship

Note:
    This is an ERROR, not a WARNING. The death table schema does not support
    direct joins to clinical event tables except via person_id.
"""

import logging
from typing import List, Set

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


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

DEATH_TABLE = "death"
PERSON_ID_COL = "person_id"

CLINICAL_EVENT_TABLES: Set[str] = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "visit_occurrence",
    "visit_detail",
    "device_exposure",
    "specimen",
    "note",
}


# --- Helpers -----------------------------------------------------------------

def _normalize_table_col(table: str, col: str):
    """Normalize table and column names safely."""
    if not table or not col:
        return None, None
    return normalize_name(table), normalize_name(col)


def _is_death_to_clinical_join(t1: str, t2: str) -> bool:
    """Check if join is between death and a clinical event table."""
    return (
        (t1 == DEATH_TABLE and t2 in CLINICAL_EVENT_TABLES)
        or (t2 == DEATH_TABLE and t1 in CLINICAL_EVENT_TABLES)
    )


def _is_valid_person_join(t1: str, c1: str, t2: str, c2: str) -> bool:
    """Check if join includes death.person_id = clinical.person_id."""
    return (
        (t1 == DEATH_TABLE and c1 == PERSON_ID_COL and t2 in CLINICAL_EVENT_TABLES and c2 == PERSON_ID_COL)
        or (t2 == DEATH_TABLE and c2 == PERSON_ID_COL and t1 in CLINICAL_EVENT_TABLES and c1 == PERSON_ID_COL)
    )


def _find_violations(tree: exp.Expression) -> List[str]:
    """Find joins between death and clinical tables missing person_id condition."""
    issues: List[str] = []

    if not has_table_reference(tree, DEATH_TABLE):
        return []

    aliases = extract_aliases(tree)

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")

        is_relevant_join = False
        has_valid_person_join = False

        # --- ON clause ---
        if on_clause:
            for eq in on_clause.find_all(exp.EQ):
                left = eq.this
                right = eq.expression

                if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                    continue

                left_table, left_col = resolve_table_col(left, aliases)
                right_table, right_col = resolve_table_col(right, aliases)

                left_table, left_col = _normalize_table_col(left_table, left_col)
                right_table, right_col = _normalize_table_col(right_table, right_col)

                if not left_table or not right_table:
                    continue

                if _is_death_to_clinical_join(left_table, right_table):
                    is_relevant_join = True

                    if _is_valid_person_join(left_table, left_col, right_table, right_col):
                        has_valid_person_join = True

        if is_relevant_join and not has_valid_person_join:
            issues.append(
                "Invalid JOIN between death and clinical event table. "
                "Join must include death.person_id = <clinical_table>.person_id."
            )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class DeathJoinToPersonNotToClinicalEventRule(Rule):
    """
    OMOP_134: Ensure death table joins to clinical tables via person_id only.
    """

    rule_id = "domain_specific.death_join_to_person_not_to_clinical_event"
    name = "Death Join to Person Not to Clinical Event"

    description = (
        "The death table joins to person on person_id and has no foreign keys to clinical event tables. "
        "Joins between death and clinical tables must include person_id."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Ensure JOIN includes: death.person_id = <clinical_table>.person_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter
        if DEATH_TABLE not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_134",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            issues = _find_violations(tree)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["DeathJoinToPersonNotToClinicalEventRule"]
"""CDM Source Clinical Join Rule.

OMOP semantic rule OMOP_113:
cdm_source has no primary key and typically contains a single row describing
the CDM instance. Joining clinical tables to cdm_source is semantically incorrect
and produces meaningless cartesian products.

The Problem:
    The cdm_source table is a metadata table with no primary key containing
    a single row that describes the CDM instance (version, vocabulary version,
    source name, etc.). It has no relationship to clinical data.

    Joining cdm_source to clinical tables (person, condition_occurrence, etc.)
    creates a cartesian product that appends the same metadata row to every
    clinical record, which is analytically meaningless.

Violation patterns:
    SELECT * FROM condition_occurrence JOIN cdm_source ON 1=1
    -- ERROR: Cartesian product with metadata table

    SELECT * FROM drug_exposure CROSS JOIN cdm_source
    -- ERROR: Appends same metadata to every drug exposure

Correct patterns:
    SELECT cdm_source_name, vocabulary_version FROM cdm_source
    -- OK: Query metadata independently

    SELECT cdm_source_name, vocabulary_version FROM cdm_source
    WHERE cdm_version = '5.4'
    -- OK: Filter metadata table

    SELECT * FROM cdm_source cs
    JOIN concept c ON cs.cdm_version_concept_id = c.concept_id
    -- OK: Join to vocabulary table via valid FK
"""

import logging
from typing import List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    normalize_name,
    parse_sql,
    has_table_reference,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

TABLE_NAME = "cdm_source"

CLINICAL_TABLES: Set[str] = {
    "person",
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
    "note_nlp",
    "payer_plan_period",
    "cost",
    "condition_era",
    "drug_era",
    "dose_era",
    "episode",
    "episode_event",
}


# --- Helpers -----------------------------------------------------------------

def _find_invalid_joins(tree: exp.Expression) -> List[str]:
    """Find JOINs between cdm_source and clinical tables."""
    issues: List[str] = []

    for select in tree.find_all(exp.Select):
        # Collect all tables in this SELECT (avoiding nested subqueries)
        tables_in_select: Set[str] = set()

        for table_node in select.find_all(exp.Table):
            # Check if this table belongs to a nested SELECT
            parent = table_node.parent
            crossed_boundary = False
            while parent and parent is not select:
                if isinstance(parent, exp.Select):
                    crossed_boundary = True
                    break
                parent = parent.parent

            if not crossed_boundary:
                tables_in_select.add(normalize_name(table_node.name))

        # Check if cdm_source and clinical tables appear together
        if TABLE_NAME in tables_in_select:
            clinical_found = [t for t in tables_in_select if t in CLINICAL_TABLES]
            for clinical_table in clinical_found:
                issues.append(
                    f"cdm_source is joined to clinical table '{clinical_table}'. "
                    f"cdm_source is a single-row metadata table with no primary key "
                    f"and should not be joined to patient-level clinical data. "
                    f"Query cdm_source independently for metadata."
                )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class CdmSourceClinicalJoinRule(Rule):
    """
    OMOP_113: Prevent joins between cdm_source and clinical tables.
    """

    rule_id = "anti_patterns.cdm_source_clinical_join"
    name = "CDM Source Clinical Join"

    description = (
        "cdm_source is a single-row metadata table with no primary key. "
        "Joining it to clinical tables creates meaningless cartesian products."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Remove the JOIN to cdm_source. Query it separately or use a scalar subquery, "
        "e.g. (SELECT cdm_version FROM cdm_source)."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        if TABLE_NAME not in sql.lower():
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_113",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree or not has_table_reference(tree, TABLE_NAME):
                continue

            issues = _find_invalid_joins(tree)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["CdmSourceClinicalJoinRule"]

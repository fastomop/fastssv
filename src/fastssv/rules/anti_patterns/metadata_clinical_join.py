"""Metadata Clinical Join Rule.

OMOP semantic rule OMOP_121:
The metadata table stores CDM-level metadata (ETL provenance, data characterization
results), not patient-level clinical data. It has no person_id column and should
never be joined to clinical tables for patient analysis.

The Problem:
    The metadata table is a metadata table with no primary key and no foreign key
    relationships to clinical data. It stores CDM instance metadata such as:
    - ETL provenance information
    - Data characterization results
    - CDM instance-level metrics

    It has no relationship to patient-level clinical data.

    Joining metadata to clinical tables (person, condition_occurrence, etc.)
    is semantically incorrect and indicates confusion about the table's purpose.

Why this is wrong:
    - metadata has no person_id or any FK to clinical tables
    - metadata_id does NOT link to clinical event IDs
    - The table stores instance-level metadata, not patient data
    - Joining creates meaningless results

Violation patterns:
    SELECT * FROM metadata m
    JOIN person p ON m.metadata_id = p.person_id
    -- ERROR: metadata_id has no relationship to person_id

    SELECT * FROM metadata m
    JOIN condition_occurrence co ON m.metadata_concept_id = co.condition_concept_id
    -- ERROR: metadata concepts are not clinical concepts

    SELECT * FROM metadata, drug_exposure
    -- ERROR: Cartesian product with clinical table

Correct patterns:
    SELECT name, value_as_string
    FROM metadata
    WHERE metadata_concept_id = 0
    -- OK: Standalone metadata query

    SELECT *
    FROM metadata
    WHERE name = 'CDM Version'
    -- OK: Retrieve CDM instance metadata

    SELECT m.value_as_string, c.concept_name
    FROM metadata m
    JOIN concept c ON m.metadata_concept_id = c.concept_id
    -- OK: Join to vocabulary table to look up concept names
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


TABLE_NAME = "metadata"

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

def _get_tables_in_select_scope(select: exp.Select) -> Set[str]:
    """
    Extract all table names from a single SELECT scope.
    Does not include tables from nested subqueries.
    """
    tables: Set[str] = set()

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
            tables.add(normalize_name(table_node.name))

    return tables


def _find_invalid_joins(tree: exp.Expression) -> List[str]:
    """Find instances where metadata is used with clinical tables."""
    issues: List[str] = []

    for select in tree.find_all(exp.Select):
        tables_in_select = _get_tables_in_select_scope(select)

        if TABLE_NAME in tables_in_select:
            clinical_found = [t for t in tables_in_select if t in CLINICAL_TABLES]

            for clinical_table in clinical_found:
                issues.append(
                    f"metadata table is used with {clinical_table}. "
                    "The metadata table stores CDM instance metadata (ETL provenance, "
                    "data characterization results), not patient-level clinical data. "
                    "It has no foreign key relationships to clinical tables and should "
                    "only be queried standalone to retrieve CDM instance information."
                )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class MetadataClinicalJoinRule(Rule):
    """
    OMOP_121: Prevent joining metadata table to clinical tables.
    """

    rule_id = "anti_patterns.metadata_clinical_join"
    name = "Metadata Clinical Join"

    description = (
        "The metadata table stores CDM instance metadata, not patient-level clinical data. "
        "It should never be joined to clinical tables."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Query the metadata table standalone to retrieve CDM instance information. "
        "Do not join it to clinical tables like person, condition_occurrence, or drug_exposure."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        if TABLE_NAME not in sql.lower():
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_121",
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


__all__ = ["MetadataClinicalJoinRule"]
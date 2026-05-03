"""Singleton Metadata Clinical Join Rule.

Detects joins between singleton metadata tables (cdm_source, metadata) and
clinical tables. Both tables describe the CDM instance and have no foreign-key
relationships to clinical data — joining them produces meaningless cartesian
products.

Replaces the previously separate rules:
- anti_patterns.cdm_source_clinical_join (OMOP_113)
- anti_patterns.metadata_clinical_join   (OMOP_121)

Violation patterns:
    SELECT * FROM person p, cdm_source cs;
    SELECT * FROM metadata m JOIN condition_occurrence co ON 1=1;

Correct patterns:
    SELECT cdm_version FROM cdm_source;                              -- standalone
    SELECT *, (SELECT cdm_version FROM cdm_source) AS v FROM person; -- scalar subquery
    SELECT m.name, c.concept_name FROM metadata m
    JOIN concept c ON m.metadata_concept_id = c.concept_id;          -- vocabulary join is fine
"""

import logging
from typing import Dict, List, Set

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

# Singleton/instance metadata tables that have no FK to clinical data.
# Mapping: table name -> short rationale used in the violation message.
METADATA_TABLES: Dict[str, str] = {
    "cdm_source": ("single-row metadata table describing the CDM instance (version, vocabulary version, source name)"),
    "metadata": ("stores CDM-level metadata (ETL provenance, data characterization), not patient-level clinical data"),
}

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


def _tables_in_select_scope(select: exp.Select) -> Set[str]:
    """Collect table names referenced directly in this SELECT, ignoring nested subqueries."""
    tables: Set[str] = set()

    for table_node in select.find_all(exp.Table):
        parent = table_node.parent
        crossed_boundary = False
        while parent and parent is not select:
            if isinstance(parent, exp.Select):
                crossed_boundary = True
                break
            parent = parent.parent

        if not crossed_boundary and table_node.name:
            tables.add(normalize_name(table_node.name))

    return tables


# --- Rule --------------------------------------------------------------------


@register
class SingletonMetadataClinicalJoinRule(Rule):
    """Prevent joins between singleton metadata tables and clinical tables."""

    rule_id = "anti_patterns.singleton_metadata_clinical_join"
    name = "Singleton Metadata Joined to Clinical Table"

    description = (
        "Singleton CDM-instance metadata tables (cdm_source, metadata) have no "
        "foreign keys to clinical data. Joining them to clinical tables creates "
        "meaningless cartesian products."
    )

    severity = Severity.ERROR

    suggested_fix = "REMOVE: the JOIN to cdm_source / metadata. REPLACE WITH a scalar subquery on the SELECT list, e.g. `SELECT col, (SELECT cdm_version FROM cdm_source) AS v FROM <clinical_table>`."
    long_description = (
        "cdm_source and metadata describe the CDM instance itself (release "
        "version, ETL provenance, data characterization). Neither has a "
        "primary key or foreign keys into clinical data. Joining them to "
        "clinical tables multiplies every clinical row by the metadata row "
        "count, yielding a Cartesian product. Read these tables on their own "
        "or via a scalar subquery; never through a JOIN."
    )
    example_bad = "SELECT *\nFROM person p, cdm_source cs;"
    example_good = "SELECT *,\n       (SELECT cdm_version FROM cdm_source) AS cdm_version\nFROM person;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()
        if not any(meta in sql_lower for meta in METADATA_TABLES):
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for %s",
                self.rule_id,
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            relevant = [m for m in METADATA_TABLES if has_table_reference(tree, m)]
            if not relevant:
                continue

            seen: Set[tuple] = set()

            for select in tree.find_all(exp.Select):
                tables_in_select = _tables_in_select_scope(select)

                for meta_table in relevant:
                    if meta_table not in tables_in_select:
                        continue

                    clinical_found = sorted(t for t in tables_in_select if t in CLINICAL_TABLES)
                    for clinical_table in clinical_found:
                        key = (meta_table, clinical_table)
                        if key in seen:
                            continue
                        seen.add(key)

                        violations.append(
                            self.create_violation(
                                message=(
                                    f"{meta_table} is joined to clinical table "
                                    f"'{clinical_table}'. {meta_table} is a "
                                    f"{METADATA_TABLES[meta_table]} and should not "
                                    f"be joined to patient-level clinical data. "
                                    f"Query {meta_table} independently for metadata."
                                ),
                                severity=self.severity,
                                details={
                                    "metadata_table": meta_table,
                                    "clinical_table": clinical_table,
                                },
                            )
                        )

        return violations


__all__ = ["SingletonMetadataClinicalJoinRule"]

"""Singleton Metadata Clinical Join Rule.

CDM_SOURCE and METADATA describe the CDM instance, not patients. In CDM v5.4 their only foreign keys point to
CONCEPT, and no table refers to them, so nothing relates their rows to clinical rows. When a SELECT reads one of
them next to a patient-level table and no predicate ties the two, every clinical row is repeated once per metadata
row. With a single CDM_SOURCE row that only attaches its values; with more rows every count is multiplied. The
specification does not limit either table to one row, so the outcome depends on the data and the finding is a
WARNING.

    SELECT * FROM person p, cdm_source cs;                     -- person rows repeated per cdm_source row
    SELECT * FROM metadata m JOIN condition_occurrence co ON 1 = 1;
    SELECT * FROM metadata m JOIN observation o ON m.metadata_id = o.observation_event_id;  -- equal by coincidence

An identifier equality that a joins key-pair rule reports as an ERROR (``m.metadata_id = p.person_id``:
joins.person_id_join_validation) carries the predicate in ``details["predicates"]``, and deduplication keeps only the
ERROR.

Not reported:
    SELECT p.person_id, (SELECT cdm_version FROM cdm_source) AS v FROM person p;           -- scalar subquery
    SELECT COUNT(*) FROM condition_occurrence co JOIN cdm_source cs
        ON co.condition_start_date > cs.cdm_release_date;                                   -- predicate ties them
    SELECT p.person_id, s.v FROM person p CROSS JOIN (SELECT MAX(cdm_version) AS v FROM cdm_source) s;  -- one row
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.deduplication import predicate_key
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.references import cdm_table
from fastssv.core.registry import register
from fastssv.rules.anti_patterns._ap_product import METADATA_TABLES, Scope

logger = logging.getLogger(__name__)

_DESCRIBES = {
    "cdm_source": "describes the CDM instance (source name, CDM version, vocabulary version)",
    "metadata": "holds metadata about the dataset",
}


KeyJoin = Tuple[str, str]  # (equality as written, normalised predicate)


def _findings(select: exp.Select) -> List[Tuple[str, str, Optional[KeyJoin]]]:
    """(metadata table, clinical source label, key equality or None) for each metadata source of ``select`` that is
    paired with a clinical source either by no predicate at all, or by an equality between identifier columns (a
    key relation the CDM does not define)."""
    scope = Scope(select)
    group_of = {i: n for n, members in enumerate(scope.groups()) for i in members}
    key_joins: Dict[Tuple[int, int], KeyJoin] = {}
    for a, col_a, b, col_b in scope.column_equalities():
        if normalize_name(col_a.name).endswith("_id") and normalize_name(col_b.name).endswith("_id"):
            atom = col_a.find_ancestor(exp.EQ, exp.NullSafeEQ)
            joined = (f"{col_a.sql()} = {col_b.sql()}", predicate_key(atom))
            key_joins.setdefault((a, b), joined)
            key_joins.setdefault((b, a), joined)
    out: List[Tuple[str, str, Optional[KeyJoin]]] = []
    for i, meta in enumerate(scope.sources):
        if not meta.metadata:
            continue
        for j, other in enumerate(scope.sources):
            if j == i or not other.patient_level:
                continue
            key = key_joins.get((i, j))
            if key is None and (meta.one_row or group_of[i] == group_of[j]):
                continue
            for name in sorted(meta.reads & METADATA_TABLES):
                out.append((name, other.label, key))
    return out


@register
class SingletonMetadataClinicalJoinRule(Rule):
    """CDM_SOURCE or METADATA paired with a clinical table without any predicate."""

    rule_id = "anti_patterns.singleton_metadata_clinical_join"
    name = "Instance Metadata Paired With Clinical Rows"

    description = (
        "cdm_source and metadata describe the CDM instance and share no key with clinical data. Read next to a "
        "clinical table with no predicate tying the two, every clinical row is repeated once per metadata row."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "REPLACE: the join to cdm_source / metadata WITH a scalar subquery in the SELECT list, e.g. "
        "`(SELECT cdm_version FROM cdm_source) AS cdm_version`. Unlike the join, the subquery raises an error "
        "instead of repeating rows if the table holds more than one row."
    )
    long_description = (
        "cdm_source and metadata describe the CDM instance (source, release dates, CDM and vocabulary versions, "
        "dataset metadata). Their only foreign keys point to CONCEPT and no table refers to them, so no key "
        "relates their rows to clinical rows. When a SELECT reads one of them next to a patient-level table and "
        "no predicate ties the two, every clinical row is repeated once per metadata row: harmless with a single "
        "cdm_source row, wrong counts with more. The specification does not limit either table to one row, so "
        "the finding is a WARNING. An equality between an identifier of the metadata table and one of the clinical "
        "table (metadata_id = fact_id_1) is reported too: no key relates them, so the match is a coincidence. When "
        "a rule reports that equality as an ERROR (metadata_id = person_id: joins.person_id_join_validation), the "
        "validator keeps only the ERROR. A "
        "predicate that compares other values (events after cdm_source.cdm_release_date), a scalar subquery, and "
        "a one-row derived table are not reported."
    )
    example_bad = "SELECT *\nFROM person p, cdm_source cs;"
    example_good = "SELECT *,\n       (SELECT cdm_version FROM cdm_source) AS cdm_version\nFROM person;"
    references = (cdm_table("cdm_source"), cdm_table("metadata"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []
        lowered = sql.lower()
        if not any(meta in lowered for meta in METADATA_TABLES):
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning("SQL parsing failed for %s", self.rule_id, extra={"sql": sql[:500], "dialect": dialect})
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            seen: Set[Tuple[str, str]] = set()
            for select in tree.find_all(exp.Select):
                for meta, clinical, joined in _findings(select):
                    key_join = joined[0] if joined else None
                    key = (meta, normalize_name(clinical))
                    if key in seen:
                        continue
                    seen.add(key)
                    if key_join:
                        message = (
                            f"{meta} is joined to clinical table '{clinical}' on {key_join}. {meta} "
                            f"{_DESCRIBES[meta]}; its only foreign keys point to CONCEPT and no clinical table refers "
                            "to it, so equal identifiers are a coincidence, not a relationship. Read it on its own "
                            "or through a scalar subquery."
                        )
                    else:
                        message = (
                            f"{meta} is read next to clinical table '{clinical}' with no predicate tying the two. "
                            f"{meta} {_DESCRIBES[meta]} and shares no key with patient-level data, so every "
                            f"{clinical} row is repeated once per {meta} row: harmless if {meta} holds a single row, "
                            "multiplied counts if it holds more. Read it through a scalar subquery."
                        )
                    violations.append(
                        self.create_violation(
                            message=message,
                            details={
                                "metadata_table": meta,
                                "clinical_table": clinical,
                                "key_join": key_join,
                                # A joins key-pair rule that reports this equality as an ERROR owns it; central
                                # deduplication then drops this finding.
                                "predicates": [joined[1]] if joined else [],
                            },
                        )
                    )
        return violations


__all__ = ["SingletonMetadataClinicalJoinRule"]

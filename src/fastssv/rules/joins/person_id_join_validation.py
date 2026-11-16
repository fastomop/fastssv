"""Person ID Join Validation Rule.

``person_id`` identifies an individual (OMOP CDM v5.4 conventions: "PERSON_ID uniquely identifies each individual"),
and every person-level table references PERSON through its own ``person_id`` foreign key. A predicate that equates a
person key with the key of another entity (``visit_occurrence_id``, ``location_id``, ``provider_id``,
``care_site_id``, a concept id, ...) compares unrelated id sequences and matches unrelated rows on every conformant
CDM.

Each operand is followed through casts, CTEs, derived tables and ``IN (SELECT …)`` to the CDM column it reads, so a
column is judged on what it carries, not on its name: ``(SELECT gender_concept_id AS person_id FROM person) a``
supplies a concept id under the name person_id. ON, WHERE and HAVING of every SELECT are read. Keys without a declared
target are not judged: ``cohort.subject_id``, which holds a person id for a person cohort and can hold a provider,
visit or care site id (CDM v5.4 COHORT), the polymorphic keys (``fact_id_1``, ``*_event_id``, ...), and columns the
CDM does not define, whose meaning the query alone does not fix. A comparison of two columns of the same row is a
data check, not a join, and is not judged either.

This rule also covers a person joined to LOCATION on the wrong key (formerly
``joins.person_location_join_validation``).

Violation pattern:
    SELECT p.person_id, vo.visit_start_date
    FROM person p
    JOIN visit_occurrence vo ON p.person_id = vo.visit_occurrence_id

Correct pattern:
    SELECT p.person_id, vo.visit_start_date
    FROM person p
    JOIN visit_occurrence vo ON p.person_id = vo.person_id
"""

from typing import List

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from ._j2_key_pairs import key_pair_violations
from ._lineage import PERSON_KEY, KeyMismatch


def _explain(m: KeyMismatch) -> str:
    other = m.right_entity if m.left_entity == PERSON_KEY else m.left_entity
    if other == ("location", "location_id"):
        return "A person's address is reached through person.location_id = location.location_id."
    return "Join person_id only to another person_id (or to cohort.subject_id for a person cohort)."


@register
class PersonIdJoinValidationRule(Rule):
    """A person key equated with a key of another entity."""

    rule_id = "joins.person_id_join_validation"
    name = "Person ID Join Validation"
    description = (
        "Flags a predicate that equates a person key (person.person_id or a person_id foreign key) with the key of "
        "another entity, such as visit_occurrence_id, location_id or provider_id. Columns are judged by what they "
        "carry, followed through CTEs and derived tables, not by their name."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the equality WITH one between two columns that identify the same entity, usually "
        "`<table>.person_id = <other>.person_id` (`person.location_id = location.location_id` for an address)."
    )
    example_bad = (
        "SELECT p.person_id, vo.visit_start_date\n"
        "FROM person p\n"
        "JOIN visit_occurrence vo ON p.person_id = vo.visit_occurrence_id;"
    )
    example_good = (
        "SELECT p.person_id, vo.visit_start_date\n"
        "FROM person p\n"
        "JOIN visit_occurrence vo ON p.person_id = vo.person_id;"
    )
    extra_examples = (
        RuleExample(
            "SELECT p.person_id, l.zip\nFROM person p\nJOIN location l ON p.person_id = l.location_id;",
            Severity.ERROR,
            "A person's address joined on person_id instead of person.location_id.",
        ),
    )
    references = (
        section(CDM_CONVENTIONS, "fields", "Fields"),
        cdm_table("person"),
        cdm_table("location"),
        cdm_table("cohort"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        return key_pair_violations(self, sql, dialect, keep_entity=PERSON_KEY, explain=_explain, prefilter="person_id")


__all__ = ["PersonIdJoinValidationRule"]

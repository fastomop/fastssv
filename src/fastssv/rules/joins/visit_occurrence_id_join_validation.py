"""Visit Occurrence ID Join Validation Rule.

``visit_occurrence_id`` identifies a visit (OMOP CDM v5.4 conventions: "VISIT_OCCURRENCE_ID uniquely identifies a
PERSON encounter at a point of care"). The event tables, VISIT_DETAIL and NOTE reference it through their own
``visit_occurrence_id`` foreign key, and ``visit_occurrence.preceding_visit_occurrence_id`` references it too. A
predicate that equates a visit key with the key of another entity (``visit_detail_id``, ``condition_occurrence_id``,
``care_site_id``, ...) compares unrelated id sequences and matches unrelated rows on every conformant CDM.

Each operand is followed through casts, CTEs, derived tables and ``IN (SELECT …)`` to the CDM column it reads; ON,
WHERE and HAVING are all read. Keys without a declared target are not judged: the polymorphic event keys
(``cost_event_id``, ``fact_id_1``, ``episode_event.event_id``, ``*_event_id``) and ``cohort.subject_id``, which "can
refer to the Person, Provider, Visit record or Care Site" (CDM v5.4 COHORT). A pair that involves a person key is
reported by ``joins.person_id_join_validation``, and a pair that involves ``preceding_visit_occurrence_id`` by
``joins.preceding_visit_occurrence_validation``.

Violation pattern:
    SELECT co.condition_concept_id, vd.visit_detail_concept_id
    FROM condition_occurrence co
    JOIN visit_detail vd ON co.visit_occurrence_id = vd.visit_detail_id

Correct pattern:
    SELECT co.condition_concept_id, vd.visit_detail_concept_id
    FROM condition_occurrence co
    JOIN visit_detail vd ON co.visit_detail_id = vd.visit_detail_id
"""

from typing import List, Optional

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from ._j2_key_pairs import key_pair_violations
from ._lineage import VISIT_KEY, KeyMismatch, Origin

VISIT_DETAIL_KEY: Origin = ("visit_detail", "visit_detail_id")


def _keep(m: KeyMismatch) -> Optional[Origin]:
    # A visit_detail link is finer than its parent visit, so a visit / visit_detail mix-up keeps the detail key.
    if {m.left_entity, m.right_entity} == {VISIT_KEY, VISIT_DETAIL_KEY}:
        return VISIT_DETAIL_KEY
    return VISIT_KEY


def _explain(m: KeyMismatch) -> str:
    if {m.left_entity, m.right_entity} == {VISIT_KEY, VISIT_DETAIL_KEY}:
        return (
            "visit_detail_id identifies a visit detail and visit_occurrence_id its parent visit; join "
            "<table>.visit_detail_id = visit_detail.visit_detail_id, or visit_occurrence_id on both sides."
        )
    return "Join visit_occurrence_id only to another visit_occurrence_id."


@register
class VisitOccurrenceIdJoinValidationRule(Rule):
    """A visit key equated with a key of another entity."""

    rule_id = "joins.visit_occurrence_id_join_validation"
    name = "Visit Occurrence ID Join Validation"
    description = (
        "Flags a predicate that equates a visit key (visit_occurrence_id, or a foreign key to it) with the key of "
        "another entity, such as visit_detail_id, condition_occurrence_id or care_site_id. Polymorphic keys and "
        "cohort.subject_id, which may hold a visit id, are not judged."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the equality WITH one between two columns that identify the same entity: "
        "`<table>.visit_occurrence_id = <other>.visit_occurrence_id`, or `<table>.visit_detail_id = "
        "visit_detail.visit_detail_id` for a visit detail."
    )
    example_bad = (
        "SELECT co.condition_concept_id, vd.visit_detail_concept_id\n"
        "FROM condition_occurrence co\n"
        "JOIN visit_detail vd ON co.visit_occurrence_id = vd.visit_detail_id;"
    )
    example_good = (
        "SELECT co.condition_concept_id, vd.visit_detail_concept_id\n"
        "FROM condition_occurrence co\n"
        "JOIN visit_detail vd ON co.visit_detail_id = vd.visit_detail_id;"
    )
    extra_examples = (
        RuleExample(
            "SELECT vo.visit_concept_id, co.condition_concept_id\n"
            "FROM visit_occurrence vo\n"
            "JOIN condition_occurrence co ON vo.visit_occurrence_id = co.condition_occurrence_id;",
            Severity.ERROR,
            "A visit id equated with an event's own primary key.",
        ),
    )
    references = (
        section(CDM_CONVENTIONS, "fields", "Fields"),
        cdm_table("visit_occurrence"),
        cdm_table("visit_detail"),
        cdm_table("cohort"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        return key_pair_violations(self, sql, dialect, keep_entity=_keep, explain=_explain, prefilter="visit_")


__all__ = ["VisitOccurrenceIdJoinValidationRule"]

"""Provider Join Validation Rule.

PROVIDER is keyed by ``provider_id``, and every table that records a provider references it through its own
``provider_id`` foreign key (OMOP CDM v5.4 field-level specification). A predicate that equates a provider key with
a key of another entity (``vo.care_site_id = pr.provider_id``, ``co.condition_occurrence_id = pr.provider_id``)
compares unrelated id sequences and matches unrelated rows.

A predicate is judged on the base columns its operands read (through casts, CTEs, derived tables and
``IN (SELECT …)``), in ON, WHERE or HAVING. Two columns that reference the same entity are a valid join even when
neither is ``provider_id``: ``vo.care_site_id = pr.care_site_id`` pairs a visit with the providers practising at its
care site. Pairs that involve a person key are reported by ``joins.person_id_join_validation``, a visit key by
``joins.visit_occurrence_id_join_validation``, and provider-to-care_site pairs by
``joins.provider_care_site_join_validation``.

Violation pattern:
    SELECT vo.visit_occurrence_id, p.specialty_concept_id
    FROM visit_occurrence vo
    JOIN provider p ON vo.care_site_id = p.provider_id

Correct pattern:
    SELECT vo.visit_occurrence_id, p.specialty_concept_id
    FROM visit_occurrence vo
    JOIN provider p ON vo.provider_id = p.provider_id
"""

from typing import List

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.references import CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from ._j2_key_pairs import key_pair_violations
from ._lineage import PROVIDER_KEY


@register
class ProviderJoinValidationRule(Rule):
    """A provider key equated with a key of another entity."""

    rule_id = "joins.provider_join_validation"
    name = "Provider Join Validation"
    description = (
        "Flags a predicate that equates a provider key (provider.provider_id or a provider_id foreign key) with a "
        "key of another entity such as care_site_id or condition_occurrence_id. Columns that reference the same "
        "entity (care_site_id = care_site_id) are a valid join."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the provider-side equality WITH one between two columns that identify the same entity, "
        "usually `<table>.provider_id = provider.provider_id`."
    )
    example_bad = (
        "SELECT vo.visit_occurrence_id, p.specialty_concept_id\n"
        "FROM visit_occurrence vo\n"
        "JOIN provider p ON vo.care_site_id = p.provider_id;"
    )
    example_good = (
        "SELECT vo.visit_occurrence_id, p.specialty_concept_id\n"
        "FROM visit_occurrence vo\n"
        "JOIN provider p ON vo.provider_id = p.provider_id;"
    )
    references = (
        section(CDM_CONVENTIONS, "fields", "Fields"),
        cdm_table("provider"),
        cdm_table("visit_occurrence"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        return key_pair_violations(
            self,
            sql,
            dialect,
            keep_entity=PROVIDER_KEY,
            explain=lambda m: "Join a provider_id foreign key to provider.provider_id.",
            prefilter="provider",
        )


__all__ = ["ProviderJoinValidationRule"]

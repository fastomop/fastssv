"""Provider to Care Site Join Validation Rule.

``provider.care_site_id`` is the foreign key to CARE_SITE ("the CARE_SITE_ID for the location that the provider
primarily practices in", OMOP CDM v5.4). A predicate between PROVIDER and CARE_SITE that equates keys of two
different entities (``p.provider_id = cs.care_site_id``) compares unrelated id sequences and matches unrelated rows on
every conformant CDM.

Each operand is followed through casts, CTEs, derived tables and ``IN (SELECT …)`` to the CDM column it reads; ON,
WHERE and HAVING are all read.

Violation pattern:
    SELECT p.provider_id, cs.care_site_name
    FROM provider p
    JOIN care_site cs ON p.provider_id = cs.care_site_id

Correct pattern:
    SELECT p.provider_id, cs.care_site_name
    FROM provider p
    JOIN care_site cs ON p.care_site_id = cs.care_site_id
"""

from typing import List

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.references import cdm_table
from fastssv.core.registry import register

from ._j2_key_pairs import key_pair_violations


@register
class ProviderCareSiteJoinValidationRule(Rule):
    """A provider-to-care_site predicate that equates keys of different entities."""

    rule_id = "joins.provider_care_site_join_validation"
    name = "Provider to Care Site Join Validation"
    description = (
        "Flags a predicate between provider and care_site that equates keys of different entities, such as "
        "provider.provider_id = care_site.care_site_id. A provider's practice site is reached through "
        "provider.care_site_id."
    )
    severity = Severity.ERROR
    suggested_fix = "REPLACE: the equality WITH `provider.care_site_id = care_site.care_site_id`."
    example_bad = (
        "SELECT p.provider_id, cs.care_site_name\n"
        "FROM provider p\n"
        "JOIN care_site cs ON p.provider_id = cs.care_site_id;"
    )
    example_good = (
        "SELECT p.provider_id, cs.care_site_name\n"
        "FROM provider p\n"
        "JOIN care_site cs ON p.care_site_id = cs.care_site_id;"
    )
    references = (cdm_table("provider"), cdm_table("care_site"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        return key_pair_violations(
            self,
            sql,
            dialect,
            keep_entity=("care_site", "care_site_id"),
            explain=lambda m: (
                "A provider's practice site is reached through provider.care_site_id = care_site.care_site_id."
            ),
            prefilter="care_site",
        )


__all__ = ["ProviderCareSiteJoinValidationRule"]

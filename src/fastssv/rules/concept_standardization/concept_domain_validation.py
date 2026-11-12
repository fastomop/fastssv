"""Concept Domain Validation Rule.

A CDM v5.4 ``*_concept_id`` field whose field-level specification names a foreign-key domain (``fkDomain``) holds
concepts of that domain only: ``condition_occurrence.condition_concept_id`` holds 'Condition' concepts ("Only records
whose source values map to concepts with a domain of 'Condition' should go in this table"), every
``*_type_concept_id`` holds 'Type Concept' concepts, ``measurement.unit_concept_id`` holds 'Unit' concepts, and so
on. When such a column is matched to ``concept.concept_id`` and that concept row is required (through AND) to have a
different ``domain_id``, no concept row can match on a conformant CDM:

    SELECT co.person_id
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    WHERE c.domain_id = 'Drug';        -- ERROR: always empty

A ``domain_id`` filter that lists the expected domain together with others is reported as a WARNING: the extra values
can never match rows reached through that column.

A *missing* ``domain_id`` filter is not reported: the join is on the concept primary key and the column's domain is
fixed by the CDM. 'Metadata' is accepted for every column: it is the domain of concept 0, which the CDM stores for an
unmapped record. A negated filter (``<> 'Condition'``) is not reported either: it selects the unmapped records.

The column-to-concept link and the domain filter are tied to one concept table instance by lineage, so they are found
through CTEs, derived tables, IN / EXISTS subqueries, comma joins and casts, and a filter on a different concept row
that reuses the alias is never mixed in. A predicate that does not restrict the rows (under OR or NOT, in a CASE, in
the ON clause of an outer join) is not reported. Columns with no fkDomain (observation_concept_id,
value_as_concept_id, ...) can hold several domains and are not checked.

Four fields have no fkDomain in the CSV, but the specification text of the field names their domain, and they are
checked with it: cdm_source.cdm_version_concept_id (Metadata), drug_strength.drug_concept_id (Drug: "the Branded Drug
or Clinical Drug Product"), location.country_concept_id (Geography) and measurement.operator_concept_id (Meas Value
Operator). See ``_cs_cdm_fields.FK_DOMAINS``.
"""

from typing import Dict, List, Set, Tuple


from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import BOOK_VOCABULARIES, CDM_FIELD_LEVEL, cdm_table, section
from fastssv.core.registry import register

from ._cs_cdm_fields import FK_DOMAINS
from ._cs_scope import ColumnFilter, instance_filters, instance_is_null_extended, links

# (table, column) -> admissible domain_id: the fkDomain of the CDM v5.4 field-level specification, plus four fields
# whose domain only the specification text gives (see FK_DOMAINS).
EXPECTED_DOMAINS: Dict[Tuple[str, str], List[str]] = {field: [domain] for field, domain in FK_DOMAINS.items()}

# Concept 0 ("No matching concept", the CDM value for an unmapped record) has domain_id 'Metadata'.
_UNMAPPED_DOMAIN = "metadata"


def _domain_values(flt: ColumnFilter) -> Set[str]:
    """The domain_id values a required positive filter admits, lowercased; empty when it is not such a filter."""
    if not (flt.required and not flt.outer_join_on and flt.complete):
        return set()
    if flt.op in ("eq", "in"):
        return {v.lower() for v in flt.values}
    if flt.op == "like" and not any(ch in flt.values[0] for ch in "%_"):
        return {flt.values[0].lower()}
    return set()


@register
class ConceptDomainValidationRule(Rule):
    rule_id = "concept_standardization.concept_domain_validation"
    name = "Concept Domain ID Matches Target Column"
    description = (
        "A *_concept_id column joined to concept is filtered on a domain_id that the CDM v5.4 specification does not "
        "allow for that column (e.g. 'Drug' on condition_concept_id, a clinical domain on a *_type_concept_id); no "
        "concept row can match the join."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the concept alias's domain_id filter WITH the column's domain (e.g. 'Condition' for "
        "condition_occurrence.condition_concept_id, 'Type Concept' for any *_type_concept_id), OR remove it: the "
        "join on concept_id already fixes the domain."
    )
    long_description = (
        "Each CDM v5.4 *_concept_id field with an fkDomain holds concepts of that domain only: "
        "condition_occurrence.condition_concept_id is 'Condition', drug_exposure.drug_concept_id is 'Drug', every "
        "*_type_concept_id is 'Type Concept', measurement.unit_concept_id is 'Unit'. Requiring the joined concept row "
        "to have another domain_id means no concept row can match (an inner join returns zero rows). A domain_id filter that also lists other domains is reported as a "
        "warning because the extra values can never match. A missing domain_id filter is not reported: the join is on "
        "the concept primary key, so the filter would change nothing."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.domain_id = 'Drug';"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.domain_id = 'Condition';"
    )
    references = (
        CDM_FIELD_LEVEL,
        cdm_table("condition_occurrence"),
        section(BOOK_VOCABULARIES, "conceptDomains", "Domains"),
        cdm_table("cdm_source"),
        cdm_table("drug_strength"),
        cdm_table("location"),
        cdm_table("measurement"),
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT co.person_id\n"
                "FROM condition_occurrence co\n"
                "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
                "WHERE c.domain_id IN ('Condition', 'Observation');"
            ),
            severity=Severity.WARNING,
            note="The domain filter lists the column's domain plus others that can never match",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "domain_id" not in sql.lower():
            return []
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            checked = [
                link
                for link in links(tree, "concept", "concept_id")
                if link.origin in EXPECTED_DOMAINS and (link.required or instance_is_null_extended(link))
            ]
            if not checked:
                continue
            filters = instance_filters(tree, "concept", "domain_id")
            seen: Set[Tuple[int, Tuple[str, str]]] = set()
            for link in checked:
                if (id(link.node), link.origin) in seen:
                    continue
                seen.add((id(link.node), link.origin))
                table, col = link.origin
                expected = EXPECTED_DOMAINS[link.origin]
                admissible = {d.lower() for d in expected} | {_UNMAPPED_DOMAIN}
                expected_msg = " OR ".join(f"'{d}'" for d in expected)
                alias = link.alias
                for flt in filters.get(link.instance, []):
                    values = _domain_values(flt)
                    if not values:
                        continue
                    shown = ", ".join(f"'{v}'" for v in flt.values)
                    if not values & admissible:
                        violations.append(
                            self.create_violation(
                                severity=Severity.ERROR,
                                message=(
                                    f"Domain mismatch for {table}.{col}: concept '{alias}' is filtered on "
                                    f"domain_id ({shown}) but the column only holds {expected_msg} concepts, so no "
                                    "concept row can match the join."
                                ),
                                suggested_fix=(
                                    f"REPLACE: the `{alias}.domain_id` filter WITH `{alias}.domain_id = "
                                    f"'{expected[0]}'`, OR remove it."
                                ),
                            )
                        )
                    elif not values <= admissible:
                        extra = ", ".join(f"'{v}'" for v in flt.values if v.lower() not in admissible)
                        violations.append(
                            self.create_violation(
                                severity=Severity.WARNING,
                                message=(
                                    f"{alias}.domain_id also admits ({extra}), which never matches {table}.{col} "
                                    f"(only {expected_msg} concepts); the extra values are dead and suggest the "
                                    "filter was written for another column."
                                ),
                                suggested_fix=f"REMOVE: ({extra}) from the `{alias}.domain_id` filter.",
                            )
                        )
        return violations


__all__ = ["ConceptDomainValidationRule", "EXPECTED_DOMAINS"]

"""Domain Vocabulary Validation Rule.

A CDM v5.4 concept column with an fkDomain (field-level specification; four fields whose domain only the
specification text gives are included, see ``_cs_cdm_fields.FK_DOMAINS``) holds Standard concepts of that domain:
``condition_occurrence.condition_concept_id`` holds Standard Condition concepts, ``drug_exposure.drug_concept_id``
Standard Drug concepts, and so on (CDM conventions: "Non-standard Concepts can only appear in *_source_concept_id
fields"). Standard concepts of a domain come from a few vocabularies (Book of OHDSI Table 5.2: Condition from SNOMED
and ICDO3, Drug from RxNorm, RxNorm Extension and CVX). The vocabularies with at least one valid Standard concept in
each domain are taken from the Athena release named in ``_cs_vocab_release``.

Joining such a column to ``concept`` and requiring that concept row to come only from vocabularies with no Standard
concept in the column's domain (ICD10CM or ICD9CM for conditions, NDC or ATC for drugs) leaves nothing to match, so
the query returns no rows:

    SELECT co.person_id
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'ICD10CM';     -- ERROR: no ICD10CM concept is stored here

A case-only mismatch (``'snomed'``) is an ERROR on PostgreSQL and DuckDB, which compare strings case-sensitively, a
WARNING on other engines and accepted on SQL Server / MySQL. Not reported: a filter that also admits a vocabulary
that occurs (``IN ('SNOMED', 'ICD10CM')``), a negated filter, a filter under OR or in an outer join's ON clause, a
vocabulary name the release does not know (it may be a site-local vocabulary), the same filter on a
``*_source_concept_id`` column, and unit columns (unit_vocabulary_validation owns them). 'None', the vocabulary of
concept 0, is accepted everywhere.
"""

from typing import FrozenSet, List, Optional, Tuple

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.references import BOOK_VOCABULARIES, CDM_CONVENTIONS, CDM_FIELD_LEVEL, cdm_table, section
from fastssv.core.registry import register

from ._cs_cdm_fields import FK_DOMAINS
from ._cs_scope import dead_scalar_column
from ._cs_vocab_filter import CASE_VERIFIED_DIALECTS, mismatches
from ._cs_vocab_release import KNOWN_VOCABULARIES, STANDARD_VOCABULARIES_BY_DOMAIN, VOCABULARY_RELEASE

UNIT_DOMAIN = "Unit"


def allowed_vocabularies(field: Tuple[str, str]) -> Optional[FrozenSet[str]]:
    domain = FK_DOMAINS.get(field)
    if domain is None or domain == UNIT_DOMAIN:
        return None
    return STANDARD_VOCABULARIES_BY_DOMAIN.get(domain)


def _shown(vocabularies: FrozenSet[str]) -> str:
    names = sorted(vocabularies)
    return ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")


@register
class DomainVocabularyValidationRule(Rule):
    """A standard concept column matched to concept rows of vocabularies with no Standard concept in its domain."""

    rule_id = "concept_standardization.domain_vocabulary_validation"
    name = "Domain Vocabulary Validation"
    description = (
        "A standard *_concept_id column (condition_concept_id, drug_concept_id, ...) is joined to concept and "
        "filtered to vocabularies that have no Standard concept in its domain (ICD10CM, ICD9CM, NDC, ATC, ...); the "
        "column holds Standard concepts only, so nothing matches."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: `c.vocabulary_id = '<source_vocab>'` WITH a vocabulary of the column's Standard concepts (Condition: "
        "'SNOMED'; Drug: 'RxNorm' / 'RxNorm Extension'), OR filter the *_source_concept_id column, OR map the source "
        "codes through concept_relationship 'Maps to' first."
    )
    long_description = (
        "Concept columns with an fkDomain hold Standard concepts of that domain, and Standard concepts come from a "
        "few vocabularies per domain (Book of OHDSI Table 5.2: SNOMED and ICDO3 for conditions; RxNorm, RxNorm "
        f"Extension and CVX for drugs). The vocabularies are taken from the Athena release {VOCABULARY_RELEASE}. "
        "Requiring the joined concept row to come only from a vocabulary with no Standard concept in that domain "
        "(ICD10CM, ICD9CM, NDC, SPL, ATC, MedDRA, Read, ...) returns zero rows, also when the query lists or groups "
        "by vocabulary_id. Filter the *_source_concept_id column instead, or map the source codes to standard "
        "concepts with concept_relationship 'Maps to'. Unknown vocabulary names (site-local vocabularies) and lists "
        "that also admit a vocabulary that occurs are not reported."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'ICD10CM';"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_source_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'ICD10CM';"
    )
    references = (
        section(BOOK_VOCABULARIES, "standardConcepts", "Standard concepts"),
        section(
            CDM_CONVENTIONS,
            "source-values-source-concept-ids-and-standard-concept-ids",
            "Source values, source concept ids and standard concept ids",
        ),
        CDM_FIELD_LEVEL,
        cdm_table("cdm_source"),
        cdm_table("drug_strength"),
        cdm_table("location"),
        cdm_table("measurement"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if "vocabulary_id" not in lowered or "concept" not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        dialect_norm = normalize_name(dialect or "postgres")
        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            for hit in mismatches(tree, allowed_vocabularies, KNOWN_VOCABULARIES, dialect_norm):
                if hit.dead_only:
                    continue
                table, column = hit.link.origin
                domain = FK_DOMAINS[hit.link.origin]
                allowed = allowed_vocabularies(hit.link.origin) or frozenset()
                shown = ", ".join(f"'{v}'" for v in hit.impossible)
                if hit.only_case:
                    severity = Severity.ERROR if dialect_norm in CASE_VERIFIED_DIALECTS else Severity.WARNING
                    message = (
                        f"{table}.{column} joined to concept ({hit.link.alias}) is restricted to vocabulary_id "
                        f"{shown}, which differs from the stored vocabulary_id only in case; string comparison is "
                        "case-sensitive on this engine, so nothing matches."
                    )
                else:
                    severity = Severity.ERROR
                    dead = dead_scalar_column(hit.flt.node)
                    message = (
                        f"{table}.{column} joined to concept ({hit.link.alias}) is restricted to vocabulary_id "
                        f"{shown}. The column holds Standard {domain} concepts, which come from {_shown(allowed)}; "
                        f"{shown} has none, so " + (f"{dead}." if dead else "the query returns no rows.")
                    )
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=severity,
                        details={"column": f"{table}.{column}", "vocabularies": hit.impossible},
                    )
                )
        return violations


__all__ = ["DomainVocabularyValidationRule", "allowed_vocabularies"]

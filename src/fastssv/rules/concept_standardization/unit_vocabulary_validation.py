"""Unit Vocabulary Validation Rule.

Unit columns hold Standard Unit-domain concepts: the CDM v5.4 field-level specification gives fkDomain 'Unit' for
``measurement``, ``observation``, ``device_exposure`` and ``dose_era`` ``unit_concept_id`` ("UNIT_SOURCE_VALUES should
be linked to a Standard Concept within the Unit domain"), and drug_strength's amount / numerator / denominator unit
columns hold UCUM concepts only in the Athena release named in ``_cs_vocab_release``. In that release every valid
Standard Unit concept is in the UCUM vocabulary; the Unit concepts of SNOMED, Nebraska Lexicon, CDISC and CIEL are all
non-standard.

Joining a unit column to ``concept`` and requiring that concept row to come only from another known vocabulary leaves
nothing to match:

    SELECT m.person_id
    FROM measurement m
    JOIN concept c ON m.unit_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'LOINC';      -- ERROR: no unit concept is in LOINC

Lowercase ``'ucum'`` is an ERROR on PostgreSQL and DuckDB (case-sensitive comparison), a WARNING on other engines and
accepted on SQL Server / MySQL. Not reported: a filter that also admits 'UCUM' or 'None' (concept 0, an unmapped
unit), negated filters, filters under OR or in an outer join's ON clause, unknown (site-local) vocabulary names, and
specimen.unit_concept_id, which has no fkDomain.
"""

from typing import FrozenSet, List, Optional, Tuple

from fastssv.core.base import Reference, Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.references import CDM_FIELD_LEVEL, cdm_table
from fastssv.core.registry import register

from ._cs_cdm_fields import FK_DOMAINS
from ._cs_vocab_filter import CASE_VERIFIED_DIALECTS, mismatches
from ._cs_vocab_release import (
    DRUG_STRENGTH_UNIT_VOCABULARIES,
    KNOWN_VOCABULARIES,
    STANDARD_VOCABULARIES_BY_DOMAIN,
    VOCABULARY_RELEASE,
)

VALID_UNIT_VOCABULARY = "UCUM"

_DRUG_STRENGTH_UNIT_COLUMNS = {
    ("drug_strength", "amount_unit_concept_id"),
    ("drug_strength", "numerator_unit_concept_id"),
    ("drug_strength", "denominator_unit_concept_id"),
}
CDM_UNIT_COLUMNS = frozenset({f for f, d in FK_DOMAINS.items() if d == "Unit"} | _DRUG_STRENGTH_UNIT_COLUMNS)


def _allowed(field: Tuple[str, str]) -> Optional[FrozenSet[str]]:
    if field in _DRUG_STRENGTH_UNIT_COLUMNS:
        return DRUG_STRENGTH_UNIT_VOCABULARIES
    if FK_DOMAINS.get(field) == "Unit":
        return STANDARD_VOCABULARIES_BY_DOMAIN["Unit"]
    return None


@register
class UnitVocabularyValidationRule(Rule):
    """A unit column matched to concept rows of a vocabulary that holds no Standard unit concept."""

    rule_id = "concept_standardization.unit_vocabulary_validation"
    name = "Unit Vocabulary Validation"
    description = (
        "A unit_concept_id column is joined to concept and restricted to a vocabulary other than UCUM. Standard unit "
        "concepts are all UCUM, so the join finds no unit concepts."
    )
    severity = Severity.ERROR
    suggested_fix = "REPLACE: the vocabulary_id filter WITH `vocabulary_id = 'UCUM'`, OR remove it."
    long_description = (
        "Unit columns (measurement / observation / device_exposure / dose_era unit_concept_id, and drug_strength's unit "
        "columns) hold Standard Unit-domain concepts. In the Athena release "
        f"{VOCABULARY_RELEASE} every valid Standard Unit concept is in UCUM; the Unit concepts of SNOMED, Nebraska "
        "Lexicon, CDISC and CIEL are non-standard. Restricting the joined concept row to another known vocabulary "
        "therefore finds no unit concepts and returns no rows, also when the query lists or groups by vocabulary_id. "
        "A list that also admits 'UCUM' or 'None' (concept 0, an unmapped unit) is not reported."
    )
    example_bad = (
        "SELECT m.person_id\n"
        "FROM measurement m\n"
        "JOIN concept c ON m.unit_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'LOINC';"
    )
    example_good = (
        "SELECT m.person_id\n"
        "FROM measurement m\n"
        "JOIN concept c ON m.unit_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'UCUM';"
    )
    references = (
        cdm_table("measurement"),
        CDM_FIELD_LEVEL,
        Reference(
            "OHDSI Forums: Measurement entity - which vocabulary to consider",
            "https://forums.ohdsi.org/t/measurement-entity-which-vocabulary-to-consider/5813",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if "unit_concept_id" not in lowered or "vocabulary_id" not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        dialect_norm = normalize_name(dialect or "postgres")
        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            for hit in mismatches(tree, _allowed, KNOWN_VOCABULARIES, dialect_norm):
                if hit.dead_only:
                    continue
                table, column = hit.link.origin
                shown = ", ".join(f"'{v}'" for v in hit.impossible)
                if hit.only_case:
                    severity = Severity.ERROR if dialect_norm in CASE_VERIFIED_DIALECTS else Severity.WARNING
                    message = (
                        f"Unit column '{table}.{column}' is restricted to vocabulary_id {shown}; the vocabulary is "
                        f"'{VALID_UNIT_VOCABULARY}' and string comparison is case-sensitive on this engine, so the join "
                        "finds no unit concepts."
                    )
                else:
                    severity = Severity.ERROR
                    message = (
                        f"Unit column '{table}.{column}' is restricted to vocabulary_id {shown}. Standard unit "
                        f"concepts use '{VALID_UNIT_VOCABULARY}', not {shown}, so the join finds no unit concepts."
                    )
                patch = None
                if hit.flt.op == "eq":
                    span = locate(sql, hit.flt.sql)
                    literal = f"'{hit.impossible[0]}'"
                    if span is not None and literal in hit.flt.sql:
                        patch = patch_replace(span, hit.flt.sql.replace(literal, f"'{VALID_UNIT_VOCABULARY}'"))
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=severity,
                        suggested_fix_patch=patch,
                        details={"unit_column": f"{table}.{column}", "incorrect_vocabulary": hit.impossible},
                    )
                )
        return violations


__all__ = ["CDM_UNIT_COLUMNS", "UnitVocabularyValidationRule"]

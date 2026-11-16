"""Published sources that rules cite as the basis of their claims.

Rules set ``references`` to a tuple of :class:`~fastssv.core.base.Reference`. The helpers here build the
links that most rules share, so titles and URLs stay uniform across the catalogue; anything else is written
inline as ``Reference(title, url)``.
"""

from fastssv.core.base import Reference

_CDM = "https://ohdsi.github.io/CommonDataModel"
_BOOK = "https://ohdsi.github.io/TheBookOfOhdsi"

# Table anchors of the CDM v5.4 specification page (one section per table).
CDM54_TABLES = frozenset(
    {
        "care_site",
        "cdm_source",
        "cohort",
        "cohort_definition",
        "concept",
        "concept_ancestor",
        "concept_class",
        "concept_relationship",
        "concept_synonym",
        "condition_era",
        "condition_occurrence",
        "cost",
        "death",
        "device_exposure",
        "domain",
        "dose_era",
        "drug_era",
        "drug_exposure",
        "drug_strength",
        "episode",
        "episode_event",
        "fact_relationship",
        "location",
        "measurement",
        "metadata",
        "note",
        "note_nlp",
        "observation",
        "observation_period",
        "payer_plan_period",
        "person",
        "procedure_occurrence",
        "provider",
        "relationship",
        "source_to_concept_map",
        "specimen",
        "visit_detail",
        "visit_occurrence",
        "vocabulary",
    }
)

CDM_CONVENTIONS = Reference("OMOP CDM: Data Model Conventions", f"{_CDM}/dataModelConventions.html")
CDM_DRUG_DOSE = Reference("OMOP CDM: Drug Dose Calculation", f"{_CDM}/drug_dose.html")
CDM_V54_CHANGES = Reference("OMOP CDM: Changes from v5.3 to v5.4", f"{_CDM}/cdm54Changes.html")
CDM_FIELD_LEVEL = Reference(
    "OMOP CDM v5.4 field-level specification (CSV)",
    "https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv",
)
DQD_FIELD_THRESHOLDS = Reference(
    "OHDSI Data Quality Dashboard: CDM v5.4 field-level checks and thresholds (CSV)",
    "https://raw.githubusercontent.com/OHDSI/DataQualityDashboard/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv",
)
THEMIS = Reference("OHDSI THEMIS: OMOP CDM conventions", "https://ohdsi.github.io/Themis/")

BOOK_CDM = Reference("The Book of OHDSI, ch. 4: The Common Data Model", f"{_BOOK}/CommonDataModel.html")
BOOK_VOCABULARIES = Reference(
    "The Book of OHDSI, ch. 5: Standardized Vocabularies", f"{_BOOK}/StandardizedVocabularies.html"
)
BOOK_SQL = Reference("The Book of OHDSI, ch. 9: SQL and R", f"{_BOOK}/SqlAndR.html")
BOOK_COHORTS = Reference("The Book of OHDSI, ch. 10: Defining Cohorts", f"{_BOOK}/Cohorts.html")
BOOK_DATA_QUALITY = Reference("The Book of OHDSI, ch. 15: Data Quality", f"{_BOOK}/DataQuality.html")


def cdm_table(table: str) -> Reference:
    """The CDM v5.4 specification section for ``table``."""
    name = table.lower()
    if name not in CDM54_TABLES:
        raise ValueError(f"{table!r} has no section in the CDM v5.4 specification")
    return Reference(f"OMOP CDM v5.4 specification: {name.upper()}", f"{_CDM}/cdm54.html#{name}")


def section(source: Reference, anchor: str, topic: str) -> Reference:
    """A named section (``#anchor``) of one of the page-level sources above."""
    return Reference(f"{source.title}, {topic}", f"{source.url}#{anchor}")


__all__ = [
    "BOOK_CDM",
    "BOOK_COHORTS",
    "BOOK_DATA_QUALITY",
    "BOOK_SQL",
    "BOOK_VOCABULARIES",
    "CDM54_TABLES",
    "CDM_CONVENTIONS",
    "CDM_DRUG_DOSE",
    "CDM_FIELD_LEVEL",
    "CDM_V54_CHANGES",
    "DQD_FIELD_THRESHOLDS",
    "THEMIS",
    "cdm_table",
    "section",
]

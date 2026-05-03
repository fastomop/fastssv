"""OMOP Common Data Model v5.4 — Canonical Schema (column types).

This module is the **single source of truth** for the OMOP CDM v5.4 schema in
fastssv. ``CDM_COLUMN_TYPES`` is a per-table column-type map; everything else
in this package derives from it:

- ``schemas.cdm_columns.CDM_COLUMNS`` is computed at import time as
  ``{table: frozenset(cols.keys())}``.
- ``schemas.cdm_schema.CDM_SCHEMA`` declares foreign-key edges; every
  column referenced by an edge must exist here, asserted at import.
- ``schemas.semantic_schema`` declares STANDARD_/SOURCE_CONCEPT_FIELDS;
  every (table, column) listed must exist here.

A consistency test (``tests/test_schema_consistency.py``) freezes this
contract in CI.

Source for column lists and types: the OMOP CDM v5.4 ERD published by OHDSI
(https://ohdsi.github.io/CommonDataModel/cdm54.html). Two legacy tables
(``attribute_definition``, ``location_history``) are preserved with a marker
comment because rules in fastssv detect their misuse, even though they are
not part of the v5.4 core spec.
"""

from typing import Dict, FrozenSet, Set


# Type categories. Use these constants instead of raw strings so type
# changes flow through the rest of the codebase.
INTEGER = "integer"
VARCHAR = "varchar"
DATE = "date"
DATETIME = "datetime"
TIMESTAMP = "datetime"  # alias for DATETIME (kept for back-compat)
FLOAT = "float"


# ---------------------------------------------------------------------------
# CDM_COLUMN_TYPES — canonical per-table column-type map.
# Format: {table_name (lowercase): {column_name (lowercase): type_constant}}.
# ---------------------------------------------------------------------------

CDM_COLUMN_TYPES: Dict[str, Dict[str, str]] = {
    # =====================================================================
    # STANDARDIZED CLINICAL DATA
    # =====================================================================
    "person": {
        "person_id": INTEGER,
        "gender_concept_id": INTEGER,
        "year_of_birth": INTEGER,
        "month_of_birth": INTEGER,
        "day_of_birth": INTEGER,
        "birth_datetime": DATETIME,
        "race_concept_id": INTEGER,
        "ethnicity_concept_id": INTEGER,
        "location_id": INTEGER,
        "provider_id": INTEGER,
        "care_site_id": INTEGER,
        "person_source_value": VARCHAR,
        "gender_source_value": VARCHAR,
        "gender_source_concept_id": INTEGER,
        "race_source_value": VARCHAR,
        "race_source_concept_id": INTEGER,
        "ethnicity_source_value": VARCHAR,
        "ethnicity_source_concept_id": INTEGER,
    },
    "observation_period": {
        "observation_period_id": INTEGER,
        "person_id": INTEGER,
        "observation_period_start_date": DATE,
        "observation_period_end_date": DATE,
        "period_type_concept_id": INTEGER,
    },
    "visit_occurrence": {
        "visit_occurrence_id": INTEGER,
        "person_id": INTEGER,
        "visit_concept_id": INTEGER,
        "visit_start_date": DATE,
        "visit_start_datetime": DATETIME,
        "visit_end_date": DATE,
        "visit_end_datetime": DATETIME,
        "visit_type_concept_id": INTEGER,
        "provider_id": INTEGER,
        "care_site_id": INTEGER,
        "visit_source_value": VARCHAR,
        "visit_source_concept_id": INTEGER,
        "admitted_from_concept_id": INTEGER,
        "admitted_from_source_value": VARCHAR,
        "discharged_to_concept_id": INTEGER,
        "discharged_to_source_value": VARCHAR,
        "preceding_visit_occurrence_id": INTEGER,
    },
    "visit_detail": {
        "visit_detail_id": INTEGER,
        "person_id": INTEGER,
        "visit_detail_concept_id": INTEGER,
        "visit_detail_start_date": DATE,
        "visit_detail_start_datetime": DATETIME,
        "visit_detail_end_date": DATE,
        "visit_detail_end_datetime": DATETIME,
        "visit_detail_type_concept_id": INTEGER,
        "provider_id": INTEGER,
        "care_site_id": INTEGER,
        "visit_detail_source_value": VARCHAR,
        "visit_detail_source_concept_id": INTEGER,
        "admitted_from_concept_id": INTEGER,
        "admitted_from_source_value": VARCHAR,
        "discharged_to_source_value": VARCHAR,
        "discharged_to_concept_id": INTEGER,
        "preceding_visit_detail_id": INTEGER,
        "parent_visit_detail_id": INTEGER,
        "visit_occurrence_id": INTEGER,
    },
    "condition_occurrence": {
        "condition_occurrence_id": INTEGER,
        "person_id": INTEGER,
        "condition_concept_id": INTEGER,
        "condition_start_date": DATE,
        "condition_start_datetime": DATETIME,
        "condition_end_date": DATE,
        "condition_end_datetime": DATETIME,
        "condition_type_concept_id": INTEGER,
        "condition_status_concept_id": INTEGER,
        "stop_reason": VARCHAR,
        "provider_id": INTEGER,
        "visit_occurrence_id": INTEGER,
        "visit_detail_id": INTEGER,
        "condition_source_value": VARCHAR,
        "condition_source_concept_id": INTEGER,
        "condition_status_source_value": VARCHAR,
    },
    "drug_exposure": {
        "drug_exposure_id": INTEGER,
        "person_id": INTEGER,
        "drug_concept_id": INTEGER,
        "drug_exposure_start_date": DATE,
        "drug_exposure_start_datetime": DATETIME,
        "drug_exposure_end_date": DATE,
        "drug_exposure_end_datetime": DATETIME,
        "verbatim_end_date": DATE,
        "drug_type_concept_id": INTEGER,
        "stop_reason": VARCHAR,
        "refills": INTEGER,
        "quantity": FLOAT,
        "days_supply": INTEGER,
        "sig": VARCHAR,
        "route_concept_id": INTEGER,
        "lot_number": VARCHAR,
        "provider_id": INTEGER,
        "visit_occurrence_id": INTEGER,
        "visit_detail_id": INTEGER,
        "drug_source_value": VARCHAR,
        "drug_source_concept_id": INTEGER,
        "route_source_value": VARCHAR,
        "dose_unit_source_value": VARCHAR,
    },
    "procedure_occurrence": {
        "procedure_occurrence_id": INTEGER,
        "person_id": INTEGER,
        "procedure_concept_id": INTEGER,
        "procedure_date": DATE,
        "procedure_datetime": DATETIME,
        "procedure_end_date": DATE,
        "procedure_end_datetime": DATETIME,
        "procedure_type_concept_id": INTEGER,
        "modifier_concept_id": INTEGER,
        "quantity": INTEGER,
        "provider_id": INTEGER,
        "visit_occurrence_id": INTEGER,
        "visit_detail_id": INTEGER,
        "procedure_source_value": VARCHAR,
        "procedure_source_concept_id": INTEGER,
        "modifier_source_value": VARCHAR,
    },
    "device_exposure": {
        "device_exposure_id": INTEGER,
        "person_id": INTEGER,
        "device_concept_id": INTEGER,
        "device_exposure_start_date": DATE,
        "device_exposure_start_datetime": DATETIME,
        "device_exposure_end_date": DATE,
        "device_exposure_end_datetime": DATETIME,
        "device_type_concept_id": INTEGER,
        "unique_device_id": VARCHAR,
        "production_id": VARCHAR,
        "quantity": INTEGER,
        "provider_id": INTEGER,
        "visit_occurrence_id": INTEGER,
        "visit_detail_id": INTEGER,
        "device_source_value": VARCHAR,
        "device_source_concept_id": INTEGER,
        "unit_concept_id": INTEGER,
        "unit_source_value": VARCHAR,
        "unit_source_concept_id": INTEGER,
    },
    "measurement": {
        "measurement_id": INTEGER,
        "person_id": INTEGER,
        "measurement_concept_id": INTEGER,
        "measurement_date": DATE,
        "measurement_datetime": DATETIME,
        "measurement_time": VARCHAR,
        "measurement_type_concept_id": INTEGER,
        "operator_concept_id": INTEGER,
        "value_as_number": FLOAT,
        "value_as_concept_id": INTEGER,
        "unit_concept_id": INTEGER,
        "range_low": FLOAT,
        "range_high": FLOAT,
        "provider_id": INTEGER,
        "visit_occurrence_id": INTEGER,
        "visit_detail_id": INTEGER,
        "measurement_source_value": VARCHAR,
        "measurement_source_concept_id": INTEGER,
        "unit_source_value": VARCHAR,
        "unit_source_concept_id": INTEGER,
        "value_source_value": VARCHAR,
        "measurement_event_id": INTEGER,
        "meas_event_field_concept_id": INTEGER,
    },
    "observation": {
        "observation_id": INTEGER,
        "person_id": INTEGER,
        "observation_concept_id": INTEGER,
        "observation_date": DATE,
        "observation_datetime": DATETIME,
        "observation_type_concept_id": INTEGER,
        "value_as_number": FLOAT,
        "value_as_string": VARCHAR,
        "value_as_concept_id": INTEGER,
        "qualifier_concept_id": INTEGER,
        "unit_concept_id": INTEGER,
        "provider_id": INTEGER,
        "visit_occurrence_id": INTEGER,
        "visit_detail_id": INTEGER,
        "observation_source_value": VARCHAR,
        "observation_source_concept_id": INTEGER,
        "unit_source_value": VARCHAR,
        "qualifier_source_value": VARCHAR,
        "value_source_value": VARCHAR,
        "observation_event_id": INTEGER,
        "obs_event_field_concept_id": INTEGER,
    },
    "death": {
        "person_id": INTEGER,
        "death_date": DATE,
        "death_datetime": DATETIME,
        "death_type_concept_id": INTEGER,
        "cause_concept_id": INTEGER,
        "cause_source_value": VARCHAR,
        "cause_source_concept_id": INTEGER,
    },
    "note": {
        "note_id": INTEGER,
        "person_id": INTEGER,
        "note_date": DATE,
        "note_datetime": DATETIME,
        "note_type_concept_id": INTEGER,
        "note_class_concept_id": INTEGER,
        "note_title": VARCHAR,
        "note_text": VARCHAR,
        "encoding_concept_id": INTEGER,
        "language_concept_id": INTEGER,
        "provider_id": INTEGER,
        "visit_occurrence_id": INTEGER,
        "visit_detail_id": INTEGER,
        "note_source_value": VARCHAR,
        "note_event_id": INTEGER,
        "note_event_field_concept_id": INTEGER,
    },
    "note_nlp": {
        "note_nlp_id": INTEGER,
        "note_id": INTEGER,
        "section_concept_id": INTEGER,
        "snippet": VARCHAR,
        "offset": VARCHAR,
        "lexical_variant": VARCHAR,
        "note_nlp_concept_id": INTEGER,
        "note_nlp_source_concept_id": INTEGER,
        "nlp_system": VARCHAR,
        "nlp_date": DATE,
        "nlp_datetime": DATETIME,
        "term_exists": VARCHAR,
        "term_temporal": VARCHAR,
        "term_modifiers": VARCHAR,
    },
    "specimen": {
        "specimen_id": INTEGER,
        "person_id": INTEGER,
        "specimen_concept_id": INTEGER,
        "specimen_type_concept_id": INTEGER,
        "specimen_date": DATE,
        "specimen_datetime": DATETIME,
        "quantity": FLOAT,
        "unit_concept_id": INTEGER,
        "anatomic_site_concept_id": INTEGER,
        "disease_status_concept_id": INTEGER,
        "specimen_source_id": VARCHAR,
        "specimen_source_value": VARCHAR,
        "unit_source_value": VARCHAR,
        "anatomic_site_source_value": VARCHAR,
        "disease_status_source_value": VARCHAR,
    },
    "fact_relationship": {
        "domain_concept_id_1": INTEGER,
        "fact_id_1": INTEGER,
        "domain_concept_id_2": INTEGER,
        "fact_id_2": INTEGER,
        "relationship_concept_id": INTEGER,
    },
    # =====================================================================
    # STANDARDIZED HEALTH SYSTEM DATA
    # =====================================================================
    "location": {
        "location_id": INTEGER,
        "address_1": VARCHAR,
        "address_2": VARCHAR,
        "city": VARCHAR,
        "state": VARCHAR,
        "zip": VARCHAR,
        "county": VARCHAR,
        "location_source_value": VARCHAR,
        "country_concept_id": INTEGER,
        "country_source_value": VARCHAR,
        "latitude": FLOAT,
        "longitude": FLOAT,
    },
    "care_site": {
        "care_site_id": INTEGER,
        "care_site_name": VARCHAR,
        "place_of_service_concept_id": INTEGER,
        "location_id": INTEGER,
        "care_site_source_value": VARCHAR,
        "place_of_service_source_value": VARCHAR,
    },
    "provider": {
        "provider_id": INTEGER,
        "provider_name": VARCHAR,
        "npi": VARCHAR,
        "dea": VARCHAR,
        "specialty_concept_id": INTEGER,
        "care_site_id": INTEGER,
        "year_of_birth": INTEGER,
        "gender_concept_id": INTEGER,
        "provider_source_value": VARCHAR,
        "specialty_source_value": VARCHAR,
        "specialty_source_concept_id": INTEGER,
        "gender_source_value": VARCHAR,
        "gender_source_concept_id": INTEGER,
    },
    # =====================================================================
    # STANDARDIZED HEALTH ECONOMICS
    # =====================================================================
    "payer_plan_period": {
        "payer_plan_period_id": INTEGER,
        "person_id": INTEGER,
        "payer_plan_period_start_date": DATE,
        "payer_plan_period_end_date": DATE,
        "payer_concept_id": INTEGER,
        "payer_source_value": VARCHAR,
        "payer_source_concept_id": INTEGER,
        "plan_concept_id": INTEGER,
        "plan_source_value": VARCHAR,
        "plan_source_concept_id": INTEGER,
        "sponsor_concept_id": INTEGER,
        "sponsor_source_value": VARCHAR,
        "sponsor_source_concept_id": INTEGER,
        "family_source_value": VARCHAR,
        "stop_reason_concept_id": INTEGER,
        "stop_reason_source_value": VARCHAR,
        "stop_reason_source_concept_id": INTEGER,
    },
    "cost": {
        "cost_id": INTEGER,
        "cost_event_id": INTEGER,
        "cost_domain_id": VARCHAR,
        "cost_type_concept_id": INTEGER,
        "currency_concept_id": INTEGER,
        "total_charge": FLOAT,
        "total_cost": FLOAT,
        "total_paid": FLOAT,
        "paid_by_payer": FLOAT,
        "paid_by_patient": FLOAT,
        "paid_patient_copay": FLOAT,
        "paid_patient_coinsurance": FLOAT,
        "paid_patient_deductible": FLOAT,
        "paid_by_primary": FLOAT,
        "paid_ingredient_cost": FLOAT,
        "paid_dispensing_fee": FLOAT,
        "payer_plan_period_id": INTEGER,
        "amount_allowed": FLOAT,
        "revenue_code_concept_id": INTEGER,
        "revenue_code_source_value": VARCHAR,
        "drg_concept_id": INTEGER,
        "drg_source_value": VARCHAR,
    },
    # =====================================================================
    # STANDARDIZED DERIVED ELEMENTS
    # =====================================================================
    "drug_era": {
        "drug_era_id": INTEGER,
        "person_id": INTEGER,
        "drug_concept_id": INTEGER,
        "drug_era_start_date": DATE,
        "drug_era_end_date": DATE,
        "drug_exposure_count": INTEGER,
        "gap_days": INTEGER,
    },
    "dose_era": {
        "dose_era_id": INTEGER,
        "person_id": INTEGER,
        "drug_concept_id": INTEGER,
        "unit_concept_id": INTEGER,
        "dose_value": FLOAT,
        "dose_era_start_date": DATE,
        "dose_era_end_date": DATE,
    },
    "condition_era": {
        "condition_era_id": INTEGER,
        "person_id": INTEGER,
        "condition_concept_id": INTEGER,
        "condition_era_start_date": DATE,
        "condition_era_end_date": DATE,
        "condition_occurrence_count": INTEGER,
    },
    "episode": {
        "episode_id": INTEGER,
        "person_id": INTEGER,
        "episode_concept_id": INTEGER,
        "episode_start_date": DATE,
        "episode_start_datetime": DATETIME,
        "episode_end_date": DATE,
        "episode_end_datetime": DATETIME,
        "episode_parent_id": INTEGER,
        "episode_number": INTEGER,
        "episode_object_concept_id": INTEGER,
        "episode_type_concept_id": INTEGER,
        "episode_source_value": VARCHAR,
        "episode_source_concept_id": INTEGER,
    },
    "episode_event": {
        "episode_id": INTEGER,
        "event_id": INTEGER,
        "episode_event_field_concept_id": INTEGER,
    },
    # =====================================================================
    # METADATA
    # =====================================================================
    "metadata": {
        "metadata_id": INTEGER,
        "metadata_concept_id": INTEGER,
        "metadata_type_concept_id": INTEGER,
        "name": VARCHAR,
        "value_as_string": VARCHAR,
        "value_as_concept_id": INTEGER,
        "value_as_number": FLOAT,
        "metadata_date": DATE,
        "metadata_datetime": DATETIME,
    },
    "cdm_source": {
        "cdm_source_name": VARCHAR,
        "cdm_source_abbreviation": VARCHAR,
        "cdm_holder": VARCHAR,
        "source_description": VARCHAR,
        "source_documentation_reference": VARCHAR,
        "cdm_etl_reference": VARCHAR,
        "source_release_date": DATE,
        "cdm_release_date": DATE,
        "cdm_version": VARCHAR,
        "cdm_version_concept_id": INTEGER,
        "vocabulary_version": VARCHAR,
    },
    # =====================================================================
    # VOCABULARY TABLES
    # =====================================================================
    "concept": {
        "concept_id": INTEGER,
        "concept_name": VARCHAR,
        "domain_id": VARCHAR,
        "vocabulary_id": VARCHAR,
        "concept_class_id": VARCHAR,
        "standard_concept": VARCHAR,
        "concept_code": VARCHAR,
        "valid_start_date": DATE,
        "valid_end_date": DATE,
        "invalid_reason": VARCHAR,
    },
    "vocabulary": {
        "vocabulary_id": VARCHAR,
        "vocabulary_name": VARCHAR,
        "vocabulary_reference": VARCHAR,
        "vocabulary_version": VARCHAR,
        "vocabulary_concept_id": INTEGER,
    },
    "domain": {
        "domain_id": VARCHAR,
        "domain_name": VARCHAR,
        "domain_concept_id": INTEGER,
    },
    "concept_class": {
        "concept_class_id": VARCHAR,
        "concept_class_name": VARCHAR,
        "concept_class_concept_id": INTEGER,
    },
    "concept_relationship": {
        "concept_id_1": INTEGER,
        "concept_id_2": INTEGER,
        "relationship_id": VARCHAR,
        "valid_start_date": DATE,
        "valid_end_date": DATE,
        "invalid_reason": VARCHAR,
    },
    "relationship": {
        "relationship_id": VARCHAR,
        "relationship_name": VARCHAR,
        "is_hierarchical": VARCHAR,
        "defines_ancestry": VARCHAR,
        "reverse_relationship_id": VARCHAR,
        "relationship_concept_id": INTEGER,
    },
    "concept_synonym": {
        "concept_id": INTEGER,
        "concept_synonym_name": VARCHAR,
        "language_concept_id": INTEGER,
    },
    "concept_ancestor": {
        "ancestor_concept_id": INTEGER,
        "descendant_concept_id": INTEGER,
        "min_levels_of_separation": INTEGER,
        "max_levels_of_separation": INTEGER,
    },
    "source_to_concept_map": {
        "source_code": VARCHAR,
        "source_concept_id": INTEGER,
        "source_vocabulary_id": VARCHAR,
        "source_code_description": VARCHAR,
        "target_concept_id": INTEGER,
        "target_vocabulary_id": VARCHAR,
        "valid_start_date": DATE,
        "valid_end_date": DATE,
        "invalid_reason": VARCHAR,
    },
    "drug_strength": {
        "drug_concept_id": INTEGER,
        "ingredient_concept_id": INTEGER,
        "amount_value": FLOAT,
        "amount_unit_concept_id": INTEGER,
        "numerator_value": FLOAT,
        "numerator_unit_concept_id": INTEGER,
        "denominator_value": FLOAT,
        "denominator_unit_concept_id": INTEGER,
        "box_size": INTEGER,
        "valid_start_date": DATE,
        "valid_end_date": DATE,
        "invalid_reason": VARCHAR,
    },
    # =====================================================================
    # COHORT TABLES
    # =====================================================================
    "cohort": {
        "cohort_definition_id": INTEGER,
        "subject_id": INTEGER,
        "cohort_start_date": DATE,
        "cohort_end_date": DATE,
    },
    "cohort_definition": {
        "cohort_definition_id": INTEGER,
        "cohort_definition_name": VARCHAR,
        "cohort_definition_description": VARCHAR,
        "definition_type_concept_id": INTEGER,
        "cohort_definition_syntax": VARCHAR,
        "subject_concept_id": INTEGER,
        "cohort_initiation_date": DATE,
    },
    # =====================================================================
    # LEGACY / EXTENSION TABLES
    # Not in v5.4 core, but retained because fastssv ships rules that
    # specifically detect their presence (legacy v5.3 holdover or optional
    # extension).
    # =====================================================================
    "attribute_definition": {
        # Removed in v5.4; carried forward for legacy detection.
        "attribute_definition_id": INTEGER,
        "attribute_name": VARCHAR,
        "attribute_description": VARCHAR,
        "attribute_type_concept_id": INTEGER,
        "attribute_syntax": VARCHAR,
    },
    "location_history": {
        # Optional v5.4 extension; ships with fastssv for sites that use it.
        "location_history_id": INTEGER,
        "location_id": INTEGER,
        "relationship_type_concept_id": INTEGER,
        "domain_id": VARCHAR,
        "entity_id": INTEGER,
        "start_date": DATE,
        "end_date": DATE,
    },
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_column_type(table: str, column: str) -> str | None:
    """Return the type-constant string for ``table.column``, or None.

    Names are matched case-insensitively against the canonical (lowercase)
    keys.
    """
    if not table or not column:
        return None
    return CDM_COLUMN_TYPES.get(table.lower(), {}).get(column.lower())


def are_types_compatible(type1: str | None, type2: str | None) -> bool:
    """True if two columns of the given types may safely be compared/joined.

    Unknown types (None) are treated as compatible — we don't fail when we
    don't know.
    """
    if type1 is None or type2 is None:
        return True
    if type1 == type2:
        return True
    # INTEGER and FLOAT are interchangeable for arithmetic/comparison
    if {type1, type2} <= {INTEGER, FLOAT}:
        return True
    # DATE and DATETIME are interchangeable for comparison
    if {type1, type2} <= {DATE, DATETIME}:
        return True
    return False


# Free-text source-value columns that the rules treat specially.
SOURCE_VALUE_COLUMNS: Set[str] = {
    "person_source_value",
    "gender_source_value",
    "race_source_value",
    "ethnicity_source_value",
    "visit_source_value",
    "admitted_from_source_value",
    "discharged_to_source_value",
    "visit_detail_source_value",
    "condition_source_value",
    "condition_status_source_value",
    "drug_source_value",
    "route_source_value",
    "dose_unit_source_value",
    "cause_source_value",
    "stop_reason",
    "sig",
    "lot_number",
}


# ---------------------------------------------------------------------------
# Derived column-name view (was cdm_columns.py before [Unreleased]).
# ---------------------------------------------------------------------------

CDM_COLUMNS: Dict[str, FrozenSet[str]] = {table: frozenset(cols.keys()) for table, cols in CDM_COLUMN_TYPES.items()}


def get_table_columns(table_name: str) -> FrozenSet[str]:
    """Return the column-name set for ``table_name`` (case-insensitive).

    Returns an empty frozenset when the table is unknown.
    """
    if not table_name:
        return frozenset()
    return CDM_COLUMNS.get(table_name.lower(), frozenset())


__all__ = [
    "CDM_COLUMN_TYPES",
    "CDM_COLUMNS",
    "get_column_type",
    "get_table_columns",
    "are_types_compatible",
    "SOURCE_VALUE_COLUMNS",
    "INTEGER",
    "VARCHAR",
    "DATE",
    "DATETIME",
    "TIMESTAMP",
    "FLOAT",
]

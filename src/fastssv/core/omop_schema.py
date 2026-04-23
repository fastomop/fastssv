"""OMOP CDM 5.4 Schema Definition.

Complete schema for OMOP Common Data Model version 5.4.
Used for SCHEMA layer validation of table/column references and join keys.
"""

from typing import Dict, List, Set
from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnDef:
    """Column definition with type and constraints."""
    name: str
    data_type: str
    primary_key: bool = False
    foreign_key: bool = False
    foreign_table: str = None
    foreign_column: str = None


# OMOP CDM 5.4 Schema Definition
OMOP_SCHEMA: Dict[str, List[ColumnDef]] = {
    # Clinical Data Tables
    "person": [
        ColumnDef("person_id", "integer", primary_key=True),
        ColumnDef("gender_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("year_of_birth", "integer"),
        ColumnDef("month_of_birth", "integer"),
        ColumnDef("day_of_birth", "integer"),
        ColumnDef("birth_datetime", "datetime"),
        ColumnDef("race_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("ethnicity_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("location_id", "integer", foreign_key=True, foreign_table="location", foreign_column="location_id"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("care_site_id", "integer", foreign_key=True, foreign_table="care_site", foreign_column="care_site_id"),
        ColumnDef("person_source_value", "varchar"),
        ColumnDef("gender_source_value", "varchar"),
        ColumnDef("gender_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("race_source_value", "varchar"),
        ColumnDef("race_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("ethnicity_source_value", "varchar"),
        ColumnDef("ethnicity_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "observation_period": [
        ColumnDef("observation_period_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("observation_period_start_date", "date"),
        ColumnDef("observation_period_end_date", "date"),
        ColumnDef("period_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "visit_occurrence": [
        ColumnDef("visit_occurrence_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("visit_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("visit_start_date", "date"),
        ColumnDef("visit_start_datetime", "datetime"),
        ColumnDef("visit_end_date", "date"),
        ColumnDef("visit_end_datetime", "datetime"),
        ColumnDef("visit_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("care_site_id", "integer", foreign_key=True, foreign_table="care_site", foreign_column="care_site_id"),
        ColumnDef("visit_source_value", "varchar"),
        ColumnDef("visit_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("admitted_from_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("admitted_from_source_value", "varchar"),
        ColumnDef("discharged_to_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("discharged_to_source_value", "varchar"),
        ColumnDef("preceding_visit_occurrence_id", "integer", foreign_key=True, foreign_table="visit_occurrence", foreign_column="visit_occurrence_id"),
    ],

    "condition_occurrence": [
        ColumnDef("condition_occurrence_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("condition_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("condition_start_date", "date"),
        ColumnDef("condition_start_datetime", "datetime"),
        ColumnDef("condition_end_date", "date"),
        ColumnDef("condition_end_datetime", "datetime"),
        ColumnDef("condition_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("condition_status_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("stop_reason", "varchar"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("visit_occurrence_id", "integer", foreign_key=True, foreign_table="visit_occurrence", foreign_column="visit_occurrence_id"),
        ColumnDef("visit_detail_id", "integer", foreign_key=True, foreign_table="visit_detail", foreign_column="visit_detail_id"),
        ColumnDef("condition_source_value", "varchar"),
        ColumnDef("condition_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("condition_status_source_value", "varchar"),
    ],

    "drug_exposure": [
        ColumnDef("drug_exposure_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("drug_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("drug_exposure_start_date", "date"),
        ColumnDef("drug_exposure_start_datetime", "datetime"),
        ColumnDef("drug_exposure_end_date", "date"),
        ColumnDef("drug_exposure_end_datetime", "datetime"),
        ColumnDef("verbatim_end_date", "date"),
        ColumnDef("drug_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("stop_reason", "varchar"),
        ColumnDef("refills", "integer"),
        ColumnDef("quantity", "numeric"),
        ColumnDef("days_supply", "integer"),
        ColumnDef("sig", "varchar"),
        ColumnDef("route_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("lot_number", "varchar"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("visit_occurrence_id", "integer", foreign_key=True, foreign_table="visit_occurrence", foreign_column="visit_occurrence_id"),
        ColumnDef("visit_detail_id", "integer", foreign_key=True, foreign_table="visit_detail", foreign_column="visit_detail_id"),
        ColumnDef("drug_source_value", "varchar"),
        ColumnDef("drug_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("route_source_value", "varchar"),
        ColumnDef("dose_unit_source_value", "varchar"),
    ],

    "procedure_occurrence": [
        ColumnDef("procedure_occurrence_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("procedure_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("procedure_date", "date"),
        ColumnDef("procedure_datetime", "datetime"),
        ColumnDef("procedure_end_date", "date"),
        ColumnDef("procedure_end_datetime", "datetime"),
        ColumnDef("procedure_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("modifier_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("quantity", "integer"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("visit_occurrence_id", "integer", foreign_key=True, foreign_table="visit_occurrence", foreign_column="visit_occurrence_id"),
        ColumnDef("visit_detail_id", "integer", foreign_key=True, foreign_table="visit_detail", foreign_column="visit_detail_id"),
        ColumnDef("procedure_source_value", "varchar"),
        ColumnDef("procedure_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("modifier_source_value", "varchar"),
    ],

    "measurement": [
        ColumnDef("measurement_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("measurement_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("measurement_date", "date"),
        ColumnDef("measurement_datetime", "datetime"),
        ColumnDef("measurement_time", "varchar"),
        ColumnDef("measurement_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("operator_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("value_as_number", "numeric"),
        ColumnDef("value_as_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("unit_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("range_low", "numeric"),
        ColumnDef("range_high", "numeric"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("visit_occurrence_id", "integer", foreign_key=True, foreign_table="visit_occurrence", foreign_column="visit_occurrence_id"),
        ColumnDef("visit_detail_id", "integer", foreign_key=True, foreign_table="visit_detail", foreign_column="visit_detail_id"),
        ColumnDef("measurement_source_value", "varchar"),
        ColumnDef("measurement_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("unit_source_value", "varchar"),
        ColumnDef("unit_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("value_source_value", "varchar"),
        ColumnDef("measurement_event_id", "integer"),
        ColumnDef("meas_event_field_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "observation": [
        ColumnDef("observation_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("observation_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("observation_date", "date"),
        ColumnDef("observation_datetime", "datetime"),
        ColumnDef("observation_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("value_as_number", "numeric"),
        ColumnDef("value_as_string", "varchar"),
        ColumnDef("value_as_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("qualifier_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("unit_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("visit_occurrence_id", "integer", foreign_key=True, foreign_table="visit_occurrence", foreign_column="visit_occurrence_id"),
        ColumnDef("visit_detail_id", "integer", foreign_key=True, foreign_table="visit_detail", foreign_column="visit_detail_id"),
        ColumnDef("observation_source_value", "varchar"),
        ColumnDef("observation_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("unit_source_value", "varchar"),
        ColumnDef("qualifier_source_value", "varchar"),
        ColumnDef("observation_event_id", "integer"),
        ColumnDef("obs_event_field_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("value_as_datetime", "datetime"),
    ],

    "death": [
        ColumnDef("person_id", "integer", primary_key=True, foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("death_date", "date"),
        ColumnDef("death_datetime", "datetime"),
        ColumnDef("death_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("cause_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("cause_source_value", "varchar"),
        ColumnDef("cause_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    # Vocabulary Tables
    "concept": [
        ColumnDef("concept_id", "integer", primary_key=True),
        ColumnDef("concept_name", "varchar"),
        ColumnDef("domain_id", "varchar", foreign_key=True, foreign_table="domain", foreign_column="domain_id"),
        ColumnDef("vocabulary_id", "varchar", foreign_key=True, foreign_table="vocabulary", foreign_column="vocabulary_id"),
        ColumnDef("concept_class_id", "varchar", foreign_key=True, foreign_table="concept_class", foreign_column="concept_class_id"),
        ColumnDef("standard_concept", "varchar"),
        ColumnDef("concept_code", "varchar"),
        ColumnDef("valid_start_date", "date"),
        ColumnDef("valid_end_date", "date"),
        ColumnDef("invalid_reason", "varchar"),
    ],

    "vocabulary": [
        ColumnDef("vocabulary_id", "varchar", primary_key=True),
        ColumnDef("vocabulary_name", "varchar"),
        ColumnDef("vocabulary_reference", "varchar"),
        ColumnDef("vocabulary_version", "varchar"),
        ColumnDef("vocabulary_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "domain": [
        ColumnDef("domain_id", "varchar", primary_key=True),
        ColumnDef("domain_name", "varchar"),
        ColumnDef("domain_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "concept_class": [
        ColumnDef("concept_class_id", "varchar", primary_key=True),
        ColumnDef("concept_class_name", "varchar"),
        ColumnDef("concept_class_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "concept_relationship": [
        ColumnDef("concept_id_1", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("concept_id_2", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("relationship_id", "varchar", foreign_key=True, foreign_table="relationship", foreign_column="relationship_id"),
        ColumnDef("valid_start_date", "date"),
        ColumnDef("valid_end_date", "date"),
        ColumnDef("invalid_reason", "varchar"),
    ],

    "relationship": [
        ColumnDef("relationship_id", "varchar", primary_key=True),
        ColumnDef("relationship_name", "varchar"),
        ColumnDef("is_hierarchical", "varchar"),
        ColumnDef("defines_ancestry", "varchar"),
        ColumnDef("reverse_relationship_id", "varchar", foreign_key=True, foreign_table="relationship", foreign_column="relationship_id"),
        ColumnDef("relationship_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "concept_synonym": [
        ColumnDef("concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("concept_synonym_name", "varchar"),
        ColumnDef("language_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "concept_ancestor": [
        ColumnDef("ancestor_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("descendant_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("min_levels_of_separation", "integer"),
        ColumnDef("max_levels_of_separation", "integer"),
    ],

    "source_to_concept_map": [
        ColumnDef("source_code", "varchar"),
        ColumnDef("source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("source_vocabulary_id", "varchar", foreign_key=True, foreign_table="vocabulary", foreign_column="vocabulary_id"),
        ColumnDef("source_code_description", "varchar"),
        ColumnDef("target_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("target_vocabulary_id", "varchar", foreign_key=True, foreign_table="vocabulary", foreign_column="vocabulary_id"),
        ColumnDef("valid_start_date", "date"),
        ColumnDef("valid_end_date", "date"),
        ColumnDef("invalid_reason", "varchar"),
    ],

    "drug_strength": [
        ColumnDef("drug_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("ingredient_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("amount_value", "numeric"),
        ColumnDef("amount_unit_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("numerator_value", "numeric"),
        ColumnDef("numerator_unit_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("denominator_value", "numeric"),
        ColumnDef("denominator_unit_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("box_size", "integer"),
        ColumnDef("valid_start_date", "date"),
        ColumnDef("valid_end_date", "date"),
        ColumnDef("invalid_reason", "varchar"),
    ],

    # Health System Tables
    "location": [
        ColumnDef("location_id", "integer", primary_key=True),
        ColumnDef("address_1", "varchar"),
        ColumnDef("address_2", "varchar"),
        ColumnDef("city", "varchar"),
        ColumnDef("state", "varchar"),
        ColumnDef("zip", "varchar"),
        ColumnDef("county", "varchar"),
        ColumnDef("location_source_value", "varchar"),
        ColumnDef("country_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("country_source_value", "varchar"),
        ColumnDef("latitude", "numeric"),
        ColumnDef("longitude", "numeric"),
    ],

    "care_site": [
        ColumnDef("care_site_id", "integer", primary_key=True),
        ColumnDef("care_site_name", "varchar"),
        ColumnDef("place_of_service_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("location_id", "integer", foreign_key=True, foreign_table="location", foreign_column="location_id"),
        ColumnDef("care_site_source_value", "varchar"),
        ColumnDef("place_of_service_source_value", "varchar"),
    ],

    "provider": [
        ColumnDef("provider_id", "integer", primary_key=True),
        ColumnDef("provider_name", "varchar"),
        ColumnDef("npi", "varchar"),
        ColumnDef("dea", "varchar"),
        ColumnDef("specialty_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("care_site_id", "integer", foreign_key=True, foreign_table="care_site", foreign_column="care_site_id"),
        ColumnDef("year_of_birth", "integer"),
        ColumnDef("gender_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("provider_source_value", "varchar"),
        ColumnDef("specialty_source_value", "varchar"),
        ColumnDef("specialty_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("gender_source_value", "varchar"),
        ColumnDef("gender_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    # Derived Tables (Era tables)
    "condition_era": [
        ColumnDef("condition_era_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("condition_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("condition_era_start_date", "date"),
        ColumnDef("condition_era_end_date", "date"),
        ColumnDef("condition_occurrence_count", "integer"),
    ],

    "drug_era": [
        ColumnDef("drug_era_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("drug_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("drug_era_start_date", "date"),
        ColumnDef("drug_era_end_date", "date"),
        ColumnDef("drug_exposure_count", "integer"),
        ColumnDef("gap_days", "integer"),
    ],

    "dose_era": [
        ColumnDef("dose_era_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("drug_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("unit_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("dose_value", "numeric"),
        ColumnDef("dose_era_start_date", "date"),
        ColumnDef("dose_era_end_date", "date"),
    ],

    "visit_detail": [
        ColumnDef("visit_detail_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("visit_detail_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("visit_detail_start_date", "date"),
        ColumnDef("visit_detail_start_datetime", "datetime"),
        ColumnDef("visit_detail_end_date", "date"),
        ColumnDef("visit_detail_end_datetime", "datetime"),
        ColumnDef("visit_detail_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("care_site_id", "integer", foreign_key=True, foreign_table="care_site", foreign_column="care_site_id"),
        ColumnDef("visit_detail_source_value", "varchar"),
        ColumnDef("visit_detail_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("admitted_from_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("admitted_from_source_value", "varchar"),
        ColumnDef("discharged_to_source_value", "varchar"),
        ColumnDef("discharged_to_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("preceding_visit_detail_id", "integer", foreign_key=True, foreign_table="visit_detail", foreign_column="visit_detail_id"),
        ColumnDef("parent_visit_detail_id", "integer", foreign_key=True, foreign_table="visit_detail", foreign_column="visit_detail_id"),
        ColumnDef("visit_occurrence_id", "integer", foreign_key=True, foreign_table="visit_occurrence", foreign_column="visit_occurrence_id"),
    ],

    "device_exposure": [
        ColumnDef("device_exposure_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("device_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("device_exposure_start_date", "date"),
        ColumnDef("device_exposure_start_datetime", "datetime"),
        ColumnDef("device_exposure_end_date", "date"),
        ColumnDef("device_exposure_end_datetime", "datetime"),
        ColumnDef("device_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("unique_device_id", "varchar"),
        ColumnDef("production_id", "varchar"),
        ColumnDef("quantity", "integer"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("visit_occurrence_id", "integer", foreign_key=True, foreign_table="visit_occurrence", foreign_column="visit_occurrence_id"),
        ColumnDef("visit_detail_id", "integer", foreign_key=True, foreign_table="visit_detail", foreign_column="visit_detail_id"),
        ColumnDef("device_source_value", "varchar"),
        ColumnDef("device_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("unit_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("unit_source_value", "varchar"),
        ColumnDef("unit_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "specimen": [
        ColumnDef("specimen_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("specimen_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("specimen_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("specimen_date", "date"),
        ColumnDef("specimen_datetime", "datetime"),
        ColumnDef("quantity", "numeric"),
        ColumnDef("unit_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("anatomic_site_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("disease_status_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("specimen_source_id", "varchar"),
        ColumnDef("specimen_source_value", "varchar"),
        ColumnDef("unit_source_value", "varchar"),
        ColumnDef("anatomic_site_source_value", "varchar"),
        ColumnDef("disease_status_source_value", "varchar"),
    ],

    "note": [
        ColumnDef("note_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("note_date", "date"),
        ColumnDef("note_datetime", "datetime"),
        ColumnDef("note_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("note_class_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("note_title", "varchar"),
        ColumnDef("note_text", "text"),
        ColumnDef("encoding_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("language_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("provider_id", "integer", foreign_key=True, foreign_table="provider", foreign_column="provider_id"),
        ColumnDef("visit_occurrence_id", "integer", foreign_key=True, foreign_table="visit_occurrence", foreign_column="visit_occurrence_id"),
        ColumnDef("visit_detail_id", "integer", foreign_key=True, foreign_table="visit_detail", foreign_column="visit_detail_id"),
        ColumnDef("note_source_value", "varchar"),
        ColumnDef("note_event_id", "integer"),
        ColumnDef("note_event_field_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "note_nlp": [
        ColumnDef("note_nlp_id", "integer", primary_key=True),
        ColumnDef("note_id", "integer", foreign_key=True, foreign_table="note", foreign_column="note_id"),
        ColumnDef("section_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("snippet", "varchar"),
        ColumnDef("offset", "varchar"),
        ColumnDef("lexical_variant", "varchar"),
        ColumnDef("note_nlp_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("note_nlp_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("nlp_system", "varchar"),
        ColumnDef("nlp_date", "date"),
        ColumnDef("nlp_datetime", "datetime"),
        ColumnDef("term_exists", "varchar"),
        ColumnDef("term_temporal", "varchar"),
        ColumnDef("term_modifiers", "varchar"),
    ],

    "episode": [
        ColumnDef("episode_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("episode_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("episode_start_date", "date"),
        ColumnDef("episode_start_datetime", "datetime"),
        ColumnDef("episode_end_date", "date"),
        ColumnDef("episode_end_datetime", "datetime"),
        ColumnDef("episode_parent_id", "integer", foreign_key=True, foreign_table="episode", foreign_column="episode_id"),
        ColumnDef("episode_number", "integer"),
        ColumnDef("episode_object_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("episode_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("episode_source_value", "varchar"),
        ColumnDef("episode_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "episode_event": [
        ColumnDef("episode_id", "integer", foreign_key=True, foreign_table="episode", foreign_column="episode_id"),
        ColumnDef("event_id", "integer"),
        ColumnDef("episode_event_field_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "fact_relationship": [
        ColumnDef("domain_concept_id_1", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("fact_id_1", "integer"),
        ColumnDef("domain_concept_id_2", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("fact_id_2", "integer"),
        ColumnDef("relationship_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    "payer_plan_period": [
        ColumnDef("payer_plan_period_id", "integer", primary_key=True),
        ColumnDef("person_id", "integer", foreign_key=True, foreign_table="person", foreign_column="person_id"),
        ColumnDef("payer_plan_period_start_date", "date"),
        ColumnDef("payer_plan_period_end_date", "date"),
        ColumnDef("payer_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("payer_source_value", "varchar"),
        ColumnDef("payer_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("plan_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("plan_source_value", "varchar"),
        ColumnDef("plan_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("sponsor_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("sponsor_source_value", "varchar"),
        ColumnDef("sponsor_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("family_source_value", "varchar"),
        ColumnDef("stop_reason_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("stop_reason_source_value", "varchar"),
        ColumnDef("stop_reason_source_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
    ],

    # Health Economics
    "cost": [
        ColumnDef("cost_id", "integer", primary_key=True),
        ColumnDef("cost_event_id", "integer"),
        ColumnDef("cost_domain_id", "varchar"),
        ColumnDef("cost_type_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("currency_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("total_charge", "numeric"),
        ColumnDef("total_cost", "numeric"),
        ColumnDef("total_paid", "numeric"),
        ColumnDef("paid_by_payer", "numeric"),
        ColumnDef("paid_by_patient", "numeric"),
        ColumnDef("paid_patient_copay", "numeric"),
        ColumnDef("paid_patient_coinsurance", "numeric"),
        ColumnDef("paid_patient_deductible", "numeric"),
        ColumnDef("paid_by_primary", "numeric"),
        ColumnDef("paid_ingredient_cost", "numeric"),
        ColumnDef("paid_dispensing_fee", "numeric"),
        ColumnDef("payer_plan_period_id", "integer"),
        ColumnDef("amount_allowed", "numeric"),
        ColumnDef("revenue_code_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("drg_concept_id", "integer", foreign_key=True, foreign_table="concept", foreign_column="concept_id"),
        ColumnDef("revenue_code_source_value", "varchar"),
        ColumnDef("drg_source_value", "varchar"),
    ],
}


def get_table_columns(table_name: str) -> Set[str]:
    """Get all column names for a table (case-insensitive)."""
    table_lower = table_name.lower()
    if table_lower in OMOP_SCHEMA:
        return {col.name.lower() for col in OMOP_SCHEMA[table_lower]}
    return set()


def is_valid_table(table_name: str) -> bool:
    """Check if table exists in OMOP CDM."""
    return table_name.lower() in OMOP_SCHEMA


def is_valid_column(table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    columns = get_table_columns(table_name)
    return column_name.lower() in columns


def get_all_tables() -> Set[str]:
    """Get all OMOP CDM table names."""
    return set(OMOP_SCHEMA.keys())


__all__ = [
    "OMOP_SCHEMA",
    "ColumnDef",
    "get_table_columns",
    "is_valid_table",
    "is_valid_column",
    "get_all_tables",
]

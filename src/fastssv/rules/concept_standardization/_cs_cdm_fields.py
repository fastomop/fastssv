"""Concept-id fields of the OMOP CDM v5.4, generated from the field-level specification
(OHDSI/CommonDataModel inst/csv/OMOP_CDMv5.4_Field_Level.csv; the cdm-schema split from the Data Quality Dashboard
copy of the same file, which adds a databaseSchema column). Do not edit by hand."""

from typing import Dict, FrozenSet, Tuple

Field = Tuple[str, str]

# The fkDomain the CDM CSV gives a field (single-domain values only), plus four fields the CSV leaves without one
# but whose domain the specification text of the field states:
#   cdm_source.cdm_version_concept_id   Metadata             etlConventions: the concepts of vocabulary 'CDM', class
#                                                            'CDM', which are Metadata concepts
#   drug_strength.drug_concept_id       Drug                 userGuidance: "the Branded Drug or Clinical Drug Product"
#   location.country_concept_id         Geography            userGuidance: "Values should conform to the Geography
#                                                            domain"
#   measurement.operator_concept_id     Meas Value Operator  etlConventions: "these concepts belong to the 'Meas Value
#                                                            Operator' domain"
FK_DOMAINS: Dict[Field, str] = {
    ("cdm_source", "cdm_version_concept_id"): "Metadata",
    ("condition_era", "condition_concept_id"): "Condition",
    ("condition_occurrence", "condition_concept_id"): "Condition",
    ("condition_occurrence", "condition_status_concept_id"): "Condition Status",
    ("condition_occurrence", "condition_type_concept_id"): "Type Concept",
    ("death", "death_type_concept_id"): "Type Concept",
    ("device_exposure", "device_concept_id"): "Device",
    ("device_exposure", "device_type_concept_id"): "Type Concept",
    ("device_exposure", "unit_concept_id"): "Unit",
    ("dose_era", "drug_concept_id"): "Drug",
    ("dose_era", "unit_concept_id"): "Unit",
    ("drug_era", "drug_concept_id"): "Drug",
    ("drug_exposure", "drug_concept_id"): "Drug",
    ("drug_exposure", "drug_type_concept_id"): "Type Concept",
    ("drug_exposure", "route_concept_id"): "Route",
    ("drug_strength", "drug_concept_id"): "Drug",
    ("episode", "episode_concept_id"): "Episode",
    ("episode", "episode_type_concept_id"): "Type Concept",
    ("episode_event", "episode_event_field_concept_id"): "Metadata",
    ("location", "country_concept_id"): "Geography",
    ("measurement", "measurement_concept_id"): "Measurement",
    ("measurement", "measurement_type_concept_id"): "Type Concept",
    ("measurement", "operator_concept_id"): "Meas Value Operator",
    ("measurement", "unit_concept_id"): "Unit",
    ("note", "note_type_concept_id"): "Type Concept",
    ("observation", "observation_type_concept_id"): "Type Concept",
    ("observation", "unit_concept_id"): "Unit",
    ("observation_period", "period_type_concept_id"): "Type Concept",
    ("person", "ethnicity_concept_id"): "Ethnicity",
    ("person", "gender_concept_id"): "Gender",
    ("person", "race_concept_id"): "Race",
    ("procedure_occurrence", "procedure_concept_id"): "Procedure",
    ("procedure_occurrence", "procedure_type_concept_id"): "Type Concept",
    ("provider", "gender_concept_id"): "Gender",
    ("specimen", "specimen_type_concept_id"): "Type Concept",
    ("visit_detail", "admitted_from_concept_id"): "Visit",
    ("visit_detail", "discharged_to_concept_id"): "Visit",
    ("visit_detail", "visit_detail_concept_id"): "Visit",
    ("visit_detail", "visit_detail_type_concept_id"): "Type Concept",
    ("visit_occurrence", "admitted_from_concept_id"): "Visit",
    ("visit_occurrence", "discharged_to_concept_id"): "Visit",
    ("visit_occurrence", "visit_concept_id"): "Visit",
    ("visit_occurrence", "visit_type_concept_id"): "Type Concept",
}

# Every *_source_concept_id field in the cdm schema.
SOURCE_CONCEPT_FIELDS: FrozenSet[Field] = frozenset(
    [
        ("condition_occurrence", "condition_source_concept_id"),
        ("death", "cause_source_concept_id"),
        ("device_exposure", "device_source_concept_id"),
        ("device_exposure", "unit_source_concept_id"),
        ("drug_exposure", "drug_source_concept_id"),
        ("episode", "episode_source_concept_id"),
        ("measurement", "measurement_source_concept_id"),
        ("measurement", "unit_source_concept_id"),
        ("note_nlp", "note_nlp_source_concept_id"),
        ("observation", "observation_source_concept_id"),
        ("payer_plan_period", "payer_source_concept_id"),
        ("payer_plan_period", "plan_source_concept_id"),
        ("payer_plan_period", "sponsor_source_concept_id"),
        ("payer_plan_period", "stop_reason_source_concept_id"),
        ("person", "ethnicity_source_concept_id"),
        ("person", "gender_source_concept_id"),
        ("person", "race_source_concept_id"),
        ("procedure_occurrence", "procedure_source_concept_id"),
        ("provider", "gender_source_concept_id"),
        ("provider", "specialty_source_concept_id"),
        ("visit_detail", "visit_detail_source_concept_id"),
        ("visit_occurrence", "visit_source_concept_id"),
    ]
)

# Concept foreign keys of the cdm schema that hold the record's own concepts: not *_source_concept_id and not
# *_type_concept_id.
EVENT_CONCEPT_FIELDS: FrozenSet[Field] = frozenset(
    [
        ("care_site", "place_of_service_concept_id"),
        ("cdm_source", "cdm_version_concept_id"),
        ("condition_era", "condition_concept_id"),
        ("condition_occurrence", "condition_concept_id"),
        ("condition_occurrence", "condition_status_concept_id"),
        ("cost", "currency_concept_id"),
        ("cost", "drg_concept_id"),
        ("cost", "revenue_code_concept_id"),
        ("death", "cause_concept_id"),
        ("device_exposure", "device_concept_id"),
        ("device_exposure", "unit_concept_id"),
        ("dose_era", "drug_concept_id"),
        ("dose_era", "unit_concept_id"),
        ("drug_era", "drug_concept_id"),
        ("drug_exposure", "drug_concept_id"),
        ("drug_exposure", "route_concept_id"),
        ("episode", "episode_concept_id"),
        ("episode", "episode_object_concept_id"),
        ("episode_event", "episode_event_field_concept_id"),
        ("fact_relationship", "domain_concept_id_1"),
        ("fact_relationship", "domain_concept_id_2"),
        ("fact_relationship", "relationship_concept_id"),
        ("location", "country_concept_id"),
        ("measurement", "meas_event_field_concept_id"),
        ("measurement", "measurement_concept_id"),
        ("measurement", "operator_concept_id"),
        ("measurement", "unit_concept_id"),
        ("measurement", "value_as_concept_id"),
        ("metadata", "metadata_concept_id"),
        ("metadata", "value_as_concept_id"),
        ("note", "encoding_concept_id"),
        ("note", "language_concept_id"),
        ("note", "note_class_concept_id"),
        ("note", "note_event_field_concept_id"),
        ("note_nlp", "note_nlp_concept_id"),
        ("note_nlp", "section_concept_id"),
        ("observation", "obs_event_field_concept_id"),
        ("observation", "observation_concept_id"),
        ("observation", "qualifier_concept_id"),
        ("observation", "unit_concept_id"),
        ("observation", "value_as_concept_id"),
        ("payer_plan_period", "payer_concept_id"),
        ("payer_plan_period", "plan_concept_id"),
        ("payer_plan_period", "sponsor_concept_id"),
        ("payer_plan_period", "stop_reason_concept_id"),
        ("person", "ethnicity_concept_id"),
        ("person", "gender_concept_id"),
        ("person", "race_concept_id"),
        ("procedure_occurrence", "modifier_concept_id"),
        ("procedure_occurrence", "procedure_concept_id"),
        ("provider", "gender_concept_id"),
        ("provider", "specialty_concept_id"),
        ("specimen", "anatomic_site_concept_id"),
        ("specimen", "disease_status_concept_id"),
        ("specimen", "specimen_concept_id"),
        ("specimen", "unit_concept_id"),
        ("visit_detail", "admitted_from_concept_id"),
        ("visit_detail", "discharged_to_concept_id"),
        ("visit_detail", "visit_detail_concept_id"),
        ("visit_occurrence", "admitted_from_concept_id"),
        ("visit_occurrence", "discharged_to_concept_id"),
        ("visit_occurrence", "visit_concept_id"),
    ]
)

# The record's own concept of each table: the concept field named after the table (condition_occurrence ->
# condition_concept_id, visit_detail -> visit_detail_concept_id).
PRIMARY_CONCEPT_FIELDS: FrozenSet[Field] = frozenset(
    [
        ("condition_era", "condition_concept_id"),
        ("condition_occurrence", "condition_concept_id"),
        ("device_exposure", "device_concept_id"),
        ("drug_era", "drug_concept_id"),
        ("drug_exposure", "drug_concept_id"),
        ("episode", "episode_concept_id"),
        ("measurement", "measurement_concept_id"),
        ("metadata", "metadata_concept_id"),
        ("note_nlp", "note_nlp_concept_id"),
        ("observation", "observation_concept_id"),
        ("payer_plan_period", "payer_concept_id"),
        ("procedure_occurrence", "procedure_concept_id"),
        ("specimen", "specimen_concept_id"),
        ("visit_detail", "visit_detail_concept_id"),
        ("visit_occurrence", "visit_concept_id"),
    ]
)

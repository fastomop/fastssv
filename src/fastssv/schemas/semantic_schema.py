"""
This defines which columns should contain standard vs source concepts:
┌─────────────────────────┬──────────────────────────────────────────────────────────────────────────────┐
│        Constant         │                                   Purpose                                    │
├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ SOURCE_VOCABS           │ Vocabulary names that are "source" (ICD10CM, CPT4, NDC, etc.)                │
├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ STANDARD_CONCEPT_FIELDS │ Columns that must contain standard concepts (e.g., condition_concept_id)     │
├─────────────────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ SOURCE_CONCEPT_FIELDS   │ Columns that may contain source concepts (e.g., condition_source_concept_id) │
└─────────────────────────┴──────────────────────────────────────────────────────────────────────────────┘
Purpose: Used by the standard_concept_enforcement rule to know which columns require standard_concept = 'S' enforcement. If a query uses condition_occurrence.condition_concept_id, it must
ensure standard concepts are used.
"""

SOURCE_VOCABS = {
    "ICD10CM", "ICD9CM", "ICD10PCS",
    "CPT4", "HCPCS", "NDC",
    "READ", "READCODE", "OPCS4",
}

# OMOP fields that should be STANDARD concept ids
STANDARD_CONCEPT_FIELDS = {
    # person
    ("person", "gender_concept_id"),
    ("person", "race_concept_id"),
    ("person", "ethnicity_concept_id"),

    # condition_occurrence
    ("condition_occurrence", "condition_concept_id"),
    ("condition_occurrence", "condition_type_concept_id"),
    ("condition_occurrence", "condition_status_concept_id"),

    # drug_exposure
    ("drug_exposure", "drug_concept_id"),
    ("drug_exposure", "drug_type_concept_id"),
    ("drug_exposure", "route_concept_id"),

    # procedure_occurrence
    ("procedure_occurrence", "procedure_concept_id"),
    ("procedure_occurrence", "procedure_type_concept_id"),
    ("procedure_occurrence", "modifier_concept_id"),

    # measurement
    ("measurement", "measurement_concept_id"),
    ("measurement", "measurement_type_concept_id"),
    ("measurement", "unit_concept_id"),
    ("measurement", "operator_concept_id"),
    ("measurement", "value_as_concept_id"),

    # observation
    ("observation", "observation_concept_id"),
    ("observation", "observation_type_concept_id"),
    ("observation", "qualifier_concept_id"),
    ("observation", "unit_concept_id"),
    ("observation", "value_as_concept_id"),

    # device_exposure
    ("device_exposure", "device_concept_id"),
    ("device_exposure", "device_type_concept_id"),

    # visit tables
    ("visit_occurrence", "visit_concept_id"),
    ("visit_occurrence", "visit_type_concept_id"),
    ("visit_occurrence", "admitted_from_concept_id"),
    ("visit_occurrence", "discharged_to_concept_id"),
    ("visit_detail", "visit_detail_concept_id"),
    ("visit_detail", "visit_detail_type_concept_id"),
    ("visit_detail", "admitted_from_concept_id"),
    ("visit_detail", "discharged_to_concept_id"),

    # death
    ("death", "cause_concept_id"),
    ("death", "death_type_concept_id"),

    # specimen
    ("specimen", "specimen_concept_id"),
    ("specimen", "specimen_type_concept_id"),
    ("specimen", "unit_concept_id"),
    ("specimen", "anatomic_site_concept_id"),
    ("specimen", "disease_status_concept_id"),

    # episode
    ("episode", "episode_concept_id"),
    ("episode", "episode_type_concept_id"),
    ("episode", "episode_object_concept_id"),

    # episode_event
    ("episode_event", "episode_event_field_concept_id"),

    # note
    ("note", "note_type_concept_id"),
    ("note", "note_class_concept_id"),
    ("note", "encoding_concept_id"),
    ("note", "language_concept_id"),

    # note_nlp
    ("note_nlp", "note_nlp_concept_id"),
    ("note_nlp", "section_concept_id"),

    # cost
    ("cost", "cost_type_concept_id"),
    ("cost", "currency_concept_id"),
    ("cost", "revenue_code_concept_id"),
    ("cost", "drg_concept_id"),

    # payer_plan_period
    ("payer_plan_period", "payer_concept_id"),
    ("payer_plan_period", "payer_source_concept_id"),
    ("payer_plan_period", "plan_concept_id"),
    ("payer_plan_period", "plan_source_concept_id"),
    ("payer_plan_period", "sponsor_concept_id"),
    ("payer_plan_period", "sponsor_source_concept_id"),
    ("payer_plan_period", "stop_reason_concept_id"),
    ("payer_plan_period", "stop_reason_source_concept_id"),

    # observation_period
    ("observation_period", "period_type_concept_id"),

    # drug_era
    ("drug_era", "drug_concept_id"),

    # condition_era
    ("condition_era", "condition_concept_id"),

    # dose_era
    ("dose_era", "drug_concept_id"),
    ("dose_era", "unit_concept_id"),
}

# OMOP fields that are explicitly SOURCE concept ids (allowed to be ICD10CM etc.)
SOURCE_CONCEPT_FIELDS = {
    # person
    ("person", "gender_source_concept_id"),
    ("person", "race_source_concept_id"),
    ("person", "ethnicity_source_concept_id"),

    # condition_occurrence
    ("condition_occurrence", "condition_source_concept_id"),

    # drug_exposure
    ("drug_exposure", "drug_source_concept_id"),
    ("drug_exposure", "route_source_concept_id"),

    # procedure_occurrence
    ("procedure_occurrence", "procedure_source_concept_id"),
    ("procedure_occurrence", "modifier_source_concept_id"),

    # measurement
    ("measurement", "measurement_source_concept_id"),
    ("measurement", "unit_source_concept_id"),

    # observation
    ("observation", "observation_source_concept_id"),
    ("observation", "qualifier_source_concept_id"),

    # device_exposure
    ("device_exposure", "device_source_concept_id"),

    # visit tables
    ("visit_occurrence", "visit_source_concept_id"),
    ("visit_occurrence", "admitted_from_source_concept_id"),
    ("visit_occurrence", "discharged_to_source_concept_id"),
    ("visit_detail", "visit_detail_source_concept_id"),
    ("visit_detail", "admitted_from_source_concept_id"),
    ("visit_detail", "discharged_to_source_concept_id"),

    # death
    ("death", "cause_source_concept_id"),

    # specimen
    ("specimen", "specimen_source_concept_id"),
    ("specimen", "unit_source_concept_id"),
    ("specimen", "anatomic_site_source_concept_id"),
    ("specimen", "disease_status_source_concept_id"),

    # episode
    ("episode", "episode_source_concept_id"),

    # note_nlp
    ("note_nlp", "note_nlp_source_concept_id"),
}

"""CDM facts used by the domain_specific rules. Generated from the OMOP CDM v5.4 field-level CSV (OHDSI Data Quality
Dashboard copy, which carries the plausibility thresholds) and the Athena vocabulary v5.0 27-AUG-25; do not edit by
hand."""

# Float fields of COST: the monetary amounts.
COST_AMOUNT_COLUMNS = frozenset(
    [
        "amount_allowed",
        "paid_by_patient",
        "paid_by_payer",
        "paid_by_primary",
        "paid_dispensing_fee",
        "paid_ingredient_cost",
        "paid_patient_coinsurance",
        "paid_patient_copay",
        "paid_patient_deductible",
        "total_charge",
        "total_cost",
        "total_paid",
    ]
)

# Event table -> (primary key, DOMAIN.domain_id of its cost rows). The tables are the ones the COST table description
# names plus condition_occurrence and specimen; the domain is the fkDomain of the table's own concept field, or the
# DOMAIN row named after the table where that field accepts any domain.
COST_EVENT_TABLES = {
    "condition_occurrence": ("condition_occurrence_id", "Condition"),
    "device_exposure": ("device_exposure_id", "Device"),
    "drug_exposure": ("drug_exposure_id", "Drug"),
    "measurement": ("measurement_id", "Measurement"),
    "observation": ("observation_id", "Observation"),
    "procedure_occurrence": ("procedure_occurrence_id", "Procedure"),
    "specimen": ("specimen_id", "Specimen"),
    "visit_detail": ("visit_detail_id", "Visit"),
    "visit_occurrence": ("visit_occurrence_id", "Visit"),
}

# Table -> (polymorphic key, the field concept that says which table the key points into).
EVENT_FIELD_KEYS = {
    "episode_event": ("event_id", "episode_event_field_concept_id"),
    "measurement": ("measurement_event_id", "meas_event_field_concept_id"),
    "note": ("note_event_id", "note_event_field_concept_id"),
    "observation": ("observation_event_id", "obs_event_field_concept_id"),
}

# Table -> (primary key, CDM 'Field' concepts named "<table>.<primary key>").
PRIMARY_KEY_FIELD_CONCEPTS = {
    "care_site": ("care_site_id", (1147195, 1147654)),
    "concept": ("concept_id", (1146945, 1147368)),
    "concept_class": ("concept_class_id", (1146963,)),
    "condition_era": ("condition_era_id", (1147295, 1147660)),
    "condition_occurrence": ("condition_occurrence_id", (1147127, 1147663)),
    "cost": ("cost_id", (1147344, 1147680)),
    "device_exposure": ("device_exposure_id", (1147115, 1147693)),
    "domain": ("domain_id", (1146960,)),
    "dose_era": ("dose_era_id", (1147288, 1147698)),
    "drug_era": ("drug_era_id", (1147281, 1147702)),
    "drug_exposure": ("drug_exposure_id", (1147094, 1147707)),
    "episode": ("episode_id", (756290, 798885)),
    "location": ("location_id", (1147187, 1147717)),
    "measurement": ("measurement_id", (1147138, 1147729)),
    "metadata": ("metadata_id", (756314,)),
    "note": ("note_id", (1147156, 1147739)),
    "note_nlp": ("note_nlp_id", (1147529, 1147589, 1147753)),
    "observation": ("observation_id", (1147165, 1147762)),
    "observation_period": ("observation_period_id", (1147044, 1147769)),
    "payer_plan_period": ("payer_plan_period_id", (1147214, 1147778)),
    "person": ("person_id", (1147026, 1147800)),
    "procedure_occurrence": ("procedure_occurrence_id", (1147082, 1147810)),
    "provider": ("provider_id", (1147201, 1147815)),
    "relationship": ("relationship_id", (1146972, 1147400)),
    "specimen": ("specimen_id", (1147049, 1147822)),
    "visit_detail": ("visit_detail_id", (1147624, 1147856)),
    "visit_occurrence": ("visit_occurrence_id", (1147070, 1147869)),
    "vocabulary": ("vocabulary_id", (1146955, 1147415)),
}

# Tables that hold a person key which is not their primary key: several rows per person are possible.
MANY_ROWS_PER_PERSON_TABLES = frozenset(
    [
        "cohort",
        "condition_era",
        "condition_occurrence",
        "device_exposure",
        "dose_era",
        "drug_era",
        "drug_exposure",
        "episode",
        "measurement",
        "note",
        "observation",
        "observation_period",
        "payer_plan_period",
        "procedure_occurrence",
        "specimen",
        "visit_detail",
        "visit_occurrence",
    ]
)

# drug_exposure column -> (plausibleValueLow, plausibleValueHigh, integer-valued); None means no bound.
DRUG_EXPOSURE_PLAUSIBLE_RANGE = {"days_supply": (1.0, 365.0, True), "quantity": (1e-07, None, False)}

# (table, concept field) -> the only concept class it may hold (fkClass of the CDM specification, DQD threshold 0).
FK_CONCEPT_CLASS = {("dose_era", "drug_concept_id"): "Ingredient", ("drug_era", "drug_concept_id"): "Ingredient"}

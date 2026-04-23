"""
Canonical OMOP CDM v5.4 schema graph for join-path validation.
This defines the OMOP Common Data Model v5.4 schema as a graph. It maps out:

- Primary keys for each table
- Foreign key relationships (edges) between tables

"condition_occurrence": {
    "primary_key": "condition_occurrence_id",
    "edges": {
        "person": ("person_id", "person_id"),           # FK to person table
        "concept": ("condition_concept_id", "concept_id"),  # FK to concept table
        ...
    },
},

Purpose: Used by the join_path_validation rule to verify that queries correctly JOIN clinical tables to vocabulary tables. For example, it knows that
condition_occurrence.condition_concept_id should join to concept.concept_id.
"""

# Each edge: THIS_TABLE.foreign_key -> TARGET_TABLE.primary_key
CDM_SCHEMA = {
    # =========================
    # CLINICAL TABLES
    # =========================

    "person": {
        "primary_key": "person_id",
        "edges": {
            "location": ("location_id", "location_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("gender_concept_id", "concept_id"),
            "concept_race": ("race_concept_id", "concept_id"),
            "concept_ethnicity": ("ethnicity_concept_id", "concept_id"),
        },
    },

    "observation_period": {
        "primary_key": "observation_period_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "concept": ("period_type_concept_id", "concept_id"),
        },
    },

    "visit_occurrence": {
        "primary_key": "visit_occurrence_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("visit_concept_id", "concept_id"),
            "concept_type": ("visit_type_concept_id", "concept_id"),
            "concept_source": ("visit_source_concept_id", "concept_id"),
            "visit_occurrence": ("preceding_visit_occurrence_id", "visit_occurrence_id"),
            "concept_discharge_to": ("discharged_to_concept_id", "concept_id"),
            "concept_admitting_source": ("admitting_source_concept_id", "concept_id"),
        },
    },

    "visit_detail": {
        "primary_key": "visit_detail_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "visit_occurrence": ("visit_occurrence_id", "visit_occurrence_id"),
            "visit_detail": ("preceding_visit_detail_id", "visit_detail_id"),
            "visit_detail_parent": ("visit_detail_parent_id", "visit_detail_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("visit_detail_concept_id", "concept_id"),
            "concept_type": ("visit_detail_type_concept_id", "concept_id"),
            "concept_source": ("visit_detail_source_concept_id", "concept_id"),
            "concept_admitting_source": ("admitting_source_concept_id", "concept_id"),
            "concept_discharge_to": ("discharged_to_concept_id", "concept_id"),
        },
    },

    "condition_occurrence": {
        "primary_key": "condition_occurrence_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "visit_occurrence": ("visit_occurrence_id", "visit_occurrence_id"),
            "visit_detail": ("visit_detail_id", "visit_detail_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("condition_concept_id", "concept_id"),
            "concept_type": ("condition_type_concept_id", "concept_id"),
            "concept_source": ("condition_source_concept_id", "concept_id"),
            "concept_status": ("condition_status_concept_id", "concept_id"),
        },
    },

    "drug_exposure": {
        "primary_key": "drug_exposure_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "visit_occurrence": ("visit_occurrence_id", "visit_occurrence_id"),
            "visit_detail": ("visit_detail_id", "visit_detail_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("drug_concept_id", "concept_id"),
            "concept_type": ("drug_type_concept_id", "concept_id"),
            "concept_source": ("drug_source_concept_id", "concept_id"),
            "concept_route": ("route_concept_id", "concept_id"),
        },
    },

    "procedure_occurrence": {
        "primary_key": "procedure_occurrence_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "visit_occurrence": ("visit_occurrence_id", "visit_occurrence_id"),
            "visit_detail": ("visit_detail_id", "visit_detail_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("procedure_concept_id", "concept_id"),
            "concept_type": ("procedure_type_concept_id", "concept_id"),
            "concept_source": ("procedure_source_concept_id", "concept_id"),
            "concept_modifier": ("modifier_concept_id", "concept_id"),
        },
    },

    "device_exposure": {
        "primary_key": "device_exposure_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "visit_occurrence": ("visit_occurrence_id", "visit_occurrence_id"),
            "visit_detail": ("visit_detail_id", "visit_detail_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("device_concept_id", "concept_id"),
            "concept_type": ("device_type_concept_id", "concept_id"),
            "concept_source": ("device_source_concept_id", "concept_id"),
        },
    },

    "measurement": {
        "primary_key": "measurement_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "visit_occurrence": ("visit_occurrence_id", "visit_occurrence_id"),
            "visit_detail": ("visit_detail_id", "visit_detail_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("measurement_concept_id", "concept_id"),
            "concept_type": ("measurement_type_concept_id", "concept_id"),
            "concept_source": ("measurement_source_concept_id", "concept_id"),
            "concept_operator": ("operator_concept_id", "concept_id"),
            "concept_unit": ("unit_concept_id", "concept_id"),
            "concept_value": ("value_as_concept_id", "concept_id"),
        },
    },

    "observation": {
        "primary_key": "observation_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "visit_occurrence": ("visit_occurrence_id", "visit_occurrence_id"),
            "visit_detail": ("visit_detail_id", "visit_detail_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("observation_concept_id", "concept_id"),
            "concept_type": ("observation_type_concept_id", "concept_id"),
            "concept_source": ("observation_source_concept_id", "concept_id"),
            "concept_value": ("value_as_concept_id", "concept_id"),
            "concept_qualifier": ("qualifier_concept_id", "concept_id"),
            "concept_unit": ("unit_concept_id", "concept_id"),
        },
    },

    "specimen": {
        "primary_key": "specimen_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "visit_occurrence": ("visit_occurrence_id", "visit_occurrence_id"),
            "provider": ("provider_id", "provider_id"),
            "concept": ("specimen_concept_id", "concept_id"),
            "concept_type": ("specimen_type_concept_id", "concept_id"),
            "concept_source": ("specimen_source_concept_id", "concept_id"),
            "concept_unit": ("unit_concept_id", "concept_id"),
        },
    },

    "death": {
        "primary_key": "person_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "concept": ("death_type_concept_id", "concept_id"),
            "concept_cause": ("cause_concept_id", "concept_id"),
            "concept_source": ("cause_source_concept_id", "concept_id"),
        },
    },

    "note": {
        "primary_key": "note_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "visit_occurrence": ("visit_occurrence_id", "visit_occurrence_id"),
            "visit_detail": ("visit_detail_id", "visit_detail_id"),
            "provider": ("provider_id", "provider_id"),
            "care_site": ("care_site_id", "care_site_id"),
            "concept_type": ("note_type_concept_id", "concept_id"),
            "concept_class": ("note_class_concept_id", "concept_id"),
            "concept_encoding": ("encoding_concept_id", "concept_id"),
            "concept_language": ("language_concept_id", "concept_id"),
        },
    },

    "note_nlp": {
        "primary_key": "note_nlp_id",
        "edges": {
            "note": ("note_id", "note_id"),
            "concept": ("note_nlp_concept_id", "concept_id"),
            "concept_source": ("note_nlp_source_concept_id", "concept_id"),
            "concept_section": ("section_concept_id", "concept_id"),
            "concept_term": ("term_concept_id", "concept_id"),
        },
    },

    "payer_plan_period": {
        "primary_key": "payer_plan_period_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "concept": ("payer_concept_id", "concept_id"),
            "concept_plan": ("plan_concept_id", "concept_id"),
            "concept_sponsor": ("sponsor_concept_id", "concept_id"),
            "concept_stop_reason": ("stop_reason_concept_id", "concept_id"),
        },
    },

    "cost": {
        "primary_key": "cost_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "concept": ("cost_type_concept_id", "concept_id"),
            "concept_currency": ("currency_concept_id", "concept_id"),
            "concept_event_field": ("cost_event_field_concept_id", "concept_id"),
        },
    },

    # =========================
    # ERAS
    # =========================

    "condition_era": {
        "primary_key": "condition_era_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "concept": ("condition_concept_id", "concept_id"),
        },
    },

    "drug_era": {
        "primary_key": "drug_era_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "concept": ("drug_concept_id", "concept_id"),
        },
    },

    "dose_era": {
        "primary_key": "dose_era_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "concept": ("drug_concept_id", "concept_id"),
            "concept_unit": ("unit_concept_id", "concept_id"),
        },
    },

    # =========================
    # EPISODE TABLES
    # =========================

    "episode": {
        "primary_key": "episode_id",
        "edges": {
            "person": ("person_id", "person_id"),
            "concept": ("episode_concept_id", "concept_id"),
            "concept_type": ("episode_type_concept_id", "concept_id"),
            "concept_source": ("episode_source_concept_id", "concept_id"),
        },
    },

    "episode_event": {
        "primary_key": None,
        "edges": {
            "episode": ("episode_id", "episode_id"),
            "concept": ("event_field_concept_id", "concept_id"),
        },
    },

    # =========================
    # FACT_RELATIONSHIP
    # =========================

    "fact_relationship": {
        "primary_key": None,
        "edges": {
            "concept": ("relationship_concept_id", "concept_id"),
        },
    },

    # =========================
    # ORGANIZATION TABLES
    # =========================

    "location": {
        "primary_key": "location_id",
        "edges": {
            "concept": ("country_concept_id", "concept_id"),
        },
    },

    "care_site": {
        "primary_key": "care_site_id",
        "edges": {
            "location": ("location_id", "location_id"),
            "concept": ("place_of_service_concept_id", "concept_id"),
        },
    },

    "provider": {
        "primary_key": "provider_id",
        "edges": {
            "care_site": ("care_site_id", "care_site_id"),
            "concept": ("specialty_concept_id", "concept_id"),
            "concept_gender": ("gender_concept_id", "concept_id"),
        },
    },

    # =========================
    # VOCABULARY TABLES
    # =========================

    "concept": {
        "primary_key": "concept_id",
        "edges": {
            "domain": ("domain_id", "domain_id"),
            "vocabulary": ("vocabulary_id", "vocabulary_id"),
            "concept_class": ("concept_class_id", "concept_class_id"),
        },
    },

    "vocabulary": {
        "primary_key": "vocabulary_id",
        "edges": {},
    },

    "domain": {
        "primary_key": "domain_id",
        "edges": {},
    },

    "concept_class": {
        "primary_key": "concept_class_id",
        "edges": {},
    },

    "relationship": {
        "primary_key": "relationship_id",
        "edges": {},
    },

    "concept_relationship": {
        "primary_key": None,
        "edges": {
            "concept": ("concept_id_1", "concept_id"),
            "concept_2": ("concept_id_2", "concept_id"),
            "relationship": ("relationship_id", "relationship_id"),
        },
    },

    "concept_ancestor": {
        "primary_key": None,
        "edges": {
            "concept": ("ancestor_concept_id", "concept_id"),
            "concept_descendant": ("descendant_concept_id", "concept_id"),
        },
    },

    "concept_synonym": {
        "primary_key": None,
        "edges": {
            "concept": ("concept_id", "concept_id"),
            "concept_language": ("language_concept_id", "concept_id"),
        },
    },

    "drug_strength": {
        "primary_key": None,
        "edges": {
            "concept": ("drug_concept_id", "concept_id"),
            "concept_ingredient": ("ingredient_concept_id", "concept_id"),
            "concept_amount_unit": ("amount_unit_concept_id", "concept_id"),
            "concept_numerator_unit": ("numerator_unit_concept_id", "concept_id"),
            "concept_denominator_unit": ("denominator_unit_concept_id", "concept_id"),
        },
    },

    # =========================
    # METADATA TABLES
    # =========================

    "cdm_source": {
        "primary_key": None,
        "edges": {
            "concept": ("cdm_version_concept_id", "concept_id"),
            "concept_vocab_version": ("vocabulary_version_concept_id", "concept_id"),
        },
    },

    "metadata": {
        "primary_key": None,
        "edges": {},
    },

    # =========================
    # COHORT TABLES
    # =========================

    "cohort_definition": {
        "primary_key": "cohort_definition_id",
        "edges": {},
    },

    "cohort": {
        "primary_key": None,
        "edges": {
            "cohort_definition": ("cohort_definition_id", "cohort_definition_id"),
            "person": ("subject_id", "person_id"),
        },
    },

    # =========================
    # OPTIONAL CDM v5.4 TABLE
    # =========================

    "attribute_definition": {
        "primary_key": "attribute_definition_id",
        "edges": {},
    },
}

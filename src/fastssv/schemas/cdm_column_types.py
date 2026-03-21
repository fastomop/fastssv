"""
OMOP Common Data Model v5.4 - Column Data Types

This module defines column data types for OMOP CDM v5.4 tables.
Used by type validation to ensure queries don't mix incompatible types.

Source: https://github.com/OHDSI/CommonDataModel/blob/main/inst/ddl/5.4/
"""

from typing import Dict, Set

# Simplified type categories for validation
# We don't need exact precision, just broad compatibility
INTEGER = "integer"
VARCHAR = "varchar"
DATE = "date"
DATETIME = "datetime"
TIMESTAMP = "datetime"  # Alias for DATETIME
FLOAT = "float"

# Column type definitions: "table_name": {"column_name": type}
CDM_COLUMN_TYPES: Dict[str, Dict[str, str]] = {
    # ===========================
    # STANDARDIZED CLINICAL DATA
    # ===========================

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

    "condition_era": {
        "condition_era_id": INTEGER,
        "person_id": INTEGER,
        "condition_concept_id": INTEGER,
        "condition_era_start_date": DATE,
        "condition_era_end_date": DATE,
        "condition_occurrence_count": INTEGER,
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

    "death": {
        "person_id": INTEGER,
        "death_date": DATE,
        "death_datetime": DATETIME,
        "death_type_concept_id": INTEGER,
        "cause_concept_id": INTEGER,
        "cause_source_value": VARCHAR,
        "cause_source_concept_id": INTEGER,
    },

    "cohort": {
        "cohort_definition_id": INTEGER,
        "subject_id": INTEGER,
        "cohort_start_date": DATE,
        "cohort_end_date": DATE,
    },

    # ===========================
    # STANDARDIZED VOCABULARIES
    # ===========================

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
}


def get_column_type(table: str, column: str) -> str | None:
    """Get the data type of a column in a table.

    Args:
        table: Table name (normalized/lowercase)
        column: Column name (normalized/lowercase)

    Returns:
        Data type string (integer, varchar, date, datetime, float) or None if not found
    """
    table = table.lower() if table else ""
    column = column.lower() if column else ""

    if table not in CDM_COLUMN_TYPES:
        return None

    return CDM_COLUMN_TYPES[table].get(column)


def are_types_compatible(type1: str | None, type2: str | None) -> bool:
    """Check if two data types are compatible for comparison/join.

    Args:
        type1: First data type
        type2: Second data type

    Returns:
        True if types are compatible, False otherwise
    """
    if type1 is None or type2 is None:
        return True  # Unknown types - skip validation

    # Exact match
    if type1 == type2:
        return True

    # Integer and float are compatible
    if {type1, type2} <= {INTEGER, FLOAT}:
        return True

    # Date and datetime are compatible
    if {type1, type2} <= {DATE, DATETIME}:
        return True

    # Everything else is incompatible
    return False


# Columns that are commonly source values (VARCHAR)
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


__all__ = [
    "CDM_COLUMN_TYPES",
    "get_column_type",
    "are_types_compatible",
    "SOURCE_VALUE_COLUMNS",
    "INTEGER",
    "VARCHAR",
    "DATE",
    "DATETIME",
    "TIMESTAMP",
    "FLOAT",
]

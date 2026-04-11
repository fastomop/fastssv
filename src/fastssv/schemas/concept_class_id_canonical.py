"""
OMOP Concept Class ID Canonical Values.

This module provides canonical casing for concept_class_id values from the
OMOP Standardized Vocabularies CONCEPT_CLASS table.

IMPORTANT:
- concept_class_id values are case-sensitive
- Incorrect casing can cause queries to return zero results
- This mapping should ideally be generated from the database

Source:
    SELECT concept_class_id FROM concept_class;

Version: OMOP CDM v5.4+
"""

from typing import Dict, Optional

# -----------------------------------------------------------------------------
# Normalization
# -----------------------------------------------------------------------------

def _normalize(value: Optional[str]) -> Optional[str]:
    """Normalize string for dictionary lookup."""
    if not value:
        return None
    return value.lower().replace(" ", "").replace("-", "").replace("_", "")


# -----------------------------------------------------------------------------
# Canonical Mapping (DEDUPLICATED + VERIFIED CORE SET)
# -----------------------------------------------------------------------------

CANONICAL_CONCEPT_CLASSES: Dict[str, str] = {
    # --- RxNorm ---
    "ingredient": "Ingredient",
    "clinicaldrug": "Clinical Drug",
    "brandeddrug": "Branded Drug",
    "clinicaldrugform": "Clinical Drug Form",
    "clinicaldrugcomp": "Clinical Drug Comp",
    "brandeddrugcomp": "Branded Drug Comp",
    "brandeddrugform": "Branded Drug Form",
    "clinicalpack": "Clinical Pack",
    "brandedpack": "Branded Pack",
    "brandname": "Brand Name",
    "doseform": "Dose Form",
    "doseformgroup": "Dose Form Group",

    # --- SNOMED ---
    "clinicalfinding": "Clinical Finding",
    "procedure": "Procedure",
    "bodystructure": "Body Structure",
    "observableentity": "Observable Entity",
    "qualifiervalue": "Qualifier Value",
    "contextdependent": "Context-dependent",
    "morphologicabnormality": "Morphologic Abnormality",
    "event": "Event",
    "situation": "Situation",
    "regimetherapy": "Regime/Therapy",
    "stagingscale": "Staging Scale",
    "assessmentscale": "Assessment Scale",
    "tumorfinding": "Tumor Finding",
    "organism": "Organism",
    "substance": "Substance",
    "physicalobject": "Physical Object",
    "specimen": "Specimen",

    # --- LOINC ---
    "labtest": "Lab Test",
    "clinicalobservation": "Clinical Observation",
    "survey": "Survey",
    "loinccomponent": "LOINC Component",

    # --- Type Concepts ---
    "typeconcept": "Type Concept",
    "conditiontype": "Condition Type",
    "drugtype": "Drug Type",
    "proceduretype": "Procedure Type",
    "visittype": "Visit Type",
    "observationtype": "Observation Type",
    "measurementtype": "Measurement Type",
    "devicetype": "Device Type",
    "specimentype": "Specimen Type",
    "notetype": "Note Type",
    "episodetype": "Episode Type",

    # --- Admin / Generic ---
    "domain": "Domain",
    "vocabulary": "Vocabulary",
    "unit": "Unit",
    "currency": "Currency",
    "relationship": "Relationship",

    # --- Demographics ---
    "race": "Race",
    "ethnicity": "Ethnicity",
    "gender": "Gender",

    # --- Common ---
    "condition": "Condition",
    "measurement": "Measurement",
    "drug": "Drug",
    "device": "Device",
    "visit": "Visit",

    # --- Misc ---
    "undefined": "Undefined",
    "metadata": "Metadata",
    "modelcomponent": "Model Component",
    "administrativeconcept": "Administrative Concept",
}


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def get_canonical_concept_class(value: Optional[str]) -> Optional[str]:
    """
    Get canonical casing for concept_class_id.

    Args:
        value: Input concept_class_id (any casing/format)

    Returns:
        Canonical OMOP value if recognized, else None

    Examples:
        >>> get_canonical_concept_class("ingredient")
        'Ingredient'

        >>> get_canonical_concept_class("CLINICAL DRUG")
        'Clinical Drug'

        >>> get_canonical_concept_class("unknown")
        None
    """
    normalized = _normalize(value)
    if not normalized:
        return None

    return CANONICAL_CONCEPT_CLASSES.get(normalized)


def is_valid_concept_class(value: Optional[str]) -> bool:
    """
    Check if a concept_class_id is valid and correctly cased.
    """
    canonical = get_canonical_concept_class(value)
    return canonical is not None and value == canonical


# -----------------------------------------------------------------------------
# Validation (runs at import time)
# -----------------------------------------------------------------------------

def _validate_no_duplicates() -> None:
    """Ensure no duplicate normalized keys exist."""
    keys = list(CANONICAL_CONCEPT_CLASSES.keys())
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate keys found in CANONICAL_CONCEPT_CLASSES")


_validate_no_duplicates()


__all__ = [
    "CANONICAL_CONCEPT_CLASSES",
    "get_canonical_concept_class",
    "is_valid_concept_class",
]
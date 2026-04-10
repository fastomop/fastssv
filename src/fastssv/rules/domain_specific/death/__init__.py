"""Death Domain Rules.

Rules specific to death-related OMOP CDM tables and concepts.
"""

from .death_cause_source_concept_validation import DeathCauseSourceConceptValidationRule

__all__ = [
    "DeathCauseSourceConceptValidationRule",
]

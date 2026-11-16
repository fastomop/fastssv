"""Observation Domain Rules.

Rules specific to the observation table and related validations.
"""

from .observation_value_as_concept_confusion import ObservationValueAsConceptConfusionRule

__all__ = [
    "ObservationValueAsConceptConfusionRule",
]

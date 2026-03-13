"""Measurement Domain Rules.

Rules specific to the measurement table and related validations.
"""

from .measurement_unit_validation import MeasurementUnitValidationRule

__all__ = [
    "MeasurementUnitValidationRule",
]

"""Analytics Rules.

Rules for detecting analytical methodology issues and recommendations.
"""

from .percentile_methodology import PercentileMethodologyRule
from .integer_division_precision_loss import IntegerDivisionPrecisionLossRule
from .division_by_zero_risk import DivisionByZeroRiskRule

__all__ = [
    "PercentileMethodologyRule",
    "IntegerDivisionPrecisionLossRule",
    "DivisionByZeroRiskRule",
]

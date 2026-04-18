"""Analytics Rules.

Rules for detecting analytical methodology issues and recommendations.
"""

from .percentile_methodology import PercentileMethodologyRule

__all__ = [
    "PercentileMethodologyRule",
]

"""Performance Rules.

Rules for detecting performance issues and optimization opportunities.
"""

from .cross_join_large_table import CrossJoinLargeTableRule

__all__ = [
    "CrossJoinLargeTableRule",
]

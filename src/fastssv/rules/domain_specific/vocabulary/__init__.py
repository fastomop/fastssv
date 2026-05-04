"""Vocabulary Table Rules.

Rules specific to OMOP vocabulary tables (concept, relationship, etc.)
"""

from .vocabulary_relationship_boolean_comparison import RelationshipBooleanComparisonRule

__all__ = [
    "RelationshipBooleanComparisonRule",
]

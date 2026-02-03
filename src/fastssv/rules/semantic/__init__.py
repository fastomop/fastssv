"""Semantic validation rules for OMOP SQL queries.

Rules:
- standard_concept: Ensures queries using STANDARD concept fields enforce standard concepts
- unmapped_concept: Warns when filtering by concept_id without handling unmapped (0)
- join_path: Validates proper JOIN paths between clinical and vocabulary tables
- maps_to_direction: Checks 'Maps to' relationship direction
- invalid_reason: Ensures vocabulary tables filter by invalid_reason to use only valid concepts
"""

# Import all rule modules to trigger registration
from . import invalid_reason, join_path, maps_to_direction, standard_concept, unmapped_concept

__all__ = [
    "standard_concept",
    "unmapped_concept",
    "join_path",
    "maps_to_direction",
    "invalid_reason",
]

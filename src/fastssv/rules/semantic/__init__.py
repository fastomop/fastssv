"""Semantic validation rules for OMOP SQL queries.

Rules:
- standard_concept: Ensures queries using STANDARD concept fields enforce standard concepts
- unmapped_concept: Warns when filtering by concept_id without handling unmapped (0)
- join_path: Validates proper JOIN paths between clinical and vocabulary tables
- maps_to_direction: Checks 'Maps to' relationship direction
- temporal_constraint_mapping: Ensures temporal constraints are anchored to observation_period
- hierarchy_expansion: Ensures drug/condition concept filters use concept_ancestor
- domain_segregation: Ensures clinical tables are joined to concepts from their expected domain
- measurement_unit_validation: Ensures value_as_number filters include a unit_concept_id constraint
- future_information_leakage: Detects cross-table date comparisons not bounded by observation_period_end_date
"""

# Import all rule modules to trigger registration
from . import (
    join_path,
    maps_to_direction,
    standard_concept,
    temporal_constraint_mapping,
    invalid_reason,
    hierarchy_expansion,
    unmapped_concept,
    domain_segregation,
    measurement_unit_validation,
    future_information_leakage,
)

__all__ = [
    "standard_concept",
    "unmapped_concept",
    "join_path",
    "maps_to_direction",
    "temporal_constraint_mapping",
    "invalid_reason",
    "hierarchy_expansion",
    "domain_segregation",
    "measurement_unit_validation",
    "future_information_leakage",
]

"""Domain-Specific Rules.

Rules specific to particular OMOP CDM domains (measurement, drug, condition, etc.).

Each table-specific subpackage (`drug/`, `measurement/`, …) is imported here
purely for its `@register` side effects; the directory nesting is
organisational only and does not appear in `rule_id`s, which stay flat as
`domain_specific.<rule_name>`.
"""

from . import (  # noqa: F401  -- side-effect imports trigger @register
    cohort,
    condition,
    cost,
    death,
    dose_era,
    drug,
    episode,
    location,
    measurement,
    note,
    observation,
    person,
    procedure,
    specimen,
    visit,
    visit_detail,
    vocabulary,
)

# Cross-cutting rules (genuinely domain-agnostic) stay flat.
from .event_cardinality_validation import EventCardinalityValidationRule
from .event_field_polymorphic_resolution import EventFieldPolymorphicResolutionRule

__all__ = [
    "EventCardinalityValidationRule",
    "EventFieldPolymorphicResolutionRule",
]

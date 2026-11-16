"""Domain-Specific Rules.

Rules specific to particular OMOP CDM domains (measurement, drug, condition, etc.).

Each table-specific subpackage (`drug/`, `measurement/`, …) is imported here
purely for its `@register` side effects; the directory nesting is
organisational only and does not appear in `rule_id`s, which stay flat as
`domain_specific.<rule_name>`.
"""

from . import (  # noqa: F401  -- side-effect imports trigger @register
    cost,
    drug,
    measurement,
    note,
    observation,
    person,
    visit,
    visit_detail,
    vocabulary,
)

# Cross-cutting rules (genuinely domain-agnostic) stay flat.
from .event_field_polymorphic_resolution import EventFieldPolymorphicResolutionRule
from .event_rows_counted_as_persons import EventRowsCountedAsPersonsRule
from .numeric_value_without_unit_constraint import NumericValueWithoutUnitConstraintRule
from .value_as_columns_conjunction import ValueAsColumnsConjunctionRule

__all__ = [
    "EventFieldPolymorphicResolutionRule",
    "EventRowsCountedAsPersonsRule",
    "NumericValueWithoutUnitConstraintRule",
    "ValueAsColumnsConjunctionRule",
]

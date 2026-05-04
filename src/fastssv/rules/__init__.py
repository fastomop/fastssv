"""FastSSV validation rules package.

Importing this package triggers `@register` side effects for every rule
in every category. The user-facing validation API lives at the package
root (`fastssv.validate_sql`, `fastssv.validate_sql_structured`,
`fastssv.validate_<category>`); this package is intentionally
side-effect-only.

Categories:
- anti_patterns: Common mistakes and anti-patterns to avoid
- concept_standardization: Rules for standard, valid, domain-appropriate concepts
- data_quality: Rules for schema compliance and unmapped data handling
- domain_specific: Table-specific validation rules (measurement, drug, …)
- joins: Rules for proper table relationships and join paths
- temporal: Rules for temporal logic and observation period validation
"""

from . import (  # noqa: F401  -- side-effect imports trigger @register
    anti_patterns,
    concept_standardization,
    data_quality,
    domain_specific,
    joins,
    temporal,
)

# FastSSV Architecture

## Directory Structure

```text
src/fastssv/
├── __init__.py                 # Main API: validate_sql(), validate_sql_structured()
├── core/
│   ├── __init__.py
│   ├── base.py                 # Rule base class, RuleViolation, Severity
│   ├── registry.py             # Plugin registry with @register decorator
│   └── helpers.py              # SQL parsing utilities
├── rules/
│   ├── __init__.py             # Imports rule packages and category validator functions
│   ├── concept_standardization/
│   ├── anti_patterns/
│   ├── joins/
│   ├── temporal/
│   ├── data_quality/
│   └── domain_specific/
└── schemas/
    ├── __init__.py
    ├── cdm_column_types.py      # OMOP CDM v5.4 table → {column → type} map (single source of truth)
    └── semantic_schema.py       # STANDARD_CONCEPT_FIELDS — table/column pairs that must hold standard concept ids
```

## Architecture Overview

FastSSV uses a **plugin-based architecture** where validation rules are automatically discovered and registered at import time. For a step-by-step walkthrough of writing a new rule, see [Plugin system](plugin_architecture.md) — this page focuses on the conceptual layout.

### Separation of Concerns

1. **Core** (`core/`)
   - `base.py`: Abstract `Rule` base class, `RuleViolation`, and `Severity` enum
   - `registry.py`: Plugin registry with `@register` decorator for automatic rule discovery
   - `helpers.py`: SQL parsing utilities (sqlglot-based)

2. **Schemas** (`schemas/`)
   - Pure data definitions
   - CDM table relationships
   - Vocabulary field classifications
   - No validation logic

3. **Rules** (`rules/`)
   - Each rule is a class inheriting from `Rule`
   - Registered automatically via `@register` decorator
   - Organized by implementation area
   - Runtime categories come from the `rule_id` prefix
   - Returns list of `RuleViolation` objects

4. **Main API** (`__init__.py`)
   - Unified interface: `validate_sql()` and `validate_sql_structured()`
   - Coordinates multiple rules via registry
   - Supports filtering by rule ID or category
   - Exposes grouped results and structured violations

## Rule Interface

Each rule follows this pattern:

```python
from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.registry import register

@register
class MyRule(Rule):
    """Validation rule for specific OMOP CDM constraint."""

    rule_id = "category.rule_name"
    name = "Rule Name"
    description = "Rule description"
    severity = Severity.ERROR

    def validate(self, sql: str, dialect: str = "postgres") -> list[RuleViolation]:
        """
        Args:
            sql: SQL query to validate
            dialect: SQL dialect for parsing

        Returns:
            List of RuleViolation objects (empty if valid)
        """
        violations = []

        # Validation logic here
        if violation_detected:
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    severity=Severity.ERROR,  # or Severity.WARNING
                    message="Description of violation",
                    suggested_fix="Recommended fix for this violation",
                    location="optional location info",  # Optional
                    details={"key": "value"}  # Optional structured metadata
                )
            )

        return violations
```

### RuleViolation Structure

```python
@dataclass
class RuleViolation:
    rule_id: str                    # e.g., "concept_standardization.standard_concept_enforcement"
    severity: Severity              # ERROR or WARNING
    message: str                    # Human-readable error message
    suggested_fix: str              # Recommendation for fixing the violation
    location: Optional[str] = None  # Optional: file, line, or SQL fragment
    details: dict = field(default_factory=dict)  # Additional structured metadata

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
```

## Adding New Rules

The full step-by-step walkthrough lives in [Plugin system](plugin_architecture.md#creating-a-new-rule) — pick a category under `src/fastssv/rules/`, write a `Rule` subclass with `@register`, wire it into the category's `__init__.py`, and add a test to `tests/test_rules.py`. Once registered the rule is reachable via `validate_sql_structured(sql, categories=[...])` / `rule_ids=[...]` and the CLI's `--categories` / `--rules` flags.

## Current Rules

### Category Summary

- `anti_patterns`: 20 rules
- `concept_standardization`: 18 rules
- `data_quality`: 22 rules
- `domain_specific`: 48 rules
- `joins`: 36 rules
- `temporal`: 10 rules

**Total: 154 rules**

> The category `schema` also exists as a Python package but contains the
> single fundamental-correctness rule `data_quality.schema_validation`;
> it is counted under `data_quality` above.

### Representative Rules

1. **join_path** (`joins.join_path_validation`)
   - **Severity:** WARNING
   - Validates table joins against the OMOP CDM v5.4 schema graph

2. **standard_concept** (`concept_standardization.standard_concept_enforcement`)
   - **Severity:** WARNING
   - Ensures STANDARD concept fields enforce `concept.standard_concept = 'S'`

3. **concept_ancestor_rollup_direction** (`concept_standardization.concept_ancestor_rollup_direction`)
   - **Severity:** ERROR
   - Validates the ancestor/descendant direction of `concept_ancestor` rollups

4. **observation_period_anchoring** (`temporal.observation_period_anchoring`)
   - **Severity:** WARNING
   - Anchors temporal logic to `observation_period`

5. **maps_to_direction** (`joins.maps_to_direction`)
   - **Severity:** WARNING
   - Validates `'Maps to'` directionality in `concept_relationship`

6. **unmapped_concept** (`data_quality.unmapped_concept_handling`)
   - **Severity:** WARNING
   - Requires explicit handling of `concept_id = 0`

7. **no_string_id** (`anti_patterns.no_string_identification`)
   - **Severity:** ERROR
   - Prevents portable logic from depending on source text columns

## Extension Points

The plugin architecture makes it easy to add new rules. New rules should extend one of the existing categories unless there is a strong reason to introduce a new package and category prefix.

## Registry System

The registry (`core/registry.py`) provides:

- `@register`: Decorator to register rules by `rule_id`
- `get_all_rules()`: Get all registered rule classes
- `get_rule(rule_id)`: Get specific rule by ID
- `get_rules_by_category(category)`: Get all rules in a category

Rules are automatically discovered at import time when their module is imported.

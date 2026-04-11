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
    ├── cdm_schema.py            # OMOP CDM v5.4 schema definition
    └── semantic_schema.py       # Vocabulary field classifications (STANDARD vs SOURCE)
```

## Architecture Overview

FastSSV uses a **plugin-based architecture** where validation rules are automatically discovered and registered at import time.

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

To add a new validation rule:

1. Create a new rule file in the appropriate implementation directory:
   - Concept logic: `src/fastssv/rules/concept_standardization/my_rule.py`
   - Join logic: `src/fastssv/rules/joins/my_rule.py`
   - Temporal logic: `src/fastssv/rules/temporal/my_rule.py`
   - Table-family logic: `src/fastssv/rules/domain_specific/<family>/my_rule.py`

2. Implement the rule class:
   ```python
   from fastssv.core.base import Rule, RuleViolation, Severity
   from fastssv.core.registry import register

   @register
   class MyRule(Rule):
       """Detailed documentation of the rule."""

       rule_id = "category.my_rule"
       name = "My Rule"
       description = "Brief description of what this rule checks"
       severity = Severity.ERROR

       def validate(self, sql: str, dialect: str = "postgres") -> list[RuleViolation]:
           violations = []
           # Validation logic here
           return violations
   ```

3. Import in the package's `__init__.py`:
   ```python
   # In src/fastssv/rules/<package>/__init__.py
   from . import my_rule  # This triggers registration
   ```

4. Add tests in `tests/test_my_rule.py`

**That's it!** The rule is automatically discovered and available via:
- `validate_sql_structured(sql, categories=["category"])`
- `validate_sql_structured(sql, rule_ids=["category.my_rule"])`
- CLI: `fastssv query.sql --rules category.my_rule`

## Current Rules

### Category Summary

- `anti_patterns`: 10 rules
- `concept_standardization`: 19 rules
- `data_quality`: 12 rules
- `domain_specific`: 21 rules
- `joins`: 35 rules
- `temporal`: 9 rules

### Representative Rules

1. **join_path** (`joins.join_path_validation`)
   - **Severity:** WARNING
   - Validates table joins against the OMOP CDM v5.4 schema graph

2. **standard_concept** (`concept_standardization.standard_concept_enforcement`)
   - **Severity:** ERROR
   - Ensures STANDARD concept fields enforce `concept.standard_concept = 'S'`

3. **hierarchy_expansion** (`concept_standardization.hierarchy_expansion_required`)
   - **Severity:** ERROR
   - Ensures drug and condition filters use `concept_ancestor`

4. **observation_period_anchoring** (`temporal.observation_period_anchoring`)
   - **Severity:** ERROR
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

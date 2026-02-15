# FastSSV Architecture

## Directory Structure

```
src/fastssv/
├── __init__.py                 # Main API: validate_sql(), validate_sql_structured()
├── core/
│   ├── __init__.py
│   ├── base.py                 # Rule base class, RuleViolation, Severity
│   ├── registry.py             # Plugin registry with @register decorator
│   └── helpers.py              # SQL parsing utilities
├── rules/
│   ├── __init__.py             # Legacy validator functions
│   ├── semantic/
│   │   ├── __init__.py
│   │   ├── join_path.py                   # Join path validation rule
│   │   ├── standard_concept.py            # Standard concept enforcement rule
│   │   ├── maps_to_direction.py           # Maps-to relationship direction rule
│   │   ├── unmapped_concept.py            # Unmapped concept detection rule
│   │   ├── hierarchy_expansion.py         # Concept hierarchy expansion rule
│   │   ├── temporal_constraint_mapping.py # Observation period anchoring rule
│   │   └── invalid_reason.py              # Invalid reason enforcement rule
│   ├── vocabulary/
│   │   ├── __init__.py
│   │   ├── no_string_id.py          # String ID lookup detection rule
│   │   ├── concept_lookup.py        # Concept table string filter rule
│   │   └── concept_code_vocab_id.py # Concept code uniqueness rule
│   ├── semantic_rules.py       # Deprecated: legacy semantic validators
│   └── vocabulary_rules.py     # Deprecated: legacy vocabulary validators
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
   - Organized by category (semantic, vocabulary)
   - Returns list of `RuleViolation` objects

4. **Main API** (`__init__.py`)
   - Unified interface: `validate_sql()` (legacy) and `validate_sql_structured()` (recommended)
   - Coordinates multiple rules via registry
   - Supports filtering by rule ID or category
   - Maintains backward compatibility

## Rule Interface

Each rule follows this pattern:

```python
from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.registry import register

@register(rule_id="category.rule_name", category="category", description="Rule description")
class MyRule(Rule):
    """Validation rule for specific OMOP CDM constraint."""

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
    rule_id: str                    # e.g., "semantic.standard_concept_enforcement"
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

1. Create a new rule file in the appropriate category directory:
   - For semantic rules: `src/fastssv/rules/semantic/my_rule.py`
   - For vocabulary rules: `src/fastssv/rules/vocabulary/my_rule.py`
   - For a new category: `src/fastssv/rules/new_category/my_rule.py`

2. Implement the rule class:
   ```python
   from fastssv.core.base import Rule, RuleViolation, Severity
   from fastssv.core.registry import register

   @register(
       rule_id="category.my_rule",
       category="category",
       description="Brief description of what this rule checks"
   )
   class MyRule(Rule):
       """Detailed documentation of the rule."""

       def validate(self, sql: str, dialect: str = "postgres") -> list[RuleViolation]:
           violations = []
           # Validation logic here
           return violations
   ```

3. Import in the category's `__init__.py`:
   ```python
   # In src/fastssv/rules/category/__init__.py
   from . import my_rule  # This triggers registration
   ```

4. Add tests in `tests/test_my_rule.py`

**That's it!** The rule is automatically discovered and available via:
- `validate_sql_structured(sql, categories=["category"])`
- `validate_sql_structured(sql, rule_ids=["category.my_rule"])`
- CLI: `python main.py query.sql --categories category`
- CLI: `python main.py query.sql --rules category.my_rule`

## Current Rules

### Semantic Rules (Category: `semantic`)

1. **join_path** (`semantic.join_path_validation`)
   - **Severity:** WARNING
   - Validates table joins against OMOP CDM v5.4 schema
   - Checks join predicates using sqlglot AST
   - Verifies foreign key → primary key relationships between clinical and vocabulary tables
   - Handles direct joins, CTE-mediated joins, and concept_ancestor relationships

2. **standard_concept** (`semantic.standard_concept_enforcement`)
   - **Severity:** ERROR
   - Ensures STANDARD concept fields enforce `concept.standard_concept = 'S'`
   - Validates use of 'Maps to' relationships via concept_relationship
   - Detects missing standard concept enforcement in WHERE/JOIN clauses
   - Prevents queries from using non-standard concepts in analytical fields

3. **hierarchy_expansion** (`semantic.hierarchy_expansion_required`)
   - **Severity:** ERROR
   - Ensures drug_concept_id and condition_concept_id filters use concept_ancestor
   - Validates that concept hierarchy is properly expanded to capture descendants
   - Prevents missing specific formulations (e.g., Metformin → Metformin 500mg, Metformin XR)
   - Checks for correct join direction (ancestor → descendant)

4. **observation_period_anchoring** (`semantic.observation_period_anchoring`)
   - **Severity:** ERROR
   - Validates temporal constraints join to observation_period on person_id
   - Ensures date filters are anchored within valid observation windows
   - Covers 12+ clinical tables with temporal data
   - Detects date column filters and date functions requiring anchoring

5. **maps_to_direction** (`semantic.maps_to_direction`)
   - **Severity:** ERROR
   - Validates concept_relationship 'Maps to' direction
   - Ensures proper source → standard mapping (not standard → source)
   - Prevents incorrect reverse mapping usage

6. **unmapped_concept** (`semantic.unmapped_concept_handling`)
   - **Severity:** WARNING
   - Warns when filtering by concept_id without handling concept_id = 0
   - Detects missing patterns: >, >=, !=, COALESCE, CASE
   - Helps prevent silent inclusion/exclusion of unmapped records

7. **invalid_reason** (`semantic.invalid_reason_enforcement`)
   - **Severity:** WARNING
   - Ensures vocabulary tables filter by invalid_reason IS NULL
   - Applies to concept and concept_relationship tables
   - Prevents using deprecated or invalid concepts
   - Does NOT apply to clinical event tables (historical data)

### Vocabulary Rules (Category: `vocabulary`)

1. **no_string_id** (`vocabulary.no_string_identification`)
   - **Severity:** WARNING
   - Detects string-based concept lookups (concept_name = 'text')
   - Identifies string literal matches on _source_value columns
   - Encourages concept_id-based filtering instead

2. **concept_lookup** (`vocabulary.concept_lookup_context`)
   - **Severity:** WARNING
   - Validates concept table string filters are used in concept_id lookup context
   - Ensures string filters on concept table are properly joined to clinical tables
   - Prevents orphaned concept table queries

3. **concept_code_vocab_id** (`vocabulary.concept_code_requires_vocabulary_id`)
   - **Severity:** ERROR
   - **Recently added (PR #16, commit 926909d)**
   - Ensures concept_code filters include vocabulary_id filter
   - Rationale: concept_code is unique only within a vocabulary
   - Detects patterns: concept_code = 'value', IN (...), LIKE/ILIKE
   - Prevents silently matching unintended concepts from other vocabularies

## Extension Points

The plugin architecture makes it easy to add new rules. Future validation opportunities include:

1. **Domain validation**: Ensure concepts match table domains (e.g., Drug domain concepts only in drug_exposure)
2. **Deep vocabulary validation**: Validate specific vocabulary-specific constraints (ICD10CM formatting, CPT4 ranges, etc.)
3. **Temporal information leakage**: Detect future information leakage in time-to-event analyses
4. **Cohort logic preservation**: Validate that inclusion/exclusion criteria are properly maintained through CTEs
5. **Unit validation**: Check measurement.unit_concept_id matches measurement_concept_id expected units
6. **Visit context validation**: Ensure visit_occurrence_id foreign keys are properly used

Simply create a new rule class with the `@register` decorator. No changes to the core architecture needed.

**Already implemented (do not re-implement):**
- ✅ Concept hierarchy validation (hierarchy_expansion_required)
- ✅ Temporal constraint validation (observation_period_anchoring)
- ✅ Concept code uniqueness validation (concept_code_requires_vocabulary_id)

## Registry System

The registry (`core/registry.py`) provides:

- `@register(rule_id, category, description)`: Decorator to register rules
- `get_all_rules()`: Get all registered rule classes
- `get_rule(rule_id)`: Get specific rule by ID
- `get_rules_by_category(category)`: Get all rules in a category

Rules are automatically discovered at import time when their module is imported.

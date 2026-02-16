# Working with Semantic Rules

This guide explains how to work with and extend semantic validation rules in FastSSV, which validate OMOP CDM schema relationships and concept usage patterns.

## Quick Start

### Using Semantic Validation

```python
from fastssv import validate_sql_structured

# Run all rules (recommended API)
violations = validate_sql_structured(sql_query)
for v in violations:
    print(f"{v.severity.value}: [{v.rule_id}] {v.message}")
    print(f"  Fix: {v.suggested_fix}")

# Run only semantic rules
violations = validate_sql_structured(sql_query, categories=["semantic"])

# Run specific rule
violations = validate_sql_structured(
    sql_query,
    rule_ids=["semantic.standard_concept_enforcement"]
)

# Legacy API (for backward compatibility)
from fastssv import validate_sql
results = validate_sql(sql_query, categories=["semantic"])
print(results["semantic_errors"])
```

### CLI Usage

```bash
# Run all rules (default, outputs to output/validation_report.json)
python main.py query.sql

# Run only semantic rules
python main.py query.sql --categories semantic

# Run only vocabulary rules
python main.py query.sql --categories vocabulary

# Run both semantic and vocabulary
python main.py query.sql --categories semantic vocabulary

# Run specific rules
python main.py query.sql --rules semantic.standard_concept_enforcement semantic.hierarchy_expansion_required

# Custom output path
python main.py query.sql --output my_report.json
```

## Understanding Semantic Rules

Semantic rules validate OMOP CDM analytical constraints that go beyond SQL syntax. They ensure queries follow OMOP conventions for concept usage, vocabulary relationships, and temporal constraints.

### Current Semantic Rules

FastSSV includes **7 semantic validation rules**:

#### 1. Standard Concept Enforcement (`semantic.standard_concept_enforcement`)
**Severity:** ERROR

Ensures queries using STANDARD concept fields enforce `concept.standard_concept = 'S'` or use `concept_relationship` with `'Maps to'`.

**Example violation:**
```sql
-- BAD: No standard concept enforcement
SELECT * FROM drug_exposure de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.concept_name LIKE '%aspirin%';

-- GOOD: Enforces standard concepts
SELECT * FROM drug_exposure de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.standard_concept = 'S'
  AND c.concept_name LIKE '%aspirin%';
```

#### 2. Join Path Validation (`semantic.join_path_validation`)
**Severity:** WARNING

Validates table joins against OMOP CDM v5.4 schema, ensuring proper foreign key → primary key relationships.

**Example violation:**
```sql
-- BAD: Reversed join direction
SELECT * FROM concept c
JOIN condition_occurrence co ON c.concept_id = co.condition_concept_id;

-- GOOD: Correct join direction
SELECT * FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id;
```

#### 3. Hierarchy Expansion Required (`semantic.hierarchy_expansion_required`)
**Severity:** ERROR

Ensures `drug_concept_id` and `condition_concept_id` filters use `concept_ancestor` to capture all descendant concepts.

**Example violation:**
```sql
-- BAD: Missing hierarchy expansion
SELECT * FROM drug_exposure
WHERE drug_concept_id = 1234;  -- Only matches exact concept

-- GOOD: Uses concept_ancestor for hierarchy
SELECT * FROM drug_exposure de
JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 1234;  -- Matches all descendants
```

#### 4. Observation Period Anchoring (`semantic.observation_period_anchoring`)
**Severity:** ERROR

Validates that temporal constraints join to `observation_period` on `person_id` to ensure events fall within valid observation windows.

**Example violation:**
```sql
-- BAD: Temporal filter without observation period
SELECT * FROM condition_occurrence
WHERE condition_start_date > '2020-01-01';

-- GOOD: Anchored to observation period
SELECT * FROM condition_occurrence co
JOIN observation_period op ON co.person_id = op.person_id
WHERE co.condition_start_date > '2020-01-01'
  AND co.condition_start_date BETWEEN op.observation_period_start_date
                                  AND op.observation_period_end_date;
```

#### 5. Maps-to Direction (`semantic.maps_to_direction`)
**Severity:** ERROR

Validates `concept_relationship` 'Maps to' direction (source → standard, not reversed).

**Example violation:**
```sql
-- BAD: Reversed mapping direction
SELECT cr.concept_id_1 FROM concept_relationship cr
WHERE cr.relationship_id = 'Maps to'
  AND cr.concept_id_2 = 12345;  -- Standard concept on wrong side

-- GOOD: Correct mapping direction
SELECT cr.concept_id_2 FROM concept_relationship cr
WHERE cr.relationship_id = 'Maps to'
  AND cr.concept_id_1 = 12345;  -- Source concept maps to standard
```

#### 6. Unmapped Concept Handling (`semantic.unmapped_concept_handling`)
**Severity:** WARNING

Warns when filtering by `concept_id` without explicitly handling `concept_id = 0` (unmapped records).

**Example violation:**
```sql
-- WARNING: Doesn't handle unmapped concepts
SELECT * FROM condition_occurrence
WHERE condition_concept_id = 12345;

-- BETTER: Explicitly handles unmapped
SELECT * FROM condition_occurrence
WHERE condition_concept_id = 12345
   OR condition_concept_id = 0;  -- Or explicitly exclude with > 0
```

#### 7. Invalid Reason Enforcement (`semantic.invalid_reason_enforcement`)
**Severity:** WARNING

Ensures vocabulary tables filter by `invalid_reason IS NULL` to exclude deprecated or invalid concepts.

**Example violation:**
```sql
-- WARNING: No invalid_reason filter
SELECT * FROM concept
WHERE vocabulary_id = 'SNOMED';

-- GOOD: Filters out invalid concepts
SELECT * FROM concept
WHERE vocabulary_id = 'SNOMED'
  AND invalid_reason IS NULL;
```

### Concept Field Classification

Semantic rules use field classifications from `src/fastssv/schemas/semantic_schema.py`:

#### STANDARD Concept Fields

Fields that should contain **standard** concepts (SNOMED, RxNorm, LOINC, etc.):

```python
STANDARD_CONCEPT_FIELDS = {
    ("condition_occurrence", "condition_concept_id"),
    ("drug_exposure", "drug_concept_id"),
    ("drug_exposure", "route_concept_id"),
    ("procedure_occurrence", "procedure_concept_id"),
    ("measurement", "measurement_concept_id"),
    ("observation", "observation_concept_id"),
    ("visit_occurrence", "visit_concept_id"),
    ("device_exposure", "device_concept_id"),
    # ... 50+ fields total
}
```

#### SOURCE Concept Fields

Fields that can contain **source** vocabularies (ICD10CM, CPT4, NDC, etc.):

```python
SOURCE_CONCEPT_FIELDS = {
    ("condition_occurrence", "condition_source_concept_id"),
    ("drug_exposure", "drug_source_concept_id"),
    ("procedure_occurrence", "procedure_source_concept_id"),
    ("measurement", "measurement_source_concept_id"),
    ("observation", "observation_source_concept_id"),
    # ... 30+ fields total
}
```

## Extending Semantic Validation

The plugin architecture makes adding new rules straightforward.

### Example: Add Domain Validation Rule

Create a rule to ensure concepts match table domains (e.g., Drug domain concepts only in drug_exposure):

```python
# src/fastssv/rules/semantic/domain_validation.py

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.registry import register
from fastssv.core.helpers import parse_sql, extract_aliases, resolve_table_col

EXPECTED_DOMAINS = {
    ("drug_exposure", "drug_concept_id"): "Drug",
    ("condition_occurrence", "condition_concept_id"): "Condition",
    ("measurement", "measurement_concept_id"): "Measurement",
    ("procedure_occurrence", "procedure_concept_id"): "Procedure",
    ("observation", "observation_concept_id"): "Observation",
    ("device_exposure", "device_concept_id"): "Device",
}

@register(
    rule_id="semantic.domain_validation",
    category="semantic",
    description="Validates concept domains match table expectations"
)
class DomainValidationRule(Rule):
    """Ensures concepts belong to the correct domain for their table.

    OMOP CDM concept.domain_id must match the table's expected domain.
    For example, drug_exposure.drug_concept_id should only reference
    concepts with domain_id = 'Drug'.
    """

    rule_id = "semantic.domain_validation"
    name = "Domain Validation"
    description = "Validates concept domains match table expectations"
    severity = Severity.ERROR
    suggested_fix = "Filter by concept.domain_id or use proper vocabulary mapping"

    def validate(self, sql: str, dialect: str = "postgres") -> list[RuleViolation]:
        violations = []
        trees, parse_error = parse_sql(sql, dialect)

        if parse_error:
            return violations

        tree = trees[0]
        aliases = extract_aliases(tree)

        # Extract concept field references
        concept_refs = self._extract_concept_references(tree, aliases)

        for table, column, location in concept_refs:
            expected_domain = EXPECTED_DOMAINS.get((table, column))

            if expected_domain:
                # Check if query filters by domain_id
                has_domain_filter = self._has_domain_filter(tree, expected_domain, aliases)

                if not has_domain_filter:
                    violations.append(
                        RuleViolation(
                            rule_id=self.rule_id,
                            severity=self.severity,
                            message=f"Query uses {table}.{column} but does not filter "
                                   f"by concept.domain_id = '{expected_domain}'. This may "
                                   f"include concepts from incorrect domains.",
                            suggested_fix=f"Add JOIN to concept table with WHERE clause: "
                                        f"concept.domain_id = '{expected_domain}'",
                            location=location,
                            details={
                                "table": table,
                                "column": column,
                                "expected_domain": expected_domain
                            }
                        )
                    )

        return violations

    def _extract_concept_references(self, tree, aliases):
        """Extract references to concept fields."""
        # Implementation: scan WHERE/JOIN clauses for concept_id columns
        return []

    def _has_domain_filter(self, tree, domain, aliases):
        """Check if query filters by concept.domain_id."""
        # Implementation: check for domain_id = 'domain' in WHERE/JOIN
        return False
```

### Register the New Rule

Import in `src/fastssv/rules/semantic/__init__.py`:

```python
from . import join_path
from . import standard_concept
from . import hierarchy_expansion
from . import temporal_constraint_mapping
from . import maps_to_direction
from . import unmapped_concept
from . import invalid_reason
from . import domain_validation  # Add this line
```

### Add Tests

```python
# tests/test_domain_validation.py

import unittest
from fastssv.rules.semantic.domain_validation import DomainValidationRule

class TestDomainValidation(unittest.TestCase):
    def setUp(self):
        self.rule = DomainValidationRule()

    def test_valid_domain_filter(self):
        sql = """
        SELECT * FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id = 'Drug'
        """
        violations = self.rule.validate(sql)
        self.assertEqual(len(violations), 0)

    def test_missing_domain_filter(self):
        sql = """
        SELECT * FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_name LIKE '%aspirin%'
        """
        violations = self.rule.validate(sql)
        self.assertGreater(len(violations), 0)
        self.assertEqual(violations[0].rule_id, "semantic.domain_validation")
```

## Current Implementation Status

### Implemented Rules (7)

✅ **Standard Concept Enforcement** - Validates standard_concept = 'S' usage
✅ **Join Path Validation** - Validates OMOP CDM schema joins
✅ **Hierarchy Expansion** - Ensures concept_ancestor usage for hierarchies
✅ **Observation Period Anchoring** - Validates temporal constraint anchoring
✅ **Maps-to Direction** - Validates concept_relationship direction
✅ **Unmapped Concept Handling** - Warns about concept_id = 0 handling
✅ **Invalid Reason Enforcement** - Validates invalid_reason IS NULL filtering

### Extension Opportunities

The following validation opportunities remain:

🔲 **Domain Validation** - Ensure concepts match table domains (example above)
🔲 **Vocabulary-Specific Rules** - Deep validation for specific vocabularies (ICD10CM format, CPT4 ranges)
🔲 **Unit Validation** - Check measurement.unit_concept_id matches measurement_concept_id
🔲 **Visit Context Validation** - Ensure visit_occurrence_id foreign keys are used correctly
🔲 **Temporal Information Leakage** - Detect future information leakage in time-to-event analyses
🔲 **Cohort Logic Preservation** - Validate inclusion/exclusion criteria through CTEs

## File Locations

### Schema Definitions
- `src/fastssv/schemas/semantic_schema.py` - STANDARD and SOURCE field classifications
- `src/fastssv/schemas/cdm_schema.py` - OMOP CDM v5.4 table relationships

### Rule Implementations
- `src/fastssv/rules/semantic/`
  - `join_path.py` - Join path validation
  - `standard_concept.py` - Standard concept enforcement
  - `hierarchy_expansion.py` - Concept hierarchy expansion
  - `temporal_constraint_mapping.py` - Observation period anchoring
  - `maps_to_direction.py` - Maps-to relationship validation
  - `unmapped_concept.py` - Unmapped concept detection
  - `invalid_reason.py` - Invalid reason enforcement

### Tests
- `tests/test_semantic_validation.py` - Main semantic validation tests
- Individual rule test files as needed

### Main API
- `src/fastssv/__init__.py` - Public API exports

## Adding New Semantic Rules

### Step 1: Define the Rule

Create a new file in `src/fastssv/rules/semantic/`:

```python
from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.registry import register
from fastssv.core.helpers import parse_sql

@register(
    rule_id="semantic.my_new_rule",
    category="semantic",
    description="Brief description of what this validates"
)
class MyNewRule(Rule):
    """Detailed documentation of the OMOP CDM constraint being validated."""

    rule_id = "semantic.my_new_rule"
    name = "My New Rule"
    description = "Detailed description"
    severity = Severity.ERROR  # or Severity.WARNING
    suggested_fix = "Default fix suggestion"

    def validate(self, sql: str, dialect: str = "postgres") -> list[RuleViolation]:
        violations = []
        trees, parse_error = parse_sql(sql, dialect)

        if parse_error:
            return violations

        # Validation logic here

        return violations
```

### Step 2: Register the Rule

Import in `src/fastssv/rules/semantic/__init__.py`:

```python
from . import my_new_rule
```

### Step 3: Add Tests

Create `tests/test_my_new_rule.py`:

```python
import unittest
from fastssv.rules.semantic.my_new_rule import MyNewRule

class TestMyNewRule(unittest.TestCase):
    def setUp(self):
        self.rule = MyNewRule()

    def test_valid_case(self):
        sql = "SELECT * FROM person"
        violations = self.rule.validate(sql)
        self.assertEqual(len(violations), 0)

    def test_invalid_case(self):
        sql = "SELECT * FROM invalid_pattern"
        violations = self.rule.validate(sql)
        self.assertGreater(len(violations), 0)
```

### Step 4: Run Tests

```bash
# All semantic tests
python -m unittest tests.test_semantic_validation -v

# Specific test
python -m unittest tests.test_my_new_rule -v
```

## Modifying Field Classifications

To add new concept field classifications:

### Edit Schema Definition

`src/fastssv/schemas/semantic_schema.py`:

```python
STANDARD_CONCEPT_FIELDS = {
    # ... existing fields ...
    ("new_table", "new_concept_id"),  # Add here
}

SOURCE_CONCEPT_FIELDS = {
    # ... existing fields ...
    ("new_table", "new_source_concept_id"),  # Add here
}
```

Tests automatically pick up schema changes - no test modifications needed.

## Best Practices

### 1. Clear Violation Messages

Structure RuleViolation objects with clear messages:

```python
RuleViolation(
    rule_id=self.rule_id,
    severity=Severity.ERROR,
    message="What's wrong and why it matters. Query uses X without Y, "
           "which may cause Z problem.",
    suggested_fix="Specific actionable fix. Add WHERE clause: X = Y OR "
                  "use JOIN to table Z with condition A = B",
    location="table.column or specific SQL fragment",
    details={
        "table": "condition_occurrence",
        "field": "condition_concept_id",
        "additional_context": "any structured data"
    }
)
```

### 2. Use Helper Functions

Leverage utilities from `core/helpers.py`:

```python
from fastssv.core.helpers import (
    parse_sql,              # Parse SQL to AST
    extract_aliases,        # Map aliases to table names
    resolve_table_col,      # Resolve column to (table, column)
    normalize_name,         # Case-insensitive name comparison
    uses_table,             # Check if query uses a table
    extract_join_conditions # Extract JOIN conditions
)
```

### 3. Handle Parse Errors Gracefully

```python
def validate(self, sql: str, dialect: str = "postgres") -> list[RuleViolation]:
    violations = []
    trees, parse_error = parse_sql(sql, dialect)

    if parse_error:
        # Don't fail validation on parse errors
        return violations

    # Validation logic
    return violations
```

### 4. Test Edge Cases

- Empty queries
- CTEs (Common Table Expressions)
- Subqueries
- Multiple tables with same column names
- Aliased columns
- UNION queries

## Debugging Rules

### Enable Verbose Output

```python
violations = validate_sql_structured(sql, categories=["semantic"])
for v in violations:
    print(f"\nRule: {v.rule_id}")
    print(f"Severity: {v.severity.value}")
    print(f"Issue: {v.message}")
    print(f"Fix: {v.suggested_fix}")
    if v.location:
        print(f"Location: {v.location}")
    if v.details:
        print(f"Details: {v.details}")
```

### Test Single Rule

```python
from fastssv.rules.semantic.standard_concept import StandardConceptEnforcementRule

rule = StandardConceptEnforcementRule()
violations = rule.validate(sql, dialect="postgres")

for v in violations:
    print(v.to_dict())
```

### Inspect SQL AST

```python
from fastssv.core.helpers import parse_sql

trees, error = parse_sql(sql, dialect="postgres")
if not error:
    print(trees[0].sql())  # Pretty-print parsed SQL
    print(trees[0])        # Print AST structure
```

## Summary

FastSSV's semantic validation system provides:

- **7 production-ready rules** validating OMOP CDM constraints
- **Plugin architecture** for easy extension
- **Schema-driven validation** using OMOP CDM v5.4 definitions
- **Comprehensive field classifications** (50+ STANDARD, 30+ SOURCE fields)
- **Flexible API** supporting rule filtering and categorization

To add new rules:
1. Create rule class with `@register` decorator
2. Import in `__init__.py`
3. Add tests
4. Rule is automatically available system-wide

For questions or contributions, refer to `PLUGIN_ARCHITECTURE.md` for general plugin development patterns.

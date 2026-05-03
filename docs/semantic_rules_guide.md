# Semantic Rules Guide

This guide walks through the core OMOP CDM patterns FastSSV's semantic rules enforce, with a representative example for each. For the exhaustive per-rule catalog see [Rules reference](rules_reference.md); for the rule-author tutorial see [Plugin system](plugin_architecture.md).

## Quick Start

### Using Rule Categories

```python
from fastssv import validate_sql_structured

# Run all rules (recommended API)
violations = validate_sql_structured(sql_query)
for v in violations:
    print(f"{v.severity.value}: [{v.rule_id}] {v.message}")
    print(f"  Fix: {v.suggested_fix}")

# Run only concept standardization rules
violations = validate_sql_structured(sql_query, categories=["concept_standardization"])

# Run specific rule
violations = validate_sql_structured(
    sql_query,
    rule_ids=["concept_standardization.standard_concept_enforcement"]
)

# Run grouped validation results
from fastssv import validate_sql
results = validate_sql(sql_query, categories=["concept_standardization"])
print(results["category_errors"]["concept_standardization"])
```

### CLI Usage

```bash
# Run all rules (default, outputs to output/validation_report.json)
fastssv query.sql

# Run only concept standardization rules
fastssv query.sql --categories concept_standardization

# Run only anti-pattern rules
fastssv query.sql --categories anti_patterns

# Run multiple categories
fastssv query.sql --categories concept_standardization anti_patterns

# Run specific rules
fastssv query.sql --rules concept_standardization.standard_concept_enforcement concept_standardization.concept_ancestor_rollup_direction

# Custom output path
fastssv query.sql --output my_report.json
```

## Understanding Core Rule Groups

FastSSV rules validate OMOP CDM analytical constraints that go beyond SQL syntax. They ensure queries follow OMOP conventions for concept usage, vocabulary relationships, join paths, and temporal constraints.

### Current Concept Standardization Rules

FastSSV currently includes **18 concept standardization rules**. The examples below highlight a representative subset of the most important patterns, not every rule in the category.

#### 1. Standard Concept Enforcement (`concept_standardization.standard_concept_enforcement`)
**Severity:** WARNING

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

#### 2. Join Path Validation (`joins.join_path_validation`)
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

#### 3. Concept Ancestor Rollup Direction (`concept_standardization.concept_ancestor_rollup_direction`)
**Severity:** ERROR

When rolling up to ancestor concepts via `concept_ancestor`, the join direction
must match the intent. Filtering on `ancestor_concept_id` retrieves descendants
of that ancestor; filtering on `descendant_concept_id` retrieves ancestors of
that descendant. Reversing the two silently returns the wrong set.

**Example violation:**
```sql
-- BAD: Intent is "all descendants of concept 1234" but join direction is reversed
SELECT ca.ancestor_concept_id
FROM drug_exposure de
JOIN concept_ancestor ca ON de.drug_concept_id = ca.ancestor_concept_id
WHERE ca.descendant_concept_id = 1234;

-- GOOD: Descendants of 1234
SELECT de.*
FROM drug_exposure de
JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 1234;
```

> **Historical note:** A stricter rule, `concept_standardization.hierarchy_expansion_required`,
> previously fired on any specific-concept filter (e.g. `drug_concept_id = 1234`)
> that did not go through `concept_ancestor`. It was removed in 0.2.0 because
> specific-concept filters are legitimate in many contexts (e.g. single-drug
> exposure checks, denominator definitions). See [CHANGELOG.md](https://github.com/fastomop/fastSSV/blob/main/CHANGELOG.md).

#### 4. Observation Period Anchoring (`temporal.observation_period_anchoring`)
**Severity:** WARNING

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

#### 5. Maps-to Direction (`joins.maps_to_direction`)
**Severity:** WARNING

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

#### 6. Unmapped Concept Handling (`data_quality.unmapped_concept_handling`)
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

#### 7. Invalid Reason Enforcement (`concept_standardization.invalid_reason_enforcement`)
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

> **Note.** A previous `SOURCE_CONCEPT_FIELDS` set was removed in 0.2.0 — it had drifted from the v5.4 spec and no rule consumed it. Source-concept handling now flows through individual rules (e.g. `concept_standardization.invalid_reason_enforcement`, `data_quality.source_value_field_usage`) rather than a shared classification map.

## Extending Semantic Validation

The plugin architecture makes adding new rules straightforward.

### Example: Concept Domain Validation Is Already Implemented

Concept-domain checking already exists as `concept_standardization.concept_domain_validation`, implemented in `src/fastssv/rules/concept_standardization/concept_domain_validation.py`. Do not add a second domain-validation rule.

```python
# src/fastssv/rules/concept_standardization/concept_domain_validation.py

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

@register
class DomainValidationRule(Rule):
    """Ensures concepts belong to the correct domain for their table.

    OMOP CDM concept.domain_id must match the table's expected domain.
    For example, drug_exposure.drug_concept_id should only reference
    concepts with domain_id = 'Drug'.
    """

    rule_id = "concept_standardization.concept_domain_validation"
    name = "Concept Domain Validation"
    description = "Validates concept domains match table expectations"
    severity = Severity.WARNING
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

### Registration Pattern

Import from the package `__init__.py` for the implementation area:

```python
from .concept_domain_validation import ConceptDomainValidationRule
```

### Add Tests

```python
# tests/test_domain_validation.py

import pytest
from fastssv.rules.concept_standardization.concept_domain_validation import ConceptDomainValidationRule

class TestDomainValidation:
    @pytest.fixture
    def rule(self):
        return ConceptDomainValidationRule()

    def test_valid_domain_filter(self, rule):
        sql = """
        SELECT * FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id = 'Drug'
        """
        violations = rule.validate(sql)
        assert len(violations) == 0

    def test_missing_domain_filter(self, rule):
        sql = """
        SELECT * FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_name LIKE '%aspirin%'
        """
        violations = rule.validate(sql)
        assert len(violations) > 0
        assert violations[0].rule_id == "concept_standardization.concept_domain_validation"
```

## Coverage status

The seven examples above are a sampling, not the full rule set. The current registry has **154 rules across 6 categories** (`anti_patterns`, `concept_standardization`, `data_quality`, `domain_specific`, `joins`, `temporal`) — see [Rules reference](rules_reference.md) for the per-rule catalog with severities, examples, and suggested fixes.

For the live registered set at any moment:

```python
from fastssv import get_all_rules
for rule_cls in get_all_rules():
    rule = rule_cls()
    print(rule.rule_id, rule.severity.name)
```

## File Locations

### Schema Definitions
- `src/fastssv/schemas/cdm_column_types.py` - canonical OMOP CDM v5.4 table → {column → type} map (single source of truth); `CDM_COLUMNS` is derived from this
- `src/fastssv/schemas/semantic_schema.py` - `STANDARD_CONCEPT_FIELDS` (set of (table, column) pairs that must hold standard concept ids)

### Rule Implementations
- `src/fastssv/rules/concept_standardization/` - standard concept, invalid reason, hierarchy, concept-domain rules
- `src/fastssv/rules/joins/` - join path, maps-to direction, concept relationship join rules
- `src/fastssv/rules/temporal/` - observation-period and temporal logic rules
- `src/fastssv/rules/data_quality/` - schema and structural validation rules
- `src/fastssv/rules/domain_specific/` - condition, drug, visit, measurement, and other table-family rules

### Tests
- `tests/test_rules.py` - Main validation tests for all rules
- `tests/test_integration.py` - Integration tests for the validation API

### Main API
- `src/fastssv/__init__.py` - Public API exports

## Adding New Semantic Rules

The full rule-author walkthrough — `Rule` subclass, `@register` decorator, category `__init__.py` wiring, and the test class added to `tests/test_rules.py` — lives in [Plugin system](plugin_architecture.md#creating-a-new-rule). Semantic rules use the same scaffolding as any other rule; pick `concept_standardization`, `joins`, or `temporal` as the category and follow the four-step recipe there.

### Modifying field classifications

If your new rule needs to distinguish standard- vs source-concept-id columns, add the `(table, column)` pair to `STANDARD_CONCEPT_FIELDS` in `src/fastssv/schemas/semantic_schema.py`. The schema-consistency suite at `tests/test_schema_consistency.py` will fail at import if the entry references a column that doesn't exist in `CDM_COLUMN_TYPES`, so spec drift is caught immediately.

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
    has_table_reference,    # Check if query uses a table
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
violations = validate_sql_structured(sql, categories=["concept_standardization"])
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
from fastssv.rules.concept_standardization.standard_concept_enforcement import StandardConceptEnforcementRule

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

- **154 registered rules across 6 categories** validating OMOP CDM v5.4 constraints
- **Plugin architecture** for easy extension — see [Plugin system](plugin_architecture.md)
- **Schema-driven validation** anchored in `schemas/cdm_column_types.py` (table → {column → type}) and `schemas/semantic_schema.py` (`STANDARD_CONCEPT_FIELDS`)
- **Flexible API** supporting filtering by `categories=[...]` or `rule_ids=[...]`

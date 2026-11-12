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
# Run all rules (default, outputs to output/query_report.json — named after the input file)
uv run fastssv query.sql

# Run only concept standardization rules
uv run fastssv query.sql --categories concept_standardization

# Run only anti-pattern rules
uv run fastssv query.sql --categories anti_patterns

# Run multiple categories
uv run fastssv query.sql --categories concept_standardization anti_patterns

# Run specific rules
uv run fastssv query.sql --rules concept_standardization.standard_concept_enforcement concept_standardization.concept_ancestor_rollup_direction

# Custom output path
uv run fastssv query.sql --output my_report.json
```

## Strict mode

`--strict` (CLI) or `strict=True` (Python API, `"strict": true` in the HTTP API) does two things. It escalates the WARNINGs of `anti_patterns.concept_code_requires_vocabulary_id` to ERRORs, and it enables the checks that only run in strict mode: the standard-field check of `concept_standardization.standard_concept_enforcement` and `concept_standardization.invalid_reason_enforcement`, both of which report WARNINGs. No other rule changes severity under `--strict`.

## Understanding Core Rule Groups

FastSSV rules validate OMOP CDM analytical constraints that go beyond SQL syntax. They ensure queries follow OMOP conventions for concept usage, vocabulary relationships, join paths, and temporal constraints.

### Current Concept Standardization Rules

FastSSV currently includes **14 concept standardization rules**. The examples below highlight a representative subset of the most important patterns, not every rule in the category.

#### 1. Standard Concept Enforcement (`concept_standardization.standard_concept_enforcement`)
**Severity:** WARNING

Warns when a `*_source_concept_id` (the pre-mapping layer) is compared with `concept_ancestor` ids. `concept_ancestor` holds only standard and classification concepts, so non-standard source codes silently drop out. Filtering on source concepts by themselves (literal ids, codesets) is legitimate and not flagged. Standard `<event>_concept_id` fields are CDM-guaranteed standard and are trusted in default mode; `--strict` adds a WARNING when a standard field's concept row is not checked by `standard_concept = 'S'`, a `Maps to` mapping or a `concept_ancestor` expansion (a literal concept id does not count, since it can name a non-standard concept).

**Example violation:**
```sql
-- BAD: source concept matched against the standard hierarchy
SELECT co.person_id FROM condition_occurrence co
JOIN concept_ancestor ca ON co.condition_source_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 201826;

-- GOOD: expand the hierarchy on the standard field
SELECT co.person_id FROM condition_occurrence co
JOIN concept_ancestor ca ON co.condition_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 201826;
```

#### 2. Concept Join Validation (`joins.concept_join_validation`)
**Severity:** ERROR

A `*_concept_id` column is a foreign key to `concept.concept_id`. Joining it to any other concept column (`concept_name`, `concept_code`, …) compares an integer id with text and matches nothing meaningful.

**Example violation:**
```sql
-- BAD: concept id joined to the concept name
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_name;

-- GOOD: join on the primary key
SELECT co.person_id, c.concept_name
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id;
```

#### 3. Concept Ancestor Rollup Direction (`concept_standardization.concept_ancestor_rollup_direction`)
**Severity:** WARNING

When rolling up to ancestor concepts via `concept_ancestor`, the join direction
must match the intent. Filtering on `ancestor_concept_id` retrieves descendants
of that ancestor; filtering on `descendant_concept_id` retrieves ancestors of
that descendant. A record concept joined to `ancestor_concept_id` with a fixed
`descendant_concept_id` returns the concept and its broader concepts, not its
descendants. The CDM allows querying the table "the other way around", so the
upward lookup can be intended, and the finding is a WARNING.

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

The CDM v5.4 OBSERVATION_PERIOD user guide says that the absence of an event outside the observation periods "cannot be construed as evidence of absence", and that incidence or prevalence rates should only be calculated for observed time. The rule warns on a per-person absence test over a clinical event table (`NOT EXISTS`, `NOT IN`, `LEFT JOIN … IS NULL`, `EXCEPT`) and on a count of events divided by a count of persons, when the query never reads `observation_period` for them. A plain calendar-window filter on recorded events is not reported. The related check that `observation_period` is linked to the clinical table on `person_id` is `joins.observation_period_unlinked`.

**Example violation:**
```sql
-- BAD: "no drug exposure in 2020", also for people not observed in 2020
SELECT p.person_id
FROM person p
WHERE NOT EXISTS (
  SELECT 1 FROM drug_exposure de
  WHERE de.person_id = p.person_id
    AND de.drug_exposure_start_date >= DATE '2020-01-01'
    AND de.drug_exposure_start_date < DATE '2021-01-01');

-- GOOD: only people observed for the whole window
SELECT p.person_id
FROM person p
JOIN observation_period op ON op.person_id = p.person_id
 AND op.observation_period_start_date <= DATE '2020-01-01'
 AND op.observation_period_end_date >= DATE '2020-12-31'
WHERE NOT EXISTS (
  SELECT 1 FROM drug_exposure de
  WHERE de.person_id = p.person_id
    AND de.drug_exposure_start_date >= DATE '2020-01-01'
    AND de.drug_exposure_start_date < DATE '2021-01-01');
```

#### 5. Maps-to Direction (`joins.maps_to_direction`)
**Severity:** WARNING

Validates `concept_relationship` 'Maps to' direction (source → standard, not reversed). It fires when a `*_source_concept_id` is matched against the standard side of a mapping (`concept_id_2` of `'Maps to'`, `concept_id_1` of `'Mapped from'`).

**Example violation:**
```sql
-- BAD: source concept matched against the standard side of 'Maps to'
SELECT cr.concept_id_1
FROM condition_occurrence co
JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_2
WHERE cr.relationship_id = 'Maps to';

-- GOOD: source concept on concept_id_1, standard target in concept_id_2
SELECT cr.concept_id_2
FROM condition_occurrence co
JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_1
WHERE cr.relationship_id = 'Maps to';
```

#### 6. Concept Literal Without Hierarchy (`concept_standardization.concept_literal_without_hierarchy`)
**Severity:** WARNING

Warns when a clinical `*_concept_id` is filtered on literal concept ids and the same SELECT does not use `concept_ancestor`: records coded at descendant concepts (subtypes, clinical drugs of an ingredient) may be missed. Exact-concept queries are legitimate, so this is advisory.

**Example violation:**
```sql
-- WARNING: acetaminophen ingredient; its clinical and branded drugs are descendants
SELECT COUNT(DISTINCT person_id) FROM drug_exposure
WHERE drug_concept_id = 1125315;

-- GOOD: expand through concept_ancestor
SELECT COUNT(DISTINCT de.person_id) FROM drug_exposure de
JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 1125315;
```

#### 7. Invalid Reason Enforcement (`concept_standardization.invalid_reason_enforcement`)
**Severity:** WARNING (strict mode only)

In strict mode, ensures vocabulary tables filter by `invalid_reason IS NULL` to exclude deprecated or invalid concepts. Default mode does not report it.

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

> **Note.** `SOURCE_CONCEPT_FIELDS` in the same module lists the `*_source_concept_id` columns of CDM v5.4. It was removed in 0.2.0, when no rule consumed it, and reintroduced for `concept_standardization.standard_concept_enforcement` and `joins.maps_to_direction`.

## Extending Semantic Validation

The plugin architecture makes adding new rules straightforward.

### Example: Concept Domain Validation Is Already Implemented

Concept-domain checking already exists as `concept_standardization.concept_domain_validation`, implemented in `src/fastssv/rules/concept_standardization/concept_domain_validation.py`. Do not add a second domain-validation rule.

```python
# src/fastssv/rules/concept_standardization/concept_domain_validation.py

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.references import BOOK_VOCABULARIES, CDM_FIELD_LEVEL, cdm_table, section
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
    For example, drug_exposure.drug_concept_id only references concepts
    with domain_id = 'Drug', so a concept joined to it and filtered on
    domain_id = 'Condition' can never match.
    """

    rule_id = "concept_standardization.concept_domain_validation"
    name = "Concept Domain Validation"
    description = "Reports domain_id filters that contradict the joined column's domain"
    severity = Severity.ERROR
    suggested_fix = "Filter the joined concept on the column's own domain_id"

    # Required on every rule: a triggering example, its corrected version,
    # and the published sources the claim is derived from.
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.domain_id = 'Drug';"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.domain_id = 'Condition';"
    )
    references = (
        CDM_FIELD_LEVEL,
        cdm_table("condition_occurrence"),
        section(BOOK_VOCABULARIES, "conceptDomains", "Domains"),
    )

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
                # A missing domain_id filter is not a defect: the column's domain
                # is fixed by the CDM. Only a contradicting filter empties the join.
                filtered_domain = self._filtered_domain(tree, aliases)

                if filtered_domain and filtered_domain != expected_domain:
                    violations.append(
                        RuleViolation(
                            rule_id=self.rule_id,
                            severity=self.severity,
                            message=f"Query joins {table}.{column} to a concept filtered on "
                                   f"domain_id = '{filtered_domain}'; the column only holds "
                                   f"'{expected_domain}' concepts, so the join matches nothing.",
                            suggested_fix=f"Filter on concept.domain_id = '{expected_domain}' "
                                        f"or drop the domain_id filter",
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

    def _filtered_domain(self, tree, aliases):
        """Return the domain_id literal the joined concept is filtered on, if any."""
        # Implementation: find concept.domain_id = '<domain>' in WHERE/JOIN ON
        return None
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

    def test_contradicting_domain_filter(self, rule):
        sql = """
        SELECT * FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id = 'Condition'
        """
        violations = rule.validate(sql)
        assert len(violations) > 0
        assert violations[0].rule_id == "concept_standardization.concept_domain_validation"
```

## Coverage status

The seven examples above are a sampling, not the full rule set. The current registry has **92 rules across 6 categories** (`anti_patterns`, `concept_standardization`, `data_quality`, `domain_specific`, `joins`, `temporal`) — see [Rules reference](rules_reference.md) for the per-rule catalog with severities, examples, and suggested fixes.

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
- `src/fastssv/rules/joins/` - foreign-key join, maps-to direction, concept relationship join rules
- `src/fastssv/rules/temporal/` - observation-period and temporal logic rules
- `src/fastssv/rules/data_quality/` - schema and structural validation rules
- `src/fastssv/rules/domain_specific/` - condition, drug, visit, measurement, and other table-family rules

### Tests
- `tests/test_rules.py` - Main validation tests for all rules
- `tests/test_integration.py` - Integration tests for the validation API

### Main API
- `src/fastssv/__init__.py` - Public API exports

## Adding New Semantic Rules

The full rule-author walkthrough — `Rule` subclass, `@register` decorator, category `__init__.py` wiring, and the test class added to `tests/test_rules.py` — lives in [Plugin system](plugin_architecture.md#creating-a-new-rule). Semantic rules use the same scaffolding as any other rule; pick `concept_standardization`, `joins`, or `temporal` as the category and follow the recipe there.

### Examples and sources are required

Every registered rule sets `example_bad` (SQL that triggers it), `example_good` (the corrected version, which must not trigger it) and `references` (the published sources its claim is derived from: the CDM specification, the Book of OHDSI, THEMIS, engine documentation). `tests/test_rule_metadata.py` runs both examples against the rule and fails on an empty `references`; it also requires a section for the rule in [Rules reference](rules_reference.md) with `#### Examples` and a `#### Sources` list citing every reference URL. The metadata is what the `/rules` page and `GET /v1/rules` show.

To read a rule's examples and sources:

```python
from fastssv import get_rule

rule = get_rule("concept_standardization.concept_domain_validation")()
print(rule.example_bad)
print(rule.example_good)
for ref in rule.references:
    print(ref.title, ref.url)

assert rule.validate(rule.example_bad)       # triggers
assert not rule.validate(rule.example_good)  # clean
```

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

- **92 registered rules across 6 categories** validating OMOP CDM v5.4 constraints
- **Plugin architecture** for easy extension — see [Plugin system](plugin_architecture.md)
- **Schema-driven validation** anchored in `schemas/cdm_column_types.py` (table → {column → type}) and `schemas/semantic_schema.py` (`STANDARD_CONCEPT_FIELDS`)
- **Flexible API** supporting filtering by `categories=[...]` or `rule_ids=[...]`

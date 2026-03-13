# FastSSV Rules Architecture

This directory contains all OMOP CDM validation rules, organized by the type of issue they tackle.

## Directory Structure

```
rules/
├── concept_standardization/    # Standard, valid, and domain-appropriate concepts
│   ├── standard_concept_enforcement.py
│   ├── invalid_reason_enforcement.py
│   ├── hierarchy_expansion.py
│   └── domain_segregation.py
│
├── temporal/                    # Temporal logic and observation period validation
│   ├── observation_period_anchoring.py
│   └── future_information_leakage.py
│
├── joins/                       # Table relationships and join path validation
│   ├── join_path_validation.py
│   └── maps_to_direction.py
│
├── data_quality/                # Schema compliance and unmapped data handling
│   ├── unmapped_concept_handling.py
│   └── schema_validation.py
│
├── domain_specific/             # Table-specific validation rules
│   └── measurement/
│       └── measurement_unit_validation.py
│
└── anti_patterns/               # Common mistakes and anti-patterns
    ├── no_string_identification.py
    ├── concept_code_requires_vocabulary_id.py
    ├── concept_lookup_context.py
    └── concept_name_lookup.py
```

## Rule Categories

### 1. Concept Standardization (4 rules)
**Purpose**: Ensures concepts are standard, valid, hierarchically complete, and domain-appropriate

- **standard_concept_enforcement**: Enforces `standard_concept = 'S'` or 'Maps to' relationship
- **invalid_reason_enforcement**: Filters out deprecated concepts via `invalid_reason IS NULL`
- **hierarchy_expansion**: Requires concept_ancestor for drug/condition hierarchy
- **domain_segregation**: Ensures clinical tables join to concepts with correct domain_id

**When to use**: Any query that filters or joins on concept_id fields

---

### 2. Temporal (2 rules)
**Purpose**: Validates temporal logic and prevents temporal bias in cohort studies

- **observation_period_anchoring**: Ensures temporal constraints are anchored to observation_period
- **future_information_leakage**: Detects temporal bias from cross-table date comparisons

**When to use**: Queries with date filters, cohort definitions, or temporal windows

---

### 3. Joins (2 rules)
**Purpose**: Ensures proper table relationships and join paths

- **join_path_validation**: Verifies concept/concept_relationship tables properly join to clinical tables
- **maps_to_direction**: Checks 'Maps to' relationship direction (concept_id_1 → concept_id_2)

**When to use**: Queries joining vocabulary tables to clinical tables

---

### 4. Data Quality (2 rules)
**Purpose**: Schema compliance and handling of unmapped/missing data

- **unmapped_concept_handling**: Warns when filtering by concept_id without handling concept_id = 0
- **schema_validation**: Validates column references against OMOP CDM schema

**When to use**: All queries (foundational checks)

---

### 5. Domain Specific (1 rule)
**Purpose**: Table-specific validation rules

#### Measurement
- **measurement_unit_validation**: Ensures numeric measurement filters include unit_concept_id

**When to use**: Queries filtering measurement.value_as_number

**Future domains**: drug/, condition/, visit/, etc.

---

### 6. Anti-Patterns (4 rules)
**Purpose**: Common mistakes and anti-patterns to avoid

- **no_string_identification**: Prevents string matching on *_source_value columns
- **concept_code_requires_vocabulary_id**: Ensures concept_code filters include vocabulary_id
- **concept_lookup_context**: Allows concept table string filters only in concept_id lookup contexts
- **concept_name_lookup**: Warns against filtering by concept_name (unstable, non-unique)

**When to use**: Educational - catches common beginner mistakes

---

## How Rules Work

### Registration
All rules use the `@register` decorator to auto-register themselves:

```python
from fastssv.core.registry import register

@register
class MyRule(Rule):
    rule_id = "category.my_rule"
    name = "My Rule"
    severity = Severity.ERROR

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        # Implementation
        pass
```

### Accessing Rules
```python
from fastssv.rules import get_all_rules, get_rules_by_category

# Get all rules
all_rules = get_all_rules()

# Get rules by category (legacy)
semantic_rules = get_rules_by_category("semantic")
vocabulary_rules = get_rules_by_category("vocabulary")
```

### Running Validation
```python
from fastssv.rules import get_all_rules

sql = "SELECT * FROM condition_occurrence WHERE condition_source_value = 'E11.9'"

for rule_cls in get_all_rules():
    rule = rule_cls()
    violations = rule.validate(sql)
    for v in violations:
        print(f"[{v.severity}] {v.message}")
```

---

## Adding New Rules

### Step 1: Choose the Right Category

- **Concept issues?** → `concept_standardization/`
- **Temporal issues?** → `temporal/`
- **Join issues?** → `joins/`
- **Data quality issues?** → `data_quality/`
- **Table-specific?** → `domain_specific/{table}/`
- **Common mistake?** → `anti_patterns/`

### Step 2: Create the Rule File

```python
"""My New Rule.

Brief description of what this rule validates and why it matters.
"""

from typing import List
from sqlglot import exp
from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register


@register
class MyNewRule(Rule):
    """One-line description."""

    rule_id = "category.my_new_rule"
    name = "My New Rule"
    description = "Detailed description of what this rule checks"
    severity = Severity.ERROR  # or Severity.WARNING
    suggested_fix = "How to fix violations"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            # Your validation logic here
            pass

        return violations


__all__ = ["MyNewRule"]
```

### Step 3: Update Category `__init__.py`

Add your rule to the category's `__init__.py`:

```python
from .my_new_rule import MyNewRule

__all__ = [
    # ... existing rules
    "MyNewRule",
]
```

### Step 4: Test Your Rule

```python
from fastssv.rules import get_all_rules

# Your rule should auto-register
rules = get_all_rules()
print(f"Total rules: {len(rules)}")

# Test with sample SQL
test_sql = "..."
for rule_cls in rules:
    if rule_cls.rule_id == "category.my_new_rule":
        rule = rule_cls()
        violations = rule.validate(test_sql)
        print(violations)
```

---

## Migration Notes

### Before (Old Structure)
```
rules/
├── semantic/       # Mixed semantic rules
│   ├── standard_concept.py
│   ├── unmapped_concept.py
│   └── ...
└── vocabulary/     # Mixed vocabulary rules
    ├── no_string_id.py
    └── ...
```

### After (New Structure)
Rules are organized by **issue type**, not just "semantic" vs "vocabulary". This makes it easier to:
- Find rules related to a specific problem
- Add new rules to logical categories
- Understand what each category validates

### Legacy Compatibility
The old `semantic` and `vocabulary` categories still work via `get_rules_by_category()`, but new code should use the new structure.

---

## Related Documentation

- **Implementation Status**: See `/rules/IMPLEMENTATION_STATUS.md` for a checklist of all rules from `omop_rules.json`
- **Rule Reference**: See `/rules/omop_rules.json` for the full list of planned rules
- **Core API**: See `/src/fastssv/core/` for base classes and helpers

---

## Statistics

- **Total Rules**: 15 (as of 2025-03-13)
- **Categories**: 6
- **Coverage**: ~7-10% of omop_rules.json (350+ rules)
- **Focus**: Critical semantic violations that lead to incorrect analytical results

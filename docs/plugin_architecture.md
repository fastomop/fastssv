# FastSSV Plugin Architecture

## Overview

FastSSV uses a **plugin-based rule system** where validation rules are automatically discovered and registered at import time. This architecture provides:

- **Extensibility**: Add new rules without modifying core code
- **Modularity**: Each rule is independent and self-contained
- **Flexibility**: Enable/disable rules by category or ID
- **Maintainability**: Clear separation of concerns

## Core Components

### 1. Rule Base Class (`core/base.py`)

All validation rules inherit from the abstract `Rule` class:

```python
from abc import ABC, abstractmethod
from typing import List

class Rule(ABC):
    """Abstract base class for validation rules."""

    rule_id: str
    name: str
    description: str
    severity: Severity
    suggested_fix: str

    @abstractmethod
    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL query and return violations."""
        pass
```

### 2. RuleViolation Data Class (`core/base.py`)

Violations are returned as structured objects:

```python
from dataclasses import dataclass, field
from enum import Enum

class Severity(Enum):
    """Severity level for rule violations."""
    ERROR = "error"      # Blocks validation (gates the CLI exit code)
    WARNING = "warning"  # Best-practice issue; escalated under --strict

@dataclass
class RuleViolation:
    rule_id: str                    # e.g., "concept_standardization.standard_concept_enforcement"
    severity: Severity              # ERROR or WARNING
    message: str                    # Human-readable issue description
    suggested_fix: str              # Prose fix recommendation
    location: Optional[str] = None  # Optional location info (table.column or SQL fragment)
    details: dict = field(default_factory=dict)  # In-process metadata; not serialised
    suggested_fix_patch: Optional[dict] = None   # Mechanical patch (REPLACE/ADD/REMOVE) when applicable
```

In-process consumers (Python API users, rule authors writing tests) interact with these fields directly. The JSON serialisation differs:

```python
def to_dict(self) -> dict:
    """Serialise for the CLI report and the HTTP API.

    The single ``fix`` field unifies prose and mechanical patches:
    - For FREEFORM patches (the default for ~60% of rules), ``fix`` is the
      prose ``suggested_fix`` string.
    - For REPLACE/ADD/REMOVE patches (~40%), ``fix`` is a patch dict
      ``{"action": ..., "span": [s, e] | "at": pos, "text": ...}``.
    Consumers switch on ``isinstance(fix, str)`` vs ``dict``.

    ``details`` stays on the dataclass for tests and rule-side
    diagnostics but is intentionally excluded from the wire format —
    the keys vary too much across rules to be programmatically useful.
    """
    result = {
        "rule_id": self.rule_id,
        "severity": self.severity.value,   # "error" | "warning"
        "issue": self.message,
    }
    fix = self._serialised_fix()           # prose string OR patch dict
    if fix is not None:
        result["fix"] = fix
    if self.location:
        result["location"] = self.location
    return result
```

So the wire shape for a single violation is `{rule_id, severity, issue, fix?, location?}`. See [JSON output](json_output.md) for the full CLI report shape (which wraps these in `errors[]` and `warnings[]` arrays) and [HTTP API](api.md) for the same shape on the API surface.

### 3. Registry System (`core/registry.py`)

The registry manages rule discovery and access:

```python
from typing import Type

# Global registry
_registry: dict[str, Type[Rule]] = {}

def register(cls: Type[Rule]) -> Type[Rule]:
    """Decorator to register a rule class by its rule_id attribute."""
    _registry[cls.rule_id] = cls
    return cls

def get_rule(rule_id: str) -> Type[Rule]:
    """Get a rule class by ID."""
    return _registry[rule_id]

def get_rules_by_category(category: str) -> list[Type[Rule]]:
    """Get all rules in a category."""
    prefix = category + "."
    return [r for r in _registry.values() if r.rule_id.startswith(prefix)]

def get_all_rules() -> list[Type[Rule]]:
    """Get all registered rules."""
    return list(_registry.values())
```

## Creating a New Rule

### Step 1: Create Rule File

Create a new Python file in the appropriate implementation directory:

```
src/fastssv/rules/
├── concept_standardization/
├── anti_patterns/
├── joins/
├── temporal/
├── data_quality/
└── domain_specific/
```

### Step 2: Implement the Rule Class

```python
# src/fastssv/rules/joins/my_new_rule.py

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.registry import register
from fastssv.core.helpers import parse_sql

@register
class MyNewRule(Rule):
    """
    Detailed documentation of the rule.

    Explains:
    - What OMOP CDM constraint this validates
    - Why this constraint matters
    - Examples of violations
    - How to fix violations
    """

    rule_id = "joins.my_new_rule"
    name = "My New Rule"
    description = "Brief description of what this rule checks"
    severity = Severity.ERROR
    suggested_fix = "Specific recommendation for fixing this violation"

    def validate(self, sql: str, dialect: str = "postgres") -> list[RuleViolation]:
        """
        Validate SQL query against this rule.

        Args:
            sql: SQL query string to validate
            dialect: SQL dialect (postgres, mysql, duckdb, etc.)

        Returns:
            List of RuleViolation objects. Empty list means no violations.
        """
        violations = []

        # Parse SQL into AST
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            # Skip validation if SQL doesn't parse
            return violations

        # Validation logic here
        if self._detect_violation(trees[0]):
            violations.append(
                RuleViolation(
                    rule_id=self.rule_id,
                    severity=Severity.ERROR,  # or Severity.WARNING
                    message="Clear explanation of what went wrong",
                    suggested_fix="Specific recommendation for fixing this violation",
                    location="optional: table.column or line number",  # Optional
                    details={"key": "value"}  # Optional structured metadata
                )
            )

        return violations

    def _detect_violation(self, tree) -> bool:
        """Helper method for violation detection logic."""
        # Implementation details
        return False
```

### Step 3: Register the Rule

Import the rule in the package's `__init__.py`:

```python
# src/fastssv/rules/joins/__init__.py

from .my_new_rule import MyNewRule
```

### Step 4: Add Tests

Add a test class to `tests/test_rules.py` — the canonical file for rule tests. New test files are not picked up by the existing `extend-exclude` ruff config and would diverge from project convention.

```python
# tests/test_rules.py — append a new class

import pytest
from fastssv.rules.joins.my_new_rule import MyNewRule

class TestMyNewRule:

    @pytest.fixture
    def rule(self):
        return MyNewRule()

    def test_valid_query_passes(self, rule):
        sql = "SELECT * FROM person"
        violations = rule.validate(sql)
        assert len(violations) == 0

    def test_invalid_query_fails(self, rule):
        sql = "SELECT * FROM invalid_table"
        violations = rule.validate(sql)
        assert len(violations) > 0
        assert violations[0].rule_id == "joins.my_new_rule"
```

## Using Rules

### Command Line Interface

```bash
# Run all rules (default)
fastssv query.sql

# Run specific category
fastssv query.sql --categories joins

# Run multiple categories
fastssv query.sql --categories joins temporal

# Run specific rule
fastssv query.sql --rules joins.my_new_rule

# Run multiple specific rules
fastssv query.sql --rules joins.join_path_validation concept_standardization.standard_concept_enforcement
```

### Python API

```python
from fastssv import validate_sql_structured

# Run all rules
violations = validate_sql_structured(sql)

# Run specific category
violations = validate_sql_structured(sql, categories=["joins"])

# Run specific rules
violations = validate_sql_structured(
    sql,
    rule_ids=["joins.my_new_rule", "anti_patterns.concept_name_lookup"]
)

# Process violations
for v in violations:
    print(f"{v.severity}: [{v.rule_id}] {v.message}")
```

## Rule Categories

### Core Categories

Current registered category counts (see [rules_reference.md](rules_reference.md)
for the full list):

- `anti_patterns`: 20
- `concept_standardization`: 18
- `data_quality`: 22
- `domain_specific`: 48
- `joins`: 36
- `temporal`: 10

**Total: 154**

Representative examples from the current public categories:

- `joins.join_path_validation` - Table join validation against OMOP CDM v5.4 schema
- `concept_standardization.standard_concept_enforcement` - Standard concept enforcement (concept.standard_concept = 'S')
- `concept_standardization.concept_ancestor_rollup_direction` - Validates ancestor/descendant direction of concept_ancestor rollups
- `temporal.observation_period_anchoring` - Temporal constraints anchored to observation_period
- `joins.maps_to_direction` - Maps-to relationship direction validation (source → standard)
- `data_quality.unmapped_concept_handling` - Unmapped concept (concept_id = 0) handling
- `concept_standardization.invalid_reason_enforcement` - Invalid reason enforcement for vocabulary tables
- `anti_patterns.no_string_identification` - String-based concept ID lookup detection
- `anti_patterns.concept_name_lookup` - Concept table concept_name filter detection
- `anti_patterns.concept_code_requires_vocabulary_id` - Concept code uniqueness enforcement (requires vocabulary_id)

### Adding New Categories

To add a new category:

1. Create directory: `src/fastssv/rules/new_category/`
2. Add `__init__.py`
3. Add rule files
4. Import in `src/fastssv/rules/__init__.py`:
   ```python
   from . import joins, new_category
   ```

## Best Practices

### Rule Implementation

1. **Single Responsibility**: Each rule should check one specific constraint
2. **Clear Messages**: Violation messages should explain what's wrong AND how to fix it
3. **Graceful Degradation**: Handle parse errors gracefully
4. **Performance**: Cache expensive operations when possible
5. **Testing**: Write comprehensive tests for valid and invalid cases

### Violation Messages

Good violation messages follow this pattern:

**Message field**: \[What's wrong\] + \[Why it matters\]
**Suggested_fix field**: \[How to fix it\]

Example:
```python
RuleViolation(
    rule_id="concept_standardization.standard_concept_enforcement",
    severity=Severity.ERROR,
    message="Query uses STANDARD concept field 'drug_concept_id' without enforcing "
            "standard_concept = 'S'. This may include non-standard concepts in results.",
    suggested_fix="Add WHERE clause: concept.standard_concept = 'S' OR use concept_relationship "
                  "with relationship_id = 'Maps to'"
)
```

### Severity Guidelines

Use **ERROR** for:
- Schema violations (invalid joins, missing tables)
- Semantic violations (wrong concept usage)
- Logic errors that will produce incorrect results

Use **WARNING** for:
- Style issues (inefficient patterns)
- Best practice recommendations
- Potential performance issues

## Helper Utilities

### SQL Parsing (`core/helpers.py`)

```python
from fastssv.core.helpers import parse_sql

# Parse SQL into sqlglot AST
trees, parse_error = parse_sql(sql, dialect="postgres")

if parse_error:
    # Handle parse error
    return []

# Work with AST
tree = trees[0]
```

### Common Patterns

Extract tables:
```python
from sqlglot import exp

tables = [
    table.name
    for table in tree.find_all(exp.Table)
]
```

Extract joins:
```python
joins = list(tree.find_all(exp.Join))
for join in joins:
    # Process join
    pass
```

Extract WHERE conditions:
```python
where = tree.find(exp.Where)
if where:
    # Process WHERE clause
    pass
```

## Architecture Benefits

1. **Zero Configuration**: Rules are auto-discovered
2. **Easy Extension**: Add rules without touching core code
3. **Selective Execution**: Run only needed rules
4. **Clean Separation**: Each rule is independent
5. **Testability**: Rules can be tested in isolation
6. **Maintainability**: Changes to one rule don't affect others

## Summary

The plugin architecture makes FastSSV:
- **Extensible**: Easy to add new rules
- **Flexible**: Granular control over which rules run
- **Maintainable**: Clear structure and separation
- **Testable**: Each rule can be tested independently

To add a new rule, simply:
1. Create a rule class with `@register`
2. Import it in `__init__.py`
3. Add tests

That's it! The rule is automatically available throughout the system.

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
from typing import List, Optional, Tuple

class Rule(ABC):
    """Abstract base class for validation rules."""

    rule_id: str
    name: str
    description: str
    severity: Severity
    suggested_fix: str

    # Metadata shown on the /rules page, in GET /v1/rules and in the rules reference.
    example_bad: Optional[str] = None                 # SQL that triggers the rule
    example_good: Optional[str] = None                # corrected version; must not trigger
    extra_examples: Tuple[RuleExample, ...] = ()      # one per further severity / variant
    references: Tuple[Reference, ...] = ()            # published sources of the claim

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
    WARNING = "warning"  # Advice; gates the exit code only for rules escalated under --strict

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

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import cdm_table
from fastssv.core.registry import register


@register
class MyNewRule(Rule):
    """payer_plan_period belongs to a person; a join to it must use person_id."""

    rule_id = "joins.my_new_rule"
    name = "My New Rule"
    description = "payer_plan_period is joined without its person_id key."
    severity = Severity.ERROR
    suggested_fix = "ADD: `ppp.person_id = <other>.person_id` to the JOIN ON clause."

    # Required metadata: an example that triggers the rule, its corrected
    # version, and the published sources the claim is derived from.
    example_bad = (
        "SELECT p.person_id, ppp.payer_concept_id\n"
        "FROM person p\n"
        "JOIN payer_plan_period ppp ON p.provider_id = ppp.payer_plan_period_id;"
    )
    example_good = (
        "SELECT p.person_id, ppp.payer_concept_id\n"
        "FROM person p\n"
        "JOIN payer_plan_period ppp ON p.person_id = ppp.person_id;"
    )
    references = (cdm_table("payer_plan_period"),)

    def validate(self, sql: str, dialect: str = "postgres") -> list[RuleViolation]:
        """Return one violation per offending join; an empty list means none."""
        violations = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            # Skip validation if SQL doesn't parse
            return violations

        for tree in trees:
            for join in tree.find_all(exp.Join):
                on = join.args.get("on")
                if not isinstance(join.this, exp.Table) or on is None:
                    continue
                if join.this.name.lower() != "payer_plan_period":
                    continue
                if "person_id" not in {c.name.lower() for c in on.find_all(exp.Column)}:
                    violations.append(
                        self.create_violation(
                            message="payer_plan_period is joined on a key other than person_id.",
                            location=join.sql(dialect=dialect),  # Optional
                            details={"on": on.sql(dialect=dialect)},  # Optional structured metadata
                        )
                    )

        return violations
```

`example_bad`, `example_good` and `references` are required for every
registered rule. `tests/test_rule_metadata.py` runs over the whole registry and
fails when `example_bad` does not trigger the rule, when `example_good` does,
or when `references` is empty.

- `references` takes `Reference(title, url)` values (`fastssv.core.base`).
  `fastssv.core.references` has helpers for the sources most rules share:
  `cdm_table("<table>")` for a table's section of the CDM v5.4 specification,
  page-level constants such as `CDM_CONVENTIONS` and `BOOK_VOCABULARIES`, and
  `section(SOURCE, "anchor", "topic")` for a named section of one of them.
  Write `Reference("Title", "https://…")` inline for anything else. Cite a
  source only after reading the passage that supports the rule's claim.
- If the rule emits a second severity or a materially different variant, add
  `extra_examples = (RuleExample(sql, Severity.WARNING, "what this variant shows"),)`
  (`RuleExample` is in `fastssv.core.base`; pass `strict=True` for a finding
  that only appears in strict mode). The same test checks that each one emits
  its declared severity.

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
        violations = rule.validate(rule.example_good)
        assert len(violations) == 0

    def test_invalid_query_fails(self, rule):
        violations = rule.validate(rule.example_bad)
        assert len(violations) > 0
        assert violations[0].rule_id == "joins.my_new_rule"
```

### Step 5: Document the Rule

Add a section for the rule to [rules_reference.md](rules_reference.md) and a row
to its quick-reference table. The section needs an `#### Examples` block that
contains `example_bad` and `example_good`, and a `#### Sources` list with every
URL in `references`; `tests/test_rule_metadata.py` checks the section, the
examples heading and each URL. The `/rules` page and `GET /v1/rules` pick the
metadata up from the class, with no further wiring.

## Using Rules

### Command Line Interface

```bash
# Run all rules (default)
uv run fastssv query.sql

# Run specific category
uv run fastssv query.sql --categories joins

# Run multiple categories
uv run fastssv query.sql --categories joins temporal

# Run specific rule
uv run fastssv query.sql --rules joins.my_new_rule

# Run multiple specific rules
uv run fastssv query.sql --rules joins.concept_join_validation concept_standardization.standard_concept_enforcement
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

- `anti_patterns`: 15
- `concept_standardization`: 14
- `data_quality`: 14
- `domain_specific`: 19
- `joins`: 23
- `temporal`: 7

**Total: 92**

Representative examples from the current public categories:

- `joins.concept_join_validation` - `*_concept_id` columns must join to `concept.concept_id`
- `concept_standardization.standard_concept_enforcement` - Source concepts matched against concept_ancestor (strict mode: a WARNING when a standard field's concept row is not checked)
- `concept_standardization.concept_ancestor_rollup_direction` - Validates ancestor/descendant direction of concept_ancestor rollups
- `temporal.observation_period_anchoring` - Absence tests and event-per-person rates without observation_period
- `joins.maps_to_direction` - Maps-to relationship direction validation (source → standard)
- `concept_standardization.invalid_reason_enforcement` - Invalid reason enforcement for vocabulary tables
- `anti_patterns.no_string_identification` - String filters on `*_source_value` columns that have a concept counterpart
- `anti_patterns.concept_name_lookup` - concept_name / concept_synonym_name lookup detection
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

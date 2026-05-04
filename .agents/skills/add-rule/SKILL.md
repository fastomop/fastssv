---
name: add-rule
description: Add a new validation rule to FastSSV — pick a category, write the Rule subclass with @register, wire it into the category __init__.py, and cover it in tests/test_rules.py. Use whenever the user wants to introduce a new check on OMOP SQL.
---

# Add a new validation rule

FastSSV rules are one-rule-per-file Python modules under `src/fastssv/rules/<category>/`. Each rule subclasses `Rule` (from `fastssv.core.base`) and self-registers via the `@register` decorator (from `fastssv.core.registry`). The category `__init__.py` imports the class so the registration side effect fires when `fastssv.rules` is imported.

## 1. Pick a category

| Category | When |
| --- | --- |
| `anti_patterns` | Common mistakes / patterns to avoid (e.g. filtering by `concept_name`, missing vocabulary qualifier) |
| `concept_standardization` | Standard-concept enforcement, domain validation, concept-relationship direction |
| `data_quality` | Generic SQL/data hygiene (NULLs, duplicates, unbounded scans) that still rides on OMOP shape |
| `domain_specific` | Rules tied to a specific OMOP domain table (drug, condition, measurement, …) or cross-cutting domain logic |
| `joins` | Join-shape and join-key correctness across OMOP tables |
| `temporal` | Time-window, observation-period, era-overlap, datetime literal handling |

If no category fits cleanly, prefer the closest existing one over inventing a new bucket — categories are also a public surface (the `rule_id` prefix).

## 2. Create the rule file

**File location depends on the category.** Five categories are flat (one rule per file, directly under the category package). `domain_specific` is the exception: it has nested table-specific subpackages so per-table rules can be grouped, while still keeping cross-cutting domain rules at the top level.

| Category | Layout | Path for a new rule |
| --- | --- | --- |
| `anti_patterns`, `concept_standardization`, `data_quality`, `joins`, `temporal` | flat | `src/fastssv/rules/<category>/<snake_name>.py` |
| `domain_specific` (table-specific rule, e.g. measurement, drug, condition, person, procedure, observation, visit, death, specimen, cohort, cost, episode, visit_detail, location, vocabulary, note) | nested | `src/fastssv/rules/domain_specific/<table>/<table>_<snake_name>.py` |
| `domain_specific` (cross-cutting, not tied to one table — e.g. `event_cardinality_validation.py`, `event_field_polymorphic_resolution.py`, `dose_era_cross_unit_comparison.py`) | flat (top level) | `src/fastssv/rules/domain_specific/<snake_name>.py` |

Naming: descriptive snake_case **without** a `rule_` prefix (e.g. `datetime_between_date_literal.py`, `observation_period_anchoring.py`). For nested `domain_specific` rules, the filename conventionally **starts with the table name** (`measurement_cross_unit_comparison.py`, `drug_days_supply_validation.py`) so the rule reads cleanly when the file is opened in isolation. Class name: PascalCase ending in `Rule`.

`rule_id` is `<category>.<snake_name>` — the [documented stable format](../../../README.md#stability) and the convention across all 6 categories. The directory nesting under `domain_specific/<table>/` is organisational only and **does not** appear in the id (e.g. `domain_specific.measurement_cross_unit_comparison`, not `domain_specific.measurement.cross_unit_comparison`).

Template:

```python
"""<One-line summary> Rule.

OMOP context:
<Why this matters in OMOP terms — what silent failure does it catch?>
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register


@register
class <Name>Rule(Rule):
    """<One-sentence what-it-warns-against.>"""

    rule_id = "<category>.<snake_name>"
    name = "<Human-readable name>"
    description = (
        "<Short summary shown in tool output and rule lists. "
        "Two sentences max.>"
    )
    severity = Severity.WARNING  # or Severity.ERROR
    suggested_fix = (
        "<Imperative fix. Prefer the structured prefixes used elsewhere: "
        "`REPLACE: ... WITH ...`, `ADD: ...`, `REMOVE: ...`.>"
    )
    long_description = (
        "<Optional. Multi-sentence explanation rendered on /rules.>"
    )
    example_bad = "<Optional. Minimal SQL that trips the rule.>"
    example_good = "<Optional. Corrected version of example_bad.>"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []
        trees, _err = parse_sql(sql, dialect)
        if not trees:
            return violations
        for tree in trees:
            # Swap exp.Select for the node type your rule targets, then
            # implement the check. Reuse helpers from fastssv.core.helpers
            # (extract_aliases, resolve_table_col, is_string_literal,
            # normalize_name, …) before hand-rolling AST walks. Emit
            # violations with self.create_violation(...).
            for _node in tree.find_all(exp.Select):
                pass
        return violations
```

The scaffold above is **F401-clean** — every import has a use site (`exp.Select` in `find_all`, `parse_sql` in the body, the rest in the class definition). Strip the inner `for _node` loop only when you've replaced it with your real check; deleting the import without deleting its only use will break ruff.

The `rule_id` MUST be `<category>.<snake_name>` and match the file name. The category prefix is what `get_rules_by_category()` keys on.

Look at a sibling rule in the same category as a working template before writing from scratch — naming, helper usage, and message tone are all consistent within a category.

## 3. Wire it into the closest `__init__.py`

The `@register` decorator only fires when the module is imported, so the rule's package `__init__.py` must import the class. **Pick the closest `__init__.py` to the file you just created**:

| Where the rule file lives | `__init__.py` to edit |
| --- | --- |
| `src/fastssv/rules/<category>/<snake_name>.py` (flat categories) | `src/fastssv/rules/<category>/__init__.py` |
| `src/fastssv/rules/domain_specific/<table>/<table>_<snake_name>.py` | `src/fastssv/rules/domain_specific/<table>/__init__.py` (the parent already re-exports the table subpackage via `from .<table> import *`) |
| `src/fastssv/rules/domain_specific/<snake_name>.py` (top-level cross-cutting) | `src/fastssv/rules/domain_specific/__init__.py` |

Add the import:

```python
from .<snake_name> import <Name>Rule
```

…and append `"<Name>Rule"` to `__all__`. **Do not edit `src/fastssv/rules/__init__.py`** — it imports category packages, not individual rules.

## 4. Test passing AND failing SQL

`tests/test_rules.py` is the canonical home for rule tests. Add at least:

- One SQL string the rule should **pass** (no violations).
- One SQL string the rule should **fail** (one or more violations) — assert the `rule_id` matches.

If the rule exposes new public behaviour (severity, message wording, fix shape), assert on it directly rather than through string matching where possible.

Run:

```sh
uv run --frozen --no-sync pytest tests/test_rules.py -v -k <snake_name>
```

…then the full file before opening a PR:

```sh
uv run --frozen --no-sync pytest tests/test_rules.py -v
```

## 5. Update the rest of the repo

- **CHANGELOG.md** — add a bullet under `## [Unreleased]` → `### Added`. Match the existing bold-lead-in style: what was added, why it matters in OMOP terms, what severity it ships with.
- **README.md** — bump the rules badge if you're tracking the count there (`![Rules](https://img.shields.io/badge/rules-N-orange)`).
- **docs/rules_reference.md** — re-generate or update the per-rule entry if the docs site lists rules manually.

## 6. Pre-flight

```sh
uvx prek run --all-files                              # whitespace, EOL, ruff
uv run --frozen --no-sync pytest tests/ -v --cov      # full suite + coverage gate (79)
```

Coverage gate is `fail_under = 79`; new rules typically lift it, so a drop means the new rule has untested branches.

## Anti-patterns to avoid

- **No `rule_` prefix on filenames.** Match existing naming.
- **Don't catch bare `Exception` in `validate()`.** Let parse failures bubble — the engine handles them centrally.
- **Don't hand-roll AST traversal** when `fastssv.core.helpers` already has `extract_aliases`, `resolve_table_col`, `is_string_literal`, etc. Reusing helpers keeps rules consistent across categories.
- **Don't add new top-level dependencies for a single rule.** If you need a heavy library, raise it in the PR before adding it.
- **Don't gate on `dialect`** unless the rule genuinely behaves differently per dialect; default to `postgres` semantics and let `sqlglot` normalise the AST.

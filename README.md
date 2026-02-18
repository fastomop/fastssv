# FastSSV — FastOMOP Semantic Static Validator

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![OMOP CDM](https://img.shields.io/badge/OMOP-CDM%20v5.4-5C4EE5)
![Rules](https://img.shields.io/badge/rules-11-orange)
![Status](https://img.shields.io/badge/status-beta-yellow)
![Tests](https://github.com/fastomop/fastSSV/actions/workflows/tests.yml/badge.svg)

OMOP SQL that runs without errors can still be analytically wrong.

A query that silently drops 30% of patients because it misses concept descendants, filters on a deprecated concept, or applies a temporal constraint outside a patient's observation window will execute cleanly, return plausible numbers, and produce a flawed study. FastSSV catches these violations before they reach results.

---

## Why FastSSV

Existing OHDSI tools validate data quality, characterise cohorts, and measure phenotype performance. None of them validate whether the *SQL logic itself* follows OMOP CDM rules. FastSSV fills that gap.

**It targets silent failures.** The violations FastSSV catches are not syntax errors or missing columns — they are cases where the SQL is valid, the query returns results, and those results are wrong. Missing hierarchy expansion, reversed concept relationship direction, temporal filters outside observation windows: all of these pass any SQL linter and fail any replication attempt.

**It is static and deterministic.** FastSSV parses SQL into an abstract syntax tree and checks structural patterns against the OMOP CDM v5.4 schema. The same query produces the same result every time, on any machine, without connecting to a database.

**It is AI-agnostic.** SQL produced by humans, ATLAS, scripts, or AI agents is validated identically. FastSSV treats any SQL generator as a black box whose output needs checking.

**It is rule-based and extensible.** Every check is a discrete, documented rule with a unique ID, a severity level, a violation message, and a suggested fix. New rules can be added without touching existing ones.

---

## Quick Start

**1. Install and activate**
```bash
uv sync && source .venv/bin/activate
# or: python -m venv .venv && source .venv/bin/activate && pip install -e .
```

**2. Validate a SQL file**
```bash
python main.py path/to/query.sql
```

**3. Check the report**
```bash
cat output/validation_report.json
```

Run the test suite at any point with `python -m unittest`.

---

## What FastSSV Catches

FastSSV ships with 11 rules across two categories.

### Semantic rules

These rules enforce how OMOP clinical and vocabulary tables should be joined, filtered, and reasoned about.

**Standard concept enforcement** (`semantic.standard_concept_enforcement`, ERROR) — Every standard concept field (`condition_concept_id`, `drug_concept_id`, etc.) must be constrained to `standard_concept = 'S'` or resolved via a `'Maps to'` relationship. Without this, queries silently include non-standard, source-vocabulary, or metadata concepts.

**Join path validation** (`semantic.join_path_validation`, WARNING) — Joins between CDM tables must use valid foreign-key pairs as defined by the OMOP CDM v5.4 schema graph. A join on the wrong column pair produces an implicit cross-join or empty results with no database error.

**Hierarchy expansion required** (`semantic.hierarchy_expansion_required`, ERROR) — Filtering `drug_concept_id` or `condition_concept_id` on specific concept IDs without using `concept_ancestor` misses every descendant concept. Filtering for "Metformin" (a single ingredient concept) without expansion excludes every specific formulation.

**Observation period anchoring** (`semantic.observation_period_anchoring`, ERROR) — Temporal filters on clinical tables (date comparisons, `DATEDIFF`, `INTERVAL`) must be anchored to `observation_period` on `person_id`. Events outside a patient's observation window may be present in the data but do not represent complete, active observation.

**Maps-to direction** (`semantic.maps_to_direction`, WARNING) — The `'Maps to'` relationship in `concept_relationship` is directional: `concept_id_1` holds the source concept, `concept_id_2` holds the standard concept. Joining a standard concept field to `concept_id_1` retrieves nothing useful.

**Unmapped concept handling** (`semantic.unmapped_concept_handling`, WARNING) — Records that could not be mapped during ETL receive `concept_id = 0`. Queries that filter specific concept IDs without acknowledging `concept_id = 0` silently exclude those records, which can be 10–30% of events in some datasets.

**Invalid reason enforcement** (`semantic.invalid_reason_enforcement`, ERROR/WARNING) — Vocabulary tables contain deprecated and superseded concepts marked with a non-null `invalid_reason`. Querying `concept` or `concept_relationship` without `invalid_reason IS NULL` can return retired concept IDs that no longer represent valid clinical entities.

**Domain segregation** (`semantic.domain_segregation`, ERROR/WARNING) — Each CDM table is designed for one domain: `condition_occurrence` for Condition concepts, `drug_exposure` for Drug concepts, and so on. Joining a clinical table to `concept` with the wrong `domain_id` filter returns zero rows. Joining without any `domain_id` filter risks cross-domain matches.

### Vocabulary rules

These rules enforce correct concept identification — ensuring concepts are looked up by ID rather than by string, and that string-based lookups are unambiguous.

**No string identification** (`vocabulary.no_string_identification`, ERROR) — `*_source_value` columns contain raw, site-specific text preserved from the source system. Filtering on them with `LIKE`, `=`, or `IN` makes queries non-portable and analytically incorrect across CDM instances.

**Concept lookup context** (`vocabulary.concept_lookup_context`, ERROR) — Filtering the `concept` table by `concept_name`, `concept_code`, or similar text columns is valid only inside a subquery or CTE that outputs `concept_id`. String-based concept selection in the main query body is tied to a specific vocabulary version and breaks silently on upgrade.

**Concept code requires vocabulary ID** (`vocabulary.concept_code_requires_vocabulary_id`, ERROR) — `concept_code` is not unique across vocabularies. The code `'E11.9'` exists in ICD-10-CM, ICD-10, and potentially other vocabularies simultaneously. Every `concept_code` filter must be paired with a `vocabulary_id` filter in the same scope.

For full intent, examples, edge cases, and cross-rule interactions, see [docs/RULES_REFERENCE.md](docs/RULES_REFERENCE.md).

---

## Example

This query has three violations — it runs cleanly and returns results, but all three make the cohort analytically incorrect:

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_concept_id IN (201826, 443238);
```

FastSSV output (`output/validation_report.json`):

```json
{
  "is_valid": false,
  "error_count": 2,
  "warning_count": 1,
  "violations": [
    {
      "rule_id": "semantic.standard_concept_enforcement",
      "severity": "error",
      "issue": "Query uses STANDARD concept fields but does not ensure standard concepts. Must either: (A) filter with concept.standard_concept = 'S', or (B) use concept_relationship.relationship_id = 'Maps to'. STANDARD fields referenced: condition_occurrence.condition_concept_id",
      "suggested_fix": "JOIN concept table and add: WHERE concept.standard_concept = 'S'"
    },
    {
      "rule_id": "semantic.hierarchy_expansion_required",
      "severity": "error",
      "issue": "Query filters on condition_occurrence.condition_concept_id without using concept_ancestor for hierarchy expansion. This will miss descendant concepts.",
      "suggested_fix": "JOIN concept_ancestor ca ON condition_occurrence.condition_concept_id = ca.descendant_concept_id WHERE ca.ancestor_concept_id IN (...)"
    },
    {
      "rule_id": "semantic.unmapped_concept_handling",
      "severity": "warning",
      "issue": "Query filters condition_occurrence.condition_concept_id by specific value(s) but does not explicitly handle concept_id = 0 (unmapped records).",
      "suggested_fix": "Add: condition_concept_id > 0"
    }
  ]
}
```

A fully compliant version of the same query:

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept_ancestor ca
  ON co.condition_concept_id = ca.descendant_concept_id
JOIN concept c
  ON ca.ancestor_concept_id = c.concept_id
  AND c.standard_concept = 'S'
  AND c.domain_id = 'Condition'
  AND c.invalid_reason IS NULL
JOIN observation_period op
  ON co.person_id = op.person_id
WHERE ca.ancestor_concept_id IN (201826, 443238)
  AND co.condition_concept_id > 0
  AND co.condition_start_date BETWEEN
      op.observation_period_start_date AND op.observation_period_end_date;
```

---

## Running Specific Rules

```bash
# All rules (default)
python main.py query.sql

# One category
python main.py query.sql --categories semantic
python main.py query.sql --categories vocabulary

# Specific rules
python main.py query.sql --rules semantic.domain_segregation semantic.hierarchy_expansion_required

# Different dialect
python main.py query.sql --dialect duckdb
```

---

## Python API

```python
from fastssv import validate_sql_structured

violations = validate_sql_structured(sql)

for v in violations:
    print(f"[{v.severity.value.upper()}] {v.rule_id}")
    print(f"  {v.message}")
    print(f"  Fix: {v.suggested_fix}")
```

Filter by severity, category, or specific rule:

```python
from fastssv.core.base import Severity

# Filter by severity
errors   = [v for v in violations if v.severity == Severity.ERROR]
warnings = [v for v in violations if v.severity == Severity.WARNING]

# Run only specific categories or rules
violations = validate_sql_structured(sql, categories=["semantic"])
violations = validate_sql_structured(sql, rule_ids=["semantic.domain_segregation"])
violations = validate_sql_structured(sql, dialect="bigquery")
```

---

## Position in the OHDSI Ecosystem

FastSSV validates what other OHDSI tools assume to be correct.

| Layer | Tool |
|-------|------|
| Data correctness | DataQualityDashboard |
| Data characterisation | Achilles |
| Cohort inspection | CohortDiagnostics |
| Phenotype validity | PheValuator |
| Model performance | PatientLevelPrediction |
| **Analysis logic validity** | **FastSSV** |

---

## Supported Dialects

FastSSV uses [sqlglot](https://github.com/tobymao/sqlglot) for parsing. Any dialect sqlglot supports can be passed via `--dialect`. Tested:

| Dialect | Flag |
|---------|------|
| PostgreSQL (default) | `--dialect postgres` |
| DuckDB | `--dialect duckdb` |
| Spark SQL | `--dialect spark` |
| BigQuery | `--dialect bigquery` |
| Snowflake | `--dialect snowflake` |

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/RULES_REFERENCE.md](docs/RULES_REFERENCE.md) | Complete reference for all 11 rules: intent, examples, edge cases, cross-rule interactions |
| [docs/SEMANTIC_RULES_GUIDE.md](docs/SEMANTIC_RULES_GUIDE.md) | Developer guide for extending semantic rules |
| [docs/PLUGIN_ARCHITECTURE.md](docs/PLUGIN_ARCHITECTURE.md) | Plugin system design and adding new rules |
| [docs/architecture.md](docs/architecture.md) | Source structure and component overview |
| [docs/JSON_OUTPUT.md](docs/JSON_OUTPUT.md) | Validation report JSON format |

---

## Project Structure

```
src/fastssv/
├── core/
│   ├── base.py            # Rule base class, RuleViolation, Severity
│   ├── registry.py        # Plugin registry (@register decorator)
│   └── helpers.py         # SQL parsing utilities
├── schemas/
│   ├── cdm_schema.py      # OMOP CDM v5.4 — 43 tables, all FK relationships
│   └── semantic_schema.py # Standard vs source concept field classifications
└── rules/
    ├── semantic/          # 8 rules: standard concept, join path, hierarchy,
    │                      #   observation period, maps-to, unmapped, invalid reason,
    │                      #   domain segregation
    └── vocabulary/        # 3 rules: no string ID, concept lookup context,
                           #   concept code + vocabulary ID
```

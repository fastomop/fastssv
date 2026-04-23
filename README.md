<p align="center">
  <img src="docs/logo.png" alt="FastSSV" width="300">
</p>

# FastSSV - Fast Semantic Static Validator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![OMOP CDM](https://img.shields.io/badge/OMOP-CDM%20v5.4-5C4EE5)
![Rules](https://img.shields.io/badge/rules-157-orange)
![Status](https://img.shields.io/badge/status-beta-yellow)
![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Tests](https://github.com/fastomop/fastSSV/actions/workflows/tests.yml/badge.svg)

OMOP SQL that runs without errors can still be analytically wrong.

A query that silently drops 30% of patients because it misses concept descendants, filters on a deprecated concept, or applies a temporal constraint outside a patient's observation window will execute cleanly, return plausible numbers, and produce a flawed study. FastSSV catches these violations before they reach results.

---

## Why FastSSV

Existing OHDSI tools validate data quality, characterise cohorts, and measure phenotype performance. None of them validate whether the *SQL logic itself* follows OMOP CDM rules. FastSSV fills that gap.

**It targets silent failures.** The violations FastSSV catches are not syntax errors or missing columns, they are cases where the SQL is valid, the query returns results, and those results are wrong. Missing hierarchy expansion, reversed concept relationship direction, temporal filters outside observation windows: all of these pass any SQL linter and fail any replication attempt.

**It is static and deterministic.** FastSSV parses SQL into an abstract syntax tree and checks structural patterns against the OMOP CDM v5.4 schema. The same query produces the same result every time, on any machine, without connecting to a database.

**It is AI-agnostic.** SQL produced by humans, ATLAS, scripts, or AI agents is validated identically. FastSSV treats any SQL generator as a black box whose output needs checking.

**It is rule-based and extensible.** Every check is a discrete, documented rule with a unique ID, a severity level, a violation message, and a suggested fix. New rules can be added without touching existing ones.

---

## Quick Start

**1. Install**
```bash
pip install fastssv
# for local development, run `uv sync`
```

**2. Validate a SQL file**
```bash
fastssv path/to/query.sql
```

**3. Use it from Python**
```python
from fastssv import validate_anti_patterns, validate_sql_structured
```

**4. Check the report**
```bash
cat output/validation_report.json
```

**5. Enable logging (optional)**
```bash
# Debug logging
fastssv query.sql --log-level DEBUG

# Log to file
fastssv query.sql --log-file logs/validation.log

# JSON structured logs for production
fastssv query.sql --log-format json --log-file logs/validation.json
```

See [docs/LOGGING.md](docs/LOGGING.md) for comprehensive logging documentation.

---

## HTTP API

FastSSV also ships an optional FastAPI service that exposes the validator over HTTP. Install with the `api` extra and run with any ASGI server:

```bash
pip install "fastssv[api]"
uvicorn fastssv.api.app:app --host 0.0.0.0 --port 8000
# or, for production:
gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000 fastssv.api.app:app
```

Endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/`            | HTMX web UI — paste SQL, see violations inline |
| `GET`  | `/rules`       | HTMX rules browser with category filter |
| `POST` | `/v1/validate` | JSON API: validate one SQL query, returns structured violations |
| `GET`  | `/v1/rules`    | JSON API: list all registered rules (id, name, severity, category) |
| `GET`  | `/v1/health`   | JSON API: liveness probe — returns version and rules loaded |
| `GET`  | `/docs`        | Interactive Swagger UI |

Example:
```bash
curl -X POST http://localhost:8000/v1/validate \
  -H "content-type: application/json" \
  -d '{"sql":"SELECT * FROM no_such_table;","dialect":"postgres"}'
```

A production-grade Dockerfile is included at `deploy/Dockerfile`. The service is stateless, enforces body-size / parse-timeout / rate limits, sets strict security headers, and never logs raw SQL bodies.

See [docs/API.md](docs/API.md) for the full API reference, configuration, and deployment guide.

---

## What FastSSV Catches

FastSSV ships with 157 validation rules across 6 categories covering OMOP CDM v5.4 semantic correctness.

### Core Categories

**Anti-Pattern Rules (24 rules)** - Detect common OMOP query anti-patterns including string-based concept identification, improper type concept usage, concept_relationship misuse, redundant hierarchy patterns, ambiguous column references, cross joins, metadata joins, and context-dependent vocabulary lookups.

**Concept Standardization Rules (18 rules)** - Enforce standard concept usage, concept_ancestor rollup direction, invalid reason checks, domain and vocabulary validation, source concept handling, Maps to target correctness, concept relationship validity, and CDM version migration issues.

**Domain-Specific Rules (42 rules)** - Table-specific validation for cohort, condition, cost, death, drug, episode, location, measurement, note, observation, person, procedure, specimen, visit, visit_detail, and vocabulary domains. Includes cardinality awareness, field validation, temporal constraints, domain-specific semantic rules, and CDM version compatibility checks.

**Join Rules (36 rules)** - Validate foreign key relationships, join path correctness, concept relationship direction, cross-table linkage requirements, left join logic, alias reuse, and observation_period date overlap requirements.

**Temporal Rules (10 rules)** - Validate date logic, observation period constraints, temporal consistency across clinical events, NULL handling for date columns, datetime comparisons, and required date column usage.

**Data Quality Rules (27 rules)** - Catch schema violations, type mismatches, structural issues, unmapped concepts, case-sensitivity mistakes, whitespace issues, negative concept IDs, free-text field constraints, fact relationship validation, episode handling, union domain indicators, and other data quality problems in OMOP queries.

### Key Anti-Pattern Rules (24 rules)

**Type concept ID misuse** (`anti_patterns.type_concept_id_misuse`, WARNING) - Type concept fields (*_type_concept_id) encode provenance metadata (EHR, claims, registry) and should not be used for clinical filtering. Type concepts indicate data source, not clinical meaning.

**No string identification** (`anti_patterns.no_string_identification`, ERROR) - `*_source_value` columns contain raw, site-specific text from the source system. Filtering on them with `LIKE`, `=`, or `IN` makes queries non-portable and analytically incorrect across CDM instances.

**Concept lookup context** (`anti_patterns.concept_lookup_context`, ERROR) - Filtering the `concept` table by `concept_name`, `concept_code`, or similar text columns is valid only inside a subquery or CTE that outputs `concept_id`. String-based concept selection in the main query body is tied to a specific vocabulary version and breaks silently on upgrade.

**Concept code requires vocabulary ID** (`anti_patterns.concept_code_requires_vocabulary_id`, WARNING) - `concept_code` is not unique across vocabularies. The code `'E11.9'` exists in ICD-10-CM, ICD-10, and other vocabularies simultaneously. Every `concept_code` filter should be paired with a `vocabulary_id` filter in the same scope for unambiguous concept resolution.

**Concept name lookup anti-pattern** (`anti_patterns.concept_name_lookup`, WARNING) - Filtering by `concept_name` using string matching is fragile and vocabulary-version-dependent. Use concept IDs or concept_code + vocabulary_id instead.

**Concept relationship transitive misuse** (`anti_patterns.concept_relationship_transitive_misuse`, WARNING) - Treating `concept_relationship` as a transitive hierarchy mechanism produces incomplete or semantically wrong expansions. Use `concept_ancestor` for descendant rollups.

**Missing relationship filter in concept_relationship** (`joins.concept_relationship_requires_relationship_id`, ERROR) - Querying `concept_relationship` without constraining `relationship_id` mixes unrelated relationship types and produces ambiguous mappings.

### Key Concept Standardization Rules (18 rules)

**Standard concept enforcement** (`concept_standardization.standard_concept_enforcement`, WARNING) - Every standard concept field (`condition_concept_id`, `drug_concept_id`, etc.) should be constrained to `concept.standard_concept = 'S'` or resolved via a `'Maps to'` relationship. This is a best practice to ensure vocabulary hygiene, though queries may execute without it depending on data quality.

**Concept ancestor rollup direction** (`concept_standardization.concept_ancestor_rollup_direction`, ERROR) - When joining `concept_ancestor` to roll up to ancestor concepts, the join direction must match the intent. Confusing `ancestor_concept_id` and `descendant_concept_id` silently returns the wrong concept set.

**Invalid reason enforcement** (`concept_standardization.invalid_reason_enforcement`, WARNING) - Vocabulary tables contain deprecated and superseded concepts marked with non-null `invalid_reason`. Querying `concept` or `concept_relationship` without `invalid_reason IS NULL` may return retired concept IDs. This is a best practice for vocabulary hygiene.

**Concept domain validation** (`concept_standardization.concept_domain_validation`, WARNING for missing filter, ERROR for wrong filter) - Each CDM table is designed for one domain: `condition_occurrence` for Condition concepts, `drug_exposure` for Drug concepts. Missing a `domain_id` filter is a best practice warning. Using the **wrong** `domain_id` is always an ERROR as it will return zero rows.

**Era table validation** (`concept_standardization.era_table_standard_concepts`, ERROR) - Era tables (condition_era, drug_era) must use standard concepts only. Source concepts in era tables indicate ETL errors.

**Source concept ID warning** (`concept_standardization.source_concept_id_warning`, WARNING) - Source concept fields (*_source_concept_id) should not be used for primary filtering. Use standard concept fields instead.

**Source to concept map validation** (`concept_standardization.source_to_concept_map_validation`, WARNING) - When using source_to_concept_map table, ensure proper vocabulary_id and target concept validation.

**Standard concept value validation** (`concept_standardization.standard_concept_value_validation`, ERROR) - Validates that standard_concept column values are valid ('S', 'C', or NULL).

**Concept ancestor cross-domain validation** (`concept_standardization.concept_ancestor_cross_domain`, WARNING) - Expanding a concept hierarchy across mismatched domains can over-broaden cohorts or mix incompatible semantics.

**Maps to target standard validation** (`concept_standardization.maps_to_target_standard_validation`, ERROR) - `'Maps to'` targets should resolve to valid standard concepts. Mapping chains that land on non-standard targets indicate incorrect vocabulary logic.

**Maps to target standard validation** (`concept_standardization.maps_to_target_standard_validation`, WARNING) - When using 'Maps to' relationships, ensure the target concept is a valid standard concept (`standard_concept = 'S'` AND `invalid_reason IS NULL`). Prevents landing on deprecated or intermediate concepts.

### Key Domain-Specific Rules (42 rules)

**Person birth field validation** (`domain_specific.person_birth_field_validation`, ERROR) - Validates plausible ranges for birth date components: year_of_birth (1900 to current), month_of_birth (1 to 12), day_of_birth (1 to 31).

**Condition/Drug cardinality awareness** (`domain_specific.condition_occurrence_cardinality_validation`, `domain_specific.drug_exposure_cardinality_validation`, WARNING) - Patients can have multiple records per condition or drug (recurrences, refills). Counting rows without `DISTINCT person_id` or aggregation produces misleading statistics.

**Condition visit hierarchy** (`domain_specific.condition_visit_hierarchy_validation`, ERROR) - Condition occurrence must reference valid visit_occurrence via visit_occurrence_id. Ensures proper linkage between conditions and visits.

**Measurement validation** (`domain_specific.measurement_operator_concept_validation`, `domain_specific.measurement_range_low_high_validation`, `domain_specific.measurement_value_as_number_and_concept_validation`, ERROR/WARNING) - Ensures measurement operators, ranges, and value representations are used correctly and consistently.

**Measurement unit validation** (`domain_specific.measurement_unit_validation`, WARNING) - When filtering on `value_as_number`, must also filter or join on `unit_concept_id` to ensure comparable measurements.

**Visit validation** (`domain_specific.visit_outpatient_same_day_validation`, `domain_specific.visit_event_temporal_validation`, `domain_specific.visit_detail_dates_within_parent_visit`, WARNING) - Validates visit temporal logic, outpatient same-day rules, and visit_detail dates within parent visit_occurrence.

**Drug validation** (`domain_specific.drug_days_supply_validation`, `domain_specific.drug_quantity_validation`, WARNING) - Validates drug_exposure.days_supply (typically <= 365) and quantity (> 0) for plausibility.

**Drug era validation** (`domain_specific.drug_era_concept_class_validation`, ERROR) - Drug era records must use Drug ingredient concept class. Drug clinical drug or branded drug concepts in drug_era indicate ETL errors.

**Death cause validation** (`domain_specific.death_cause_source_concept_validation`, ERROR) - Death cause source concept fields should not be used for analytical filtering. Use standard cause_concept_id instead.

**Procedure quantity** (`domain_specific.procedure_occurrence_quantity_semantics`, WARNING) - Validates that procedure_occurrence.quantity represents count of procedures performed, not other quantities.

**Observation value confusion** (`domain_specific.observation_value_as_concept_confusion`, ERROR) - Observation table uses value_as_concept_id for categorical values, not value_as_number. Mixing these fields causes logical errors.

**CDM v5.3 to v5.4 column renames** (`domain_specific.cdm_v53_to_v54_column_renames`, ERROR) - Detects use of deprecated column names from OMOP CDM v5.3 that were renamed in v5.4 on visit_occurrence and visit_detail tables (e.g., admitting_source_concept_id to admitted_from_concept_id, discharge_to_concept_id to discharged_to_concept_id).

### Key Join Rules (36 rules)

**Person ID join validation** (`joins.person_id_join_validation`, ERROR) - All joins from clinical tables to `person` table must use `person_id` as the join key. Using wrong columns (e.g., `person.person_source_value`) produces incorrect results.

**Visit occurrence ID join validation** (`joins.visit_occurrence_id_join_validation`, ERROR) - All joins from clinical tables to `visit_occurrence` must use `visit_occurrence_id` as the join key.

**Clinical person ID linkage** (`joins.clinical_person_id_linkage_validation`, ERROR) - All clinical tables must be connected via `person_id` joins. Queries joining clinical tables without linking through `person_id` risk cross-patient data leakage.

**Visit detail join validation** (`joins.visit_detail_join_validation`, WARNING) - `visit_detail` must reference `visit_occurrence` via `visit_occurrence_id`. Visit details without parent visits are orphaned records.

**Concept join validation** (`joins.concept_join_validation`, ERROR) - Joins from `*_concept_id` columns to `concept` table must use the correct foreign key (`concept_id`). Common errors include joining to `concept_code` or `concept_name`.

**Concept relationship validation** (`joins.concept_relationship_relationship_join_validation`, ERROR) - When querying `concept_relationship`, must join to `relationship` table via `relationship_id` to get relationship names/descriptions.

**Care site join validation** (`joins.care_site_id_join_validation`, `joins.care_site_location_join_validation`, ERROR) - Care site joins must use `care_site_id`. Location joins must properly chain through `location_id`.

**Provider join validation** (`joins.provider_join_validation`, `joins.provider_care_site_join_validation`, ERROR) - Provider joins must use `provider_id`. Provider-to-care_site joins must use `care_site_id`.

**Era table forbidden joins** (`joins.era_forbidden_join_validation`, ERROR) - Era tables (condition_era, drug_era) cannot be joined to raw clinical tables (condition_occurrence, drug_exposure) without causing logical errors. Era tables are pre-aggregated derivations.

**Drug strength join validation** (`joins.drug_exposure_drug_strength_join_validation`, ERROR) - To get dosage information, must join `drug_exposure` to `drug_strength` via `drug_concept_id` (not `drug_source_concept_id`).

**Clinical primary key validation** (`joins.clinical_pk_cross_join_validation`, ERROR) - Warns about potential cross-joins when clinical tables are joined without proper foreign key relationships.

**Maps-to direction** (`joins.maps_to_direction`, WARNING) - The `'Maps to'` relationship in `concept_relationship` is directional: `concept_id_1` holds the source concept, `concept_id_2` holds the standard concept. Joining a standard concept field to `concept_id_1` retrieves nothing useful.

**Join path validation** (`joins.join_path_validation`, WARNING) - Joins between CDM tables must use valid foreign-key pairs as defined by the OMOP CDM v5.4 schema graph. A join on the wrong column pair produces an implicit cross-join or empty results with no database error.

**Concept relationship requires relationship ID** (`joins.concept_relationship_requires_relationship_id`, WARNING) - When querying `concept_relationship`, should filter on `relationship_id` to specify the relationship type (e.g., 'Maps to', 'Is a'). Without this filter, queries may mix unrelated concept relationships, though the query will execute.


**Observation period join validation** (`joins.observation_period_join_validation`, WARNING) - Patients can have multiple observation periods. Joining clinical tables to observation_period on person_id alone creates a Cartesian product. Must include date overlap constraint: clinical_date BETWEEN observation_period_start_date AND observation_period_end_date.

### Key Temporal Rules (10 rules)

**Observation period anchoring** (`temporal.observation_period_anchoring`, WARNING) - Queries with temporal constraints (washout, follow-up, event windows) should join to `observation_period` on `person_id` to ensure events are within a patient's observation window. Events outside observation windows may be incomplete or missing.

**Observation period date range logic** (`temporal.observation_period_date_range_logic`, ERROR) - Ensures clinical event dates are tested within observation_period bounds. Detects reversed logic where observation_period dates are incorrectly used as values.

**End before start validation** (`temporal.end_before_start_validation`, ERROR) - Detects impossible temporal constraints where start_date > end_date in filters (e.g., condition_start_date > '2020-06-01' AND condition_end_date < '2020-05-01').

**Death date before birth** (`temporal.death_date_before_birth_validation`, ERROR) - Catches impossible temporal relationships where `death.death_date` precedes `person.birth_datetime` or inferred birth date from `year_of_birth`.

**Death date in future** (`temporal.death_date_in_future_validation`, WARNING) - Warns about death dates in the future, which may indicate data quality issues or scheduled end-of-life care records.

**Clinical event date in future** (`temporal.clinical_event_date_in_future_validation`, WARNING) - Warns about clinical event dates (condition_start_date, drug_exposure_start_date, etc.) dated in the future, indicating possible data entry errors or scheduled procedures.

**Future information leakage** (`temporal.future_information_leakage`, WARNING) - Detects cross-table date comparisons (e.g., condition_start_date > drug_exposure_start_date) without bounding the future event against observation_period_end_date, which introduces temporal bias.

**Nullable end date NULL handling** (`temporal.nullable_end_date_null_handling`, WARNING) - Ensures nullable end_date columns are properly handled when used in functions, arithmetic, or comparisons to avoid NULL propagation issues.

**Required date column validation** (`temporal.required_date_column_validation`, WARNING) - Temporal queries on clinical tables should use required (NOT NULL) date columns instead of nullable columns to avoid silently excluding records.

### Key Data Quality Rules (27 rules)

**Schema validation** (`data_quality.schema_validation`, ERROR) - Validates that queries reference only valid OMOP CDM v5.4 tables and columns. Catches typos in table/column names, references to non-existent columns, and schema violations like using `concept_ancestor` columns on `concept_relationship` table.

**Unmapped concept handling** (`data_quality.unmapped_concept_handling`, WARNING) - Records that could not be mapped during ETL receive `concept_id = 0`. Queries filtering on specific concept IDs without acknowledging `concept_id = 0` silently exclude those records (10-30% of events in some datasets).

**Negative concept ID validation** (`data_quality.negative_concept_id_validation`, ERROR) - Validates that concept_id values are non-negative integers (>= 0). Negative values are never valid in OMOP and indicate data quality issues or ETL errors.

**Union concept ID domain indicator** (`data_quality.union_concept_id_domain_indicator`, WARNING) - UNION queries combining concept_id columns from multiple domains (condition, drug, procedure, etc.) must include a domain indicator column to disambiguate which domain each concept_id belongs to.

**Column type validation** (`data_quality.column_type_validation`, ERROR) - Validates that columns are used with appropriate data types. Catches common errors like treating integer `person_id` as string, or using string operations on date columns.

**Vocabulary table protection** (`data_quality.vocabulary_table_protection`, ERROR) - Prevents UPDATE, DELETE, or DROP operations on vocabulary tables (concept, concept_relationship, vocabulary, domain, concept_class, relationship). Vocabulary tables should be read-only in analytical queries.

**Clinical event date before 1900** (`data_quality.clinical_event_date_before_1900_validation`, WARNING) - Warns about suspiciously old dates (before year 1900) in clinical tables, which often indicate missing data coded as `1900-01-01` or data quality issues.

**Case-sensitivity validations** (`data_quality.concept_class_id_case_sensitivity`, `data_quality.domain_id_case_sensitivity`, `data_quality.vocabulary_id_validation`, WARNING/ERROR) - Detects case mismatches in canonical vocabulary literals such as `domain_id`, `concept_class_id`, and `vocabulary_id` that silently produce empty results.

**Concept name whitespace** (`data_quality.concept_name_whitespace`, WARNING) - Flags leading or trailing whitespace in `concept_name` equality comparisons, which often indicates accidental copy/paste errors.

**Standard concept NULL handling** (`data_quality.standard_concept_null_handling`, WARNING) - Warns when `standard_concept` is treated as a simple string field without acknowledging its tri-state semantics (`'S'`, `'C'`, `NULL`).

For comprehensive documentation of all 157 rules with detailed examples, see [docs/RULES_REFERENCE.md](docs/RULES_REFERENCE.md). For the live registered rule set, use `from fastssv import get_all_rules`.

---

## Example

This query has two violations. It runs cleanly and returns results, but both violations make the cohort analytically incorrect:

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
      "rule_id": "concept_standardization.standard_concept_enforcement",
      "severity": "error",
      "issue": "Query filters on condition_occurrence.condition_concept_id without constraining concept.standard_concept = 'S'. Non-standard concepts may be returned, producing an inconsistent cohort.",
      "suggested_fix": "Join to the concept table and add WHERE concept.standard_concept = 'S', or resolve the source concept through a 'Maps to' relationship in concept_relationship."
    },
    {
      "rule_id": "data_quality.unmapped_concept_handling",
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
fastssv query.sql

# Specific rules
fastssv query.sql --rules concept_standardization.concept_domain_validation concept_standardization.standard_concept_enforcement

# Different dialect
fastssv query.sql --dialect duckdb
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
violations = validate_sql_structured(sql, categories=["concept_standardization"])
violations = validate_sql_structured(sql, categories=["joins"])
violations = validate_sql_structured(sql, categories=["temporal"])
violations = validate_sql_structured(sql, rule_ids=["concept_standardization.concept_domain_validation"])
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

## Validation Architecture

FastSSV implements a three-layer validation architecture that separates structural issues, data model compliance, and best practice suggestions:

### Layer 1: STRUCTURAL (Always ERROR)
**Purpose**: SQL syntax and parseability validation
**Severity**: Always ERROR (non-negotiable)
**Rationale**: These queries cannot execute - fundamental correctness issues

- SQL must be parseable
- Valid operators and expressions
- Proper quoting and escaping

### Layer 2: SCHEMA (ERROR or context-aware WARNING)
**Purpose**: OMOP CDM data model compliance
**Severity**: ERROR (default), WARNING (context-aware)
**Rationale**: Violations indicate incorrect data model usage or likely produce wrong results

Implementation:
- Complete OMOP CDM 5.4 schema (30+ tables, all columns, types, foreign keys)
- Comprehensive schema validator catches ALL invalid columns/tables
- Context-aware vocabulary rules (exploratory vs production)
- Foreign key relationship validation
- Type compatibility checking

Rules in this layer:
- Valid table and column names
- Correct join keys between tables
- Type compatibility in comparisons
- Domain-specific constraints
- Required filters for correctness

### Layer 3: BEST_PRACTICE (Always WARNING)
**Purpose**: Portability, optimization, and maintainability
**Severity**: Always WARNING
**Rationale**: Suggestions for improvement, not correctness issues

Rules in this layer:
- Dialect-specific function usage (portability)
- Performance anti-patterns
- Code maintainability
- Semantic best practices

### Strict Mode

Cohort definition validation with elevated standards:

```bash
fastssv cohort_definition.sql --strict
```

In strict mode, certain best practice warnings are escalated to errors:
- `standard_concept_enforcement`: WARNING → ERROR
- `invalid_reason_enforcement`: WARNING → ERROR
- `concept_domain_validation`: WARNING → ERROR (for missing domain filters, wrong domain always ERROR)
- `concept_code_requires_vocabulary_id`: WARNING → ERROR
- `concept_relationship_requires_relationship_id`: WARNING → ERROR

**When to use strict mode**:
- Cohort definitions for research studies
- Queries building patient populations for analysis
- Any query where concept validity and vocabulary hygiene are critical
- Research publications and regulatory submissions

**When NOT to use strict mode**:
- Descriptive analytics and exploratory queries
- Aggregate statistical analysis
- Reports with heavy GROUP BY/aggregation
- Development/debugging

**Validation Results** (condition_era.sql example):
| Mode | Valid | Invalid | Description |
|------|-------|---------|-------------|
| **Normal** | **10/15 (67%)** | **5/15 (33%)** | Only statistical errors block (percentile calculations) |
| **Strict** | **1/15 (7%)** | **14/15 (93%)** | Statistical errors + best-practice violations (vocabulary hygiene) |

**Impact**: Strict mode enforces vocabulary hygiene critical for cohort definitions while normal mode allows flexibility for analytical queries that produce correct results despite missing best-practice filters.

### Dialect-Agnostic Validation

FastSSV automatically detects SQL dialect and adjusts validation accordingly:

**Auto-Detection**:
- Automatic SQL Server vs Postgres detection
- Patterns: `@vocab.`, `getdate()`, `TOP N`, etc.
- Override with `--dialect postgres` or `--dialect tsql`

**Portability vs Correctness**:
- SQL Server functions produce WARNING (portability), not ERROR (correctness)
- Consistent behavior: same pattern = same severity within dialect
- Context includes dialect in violation details

```bash
# Auto-detect (default)
fastssv query.sql

# Explicit dialect
fastssv query.sql --dialect postgres
```

### Error Deduplication

FastSSV implements intelligent error deduplication to prevent the same underlying issue from being reported multiple times by different rules:

- Normalizes issues by underlying problem (table+column, type mismatch, etc.)
- Keeps highest severity (ERROR > WARNING)
- Keeps most specific rule (longer rule_id = more specific)
- Preserves original order

**Impact**: Reduces redundant error reporting by ~29% while preserving all unique issues.

---

## Documentation

| Document | Contents |
|----------|----------|
| [docs/RULES_REFERENCE.md](docs/RULES_REFERENCE.md) | Comprehensive documentation for all 157 rules with intent, detection logic, examples, and suggested fixes |
| [docs/SEMANTIC_RULES_GUIDE.md](docs/SEMANTIC_RULES_GUIDE.md) | Developer guide for extending semantic rules |
| [docs/PLUGIN_ARCHITECTURE.md](docs/PLUGIN_ARCHITECTURE.md) | Plugin system design and adding new rules |
| [docs/architecture.md](docs/architecture.md) | Source structure and component overview |
| [docs/JSON_OUTPUT.md](docs/JSON_OUTPUT.md) | Validation report JSON format |
| [docs/LOGGING.md](docs/LOGGING.md) | Logging system configuration, formats, and production best practices |
| [tasks/IMPLEMENTATION_STATUS.md](tasks/IMPLEMENTATION_STATUS.md) | CLIN_001-057 implementation tracking and status |

---

## Comprehensive OMOP Schema Validation

FastSSV includes complete OMOP CDM 5.4 schema definition for comprehensive validation:

### Schema Coverage
**30+ Tables Fully Defined**:
- **Clinical**: `person`, `observation_period`, `visit_occurrence`, `condition_occurrence`, `drug_exposure`, `procedure_occurrence`, `measurement`, `observation`, `death`, `note`, `specimen`, `fact_relationship`, `episode`, `episode_event`
- **Vocabulary**: `concept`, `vocabulary`, `domain`, `concept_class`, `concept_relationship`, `relationship`, `concept_synonym`, `concept_ancestor`, `source_to_concept_map`, `drug_strength`
- **Health System**: `location`, `care_site`, `provider`, `payer_plan_period`, `cost`
- **Derived**: `condition_era`, `drug_era`, `dose_era`
- **Metadata**: `cdm_source`, `metadata`

**Column Details**:
- Name, data type, nullable, primary key, foreign key
- Foreign table/column references
- 200+ column definitions total

### Validation Capabilities
- All table references validated against OMOP CDM 5.4
- All column references validated
- Suggestions for similar names (typo detection)
- Schema-qualified tables handled (`@vocab.concept` becomes `concept`)
- Alias resolution
- Duplicate detection (one error per unique issue)

**Example**:
```json
{
  "rule_id": "schema.comprehensive_validation",
  "severity": "error",
  "issue": "Column 'concept_level' does not exist in table 'concept'.",
  "details": {
    "layer": "schema",
    "type": "invalid_column",
    "table": "concept",
    "column": "concept_level",
    "similar_columns": ["concept_class_id", "concept_name"]
  }
}
```

### Rule Consistency Principles

1. **Deterministic Behavior**: Same pattern always produces same severity (unless context-aware)
2. **Layer Separation**: Clear distinction between correctness (STRUCTURAL/SCHEMA) and suggestions (BEST_PRACTICE)
3. **Context Awareness**: Formally defined conditions for severity changes
4. **Dialect Agnostic**: Portability issues are warnings, not errors
5. **Uniform Application**: All rules applied to all queries without early exits

---

## Rules Architecture

The implementation lives under `src/fastssv/rules/`. Categories now align directly with the package structure and the `rule_id` prefix.

### Implementation Layout

```text
src/fastssv/rules/
  - anti_patterns/             # String lookup and concept lookup anti-patterns
  - concept_standardization/   # Standard/valid/domain concept logic
  - domain_specific/           # Table-family rules: condition, drug, visit, etc.
  - joins/                     # Join-path and foreign-key validation
  - temporal/                  # Temporal reasoning and date constraints
  - data_quality/              # Structural and type-level checks
  - __init__.py                # Imports submodules to trigger @register
```

### Registration Model

Every rule registers itself with `@register` and exposes a unique `rule_id`:

```python
from fastssv.core.registry import register


@register
class MyRule(Rule):
    rule_id = "joins.my_rule"
    name = "My Rule"
    severity = Severity.ERROR

    def validate(self, sql: str, dialect: str = "postgres") -> list[RuleViolation]:
        ...
```

`validate_sql_structured()` runs all rules by default. Filtering by category uses the `rule_id` prefix, and that prefix now matches the containing package.

### Adding a Rule

1. Add the rule file under the most appropriate implementation package.
2. Decorate the class with `@register`.
3. Give it a stable `rule_id` using the package prefix: `anti_patterns`, `concept_standardization`, `domain_specific`, `joins`, `temporal`, or `data_quality`.
4. Import it from that package's `__init__.py` so registration happens on package import.
5. Add or update tests in `tests/`.

---

## Project Structure

```text
src/fastssv/
  - core/
      - base.py                 # Rule base class, RuleViolation, Severity
      - registry.py             # Plugin registry (@register decorator)
      - helpers.py              # SQL parsing utilities
      - rule_layer.py           # Three-layer architecture definition
      - omop_schema.py          # Complete OMOP CDM 5.4 schema (1000+ lines)
      - validation_context.py   # Strict mode and context management
      - deduplication.py        # Error deduplication system
  - schemas/
      - cdm_schema.py           # OMOP CDM v5.4 - 43 tables, all FK relationships
      - semantic_schema.py      # Standard vs source concept field classifications
  - rules/
      - anti_patterns/          # SQL anti-patterns and common mistakes
      - concept_standardization/ # Concept hierarchy and standardization
      - domain_specific/        # Table-specific validation rules
      - joins/                  # Join path and foreign key validation
      - temporal/               # Temporal logic and date constraints
      - data_quality/           # Type checking and data quality
      - schema/                 # OMOP schema validation
      - __init__.py
```

---

## Stability and Versioning

FastSSV follows [Semantic Versioning](https://semver.org/) starting from `1.0.0`.
During the current pre-1.0 phase (`0.x.y`), the following stability contract applies:

**Stable (breaking changes bumped as major/1.0+):**

- The top-level Python API: `validate_sql_structured()`, `validate_sql()`,
  `RuleViolation`, `Severity`, `get_all_rules()`, `get_rule()`,
  `get_rules_by_category()`.
- The `rule_id` field format: `<category>.<rule_name>` strings are treated as
  API. Rules that are renamed will keep an alias for one minor version.
- The `parse.syntax_error` rule_id for parse failures.
- The `RuleViolation` schema (rule_id, severity, message, suggested_fix,
  details, location fields).

**Evolving (may change between minor versions during 0.x):**

- The exact set of rules that ship and their precise detection logic.
  FastSSV is actively calibrated against real-world OHDSI SQL corpora; rules
  that produce false positives on standard patterns may be tightened or
  removed. See [CHANGELOG.md](CHANGELOG.md) for each release's rule changes.
- Message text inside violations (stable field names, not stable wording).
- Severity of individual rules (WARNING ↔ ERROR) based on calibration
  feedback.

**Deprecation policy:**

- Rule_id renames: old rule_id keeps firing as an alias for one minor version,
  with a warning logged. Removed in the following minor version.
- Removed rules: documented in CHANGELOG with rationale. Not restored unless a
  concrete bug in the removal is demonstrated.

**If your workflow pins on specific `rule_id`s** (e.g., a CI gate that fails
on `anti_patterns.type_concept_id_misuse`), pin FastSSV to a minor version
(`fastssv>=0.2,<0.3`) and review CHANGELOG between upgrades.

---

## License

FastSSV is licensed under the Apache License 2.0.

Copyright 2024-2026 FastSSV Contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

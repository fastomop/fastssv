# FastSSV Rules Reference

This document provides a comprehensive reference for every validation rule in FastSSV. For each rule you will find:

- **Intent** — the OMOP CDM constraint being enforced and why it matters scientifically
- **How it works** — what the rule inspects in the SQL AST
- **Severity** — whether violations are ERRORs or WARNINGs, and why
- **Examples** — failing SQL, passing SQL, and edge cases
- **Common scenarios** — real-world situations where this fires

---

## Quick Reference

| Rule ID | Name | Severity | Category |
|---------|------|----------|----------|
| [`semantic.standard_concept_enforcement`](#1-standard-concept-enforcement) | Standard Concept Enforcement | ERROR | Semantic |
| [`semantic.join_path_validation`](#2-join-path-validation) | Join Path Validation | WARNING | Semantic |
| [`semantic.hierarchy_expansion_required`](#3-hierarchy-expansion-required) | Hierarchy Expansion Required | ERROR | Semantic |
| [`semantic.observation_period_anchoring`](#4-observation-period-anchoring) | Observation Period Anchoring | ERROR | Semantic |
| [`semantic.maps_to_direction`](#5-maps-to-direction) | Maps To Direction | WARNING | Semantic |
| [`semantic.unmapped_concept_handling`](#6-unmapped-concept-handling) | Unmapped Concept Handling | WARNING | Semantic |
| [`semantic.invalid_reason_enforcement`](#7-invalid-reason-enforcement) | Invalid Reason Enforcement | ERROR / WARNING | Semantic |
| [`semantic.domain_segregation`](#8-domain-segregation) | Domain Segregation | ERROR / WARNING | Semantic |
| [`vocabulary.no_string_identification`](#9-no-string-identification) | No String Identification | ERROR | Vocabulary |
| [`vocabulary.concept_lookup_context`](#10-concept-lookup-context) | Concept Lookup Context | ERROR | Vocabulary |
| [`vocabulary.concept_code_requires_vocabulary_id`](#11-concept-code-requires-vocabulary-id) | Concept Code Requires Vocabulary ID | ERROR | Vocabulary |

---

## Severity levels

**ERROR** — the SQL logic is analytically incorrect and will produce wrong results (wrong cohort, wrong counts, wrong associations). Treat as a blocker.

**WARNING** — the SQL may produce incorrect results depending on data quality or study intent. Treat as a mandatory review item before publishing results.

---

## Semantic Rules

Semantic rules validate whether SQL queries correctly follow the OMOP CDM data model: concept usage, vocabulary relationships, temporal conventions, and schema join paths.

---

### 1. Standard Concept Enforcement

**Rule ID:** `semantic.standard_concept_enforcement`
**Severity:** ERROR

#### Intent

OMOP CDM distinguishes between **standard** concepts and **source** concepts. Standard concepts (`standard_concept = 'S'`) are the vocabulary-harmonised representations used for cross-site analysis — SNOMED CT conditions, RxNorm drugs, LOINC measurements, and so on. Source concepts are the raw codes from the originating system (ICD-10-CM, CPT4, NDC, etc.) preserved verbatim during ETL.

Analytical fields such as `condition_concept_id`, `drug_concept_id`, and `measurement_concept_id` are designed to hold **standard** concepts only. Querying them without enforcing `standard_concept = 'S'` risks including non-standard, source-vocabulary, or metadata concepts, producing a biased or inflated cohort.

#### How it works

The rule detects references to any [STANDARD concept field](../src/fastssv/schemas/semantic_schema.py) (`*_concept_id` columns that are not source fields). When such a reference is found, it checks whether the query satisfies at least one of:

1. Joins the `concept` table and filters `concept.standard_concept = 'S'` in a WHERE or JOIN ON clause.
2. Uses `concept_relationship` with `relationship_id = 'Maps to'` (the ETL mapping pattern).

If neither is present the rule fires.

#### Examples

**Violation — no enforcement:**
```sql
SELECT co.person_id
FROM condition_occurrence co
WHERE co.condition_concept_id IN (201826, 201254);
-- concept_ids are hardcoded but the query never verifies they are standard
```

**Violation — joins concept but skips the standard_concept filter:**
```sql
SELECT co.person_id, c.concept_name
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.vocabulary_id = 'SNOMED';
-- SNOMED contains both standard and non-standard concepts; this is insufficient
```

**Pass — explicit standard concept enforcement:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.standard_concept = 'S'
  AND c.invalid_reason IS NULL;
```

**Pass — Maps to relationship pattern (ETL-style lookup):**
```sql
SELECT cr.concept_id_2 AS standard_concept_id
FROM concept_relationship cr
WHERE cr.concept_id_1 = 45542286          -- source ICD-10 concept
  AND cr.relationship_id = 'Maps to'
  AND cr.invalid_reason IS NULL;
```

**Pass — standard_concept filter in JOIN ON clause:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c
  ON co.condition_concept_id = c.concept_id
  AND c.standard_concept = 'S';
```

#### Common scenarios

- Hardcoding concept IDs in an IN list without verifying they are standard — the IDs may be valid but non-standard.
- Filtering `vocabulary_id = 'SNOMED'` instead of `standard_concept = 'S'`; SNOMED has classification concepts that are not standard.
- Querying `drug_exposure.drug_concept_id` and joining `concept` only for display (concept_name), forgetting the standard_concept filter.

#### Related rules

- **Maps To Direction** — validates the correct column roles when using the Maps to pattern.
- **Domain Segregation** — validates that the standard concepts belong to the right domain.

---

### 2. Join Path Validation

**Rule ID:** `semantic.join_path_validation`
**Severity:** WARNING

#### Intent

The OMOP CDM v5.4 schema defines precise foreign-key relationships between tables. For example, `condition_occurrence.condition_concept_id` is a foreign key to `concept.concept_id`, not to any other column. Joining tables on wrong or unconventional column pairs produces an implicit cross-join, Cartesian products, or silently empty results.

#### How it works

The rule uses the full [CDM schema graph](../src/fastssv/schemas/cdm_schema.py) — 43 tables, all foreign key edges — to verify that every JOIN in a query uses a valid `(foreign_key → primary_key)` column pair as defined by the spec. Joins to CTE aliases are explicitly excluded since they represent derived datasets, not physical CDM tables.

#### Examples

**Violation — joining vocabulary table to clinical table on wrong column:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.person_id = c.concept_id;
-- person_id has no relationship to concept.concept_id
```

**Violation — joining two clinical tables on a non-FK column:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN drug_exposure de ON co.condition_concept_id = de.drug_concept_id;
-- no CDM relationship between these two concept columns
```

**Pass — standard clinical-to-vocabulary join:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id;
```

**Pass — clinical-to-clinical join on person_id:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN drug_exposure de ON co.person_id = de.person_id;
-- person_id is the correct join key between event tables
```

**Pass — CTE join (excluded from schema checking):**
```sql
WITH diabetes_concepts AS (
    SELECT descendant_concept_id AS concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201826
)
SELECT co.person_id
FROM condition_occurrence co
JOIN diabetes_concepts dc ON co.condition_concept_id = dc.concept_id;
-- CTE join is not schema-validated; only the inner CTE query is
```

#### Common scenarios

- Accidentally joining `concept.concept_id` to `person.person_id` (same integer type, different semantics).
- Joining two vocabulary tables on concept_name instead of concept_id.
- Self-joins on clinical tables using a non-standard key.

---

### 3. Hierarchy Expansion Required

**Rule ID:** `semantic.hierarchy_expansion_required`
**Severity:** ERROR

#### Intent

OMOP vocabularies (SNOMED, RxNorm) are hierarchical. A concept like "Metformin" (RxNorm) has many descendants: "Metformin 500 MG", "Metformin 500 MG Oral Tablet", "Metformin XR 750 MG", and hundreds more. Filtering `drug_concept_id = 1503297` (Metformin) without hierarchy expansion returns only records coded to the ingredient level concept, silently missing all specific formulations.

The `concept_ancestor` table materialises the full hierarchy. Any filter on `drug_concept_id` or `condition_concept_id` that uses a hardcoded concept ID without a `concept_ancestor` join is almost certainly incomplete.

#### How it works

The rule checks whether a query filters `drug_exposure.drug_concept_id` or `condition_occurrence.condition_concept_id` using specific numeric literals (an equality or IN list), and whether the query also uses the `concept_ancestor` table. If the filter is present but `concept_ancestor` is absent, an ERROR is raised.

The rule is intentionally limited to `drug_concept_id` and `condition_concept_id`, the two fields where hierarchy expansion is most clinically critical.

#### Examples

**Violation — single concept_id without hierarchy expansion:**
```sql
SELECT de.person_id, de.drug_exposure_start_date
FROM drug_exposure de
WHERE de.drug_concept_id = 1503297;  -- Metformin ingredient only
-- misses Metformin 500mg, Metformin XR, combination products, etc.
```

**Violation — IN list without hierarchy expansion:**
```sql
SELECT co.person_id
FROM condition_occurrence co
WHERE co.condition_concept_id IN (201826, 201254, 443238);
-- hardcoded concept IDs with no descendant expansion
```

**Pass — correct hierarchy expansion via concept_ancestor:**
```sql
SELECT de.person_id, de.drug_exposure_start_date
FROM drug_exposure de
JOIN concept_ancestor ca
  ON de.drug_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 1503297;  -- captures all Metformin descendants
```

**Pass — CTE-based hierarchy expansion:**
```sql
WITH metformin_concepts AS (
    SELECT descendant_concept_id AS concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 1503297
)
SELECT de.person_id
FROM drug_exposure de
JOIN metformin_concepts mc ON de.drug_concept_id = mc.concept_id;
```

**Pass — subquery hierarchy expansion:**
```sql
SELECT de.person_id
FROM drug_exposure de
WHERE de.drug_concept_id IN (
    SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 1503297
);
```

#### Common scenarios

- Phenotype definitions that copy concept IDs from ATLAS or published studies and paste them directly into IN lists.
- Drug utilisation studies using a single ingredient concept ID.
- Condition prevalence studies using a single condition concept ID without checking SNOMED children.

#### Note on concept_ancestor validity

If combining this rule with `semantic.invalid_reason_enforcement`, note that `concept_ancestor` itself has no `invalid_reason` column. The companion rule recommends joining back to `concept` to check `invalid_reason IS NULL` on the ancestor concepts.

---

### 4. Observation Period Anchoring

**Rule ID:** `semantic.observation_period_anchoring`
**Severity:** ERROR

#### Intent

In OMOP, a patient's `observation_period` defines the window during which their data is considered complete and reliable. Events recorded outside this window may be present in the data for historical or administrative reasons but do not represent complete, active observation.

Temporal analyses — washout periods, follow-up windows, time-to-event — are only valid within observation windows. A query that filters `condition_start_date > '2020-01-01'` without anchoring to `observation_period` may include patients who entered the database after 2020 but whose observation window started even later, inflating baseline period lengths or introducing immortal time.

#### How it works

The rule detects temporal filtering: comparisons (`>`, `<`, `BETWEEN`, `>=`, `<=`) on date columns (columns ending in `_date`, `_datetime`, `_time`, or belonging to the known set of 24+ CDM date columns). It also detects date arithmetic functions (`DATEADD`, `DATEDIFF`, `INTERVAL`, etc.).

When temporal activity is detected on clinical tables, the rule checks whether `observation_period` is also present in the query and properly joined to the clinical table on `person_id`.

#### Examples

**Violation — date filter with no observation period:**
```sql
SELECT co.person_id
FROM condition_occurrence co
WHERE co.condition_start_date BETWEEN '2020-01-01' AND '2022-12-31';
-- no guarantee patients were actively observed throughout this window
```

**Violation — date arithmetic without observation period:**
```sql
SELECT de.person_id,
       DATEDIFF('day', de.drug_exposure_start_date, de.drug_exposure_end_date) AS days_supply
FROM drug_exposure de
WHERE de.drug_exposure_start_date > '2019-01-01';
```

**Violation — observation_period present but joined on wrong column:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN observation_period op ON co.condition_concept_id = op.person_id
-- wrong join key; should be co.person_id = op.person_id
WHERE co.condition_start_date > '2020-01-01';
```

**Pass — temporal filter anchored to observation period:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN observation_period op ON co.person_id = op.person_id
WHERE co.condition_start_date BETWEEN op.observation_period_start_date
                                  AND op.observation_period_end_date
  AND op.observation_period_start_date <= '2018-01-01';  -- at least 2 years prior
```

**Pass — washout period using observation period start:**
```sql
SELECT co.person_id, co.condition_start_date AS index_date
FROM condition_occurrence co
JOIN observation_period op ON co.person_id = op.person_id
WHERE co.condition_start_date >= DATEADD('year', 1, op.observation_period_start_date)
  AND co.condition_start_date <= op.observation_period_end_date;
-- ensures at least 1 year washout before the index event
```

**Pass — observation period in CTE:**
```sql
WITH eligible_patients AS (
    SELECT person_id
    FROM observation_period
    WHERE observation_period_end_date >= '2022-01-01'
      AND DATEDIFF('year', observation_period_start_date, observation_period_end_date) >= 1
)
SELECT co.person_id
FROM condition_occurrence co
JOIN eligible_patients ep ON co.person_id = ep.person_id
WHERE co.condition_start_date >= '2021-01-01';
```

#### Common scenarios

- Prevalence studies counting conditions in a calendar year without checking if patients were observed for the full year.
- Time-to-event analyses where the start of follow-up is a calendar date rather than the observation period start.
- Claims data studies where patients may have gaps in coverage.

---

### 5. Maps To Direction

**Rule ID:** `semantic.maps_to_direction`
**Severity:** WARNING

#### Intent

The `concept_relationship` table stores bidirectional relationships, but the `'Maps to'` relationship has a strict directionality:

- `concept_id_1` → the **source** concept (e.g., ICD-10-CM code)
- `concept_id_2` → the **standard** concept (e.g., SNOMED CT condition)

The `'Mapped from'` relationship is the inverse. Confusing the direction means joining a standard concept field to `concept_id_1` (the source slot) and retrieving either nothing or semantically reversed mappings.

#### How it works

When a query uses `concept_relationship` with `relationship_id = 'Maps to'`, the rule checks whether any standard concept field (e.g., `condition_concept_id`) is joined to `concept_id_1`. Because standard fields hold standard concepts and `concept_id_1` holds source concepts, this join combination indicates reversed directionality.

The rule fires as a WARNING rather than ERROR because there are uncommon but valid cases where `concept_id_1` is standard (e.g., in cross-vocabulary relationships), so the rule reports a potential issue for human review.

#### Examples

**Violation — standard concept field joined to concept_id_1 (source slot):**
```sql
-- Attempting to find what source codes map to a given standard concept
SELECT cr.concept_id_1 AS source_concept
FROM condition_occurrence co
JOIN concept_relationship cr
  ON co.condition_concept_id = cr.concept_id_1  -- WRONG: standard field in source slot
WHERE cr.relationship_id = 'Maps to';
-- This returns nothing useful; condition_concept_id holds standard concepts
-- which appear in concept_id_2, not concept_id_1
```

**Pass — source concept field joined to concept_id_1, retrieving standard result:**
```sql
-- Correct ETL-style mapping: find standard concept for a source concept
SELECT cr.concept_id_2 AS standard_concept_id
FROM condition_occurrence co
JOIN concept_relationship cr
  ON co.condition_source_concept_id = cr.concept_id_1  -- source field in source slot
WHERE cr.relationship_id = 'Maps to'
  AND cr.invalid_reason IS NULL;
```

**Pass — finding source codes for a known standard concept (using 'Mapped from'):**
```sql
-- Correct reverse lookup uses 'Mapped from' not 'Maps to'
SELECT cr.concept_id_2 AS source_concept_id
FROM concept_relationship cr
WHERE cr.concept_id_1 = 201826          -- standard SNOMED concept
  AND cr.relationship_id = 'Mapped from'
  AND cr.invalid_reason IS NULL;
```

**Pass — Maps to used correctly in a concept resolution CTE:**
```sql
WITH mapped_concepts AS (
    SELECT cr.concept_id_2 AS standard_concept_id
    FROM concept_relationship cr
    WHERE cr.concept_id_1 IN (
        SELECT concept_id FROM concept
        WHERE concept_code IN ('E11', 'E11.9')
          AND vocabulary_id = 'ICD10CM'
    )
    AND cr.relationship_id = 'Maps to'
    AND cr.invalid_reason IS NULL
)
SELECT co.person_id
FROM condition_occurrence co
JOIN mapped_concepts mc ON co.condition_concept_id = mc.standard_concept_id;
```

#### Common scenarios

- Analysts trying to find all source codes for a known standard concept, using `'Maps to'` in reverse instead of `'Mapped from'`.
- Copy-pasted ETL code repurposed for analytical queries with column roles swapped.

---

### 6. Unmapped Concept Handling

**Rule ID:** `semantic.unmapped_concept_handling`
**Severity:** WARNING

#### Intent

During ETL, source codes that cannot be mapped to any standard concept receive `concept_id = 0`. This is the OMOP convention for "no matching concept found." Records with `concept_id = 0` are real clinical events — they simply have no standardised representation.

A query that filters `condition_concept_id = 201826` or `drug_concept_id IN (1503297, ...)` silently excludes all unmapped records. In some datasets — particularly claims data with non-standard proprietary codes — unmapped records can be 10–30% of events. Silently dropping them biases incidence, prevalence, and drug utilisation calculations.

#### How it works

The rule identifies filters on `*_concept_id` columns in clinical tables that use specific numeric literal values (either `= <id>` or `IN (<id1>, <id2>, ...)`). It then checks whether the query also explicitly handles `concept_id = 0` anywhere in the statement — through `= 0`, `!= 0`, `<> 0`, `> 0`, `>= 1`, `COALESCE(column, 0)`, or `CASE WHEN column = 0`.

If specific concept IDs are filtered without any zero-handling expression, a WARNING is raised.

#### Examples

**Violation — filtering specific concept IDs without handling unmapped:**
```sql
SELECT COUNT(*) AS condition_count
FROM condition_occurrence
WHERE condition_concept_id IN (201826, 201254, 443238);
-- unmapped records (concept_id = 0) are silently excluded
```

**Pass — explicitly excluding unmapped records:**
```sql
SELECT COUNT(*) AS condition_count
FROM condition_occurrence
WHERE condition_concept_id IN (201826, 201254, 443238)
  AND condition_concept_id > 0;  -- explicit exclusion of unmapped
```

**Pass — explicitly including unmapped records for transparency:**
```sql
SELECT
    CASE WHEN condition_concept_id = 0 THEN 'Unmapped' ELSE 'Mapped' END AS mapping_status,
    COUNT(*) AS n
FROM condition_occurrence
WHERE condition_concept_id IN (201826, 201254, 0)
GROUP BY mapping_status;
```

**Pass — COALESCE pattern:**
```sql
SELECT person_id
FROM condition_occurrence
WHERE COALESCE(condition_concept_id, 0) IN (201826, 201254);
```

**Pass — no specific concept filter, no violation:**
```sql
-- Querying all conditions for a person; unmapped records are naturally included
SELECT person_id, condition_concept_id
FROM condition_occurrence
WHERE person_id = 12345;
```

#### Common scenarios

- Cohort definitions that filter specific concept IDs but are reported as counts of "all patients with condition X" when they should say "all patients with mapped condition X."
- Drug safety studies where rare or brand-name drugs have high unmapping rates.
- Sites with locally-developed coding systems that produce many unmapped records.

---

### 7. Invalid Reason Enforcement

**Rule ID:** `semantic.invalid_reason_enforcement`
**Severity:** ERROR (for direct vocabulary tables) / WARNING (for derived tables)

#### Intent

OMOP vocabularies evolve. Concepts are deprecated, replaced, split, or merged across vocabulary releases. The `concept` and `concept_relationship` tables mark deprecated entries with a non-null `invalid_reason`:

- `'D'` — deprecated, no replacement
- `'U'` — updated, replaced by a different concept

Querying the `concept` table without filtering `invalid_reason IS NULL` can return retired concepts, leading to incorrect concept lookups, false-negative concept matching, or double-counting when both the old and new concept IDs are present.

The `concept_ancestor`, `concept_synonym`, `drug_strength`, and `source_to_concept_map` tables do not have an `invalid_reason` column, but they may reference deprecated concepts. The recommended practice is to join back to `concept` and filter there.

#### How it works

The rule detects use of vocabulary tables in the query. For tables that have `invalid_reason` directly (`concept`, `concept_relationship`), it checks for an `invalid_reason` filter in WHERE or JOIN ON clauses. For derived tables without the column (`concept_ancestor`, `concept_synonym`, `drug_strength`, `source_to_concept_map`), it checks whether a `concept` table is also joined and an `invalid_reason` filter is present there.

Missing filters on direct vocabulary tables → **ERROR**.
Missing filters for derived tables → **WARNING** (softer, since the column does not exist on those tables).

#### Examples

**ERROR — querying concept without invalid_reason filter:**
```sql
SELECT concept_id, concept_name
FROM concept
WHERE vocabulary_id = 'SNOMED'
  AND standard_concept = 'S';
-- may return deprecated SNOMED concepts superseded by newer codes
```

**ERROR — querying concept_relationship without invalid_reason filter:**
```sql
SELECT concept_id_2 AS standard_concept_id
FROM concept_relationship
WHERE concept_id_1 = 45542286
  AND relationship_id = 'Maps to';
-- the mapping itself may be deprecated
```

**WARNING — querying concept_ancestor without joining concept for validity:**
```sql
SELECT descendant_concept_id
FROM concept_ancestor
WHERE ancestor_concept_id = 201826;
-- concept_ancestor may reference concepts marked invalid in the concept table
```

**Pass — concept table with invalid_reason filter:**
```sql
SELECT concept_id, concept_name
FROM concept
WHERE vocabulary_id = 'SNOMED'
  AND standard_concept = 'S'
  AND invalid_reason IS NULL;
```

**Pass — concept_relationship with invalid_reason filter:**
```sql
SELECT concept_id_2 AS standard_concept_id
FROM concept_relationship
WHERE concept_id_1 = 45542286
  AND relationship_id = 'Maps to'
  AND invalid_reason IS NULL;
```

**Pass — concept_ancestor with concept join and invalid_reason filter:**
```sql
SELECT ca.descendant_concept_id
FROM concept_ancestor ca
JOIN concept c ON ca.descendant_concept_id = c.concept_id
WHERE ca.ancestor_concept_id = 201826
  AND c.invalid_reason IS NULL;
```

**Pass — invalid_reason filter in JOIN ON clause:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c
  ON co.condition_concept_id = c.concept_id
  AND c.invalid_reason IS NULL
  AND c.standard_concept = 'S';
```

#### Common scenarios

- Vocabulary upgrade cycles: a concept was valid in CDM v5.3 but deprecated in v5.4.
- Studies reusing concept lists from older publications without re-validating against current vocabularies.
- Cross-vocabulary mappings where the relationship itself was deprecated.

---

### 8. Domain Segregation

**Rule ID:** `semantic.domain_segregation`
**Severity:** ERROR (wrong domain) / WARNING (missing domain)

#### Intent

Every OMOP concept belongs to exactly one domain (`concept.domain_id`). The CDM enforces domain segregation at the table level: `condition_occurrence` stores only Condition-domain concepts, `drug_exposure` stores only Drug-domain concepts, and so on. This segregation is guaranteed by the ETL process.

When a query joins a clinical table to the `concept` table and filters `domain_id` to the wrong value, the join will return zero rows — silently, without any SQL error. When no `domain_id` filter is present, there is a risk of accidentally pulling in metadata, type, or observation concepts that share concept IDs with the intended domain in edge cases.

**Enforced mappings:**

| Clinical Table | Primary Concept Column | Expected `domain_id` |
|---------------|------------------------|----------------------|
| `condition_occurrence` | `condition_concept_id` | `Condition` |
| `drug_exposure` | `drug_concept_id` | `Drug` |
| `procedure_occurrence` | `procedure_concept_id` | `Procedure` |
| `measurement` | `measurement_concept_id` | `Measurement` |
| `observation` | `observation_concept_id` | `Observation` |
| `device_exposure` | `device_concept_id` | `Device` |
| `visit_occurrence` | `visit_concept_id` | `Visit` |
| `specimen` | `specimen_concept_id` | `Specimen` |
| `death` | `cause_concept_id` | `Condition` |

Note: `*_type_concept_id`, `*_source_concept_id`, modifier, unit, and status concept columns are intentionally excluded — only the primary entity concept column is checked.

#### How it works

The rule inspects JOIN ON conditions that link a clinical table to the `concept` table via a primary `*_concept_id` column. It captures the alias used for the concept table in each such join, then checks whether a `domain_id` filter is scoped to that alias.

- **Wrong domain** → ERROR (e.g., `domain_id = 'Procedure'` when querying `condition_occurrence`).
- **No domain filter** → WARNING (advisory: adding `domain_id` is defensive best practice).

CTE-based concept lookups (where the clinical table joins a derived CTE rather than `concept` directly) are correctly excluded.

#### Examples

**ERROR — wrong domain filter returns zero rows silently:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.standard_concept = 'S'
  AND c.domain_id = 'Procedure';  -- wrong: conditions are not procedure concepts
-- This query executes without error but returns 0 rows
```

**ERROR — drug exposure queried with condition domain:**
```sql
SELECT de.person_id, de.drug_exposure_start_date
FROM drug_exposure de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.domain_id = 'Condition';  -- wrong: drugs belong to 'Drug' domain
```

**WARNING — concept join without domain filter:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.standard_concept = 'S'
  AND c.invalid_reason IS NULL;
-- Valid query, but adding domain_id = 'Condition' adds explicit domain safety
```

**Pass — correct domain for condition_occurrence:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.standard_concept = 'S'
  AND c.domain_id = 'Condition'
  AND c.invalid_reason IS NULL;
```

**Pass — multiple clinical tables with correct per-table domain filters:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN drug_exposure de ON co.person_id = de.person_id
JOIN concept cc ON co.condition_concept_id = cc.concept_id
  AND cc.domain_id = 'Condition'
  AND cc.standard_concept = 'S'
JOIN concept dc ON de.drug_concept_id = dc.concept_id
  AND dc.domain_id = 'Drug'
  AND dc.standard_concept = 'S';
```

**Pass — death.cause_concept_id uses Condition domain:**
```sql
SELECT d.person_id, d.cause_concept_id
FROM death d
JOIN concept c ON d.cause_concept_id = c.concept_id
WHERE c.domain_id = 'Condition'  -- cause of death is a condition concept
  AND c.standard_concept = 'S'
  AND c.invalid_reason IS NULL;
```

**Pass — CTE-based concept join is not checked (correct behaviour):**
```sql
WITH condition_concepts AS (
    SELECT descendant_concept_id AS concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201826
)
SELECT co.person_id
FROM condition_occurrence co
JOIN condition_concepts cc ON co.condition_concept_id = cc.concept_id;
-- CTE join is not flagged; domain is implicitly controlled by the ancestor seed
```

**Pass — type_concept_id join is not checked:**
```sql
SELECT co.condition_type_concept_id
FROM condition_occurrence co
JOIN concept c ON co.condition_type_concept_id = c.concept_id
WHERE c.standard_concept = 'S';
-- type_concept_id is not a primary entity column; domain rule does not apply
```

#### Common scenarios

- Copy-pasting a procedure query and adapting it for conditions, forgetting to update `domain_id`.
- Building a multi-domain concept lookup function and passing the wrong domain constant.
- Refactoring queries that originally had no `domain_id` filter after a vocabulary upgrade introduced cross-domain concept IDs.

---

## Vocabulary Rules

Vocabulary rules validate how SQL queries interact with the OMOP vocabulary tables — ensuring concepts are identified correctly, unambiguously, and using the right column semantics.

---

### 9. No String Identification

**Rule ID:** `vocabulary.no_string_identification`
**Severity:** ERROR

#### Intent

`*_source_value` columns preserve the raw, pre-ETL text from the originating system. Their content is site-specific, non-standardised, and not guaranteed to be consistent across CDM instances. A string like `'250.00'` in `condition_source_value` might be ICD-9-CM diabetes at one site and a local lab code at another.

Filtering on these columns using LIKE, =, IN, or regex patterns makes queries non-portable, brittle to ETL variations, and analytically incorrect for multi-site research. The correct approach is to use `*_concept_id` (standard) or `*_source_concept_id` (source vocabulary ID) columns.

#### How it works

The rule scans WHERE and JOIN ON clauses for string comparison operations (equality `=`, `IN`, `LIKE`, `ILIKE`, `REGEXP`) applied to any `*_source_value` column in a CDM clinical table. Any such pattern is flagged as an ERROR regardless of context.

#### Examples

**ERROR — LIKE on condition_source_value:**
```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_source_value LIKE '%diabetes%';
-- 'diabetes' may appear differently across ETL implementations
-- not portable across CDM instances
```

**ERROR — equality on drug_source_value:**
```sql
SELECT person_id
FROM drug_exposure
WHERE drug_source_value = 'metformin 500mg';
-- site-specific free text; will not work at any other CDM site
```

**ERROR — IN list on visit_source_value:**
```sql
SELECT person_id
FROM visit_occurrence
WHERE visit_source_value IN ('IP', 'OP', 'ED');
-- these abbreviations are site-specific and unmapped to OMOP conventions
```

**Pass — using standard concept_id instead:**
```sql
SELECT person_id
FROM condition_occurrence co
JOIN concept_ancestor ca ON co.condition_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 201826;  -- Type 2 Diabetes SNOMED concept
```

**Pass — using source_concept_id for site-specific lookups:**
```sql
-- When you know the source vocabulary concept ID
SELECT person_id
FROM condition_occurrence
WHERE condition_source_concept_id = 44828967;  -- ICD-10-CM E11 concept ID
```

#### Common scenarios

- Single-site analyses where source values happen to be consistent, later broken when applied to multi-site networks.
- Exploratory queries used as a starting point, not cleaned up before production use.
- Legacy code from pre-OMOP ETLs where raw values were the only identifier.

---

### 10. Concept Lookup Context

**Rule ID:** `vocabulary.concept_lookup_context`
**Severity:** ERROR

#### Intent

String-based lookups on the `concept` table (filtering by `concept_name`, `concept_code`, `vocabulary_id`, `domain_id`, etc.) are legitimate, but only within a **concept_id resolution context** — that is, a subquery or CTE whose purpose is to produce a list of `concept_id` values for use in clinical table filters.

Using string filters directly on clinical event tables — even indirectly through a join — makes the concept selection non-reproducible, since `concept_name` strings change across vocabulary versions. Concept names that existed in OMOP v5.3 vocabularies may be reworded or split in v5.4.

The correct pattern: resolve concept names to concept IDs once in a subquery or CTE, then use those IDs to filter clinical tables.

#### How it works

The rule detects string comparisons (equality, LIKE, IN) on `concept` table text columns: `concept_name`, `concept_code`, `vocabulary_id`, `domain_id`, `concept_class_id`. It then checks whether the column reference is inside a SELECT that:

1. Selects from a vocabulary table, AND
2. Outputs `concept_id` (or a column aliased as `concept_id`) in its projection

If the string filter is outside such a context — for example, in a main SELECT that directly joins `concept` to `condition_occurrence` and filters by `concept_name` — the rule fires.

#### Examples

**ERROR — filtering clinical table by concept_name in main query:**
```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.concept_name = 'Type 2 diabetes mellitus';
-- concept_name may change across vocabulary versions; not reproducible
```

**ERROR — LIKE on concept_name in main JOIN:**
```sql
SELECT de.person_id
FROM drug_exposure de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.concept_name LIKE '%metformin%';
-- matches vary by vocabulary version; will differ between CDM instances
```

**Pass — concept_name used inside a concept_id-outputting subquery:**
```sql
SELECT co.person_id
FROM condition_occurrence co
WHERE co.condition_concept_id IN (
    SELECT concept_id            -- ← outputs concept_id
    FROM concept
    WHERE concept_name = 'Type 2 diabetes mellitus'
      AND standard_concept = 'S'
      AND invalid_reason IS NULL
);
```

**Pass — concept_name used inside a CTE that outputs concept_id:**
```sql
WITH diabetes_concepts AS (
    SELECT concept_id            -- ← outputs concept_id
    FROM concept
    WHERE concept_name LIKE '%diabetes%'
      AND standard_concept = 'S'
      AND domain_id = 'Condition'
      AND invalid_reason IS NULL
)
SELECT co.person_id
FROM condition_occurrence co
JOIN diabetes_concepts dc ON co.condition_concept_id = dc.concept_id;
```

**Pass — EXISTS subquery correlating on concept_id:**
```sql
SELECT co.person_id
FROM condition_occurrence co
WHERE EXISTS (
    SELECT 1
    FROM concept c
    WHERE c.concept_id = co.condition_concept_id
      AND c.standard_concept = 'S'
      AND c.invalid_reason IS NULL
);
-- EXISTS correlating on concept_id is a valid lookup context
```

#### Common scenarios

- Ad-hoc exploratory queries joining concept table for display purposes and filtering by concept_name.
- Studies that hard-code concept names from an older vocabulary version and break silently when the CDM is upgraded.
- Analysts unfamiliar with OMOP who search for clinical entities by text rather than by concept ID.

---

### 11. Concept Code Requires Vocabulary ID

**Rule ID:** `vocabulary.concept_code_requires_vocabulary_id`
**Severity:** ERROR

#### Intent

`concept.concept_code` stores the raw code from the originating vocabulary: `'E11.9'` for ICD-10-CM diabetes, `'1503297'` for RxNorm Metformin, `'73211009'` for SNOMED diabetes. Crucially, **concept_code is not unique across vocabularies**. The same string `'250.00'` is a valid code in both ICD-9-CM and a number of other vocabularies.

Filtering `concept_code = 'E11.9'` without a `vocabulary_id = 'ICD10CM'` constraint will match every concept with that code across all vocabularies loaded in the CDM — potentially returning concepts from the wrong vocabulary with a different clinical meaning.

#### How it works

The rule finds every `concept_code` filter (equality, IN, LIKE, ILIKE, REGEXP) in a WHERE or JOIN ON clause that is scoped to the `concept` table. It then checks whether a `vocabulary_id` equality or IN filter is present in the same SELECT scope for the same concept table alias.

Filters in nested subqueries or outer scopes are intentionally excluded; only filters within the same SELECT level are considered co-located.

#### Examples

**ERROR — concept_code without vocabulary_id:**
```sql
SELECT concept_id
FROM concept
WHERE concept_code = 'E11.9'
  AND standard_concept = 'S';
-- 'E11.9' could match across multiple vocabularies
```

**ERROR — concept_code IN list without vocabulary_id:**
```sql
SELECT concept_id
FROM concept
WHERE concept_code IN ('E11', 'E11.9', 'E11.65')
  AND invalid_reason IS NULL;
-- ambiguous: ICD-10-CM and ICD-10 both have E11 codes
```

**ERROR — concept_code LIKE without vocabulary_id:**
```sql
SELECT concept_id
FROM concept
WHERE concept_code LIKE 'E11%';
-- pattern matches across any vocabulary with E11-prefixed codes
```

**Pass — concept_code with vocabulary_id in same scope:**
```sql
SELECT concept_id
FROM concept
WHERE concept_code = 'E11.9'
  AND vocabulary_id = 'ICD10CM'
  AND invalid_reason IS NULL;
```

**Pass — concept_code IN with vocabulary_id IN:**
```sql
SELECT concept_id
FROM concept
WHERE concept_code IN ('E11', 'E11.9', 'E11.65')
  AND vocabulary_id IN ('ICD10CM', 'ICD10')
  AND standard_concept = 'S';
```

**Pass — different scopes, each with their own vocabulary_id:**
```sql
WITH icd_concepts AS (
    SELECT concept_id
    FROM concept
    WHERE concept_code LIKE 'E11%'
      AND vocabulary_id = 'ICD10CM'  -- vocabulary_id present in this scope
      AND invalid_reason IS NULL
),
rxnorm_concepts AS (
    SELECT concept_id
    FROM concept
    WHERE concept_code = '1503297'
      AND vocabulary_id = 'RxNorm'   -- vocabulary_id present in this scope
      AND invalid_reason IS NULL
)
SELECT ...
```

#### Common scenarios

- Analysts copying concept codes from clinical guidelines without specifying which coding system they came from.
- Queries migrated from single-vocabulary environments (ICD-9-CM-only) to multi-vocabulary CDMs.
- Code lookups during ETL development where the author knows which vocabulary is intended but doesn't encode it explicitly.

---

## Cross-rule interactions

Some violations are related. Addressing one rule's violation often reveals or resolves another's:

| If you fix... | Also check... |
|---------------|---------------|
| Standard Concept Enforcement | Maps To Direction (if using concept_relationship) |
| Standard Concept Enforcement | Domain Segregation (add domain_id after adding standard_concept filter) |
| Standard Concept Enforcement | Invalid Reason Enforcement (add invalid_reason IS NULL) |
| Hierarchy Expansion Required | Invalid Reason Enforcement (concept_ancestor needs concept validity check) |
| Concept Lookup Context | Invalid Reason Enforcement (subquery should filter invalid_reason) |
| Concept Lookup Context | Concept Code Requires Vocabulary ID (subquery should include vocabulary_id) |
| Maps To Direction | Invalid Reason Enforcement (concept_relationship needs invalid_reason filter) |

A fully compliant query joining a clinical table to the concept vocabulary typically requires all of the following:

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept_ancestor ca
  ON co.condition_concept_id = ca.descendant_concept_id   -- hierarchy expansion
JOIN concept c
  ON ca.ancestor_concept_id = c.concept_id               -- concept validation
  AND c.standard_concept = 'S'                            -- standard concept enforcement
  AND c.domain_id = 'Condition'                           -- domain segregation
  AND c.invalid_reason IS NULL                            -- invalid reason enforcement
JOIN observation_period op
  ON co.person_id = op.person_id                         -- observation period anchoring
WHERE co.condition_concept_id > 0                         -- unmapped concept handling
  AND co.condition_start_date BETWEEN
      op.observation_period_start_date
      AND op.observation_period_end_date;
```

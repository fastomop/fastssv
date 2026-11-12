# FastSSV Rules Reference

This document provides comprehensive documentation for FastSSV's validation rules.

**Current registry: 92 rules across 6 categories** (37 ERROR, 55 WARNING).

- **anti_patterns**: 15 rules
- **concept_standardization**: 14 rules
- **data_quality**: 14 rules
- **domain_specific**: 19 rules
- **joins**: 23 rules
- **temporal**: 7 rules

> **Note:** The prose descriptions below reflect the stable rule set. Rule
> removals and rule renames are tracked in [CHANGELOG.md](https://github.com/fastomop/fastSSV/blob/main/CHANGELOG.md).
> If a rule listed here is no longer in the registry, check the CHANGELOG
> for the removal rationale. If a rule appears in the registry but not
> below, it was added after this document's last full pass — the rule's
> own docstring and `suggested_fix` field are the authoritative source.
>
> For the live registered rule set at any moment, use:
>
> ```python
> from fastssv import get_all_rules
> for rule_cls in get_all_rules():
>     rule = rule_cls()
>     print(rule.rule_id, "—", rule.description)
> ```

For each rule you will find:

- **Intent** — the OMOP CDM constraint being enforced and why it matters scientifically
- **How it works** — what the rule inspects in the SQL AST
- **Severity** — whether violations are ERRORs or WARNINGs, and why
- **Examples** — failing SQL, passing SQL, and edge cases
- **Common scenarios** — real-world situations where this fires
- **Suggested fix** — how to resolve the violation
- **Sources** — the published specification, convention or engine documentation the rule is derived from

---

## Table of Contents

1. [Quick Reference Table](#quick-reference-table)
2. [Severity Levels](#severity-levels)
3. [Concept Standardization Rules](#concept-standardization-rules)
4. [Join Validation Rules](#join-validation-rules)
5. [Temporal Rules](#temporal-rules)
6. [Data Quality Rules](#data-quality-rules)
7. [Anti-Pattern Rules](#anti-pattern-rules)
8. [Domain-Specific Rules](#domain-specific-rules)

---

## Quick Reference Table

| Rule ID | Name | Severity | Category |
|---------|------|----------|----------|
| `concept_standardization.concept_ancestor_max_levels_misuse` | [Concept Ancestor Max Levels Misuse](#concept-standardization-concept-ancestor-max-levels-misuse) | WARNING | concept_standardization |
| `concept_standardization.concept_ancestor_rollup_direction` | [Concept Ancestor Rollup Direction](#concept-standardization-concept-ancestor-rollup-direction) | WARNING | concept_standardization |
| `concept_standardization.concept_ancestor_self_include_redundancy` | [Concept Ancestor Self-Include Redundancy](#concept-standardization-concept-ancestor-self-include-redundancy) | WARNING | concept_standardization |
| `concept_standardization.concept_domain_validation` | [Concept Domain ID Matches Target Column](#concept-standardization-concept-domain-validation) | ERROR | concept_standardization |
| `concept_standardization.concept_literal_without_hierarchy` | [Concept literal without hierarchy expansion](#concept-standardization-concept-literal-without-hierarchy) | WARNING | concept_standardization |
| `concept_standardization.domain_vocabulary_validation` | [Domain Vocabulary Validation](#concept-standardization-domain-vocabulary-validation) | ERROR | concept_standardization |
| `concept_standardization.era_table_standard_concepts` | [Era Tables Use Standard Concepts Only](#concept-standardization-era-table-standard-concepts) | ERROR | concept_standardization |
| `concept_standardization.invalid_reason_enforcement` | [Invalid Reason Enforcement (strict mode)](#concept-standardization-invalid-reason-enforcement) | WARNING | concept_standardization |
| `concept_standardization.multiple_maps_to_targets` | [Multiple Maps To Targets Not Handled](#concept-standardization-multiple-maps-to-targets) | WARNING | concept_standardization |
| `concept_standardization.source_concept_id_standard_filter` | [Source Concept ID Should Not Filter Standard Concepts](#concept-standardization-source-concept-id-standard-filter) | WARNING | concept_standardization |
| `concept_standardization.source_to_concept_map_validation` | [Source to Concept Map Validation](#concept-standardization-source-to-concept-map-validation) | WARNING | concept_standardization |
| `concept_standardization.standard_concept_enforcement` | [Standard Concept Enforcement](#concept-standardization-standard-concept-enforcement) | WARNING | concept_standardization |
| `concept_standardization.standard_concept_value_validation` | [Standard Concept Value Validation](#concept-standardization-standard-concept-value-validation) | ERROR | concept_standardization |
| `concept_standardization.unit_vocabulary_validation` | [Unit Vocabulary Validation](#concept-standardization-unit-vocabulary-validation) | ERROR | concept_standardization |
| `joins.care_site_id_join_validation` | [Care Site and Location Key Join Validation](#joins-care-site-id-join-validation) | ERROR | joins |
| `joins.care_site_join_validation` | [Care Site Join Path Validation](#joins-care-site-join-validation) | ERROR | joins |
| `joins.clinical_person_id_linkage_validation` | [Clinical Tables Without a Person-Scoped Link](#joins-clinical-person-id-linkage-validation) | WARNING | joins |
| `joins.clinical_pk_cross_join_validation` | [Clinical Primary Key Join Validation](#joins-clinical-pk-cross-join-validation) | ERROR | joins |
| `joins.cohort_clinical_join_validation` | [Cohort to Clinical Table Join Validation](#joins-cohort-clinical-join-validation) | WARNING | joins |
| `joins.concept_join_validation` | [Concept Join Validation](#joins-concept-join-validation) | ERROR | joins |
| `joins.concept_metadata_join_validation` | [Concept to Vocabulary, Domain and Concept Class Join Validation](#joins-concept-metadata-join-validation) | ERROR | joins |
| `joins.concept_relationship_relationship_join_validation` | [Concept Relationship to Relationship Join Validation](#joins-concept-relationship-relationship-join-validation) | ERROR | joins |
| `joins.concept_relationship_requires_relationship_id` | [Concept Relationship Requires Relationship ID Filter](#joins-concept-relationship-requires-relationship-id) | WARNING | joins |
| `joins.concept_synonym_join_validation` | [Concept Synonym Join Validation](#joins-concept-synonym-join-validation) | ERROR | joins |
| `joins.drug_exposure_drug_strength_join_validation` | [Drug Exposure to Drug Strength Join Validation](#joins-drug-exposure-drug-strength-join-validation) | ERROR | joins |
| `joins.era_forbidden_join_validation` | [Era to Visit Join Without Date Relation](#joins-era-forbidden-join-validation) | WARNING | joins |
| `joins.fact_relationship_join_validation` | [Fact Relationship Polymorphic Join Validation](#joins-fact-relationship-join-validation) | WARNING | joins |
| `joins.left_join_then_where_on_right_table` | [Left Join Then Where On Right Table](#joins-left-join-then-where-on-right-table) | WARNING | joins |
| `joins.maps_to_direction` | [Maps To Direction](#joins-maps-to-direction) | WARNING | joins |
| `joins.observation_period_unlinked` | [Observation Period Not Linked on person_id](#joins-observation-period-unlinked) | WARNING | joins |
| `joins.period_join_requires_date_overlap` | [Period Join Requires Date Overlap](#joins-period-join-requires-date-overlap) | WARNING | joins |
| `joins.person_id_join_validation` | [Person ID Join Validation](#joins-person-id-join-validation) | ERROR | joins |
| `joins.preceding_visit_occurrence_validation` | [Preceding Visit Occurrence Validation](#joins-preceding-visit-occurrence-validation) | ERROR | joins |
| `joins.provider_care_site_join_validation` | [Provider to Care Site Join Validation](#joins-provider-care-site-join-validation) | ERROR | joins |
| `joins.provider_join_validation` | [Provider Join Validation](#joins-provider-join-validation) | ERROR | joins |
| `joins.visit_occurrence_id_join_validation` | [Visit Occurrence ID Join Validation](#joins-visit-occurrence-id-join-validation) | ERROR | joins |
| `joins.visit_occurrence_inner_join_validation` | [Visit Occurrence INNER JOIN Validation](#joins-visit-occurrence-inner-join-validation) | WARNING | joins |
| `temporal.date_diff_interval_comparison` | [DATE difference compared with INTERVAL](#temporal-date-diff-interval-comparison) | ERROR | temporal |
| `temporal.datetime_between_date_literal` | [Datetime Column With a Date-Only Inclusive Upper Bound](#temporal-datetime-between-date-literal) | WARNING | temporal |
| `temporal.end_before_start_validation` | [End Before Start Validation](#temporal-end-before-start-validation) | WARNING | temporal |
| `temporal.implausible_date_filter` | [Implausible Date Filter](#temporal-implausible-date-filter) | WARNING | temporal |
| `temporal.nullable_date_column` | [Nullable Date Column Without NULL Handling](#temporal-nullable-date-column) | WARNING | temporal |
| `temporal.observation_period_anchoring` | [Observation Period Anchoring](#temporal-observation-period-anchoring) | WARNING | temporal |
| `temporal.observation_period_date_range_logic` | [Observation Period Date Range Logic](#temporal-observation-period-date-range-logic) | ERROR | temporal |
| `data_quality.canonical_string_value_validation` | [Canonical Vocabulary String Value Validation](#data-quality-canonical-string-value-validation) | ERROR | data_quality |
| `data_quality.column_type_validation` | [Column Type Validation (SCHEMA Layer)](#data-quality-column-type-validation) | ERROR | data_quality |
| `data_quality.concept_id_string_comparison` | [Concept ID String Comparison](#data-quality-concept-id-string-comparison) | WARNING | data_quality |
| `data_quality.episode_requires_concept_filter` | [Episode Requires Concept Filter](#data-quality-episode-requires-concept-filter) | WARNING | data_quality |
| `data_quality.fact_relationship_requires_relationship_concept_filter` | [Fact Relationship Requires Relationship Concept Filter](#data-quality-fact-relationship-requires-relationship-concept-filter) | WARNING | data_quality |
| `data_quality.free_text_column_misuse` | [Free-Text Column Misuse](#data-quality-free-text-column-misuse) | WARNING | data_quality |
| `data_quality.negative_concept_id_validation` | [Negative Concept ID Validation](#data-quality-negative-concept-id-validation) | ERROR | data_quality |
| `data_quality.non_standard_date_literal_format` | [Non-Standard Date Literal Format](#data-quality-non-standard-date-literal-format) | WARNING | data_quality |
| `data_quality.note_nlp_nlp_date_for_temporal_filtering` | [Note NLP nlp_date for Temporal Filtering](#data-quality-note-nlp-nlp-date-for-temporal-filtering) | WARNING | data_quality |
| `data_quality.note_nlp_offset_is_character_position` | [Note NLP Offset is Character Position](#data-quality-note-nlp-offset-is-character-position) | WARNING | data_quality |
| `data_quality.schema_validation` | [OMOP Schema Validation](#data-quality-schema-validation) | ERROR | data_quality |
| `data_quality.source_value_field_usage` | [Source Value Field Usage](#data-quality-source-value-field-usage) | WARNING | data_quality |
| `data_quality.union_vs_union_all_clinical_events` | [UNION vs UNION ALL for Clinical Events](#data-quality-union-vs-union-all-clinical-events) | WARNING | data_quality |
| `data_quality.vocabulary_table_protection` | [Vocabulary Table Protection](#data-quality-vocabulary-table-protection) | ERROR | data_quality |
| `anti_patterns.ambiguous_column_reference` | [Ambiguous Column Reference](#anti-patterns-ambiguous-column-reference) | ERROR | anti_patterns |
| `anti_patterns.comma_separated_cross_join` | [Cartesian Product With Clinical Rows](#anti-patterns-comma-separated-cross-join) | WARNING | anti_patterns |
| `anti_patterns.concept_code_requires_vocabulary_id` | [Concept Code Requires Vocabulary ID](#anti-patterns-concept-code-requires-vocabulary-id) | WARNING | anti_patterns |
| `anti_patterns.concept_name_lookup` | [Concept Name Lookup Anti-pattern](#anti-patterns-concept-name-lookup) | WARNING | anti_patterns |
| `anti_patterns.concept_relationship_transitive_misuse` | [Concept Relationship Transitive Misuse](#anti-patterns-concept-relationship-transitive-misuse) | WARNING | anti_patterns |
| `anti_patterns.cte_shadows_omop_table` | [CTE Shadows OMOP CDM Table](#anti-patterns-cte-shadows-omop-table) | WARNING | anti_patterns |
| `anti_patterns.destructive_operations_on_clinical_tables` | [Destructive Operations on Clinical Tables](#anti-patterns-destructive-operations-on-clinical-tables) | ERROR | anti_patterns |
| `anti_patterns.having_without_group_by` | [Having Without Group By](#anti-patterns-having-without-group-by) | ERROR | anti_patterns |
| `anti_patterns.join_key_validation` | [Join Key Validation](#anti-patterns-join-key-validation) | ERROR | anti_patterns |
| `anti_patterns.limit_without_order_by` | [LIMIT Without ORDER BY](#anti-patterns-limit-without-order-by) | WARNING | anti_patterns |
| `anti_patterns.no_distinct_on_primary_key_column` | [No DISTINCT on Primary Key Column](#anti-patterns-no-distinct-on-primary-key-column) | WARNING | anti_patterns |
| `anti_patterns.no_string_identification` | [No String Identification](#anti-patterns-no-string-identification) | WARNING | anti_patterns |
| `anti_patterns.null_comparison_operator` | [NULL Comparison Must Use IS NULL / IS NOT NULL](#anti-patterns-null-comparison-operator) | ERROR | anti_patterns |
| `anti_patterns.singleton_metadata_clinical_join` | [Instance Metadata Paired With Clinical Rows](#anti-patterns-singleton-metadata-clinical-join) | WARNING | anti_patterns |
| `anti_patterns.type_concept_id_misuse` | [Type Concept ID Not For Clinical Filtering](#anti-patterns-type-concept-id-misuse) | ERROR | anti_patterns |
| `domain_specific.cost_currency_concept_id` | [Cost Currency Concept ID For Multi-Currency](#domain-specific-cost-currency-concept-id) | WARNING | domain_specific |
| `domain_specific.cost_event_id_polymorphic_resolution` | [Cost Event ID Polymorphic Resolution](#domain-specific-cost-event-id-polymorphic-resolution) | ERROR | domain_specific |
| `domain_specific.drug_era_concept_class_validation` | [Drug Era Concept Class Validation](#domain-specific-drug-era-concept-class-validation) | ERROR | domain_specific |
| `domain_specific.drug_exposure_implausible_value_filter` | [Drug Exposure Implausible Value Filter](#domain-specific-drug-exposure-implausible-value-filter) | WARNING | domain_specific |
| `domain_specific.drug_exposure_quantity_misuse` | [Drug Exposure Quantity Misuse](#domain-specific-drug-exposure-quantity-misuse) | WARNING | domain_specific |
| `domain_specific.drug_strength_numerator_denominator_for_concentration` | [Drug Strength Completeness (Amount vs Concentration)](#domain-specific-drug-strength-numerator-denominator-for-concentration) | WARNING | domain_specific |
| `domain_specific.event_field_polymorphic_resolution` | [Event-Field Polymorphic Resolution](#domain-specific-event-field-polymorphic-resolution) | ERROR | domain_specific |
| `domain_specific.event_rows_counted_as_persons` | [Event Rows Counted as Persons](#domain-specific-event-rows-counted-as-persons) | WARNING | domain_specific |
| `domain_specific.measurement_operator_concept_validation` | [Measurement Operator Concept Validation](#domain-specific-measurement-operator-concept-validation) | ERROR | domain_specific |
| `domain_specific.measurement_range_low_high_validation` | [Measurement Range Low/High Validation](#domain-specific-measurement-range-low-high-validation) | WARNING | domain_specific |
| `domain_specific.note_nlp_snippet_misuse` | [Note NLP Snippet Misuse](#domain-specific-note-nlp-snippet-misuse) | WARNING | domain_specific |
| `domain_specific.numeric_value_without_unit_constraint` | [Numeric Value Without Unit Constraint](#domain-specific-numeric-value-without-unit-constraint) | WARNING | domain_specific |
| `domain_specific.observation_value_as_concept_confusion` | [Observation Value As Concept Confusion](#domain-specific-observation-value-as-concept-confusion) | WARNING | domain_specific |
| `domain_specific.person_birth_field_validation` | [Person Birth Field Validation](#domain-specific-person-birth-field-validation) | ERROR | domain_specific |
| `domain_specific.value_as_columns_conjunction` | [Value-As Columns Combined With AND](#domain-specific-value-as-columns-conjunction) | WARNING | domain_specific |
| `domain_specific.visit_detail_dates_within_parent_visit` | [Visit Detail Dates Within Parent Visit](#domain-specific-visit-detail-dates-within-parent-visit) | WARNING | domain_specific |
| `domain_specific.visit_detail_visit_occurrence_reference` | [Visit Detail Visit Occurrence Linkage](#domain-specific-visit-detail-visit-occurrence-reference) | WARNING | domain_specific |
| `domain_specific.visit_length_of_stay_arithmetic` | [Visit Length-of-Stay Arithmetic](#domain-specific-visit-length-of-stay-arithmetic) | WARNING | domain_specific |
| `domain_specific.vocabulary_relationship_boolean_comparison` | [Relationship Flag Comparison](#domain-specific-vocabulary-relationship-boolean-comparison) | ERROR | domain_specific |


---

## Severity Levels

**ERROR** — the SQL logic is analytically incorrect and will produce wrong results (wrong cohort, wrong counts, wrong associations). Treat as a blocker.

**WARNING** — the SQL may produce incorrect results depending on data quality or study intent. Treat as a mandatory review item before publishing results.

---

## Concept Standardization Rules

These rules validate whether SQL queries correctly use OMOP standard concepts, vocabulary hierarchies, and concept mappings.

---

### 1. Concept Ancestor Max Levels Misuse { #concept-standardization-concept-ancestor-max-levels-misuse }

**Rule ID:** `concept_standardization.concept_ancestor_max_levels_misuse`
**Severity:** WARNING

#### Intent

concept_ancestor has one row per ancestor/descendant pair, with the shortest path in min_levels_of_separation and the longest in max_levels_of_separation. A concept with several parents can be a direct child (min = 1) and also descend through a longer branch (max > 1). Restricting max_levels_of_separation to 1 keeps only children with no longer path: for 'Diabetes mellitus' (201820) in the Athena v5.0 27-AUG-25 release, 21 rows instead of the 24 with min_levels_of_separation = 1. The rule is a WARNING because that narrower set can be intended; it is silent when min_levels_of_separation is restricted on the same row, and does not report other max_levels_of_separation values.

#### How it works

Follows max_levels_of_separation to its concept_ancestor row by lineage (through CTEs, derived tables, casts) and combines the predicates of one AND-chain into an integer range. It reports the chain when that range is exactly 1, in a filter or a CASE label, unless the same row is also restricted on min_levels_of_separation.

#### Examples

**Violation pattern (WARNING):**

```sql
SELECT descendant_concept_id
FROM concept_ancestor
WHERE ancestor_concept_id = 201820
  AND max_levels_of_separation = 1;
```

**The same restriction written as a range (WARNING):**

```sql
SELECT descendant_concept_id
FROM concept_ancestor
WHERE ancestor_concept_id = 201820
  AND max_levels_of_separation > 0
  AND max_levels_of_separation < 2;
```

**Correct pattern:**

```sql
SELECT descendant_concept_id
FROM concept_ancestor
WHERE ancestor_concept_id = 201820
  AND min_levels_of_separation = 1;
```

#### Suggested fix

REPLACE: `max_levels_of_separation = 1` WITH `min_levels_of_separation = 1` for direct children, unless you want only children with no longer path.

#### Sources

- [OMOP CDM v5.4 specification: CONCEPT_ANCESTOR](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept_ancestor)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Concept ancestor](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptAncestor)

---

### 2. Concept Ancestor Rollup Direction { #concept-standardization-concept-ancestor-rollup-direction }

**Rule ID:** `concept_standardization.concept_ancestor_rollup_direction`
**Severity:** WARNING

#### Intent

concept_ancestor is directional: ancestor_concept_id is the broader concept and descendant_concept_id every concept below it (including itself). Joining a record's concept to descendant_concept_id and fixing ancestor_concept_id returns records coded at the concept or any of its descendants, the usual hierarchy query. With the columns swapped the query returns records coded at the concept or at any broader concept above it. The CDM allows both directions, so this is a WARNING; it is reported only when the descendant side is fixed to literal concepts, directly or through the concept row joined to it.

#### How it works

Finds every equality or IN-subquery that matches a record concept column (a concept foreign key of a CDM data table other than *_source_concept_id and *_type_concept_id) against concept_ancestor.ancestor_concept_id, and reports it when the same concept_ancestor row has a required literal restriction on descendant_concept_id, directly or through the concept row joined to it. Links are followed by lineage.

#### Examples

**Violation pattern (WARNING):**

```sql
SELECT co.condition_occurrence_id
FROM condition_occurrence co
JOIN concept_ancestor ca
  ON co.condition_concept_id = ca.ancestor_concept_id
WHERE ca.descendant_concept_id = 201820;
```

**Correct pattern:**

```sql
SELECT co.condition_occurrence_id
FROM condition_occurrence co
JOIN concept_ancestor ca
  ON co.condition_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 201820;
```

#### Suggested fix

REPLACE: the join and filter WITH `<record>.<x>_concept_id = ca.descendant_concept_id AND ca.ancestor_concept_id = <concept>` to get the concept and its descendants.

#### Sources

- [OMOP CDM v5.4 specification: CONCEPT_ANCESTOR](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept_ancestor)
- [The Book of OHDSI, ch. 9: SQL and R, Using the vocabulary when querying](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#using-the-vocabulary-when-querying)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Concept ancestor](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptAncestor)

---

### 3. Concept Ancestor Self-Include Redundancy { #concept-standardization-concept-ancestor-self-include-redundancy }

**Rule ID:** `concept_standardization.concept_ancestor_self_include_redundancy`
**Severity:** WARNING

#### Intent

concept_ancestor records every Standard and Classification concept as its own descendant (both levels of separation 0), so a descendant expansion of such a concept already contains the anchor. Adding the anchor again through UNION ALL keeps both copies; a codeset built that way multiplies rows when it is joined to clinical events. A plain UNION, an OR predicate, or an IN list cannot duplicate the anchor and are not flagged. Nor is a UNION ALL whose branches emit tuples that differ in a constant column, or whose every consumer deduplicates (DISTINCT, GROUP BY on the concept, COUNT(DISTINCT ...)) or reads the rows as a set (IN, EXISTS): no duplicate reaches the result.

#### How it works

Looks at each UNION ALL whose one branch emits an anchor concept explicitly (a concept row, a literal-only SELECT or VALUES) and whose other branch expands the same id through concept_ancestor without excluding the self row. It stays silent when the branches differ in a constant column, or when every consumer deduplicates (DISTINCT, DISTINCT aggregates, MIN/MAX) or reads the rows as a set (IN, EXISTS).

#### Examples

**Violation pattern (WARNING):**

```sql
SELECT descendant_concept_id AS concept_id
FROM concept_ancestor
WHERE ancestor_concept_id = 201820
UNION ALL
SELECT concept_id
FROM concept
WHERE concept_id = 201820;
```

**Correct pattern:**

```sql
SELECT descendant_concept_id AS concept_id
FROM concept_ancestor
WHERE ancestor_concept_id = 201820;
```

#### Suggested fix

REPLACE: `UNION ALL` WITH `UNION`, OR REMOVE the explicit anchor branch, OR ADD `AND descendant_concept_id <> ancestor_concept_id` to the concept_ancestor branch.

#### Sources

- [OMOP CDM: Data Model Conventions, Concept ancestor](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concept-ancestor)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Concept ancestor](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptAncestor)
- [PostgreSQL documentation: Combining Queries (UNION, INTERSECT, EXCEPT)](https://www.postgresql.org/docs/current/queries-union.html)

---

### 4. Concept Domain ID Matches Target Column { #concept-standardization-concept-domain-validation }

**Rule ID:** `concept_standardization.concept_domain_validation`
**Severity:** ERROR

#### Intent

Each CDM v5.4 *_concept_id field with an fkDomain holds concepts of that domain only: condition_occurrence.condition_concept_id is 'Condition', drug_exposure.drug_concept_id is 'Drug', every *_type_concept_id is 'Type Concept', measurement.unit_concept_id is 'Unit'. Requiring the joined concept row to have another domain_id means no concept row can match (an inner join returns zero rows). A domain_id filter that also lists other domains is reported as a warning because the extra values can never match. A missing domain_id filter is not reported: the join is on the concept primary key, so the filter would change nothing.

#### How it works

Ties each match of a CDM concept column to concept.concept_id and each domain_id predicate to one concept table instance by lineage, then compares the admitted domain_id values with the column's fkDomain from the CDM v5.4 field-level specification ('Metadata', the domain of concept 0, is always admitted). Four fields have no fkDomain in the CSV but the specification text of the field names their domain, and they are checked with it: `cdm_source.cdm_version_concept_id` (Metadata: the concepts of vocabulary 'CDM', class 'CDM'), `drug_strength.drug_concept_id` (Drug: "the Branded Drug or Clinical Drug Product"), `location.country_concept_id` (Geography: "Values should conform to the Geography domain") and `measurement.operator_concept_id` (Meas Value Operator: "these concepts belong to the 'Meas Value Operator' domain").

#### Examples

**Violation pattern (ERROR):**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.domain_id = 'Drug';
```

**The domain filter lists the column's domain plus others that can never match (WARNING):**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.domain_id IN ('Condition', 'Observation');
```

**Correct pattern:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.domain_id = 'Condition';
```

#### Suggested fix

REPLACE: the concept alias's domain_id filter WITH the column's domain (e.g. 'Condition' for condition_occurrence.condition_concept_id, 'Type Concept' for any *_type_concept_id), OR remove it: the join on concept_id already fixes the domain.

#### Sources

- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [OMOP CDM v5.4 specification: CONDITION_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#condition_occurrence)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Domains](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptDomains)
- [OMOP CDM v5.4 specification: CDM_SOURCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#cdm_source)
- [OMOP CDM v5.4 specification: DRUG_STRENGTH](https://ohdsi.github.io/CommonDataModel/cdm54.html#drug_strength)
- [OMOP CDM v5.4 specification: LOCATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#location)
- [OMOP CDM v5.4 specification: MEASUREMENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#measurement)

---

### 5. Domain Vocabulary Validation { #concept-standardization-domain-vocabulary-validation }

**Rule ID:** `concept_standardization.domain_vocabulary_validation`
**Severity:** ERROR

#### Intent

Concept columns with an fkDomain hold Standard concepts of that domain, and Standard concepts come from a few vocabularies per domain (Book of OHDSI Table 5.2: SNOMED and ICDO3 for conditions; RxNorm, RxNorm Extension and CVX for drugs). The vocabularies are taken from the Athena release v5.0 27-AUG-25. Requiring the joined concept row to come only from a vocabulary with no Standard concept in that domain (ICD10CM, ICD9CM, NDC, SPL, ATC, MedDRA, Read, ...) returns zero rows, also when the query lists or groups by vocabulary_id. Filter the *_source_concept_id column instead, or map the source codes to standard concepts with concept_relationship 'Maps to'. Unknown vocabulary names (site-local vocabularies) and lists that also admit a vocabulary that occurs are not reported.

#### How it works

Ties each match of a concept column with an fkDomain (the same field-to-domain table as `concept_standardization.concept_domain_validation`, including the four fields whose domain only the specification text gives) to one concept instance by lineage and reads that instance's required vocabulary_id restriction (=, IN, LIKE expanded against the release's vocabularies). It reports when every admitted vocabulary is known to the release and has no Standard concept in the column's domain. Unit columns belong to unit_vocabulary_validation. When the impossible predicate sits in a scalar subquery in the select list, the outer query still returns its rows: the message then says that the subquery's column is NULL on every row (0 for a COUNT), not that the query returns no rows. It stays an ERROR, because that column is certainly dead.

#### Examples

**Violation pattern (ERROR):**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.vocabulary_id = 'ICD10CM';
```

**Correct pattern:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_source_concept_id = c.concept_id
WHERE c.vocabulary_id = 'ICD10CM';
```

#### Suggested fix

REPLACE: `c.vocabulary_id = '<source_vocab>'` WITH a vocabulary of the column's Standard concepts (Condition: 'SNOMED'; Drug: 'RxNorm' / 'RxNorm Extension'), OR filter the *_source_concept_id column, OR map the source codes through concept_relationship 'Maps to' first.

#### Sources

- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Standard concepts](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#standardConcepts)
- [OMOP CDM: Data Model Conventions, Source values, source concept ids and standard concept ids](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#source-values-source-concept-ids-and-standard-concept-ids)
- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [OMOP CDM v5.4 specification: CDM_SOURCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#cdm_source)
- [OMOP CDM v5.4 specification: DRUG_STRENGTH](https://ohdsi.github.io/CommonDataModel/cdm54.html#drug_strength)
- [OMOP CDM v5.4 specification: LOCATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#location)
- [OMOP CDM v5.4 specification: MEASUREMENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#measurement)

---

### 6. Era Tables Use Standard Concepts Only { #concept-standardization-era-table-standard-concepts }

**Rule ID:** `concept_standardization.era_table_standard_concepts`
**Severity:** ERROR

#### Intent

drug_era and dose_era are built per Ingredient and condition_era per condition_concept_id of the underlying condition_occurrence records, so every era concept is Standard (condition_era can also hold concept 0 for unmapped records). Requiring the joined concept row to have standard_concept = 'C', <> 'S', NOT IN ('S') or IS NULL therefore returns no era rows. Only predicates that every surviving row must satisfy are reported: a predicate under OR or NOT, in a CASE expression, in the ON clause of an outer join, or a list that also admits 'S' leaves the Standard rows in place.

#### How it works

Ties each match of an era table's concept column to concept.concept_id with the concept row's standard_concept predicates by lineage, and reports required predicates (AND-joined to WHERE, HAVING or an inner join's ON) that no Standard concept satisfies. When the impossible predicate sits in a scalar subquery in the select list, the outer query still returns its rows: the message then says that the subquery's column is NULL on every row (0 for a COUNT), not that the query returns no rows. It stays an ERROR, because that column is certainly dead.

#### Examples

**Violation pattern (ERROR):**

```sql
SELECT de.person_id
FROM drug_era de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.standard_concept = 'C';
```

**condition_era: IS NULL keeps only eras of unmapped records (concept 0) (WARNING):**

```sql
SELECT ce.person_id
FROM condition_era ce
JOIN concept c ON ce.condition_concept_id = c.concept_id
WHERE c.standard_concept IS NULL;
```

**Correct pattern:**

```sql
SELECT de.person_id
FROM drug_era de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.standard_concept = 'S';
```

#### Suggested fix

REMOVE: the standard_concept predicate, OR REPLACE it WITH `standard_concept = 'S'`. drug_era / dose_era hold Ingredient concepts and condition_era holds the Standard concepts of its condition_occurrence records.

#### Sources

- [OMOP CDM v5.4 specification: DRUG_ERA](https://ohdsi.github.io/CommonDataModel/cdm54.html#drug_era)
- [OMOP CDM v5.4 specification: CONDITION_ERA](https://ohdsi.github.io/CommonDataModel/cdm54.html#condition_era)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Standard concepts](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#standardConcepts)

---

### 7. Invalid Reason Enforcement (strict mode) { #concept-standardization-invalid-reason-enforcement }

**Rule ID:** `concept_standardization.invalid_reason_enforcement`
**Severity:** WARNING (strict mode only)

#### Intent

Vocabulary rows carry a life cycle: invalid_reason is 'D' (deleted), 'U' (upgraded) or NULL while valid, with valid_start_date / valid_end_date. A concept set built from concept by vocabulary, domain, code or name includes retired concepts unless the query filters on validity; drug_strength and source_to_concept_map have their own validity columns, and concept_synonym has none, so it needs a joined concept to filter on.

**Gated behind strict mode.** No source makes the filter mandatory, and many queries are correct without it: Standard and Classification concepts are always valid, Athena ships only valid concept_relationship rows, retired source codes keep their 'Maps to' mappings (filtering them out of a source-code lookup loses historical records), and a concept joined to a clinical record decodes a concept that was valid when recorded. None of those is reported. Enable with ``--strict`` (CLI) or ``strict=True`` (API).

#### How it works

Runs only in strict mode. Per table instance it finds concept rows selected by attribute without a validity predicate on the same row (exempting Standard-filtered rows, source-code rows that feed a 'Maps to' relationship and rows that decode a clinical record in their own SELECT), unfiltered reads of drug_strength / source_to_concept_map, and concept_synonym used as a source without a validity filter on a joined concept. Constant-false probes are skipped.

#### Examples

**Violation pattern (WARNING):**

```sql
SELECT concept_id, concept_name
FROM concept
WHERE vocabulary_id = 'SNOMED';
```

**source_to_concept_map (or drug_strength) read without a validity filter on its own rows (WARNING):**

```sql
SELECT target_concept_id
FROM source_to_concept_map
WHERE source_vocabulary_id = 'HOSP_LAB'
  AND source_code = 'GLU';
```

**concept_synonym used as a concept source without joining concept to filter invalid_reason (WARNING):**

```sql
SELECT concept_id, concept_synonym_name
FROM concept_synonym
WHERE concept_synonym_name ILIKE '%myocardial infarction%';
```

**Correct pattern:**

```sql
SELECT concept_id, concept_name
FROM concept
WHERE vocabulary_id = 'SNOMED'
  AND invalid_reason IS NULL;
```

#### Suggested fix

ADD: `AND <alias>.invalid_reason IS NULL` on that table, OR `AND CURRENT_DATE BETWEEN valid_start_date AND valid_end_date`. Strict-mode-only.

#### Sources

- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Life-cycle](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptLifeCycle)
- [OMOP CDM v5.4 specification: CONCEPT](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept)
- [OMOP CDM: Data Model Conventions, Source to concept map](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#source-to-concept-map)
- [OMOP CDM: Data Model Conventions, Concept ancestor](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concept-ancestor)

---

### 8. Multiple Maps To Targets Not Handled { #concept-standardization-multiple-maps-to-targets }

**Rule ID:** `concept_standardization.multiple_maps_to_targets`
**Severity:** WARNING

#### Intent

Not every source concept maps 1:1 to a standard concept: one ICD-10 code can split into multiple SNOMED concepts, and one NDC can fan out to several RxNorm ingredients. A scalar subquery that returns concept_id_2 raises 'more than one row returned' on most engines as soon as such a code is encountered; LIMIT 1 keeps an arbitrary target and silently discards the others. Joins and IN-subqueries retain every target and are the correct shape; duplicate intermediate rows they create are harmless under COUNT(DISTINCT …) or set-valued consumers and are not flagged. Neither is a scalar subquery that aggregates without GROUP BY (e.g. MIN(concept_id_2)): it returns one row and picks the target deterministically.

#### How it works

Reads which concept_relationship instances are restricted to 'Maps to' / 'Mapped from' (any spelling, through CTEs) and follows each SELECT's output by lineage. A scalar subquery that outputs a mapping target, or a SELECT that outputs one under LIMIT 1 / TOP 1 / FETCH FIRST 1 ROW or DISTINCT ON without ORDER BY, is reported once.

#### Examples

**Violation pattern (WARNING):**

```sql
SELECT c.concept_id,
       (SELECT cr.concept_id_2 FROM concept_relationship cr
        WHERE cr.concept_id_1 = c.concept_id
          AND cr.relationship_id = 'Maps to') AS standard_id
FROM concept c;
```

**LIMIT 1 keeps one arbitrary target (ICD9CM 070.43 maps to two SNOMED concepts) (WARNING):**

```sql
SELECT cr.concept_id_2 AS standard_concept_id
FROM concept c
JOIN concept_relationship cr ON cr.concept_id_1 = c.concept_id
WHERE c.vocabulary_id = 'ICD9CM'
  AND c.concept_code = '070.43'
  AND cr.relationship_id = 'Maps to'
LIMIT 1;
```

**Correct pattern:**

```sql
SELECT c.concept_id, cr.concept_id_2 AS standard_id
FROM concept c
JOIN concept_relationship cr ON cr.concept_id_1 = c.concept_id
WHERE cr.relationship_id = 'Maps to';
```

#### Suggested fix

REPLACE: the scalar subquery or `LIMIT 1` WITH a `JOIN concept_relationship cr ON … AND cr.relationship_id = 'Maps to'` (or `IN (SELECT concept_id_2 …)`) so every target is kept.

#### Sources

- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Mapping relationships](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptMapping)
- [PostgreSQL documentation: Value Expressions, Scalar Subqueries](https://www.postgresql.org/docs/current/sql-expressions.html#SQL-SYNTAX-SCALAR-SUBQUERIES)
- [PostgreSQL documentation: SELECT, LIMIT clause](https://www.postgresql.org/docs/current/sql-select.html#SQL-LIMIT)

---

### 9. Source Concept ID Should Not Filter Standard Concepts { #concept-standardization-source-concept-id-standard-filter }

**Rule ID:** `concept_standardization.source_concept_id_standard_filter`
**Severity:** WARNING

#### Intent

Every OMOP clinical table has two concept columns: *_concept_id (the standard vocabulary mapping, e.g. SNOMED for conditions) and *_source_concept_id (the code from the originating system, e.g. ICD10CM). The source field 'may not necessarily be standard' (CDM v5.4 field-level specification): it is non-standard when the source system codes in a source vocabulary, and Standard when it codes in a standard one (SNOMED, LOINC, RxNorm, CVX). Requiring the joined concept to be Standard therefore keeps only the records whose source code happens to be standard and drops the rest. Either join against *_concept_id (for standard-code analytics) or drop the standard filter (to inspect source codes).

#### How it works

Ties each match of a *_source_concept_id column (all 22 of the CDM v5.4 field-level specification) to concept.concept_id with that concept row's standard_concept predicates by lineage, and reports a required predicate that admits only Standard (or Classification) concepts.

#### Examples

**Violation pattern (WARNING):**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_source_concept_id = c.concept_id
WHERE c.standard_concept = 'S';
```

**Correct pattern:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.standard_concept = 'S';
```

#### Suggested fix

REMOVE: the standard_concept filter on the concept joined to *_source_concept_id, OR join the paired *_concept_id (without `_source`) if Standard concepts are what you want.

#### Sources

- [OMOP CDM: Data Model Conventions, Source values, source concept ids and standard concept ids](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#source-values-source-concept-ids-and-standard-concept-ids)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Non-standard concepts](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#non-standard-concepts)
- [OMOP CDM v5.4 specification: CONDITION_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#condition_occurrence)

---

### 10. Source to Concept Map Validation { #concept-standardization-source-to-concept-map-validation }

**Rule ID:** `concept_standardization.source_to_concept_map_validation`
**Severity:** WARNING

#### Intent

source_to_concept_map is the per-site translation table from local source codes to OMOP standard concepts. Source codes are not unique across vocabularies: a site that loads local lab and pharmacy code lists can hold 'GLU' in both. Filtering on source_code alone can pick up matches from unrelated vocabularies, returning incorrect target_concept_ids. Always pair source_code with source_vocabulary_id so the match is unambiguous.

#### How it works

Per source_to_concept_map instance (found by lineage through CTEs and derived tables) it checks whether source_code is restricted (literal, list, pattern, column join, USING, IN-subquery) while source_vocabulary_id is not restricted by any non-negated predicate.

#### Examples

**Violation pattern (WARNING):**

```sql
SELECT target_concept_id
FROM source_to_concept_map
WHERE source_code = 'GLU';
```

**Correct pattern:**

```sql
SELECT target_concept_id
FROM source_to_concept_map
WHERE source_code = 'GLU'
  AND source_vocabulary_id = 'HOSP_LAB';
```

#### Suggested fix

ADD: `AND source_vocabulary_id = '<vocab>'` alongside any source_code filter. source_code is unique only within a source_vocabulary_id.

#### Sources

- [OMOP CDM: Data Model Conventions, Source to concept map](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#source-to-concept-map)
- [OMOP CDM v5.4 specification: SOURCE_TO_CONCEPT_MAP](https://ohdsi.github.io/CommonDataModel/cdm54.html#source_to_concept_map)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Concept codes](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#concept-codes)

---

### 11. Standard Concept Enforcement { #concept-standardization-standard-concept-enforcement }

**Rule ID:** `concept_standardization.standard_concept_enforcement`
**Severity:** WARNING (strict mode adds a second WARNING check)

#### Intent

concept_ancestor is built over standard and classification concepts only; non-standard source concepts (ICD10CM, NDC, Read, local codes) do not take part in the hierarchy. Matching *_source_concept_id against ancestor_concept_id or descendant_concept_id therefore keeps just the records whose source code is itself standard (for example SNOMED-coded sources) and silently loses every record coded in a source vocabulary. Expand the hierarchy over the standard *_concept_id instead. Filtering on source concepts by themselves (literal ids, a codeset) is legitimate and not flagged. In strict mode the rule also distrusts the ETL and warns when <event>_concept_id is used without a standardness check. The CDM requires the ETL to store Standard concepts there, so such a query is correct on a conformant CDM; the strict-mode finding is a WARNING.

#### How it works

Default mode: reports a *_source_concept_id compared with concept_ancestor ids, directly, through an IN-subquery or CTE, or through a concept row joined to both. Strict mode adds a WARNING when a standard concept field is read and neither its own concept row is filtered to Standard, nor a 'Maps to' mapping or concept_ancestor expansion constrains it.

#### Examples

**Violation pattern (WARNING):**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept_ancestor ca ON ca.descendant_concept_id = co.condition_source_concept_id
WHERE ca.ancestor_concept_id = 201826;
```

**Strict mode: standard concept field used without a standardness check (WARNING (strict mode)):**

```sql
-- strict mode only
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.concept_name ILIKE '%diabetes%';
```

**Correct pattern:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept_ancestor ca ON ca.descendant_concept_id = co.condition_concept_id
WHERE ca.ancestor_concept_id = 201826;
```

#### Suggested fix

REPLACE: the `*_source_concept_id` in the concept_ancestor comparison WITH the paired standard `*_concept_id` (e.g. condition_concept_id), which is what the hierarchy is built over.

#### Sources

- [OMOP CDM: Data Model Conventions, Concept ancestor](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concept-ancestor)
- [OMOP CDM v5.4 specification: CONCEPT_ANCESTOR](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept_ancestor)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Standard concepts](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#standardConcepts)

---

### 12. Standard Concept Value Validation { #concept-standardization-standard-concept-value-validation }

**Rule ID:** `concept_standardization.standard_concept_value_validation`
**Severity:** ERROR

#### Intent

The CDM v5.4 specification allows exactly 'S' (Standard), 'C' (Classification) and NULL in concept.standard_concept. A required filter on any other string ('N', 'Y', an empty string) matches no concept row, so the query returns nothing (ERROR). A lowercase 's' / 'c' does the same on PostgreSQL and DuckDB, which compare strings case-sensitively (ERROR there, WARNING on other case-sensitive engines, accepted on SQL Server and MySQL). A list that also contains a valid value (IN ('S', 'Y')) still returns rows and is a WARNING about the dead value; so is a comparison under OR, in a CASE label or in an outer join's ON clause. A negated impossible value (<> 'N', NOT IN ('')) excludes nothing and equals IS NOT NULL (WARNING). `standard_concept = '' OR standard_concept IS NULL` is accepted. A number or boolean compared with this varchar column (`= 1`, `IN ('S', 1)`, `= TRUE`) is a type defect and is reported by [`data_quality.column_type_validation`](#data-quality-column-type-validation), not here.

#### How it works

Follows standard_concept to the concept table by lineage and classifies every comparison of it with string literals: required and impossible is an ERROR (case-only mismatches only on PostgreSQL and DuckDB), dead values in a list, non-restricting positions and negated impossible values are WARNINGs. When the impossible predicate sits in a scalar subquery in the select list, the outer query still returns its rows: the message then says that the subquery's column is NULL on every row (0 for a COUNT), not that the query returns no rows. It stays an ERROR, because that column is certainly dead.

#### Examples

**Violation pattern (ERROR):**

```sql
SELECT concept_id
FROM concept
WHERE standard_concept = 'Y';
```

**A valid value next to an impossible one: rows still come back, 'Y' is dead (WARNING):**

```sql
SELECT concept_id
FROM concept
WHERE standard_concept IN ('S', 'Y');
```

**Negated impossible value: excludes nothing, same as IS NOT NULL (WARNING):**

```sql
SELECT concept_id
FROM concept
WHERE standard_concept <> 'N';
```

**Empty string: non-standard concepts carry NULL, so nothing matches (ERROR):**

```sql
SELECT concept_id
FROM concept
WHERE standard_concept = '';
```

**Correct pattern:**

```sql
SELECT concept_id
FROM concept
WHERE standard_concept = 'S';
```

#### Suggested fix

REPLACE: `standard_concept = '<other>'` WITH one of: `= 'S'` (standard), `= 'C'` (classification), `IS NULL` (non-standard). Those are the only values the CDM allows.

#### Sources

- [OMOP CDM v5.4 specification: CONCEPT](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Non-standard concepts](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#non-standard-concepts)
- [The Book of OHDSI, ch. 9: SQL and R, Case sensitivity in string comparisons](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#case-sensitivity-in-string-comparisons)

---

### 13. Unit Vocabulary Validation { #concept-standardization-unit-vocabulary-validation }

**Rule ID:** `concept_standardization.unit_vocabulary_validation`
**Severity:** ERROR

#### Intent

Unit columns (measurement / observation / device_exposure / dose_era unit_concept_id, and drug_strength's unit columns) hold Standard Unit-domain concepts. In the Athena release v5.0 27-AUG-25 every valid Standard Unit concept is in UCUM; the Unit concepts of SNOMED, Nebraska Lexicon, CDISC and CIEL are non-standard. Restricting the joined concept row to another known vocabulary therefore finds no unit concepts and returns no rows, also when the query lists or groups by vocabulary_id. A list that also admits 'UCUM' or 'None' (concept 0, an unmapped unit) is not reported.

#### How it works

Ties each match of a unit column (fkDomain 'Unit', plus drug_strength's unit columns) to one concept instance by lineage and reads its required vocabulary_id restriction; it reports when every admitted vocabulary is known to the release and holds no Standard unit concept.

#### Examples

**Violation pattern (ERROR):**

```sql
SELECT m.person_id
FROM measurement m
JOIN concept c ON m.unit_concept_id = c.concept_id
WHERE c.vocabulary_id = 'LOINC';
```

**Correct pattern:**

```sql
SELECT m.person_id
FROM measurement m
JOIN concept c ON m.unit_concept_id = c.concept_id
WHERE c.vocabulary_id = 'UCUM';
```

#### Suggested fix

REPLACE: the vocabulary_id filter WITH `vocabulary_id = 'UCUM'`, OR remove it.

#### Sources

- [OMOP CDM v5.4 specification: MEASUREMENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#measurement)
- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [OHDSI Forums: Measurement entity - which vocabulary to consider](https://forums.ohdsi.org/t/measurement-entity-which-vocabulary-to-consider/5813)

---

### 14. Concept literal without hierarchy expansion { #concept-standardization-concept-literal-without-hierarchy }

**Rule ID:** `concept_standardization.concept_literal_without_hierarchy`
**Severity:** WARNING

#### Intent

Filtering a record's own concept column (condition_concept_id, drug_concept_id, visit_concept_id, ...) on literal concept ids matches only records coded at exactly those concepts. Standard vocabularies are hierarchical, so records coded at descendants (subtypes, the clinical and branded drugs of an ingredient) are missed; the Book of OHDSI expands the concept through concept_ancestor instead. It is a WARNING because a leaf concept has no descendants and an exact-concept query can be intended. Concept 0 (unmapped), local ids above 2,000,000,000, drug_era (already rolled up to ingredients), columns the same SELECT expands through concept_ancestor, unread CTEs, exclusions, CASE labels and aggregates are not reported.

#### How it works

Finds literal restrictions (=, IN, = ANY(ARRAY), BETWEEN n AND n, literal subqueries, VALUES, or concept.concept_id after a join) on a record's own concept column, identified by lineage, and reports them unless the same SELECT also compares that column with concept_ancestor ids, or the literal is 0 / a local id, drug_era, a dead CTE, an exclusion, a CASE label or an aggregate.

#### Examples

**Violation pattern (WARNING):**

```sql
-- acetaminophen ingredient: its clinical and branded drugs are descendants
SELECT COUNT(DISTINCT person_id) FROM drug_exposure
WHERE drug_concept_id = 1125315;
```

**Correct pattern:**

```sql
SELECT COUNT(DISTINCT de.person_id) FROM drug_exposure de
JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 1125315;
```

#### Suggested fix

REPLACE: `e.<x>_concept_id = <id>` WITH a hierarchy expansion: `JOIN concept_ancestor ca ON e.<x>_concept_id = ca.descendant_concept_id WHERE ca.ancestor_concept_id = <id>` (or a codeset built the same way), unless exactly that one concept is intended.

#### Sources

- [The Book of OHDSI, ch. 9: SQL and R, Using the vocabulary when querying](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#using-the-vocabulary-when-querying)
- [The Book of OHDSI, ch. 10: Defining Cohorts, Concept sets](https://ohdsi.github.io/TheBookOfOhdsi/Cohorts.html#conceptSets)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Concept ancestor](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptAncestor)

---

## Join Validation Rules

These rules validate that table joins follow the OMOP CDM schema foreign key relationships.

---

### 1. Care Site and Location Key Join Validation { #joins-care-site-id-join-validation }

**Rule ID:** `joins.care_site_id_join_validation`
**Severity:** ERROR

#### Intent

In CDM v5.4 `care_site_id` on PERSON, PROVIDER, VISIT_OCCURRENCE and VISIT_DETAIL is a foreign key to CARE_SITE, and `location_id` on PERSON and CARE_SITE is a foreign key to LOCATION. Equating a care-site or location key with the key of another entity (`person.location_id = care_site.care_site_id`, `care_site.care_site_id = location.location_id`) matches rows only where unrelated ids coincide, on any CDM.

#### How it works

Every equality and `IN` subquery in every SELECT is examined. Each operand is resolved by lineage (casts, CTEs, derived tables, set operations) to the CDM column it reads, and that column to the entity whose key it holds (primary key: itself; foreign key: the referenced key), using the CDM v5.4 field-level specification. The rule reports an equality whose two operands hold keys of different entities when one of them is a care-site or location key.

Equalities between two columns that reference the same entity are accepted: `person.location_id = care_site.location_id` finds patients living at a care site's address. Comparisons with non-key columns (`care_site_source_value = location_source_value`) are not judged.

Other rules own the remaining pairs: `<table>.care_site_id = location.location_id` is `joins.care_site_join_validation`; keys of person-level events, of person and of visit_occurrence are `joins.clinical_pk_cross_join_validation`, `joins.person_id_join_validation` and `joins.visit_occurrence_id_join_validation`; provider columns are `joins.provider_care_site_join_validation`.

When one operand's table has exactly one foreign key to the other operand's entity, the finding carries a REPLACE patch to that foreign key (`p.location_id = cs.care_site_id` becomes `p.care_site_id = cs.care_site_id`).

A predicate in which one operand is a concept id (`v.care_site_id = c.concept_id`) is left to `anti_patterns.join_key_validation`.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM person p JOIN care_site cs ON p.location_id = cs.care_site_id;
```

```sql
SELECT * FROM care_site cs JOIN location l ON cs.care_site_id = l.location_id;
```

```sql
WITH pp AS (SELECT * FROM person)
SELECT * FROM pp, care_site cs
WHERE pp.location_id = cs.care_site_id;
```

**Correct patterns:**

```sql
SELECT * FROM person p JOIN care_site cs ON p.care_site_id = cs.care_site_id;
```

```sql
SELECT * FROM care_site cs JOIN location l ON cs.location_id = l.location_id;
```

```sql
SELECT p.person_id, cs.care_site_name
FROM person p
JOIN care_site cs ON p.location_id = cs.location_id;
```

#### Suggested fix

Replace the key equality with the foreign key pair: `<table>.care_site_id = care_site.care_site_id` or `<table>.location_id = location.location_id`.

#### Sources

- [OMOP CDM v5.4 specification: CARE_SITE](https://ohdsi.github.io/CommonDataModel/cdm54.html#care_site)
- [OMOP CDM v5.4 specification: PERSON](https://ohdsi.github.io/CommonDataModel/cdm54.html#person)
- [OMOP CDM v5.4 specification: LOCATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#location)

---

### 2. Care Site Join Path Validation { #joins-care-site-join-validation }

**Rule ID:** `joins.care_site_join_validation`
**Severity:** ERROR

#### Intent

In OMOP CDM v5.4 the `care_site_id` of PERSON, PROVIDER, VISIT_OCCURRENCE and VISIT_DETAIL is a foreign key to CARE_SITE; the facility's address is reached through CARE_SITE.`location_id`. Equating such a `care_site_id` with LOCATION.`location_id` compares the keys of two different tables, so the join returns arbitrary locations or none.

#### How it works

Every equality and `IN` subquery in every SELECT is examined, with both operands resolved by lineage (casts, CTEs, derived tables, set operations, unqualified columns). An equality between the `care_site_id` of person, provider, visit_occurrence or visit_detail and `location.location_id` is reported, even when a correct `… → care_site → location` chain appears elsewhere in the statement. `person.location_id = location.location_id` (the person's own address) is a different, valid link.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM visit_occurrence vo
JOIN location l ON vo.care_site_id = l.location_id;
```

```sql
SELECT * FROM visit_occurrence vo
WHERE vo.care_site_id IN (SELECT location_id FROM location WHERE state = 'NY');
```

**Correct patterns:**

```sql
SELECT * FROM visit_occurrence vo
JOIN care_site cs ON vo.care_site_id = cs.care_site_id
JOIN location l ON cs.location_id = l.location_id;
```

```sql
SELECT * FROM person p
JOIN location l ON p.location_id = l.location_id;
```

#### Suggested fix

Replace the direct join with the chain `JOIN care_site cs ON <table>.care_site_id = cs.care_site_id JOIN location l ON cs.location_id = l.location_id`.

#### Sources

- [OMOP CDM v5.4 specification: VISIT_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_occurrence)
- [OMOP CDM v5.4 specification: CARE_SITE](https://ohdsi.github.io/CommonDataModel/cdm54.html#care_site)
- [OMOP CDM v5.4 specification: LOCATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#location)

---

### 3. Clinical Primary Key Join Validation { #joins-clinical-pk-cross-join-validation }

**Rule ID:** `joins.clinical_pk_cross_join_validation`
**Severity:** ERROR

#### Intent

Each clinical event table has its own primary key (`condition_occurrence_id`, `drug_exposure_id`, `note_id`, ...), and every foreign key in the CDM v5.4 specification references exactly one table's key. An equality between two columns that identify rows of different entities matches rows only where unrelated ids happen to coincide, on any CDM.

#### How it works

Every equality and `IN` subquery in every SELECT (`ON`, `WHERE`, `HAVING`) is examined. Each operand is resolved by lineage (casts, CTEs, derived tables, set operations, the first projection of a subquery) to the CDM column it reads, and that column to the entity whose key it holds: itself for a primary key, the referenced key for a foreign key. Keys come from the CDM v5.4 field-level specification, plus `note_nlp.note_id` and `episode.episode_parent_id`, whose references the specification states in its user guidance.

The rule reports an equality when the two entities differ and one of them is a person-level event (the primary key of a table that carries `person_id`). Pairs that involve a `person` or `visit_occurrence` key are reported by `joins.person_id_join_validation` and `joins.visit_occurrence_id_join_validation`.

Not reported: an operand whose source cannot be resolved or is computed (`MIN(condition_occurrence_id) AS first_id`), a key compared with a non-key column, two foreign keys to the same table (`co.visit_detail_id = vd.parent_visit_detail_id`), and the polymorphic keys (`measurement_event_id`, `observation_event_id`, `note_event_id`, `cost_event_id`, `fact_relationship.fact_id_1/2`, `episode_event.event_id`), which hold the key of whichever table their companion column names.

When one operand's table has exactly one foreign key to the other operand's entity, the finding carries a REPLACE patch to that foreign key (`nnlp.note_nlp_id = n.note_id` becomes `nnlp.note_id = n.note_id`).

A predicate in which one operand is a concept id (`co.condition_occurrence_id = c.concept_id`) is left to `anti_patterns.join_key_validation`.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM condition_occurrence co
JOIN drug_exposure de ON co.condition_occurrence_id = de.drug_exposure_id;
```

```sql
SELECT n.person_id, nnlp.lexical_variant
FROM note_nlp nnlp
JOIN note n ON nnlp.note_nlp_id = n.note_id;
```

```sql
SELECT *
FROM (SELECT condition_occurrence_id AS id FROM condition_occurrence) c
JOIN (SELECT drug_exposure_id AS id FROM drug_exposure) d ON c.id = d.id;
```

**Correct patterns:**

```sql
SELECT * FROM condition_occurrence co
JOIN drug_exposure de ON co.person_id = de.person_id;
```

```sql
SELECT n.person_id, nnlp.lexical_variant
FROM note_nlp nnlp
JOIN note n ON nnlp.note_id = n.note_id;
```

```sql
SELECT co.*
FROM condition_occurrence co
JOIN (SELECT person_id, MIN(condition_occurrence_id) AS first_id
      FROM condition_occurrence GROUP BY person_id) f
  ON co.condition_occurrence_id = f.first_id;
```

```sql
SELECT * FROM measurement m
JOIN condition_occurrence co ON m.measurement_event_id = co.condition_occurrence_id;
```

#### Suggested fix

Replace the key equality with the foreign key that links the two tables (for example `nnlp.note_id = n.note_id` or `co.visit_detail_id = vd.visit_detail_id`), or join on `person_id` to relate a person's events.

#### Sources

- [The Book of OHDSI, ch. 4: The Common Data Model, General naming conventions of fields](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#general-naming-conventions-of-fields)
- [OMOP CDM v5.4 specification: CONDITION_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#condition_occurrence)
- [OMOP CDM v5.4 specification: NOTE_NLP](https://ohdsi.github.io/CommonDataModel/cdm54.html#note_nlp)

---

### 4. Clinical Tables Without a Person-Scoped Link { #joins-clinical-person-id-linkage-validation }

**Rule ID:** `joins.clinical_person_id_linkage_validation`
**Severity:** WARNING

#### Intent

In the OMOP CDM every clinical event row belongs to one person: CDM v5.4 gives 18 tables a `person_id` foreign key to PERSON. When one SELECT reads two of them and no predicate ties them to the same person, each row of one is paired with rows of other patients (a condition on 2020-01-15 of patient A with a drug on the same day of patient B). That is almost always a mistake. It is the intended result for some analyses, such as matching controls to cases on age and sex or counting prescriptions per provider, so the finding is a WARNING.

#### How it works

Every SELECT in the statement is checked: the main query, CTE bodies, derived tables and each branch of a set operation. Its person-level sources are the CDM tables that carry `person_id` (generated from the CDM v5.4 field-level specification) and any CTE or derived table that passes on a key of one of them.

Predicates that must hold (AND-connected equalities in `ON` / `WHERE`, and `USING`) put columns into classes of equal values. Classes run through casts, through any other table, CTE or derived table (`co.person_id = c.subject_id AND de.person_id = c.subject_id`) and through the same literal (`co.person_id = 42 AND p.person_id = 42`). Two sources are linked when one class holds, for each of them, a key of the same person-level entity:

- `person_id` on both sides;
- a visit key (`visit_occurrence_id`, `preceding_visit_occurrence_id`) or visit-detail key (`visit_detail_id`, `parent_visit_detail_id`, `preceding_visit_detail_id`): each references one visit, and a visit belongs to one person;
- an event primary key, matched with itself or with a polymorphic event key (`measurement_event_id`, `observation_event_id`, `note_event_id`, `episode_event.event_id`), which the specification defines as the primary key of the linked record.

`COALESCE(a.person_id, b.person_id) = c.person_id` puts every argument in the class, and a CTE or derived table whose own `WHERE` pins a passed-on key to a literal carries that literal. An `OR` links a source when every alternative ties it to a member of one already linked group (`ON m.person_id = a.person_id OR m.person_id = b.person_id`). Two sources attached to the same `FACT_RELATIONSHIP` or `EPISODE_EVENT` row are also linked (for example a mother and a child, or a condition and a measurement). Any other equality under `OR`, and every equality under `NOT`, links nothing; an anti-join links only through its key equality. Sources left in separate groups are reported once per SELECT. A `LATERAL` / `APPLY` source is not checked; its link is the correlation inside it. T-SQL temporary tables (`#name`) are not CDM tables.

When the first join that brings in an unlinked source has an `ON` clause and both sides expose `person_id`, the finding carries an ADD patch for `AND <a>.person_id = <b>.person_id`.

When every equality between the two sources is a wrong key pair or a type mismatch that an ERROR of another rule already names (`co.person_id = p.person_source_value`, `co.visit_occurrence_id = vd.visit_detail_id`), the validator drops this WARNING as a consequence of that ERROR. It stays when the sources are not equated at all, are equated only on non-key columns such as two dates, or are equated on a wrong pair that no ERROR rule reports (`co.person_id = de.quantity`).

#### Examples

**Violation patterns:**

```sql
SELECT co.condition_concept_id, de.drug_concept_id
FROM condition_occurrence co
JOIN drug_exposure de ON co.condition_start_date = de.drug_exposure_start_date;
```

```sql
SELECT d.person_id, co.condition_start_date
FROM death d
JOIN condition_occurrence co ON d.cause_concept_id = co.condition_concept_id;
```

```sql
WITH x AS (
  SELECT co.condition_concept_id, de.drug_concept_id
  FROM condition_occurrence co
  JOIN drug_exposure de ON co.condition_start_date = de.drug_exposure_start_date
)
SELECT * FROM x;
```

**Correct patterns:**

```sql
SELECT co.condition_concept_id, de.drug_concept_id
FROM condition_occurrence co
JOIN drug_exposure de ON co.person_id = de.person_id
  AND co.condition_start_date = de.drug_exposure_start_date;
```

```sql
SELECT d.person_id
FROM death d
JOIN person p ON d.person_id = p.person_id
JOIN condition_occurrence co ON p.person_id = co.person_id AND co.condition_concept_id = d.cause_concept_id;
```

```sql
SELECT vd.visit_detail_id, nxt.visit_start_date
FROM visit_detail vd
JOIN visit_occurrence nxt ON nxt.preceding_visit_occurrence_id = vd.visit_occurrence_id;
```

```sql
SELECT co.condition_concept_id, m.measurement_concept_id
FROM condition_occurrence co
JOIN fact_relationship fr ON fr.domain_concept_id_1 = 19 AND fr.fact_id_1 = co.condition_occurrence_id
JOIN measurement m ON fr.domain_concept_id_2 = 21 AND fr.fact_id_2 = m.measurement_id;
```

<!-- extra-examples:start -->
**Warning variant: A comma join with only a date condition.**

```sql
SELECT d.person_id, vo.visit_occurrence_id
FROM death d, visit_occurrence vo
WHERE d.death_date BETWEEN vo.visit_start_date AND vo.visit_end_date;
```

<!-- extra-examples:end -->

#### Suggested fix

Add `<a>.person_id = <b>.person_id` (or a shared visit key such as `visit_occurrence_id`) to the join between the two sources, unless pairing rows of different patients is intended.

#### Sources

- [The Book of OHDSI, ch. 4: The Common Data Model, General conventions of the model](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#model-Conv)
- [OMOP CDM v5.4 specification: PERSON](https://ohdsi.github.io/CommonDataModel/cdm54.html#person)
- [OMOP CDM v5.4 specification: VISIT_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_occurrence)
- [OMOP CDM v5.4 specification: FACT_RELATIONSHIP](https://ohdsi.github.io/CommonDataModel/cdm54.html#fact_relationship)

---

### 5. Cohort to Clinical Table Join Validation { #joins-cohort-clinical-join-validation }

**Rule ID:** `joins.cohort_clinical_join_validation`
**Severity:** WARNING

#### Intent

COHORT rows name their members in `subject_id`. The CDM v5.4 specification lets a cohort's subjects be persons, providers or visits, and COHORT_DEFINITION.`subject_concept_id` records which. Person cohorts are the common case; the Book of OHDSI joins them to clinical tables with `subject_id = person_id`. A clinical table read together with a cohort but not tied to `subject_id` pairs every cohort subject with rows of other patients, which is almost always a mistake but cannot be told from intent, so the rule warns.

#### How it works

Every SELECT that reads a cohort source together with person-level sources is checked. A cohort source is the COHORT table or a CTE / derived table that passes `cohort.subject_id` on under any name; a person-level source is a CDM table with `person_id` (generated from the CDM v5.4 field-level specification) or a CTE / derived table passing one of their keys on. T-SQL temporary tables (`#cohort`) are not the CDM table.

Predicates that must hold (AND-connected equalities in `ON` / `WHERE`, and `USING`) put columns into classes of equal values, through casts, unqualified columns, other tables and the same literal. The rule reports:

- a person-level source not tied to `cohort.subject_id`, directly (`subject_id` in one class with its `person_id`) or through other person-level sources linked to the same person (`cohort -> drug_exposure -> condition_occurrence`, or through `observation_period`); the message lists any cohort column it is equated with instead (`cohort.cohort_definition_id = person.person_id`);
- `cohort.subject_id` equated with another entity's key (a visit, provider or event id) while the SELECT does not restrict `cohort_definition.subject_concept_id`: right only for a cohort whose subjects are of that kind.

Semi-joins (`person_id IN (SELECT subject_id FROM cohort)`) and correlated `EXISTS` are separate scopes and need no join.

#### Examples

**Violation patterns:**

```sql
SELECT c.subject_id, co.condition_concept_id
FROM cohort c
JOIN condition_occurrence co ON co.condition_start_date >= c.cohort_start_date;
```

```sql
SELECT c.subject_id, co.condition_concept_id
FROM cohort c, condition_occurrence co
WHERE c.cohort_definition_id = 1;
```

```sql
SELECT * FROM cohort c
JOIN condition_occurrence co ON c.subject_id = co.visit_occurrence_id;
```

**Correct patterns:**

```sql
SELECT c.subject_id, co.condition_concept_id
FROM cohort c
JOIN condition_occurrence co ON c.subject_id = co.person_id
  AND co.condition_start_date >= c.cohort_start_date;
```

```sql
SELECT subject_id, cohort_start_date
FROM cohort
INNER JOIN condition_occurrence ON subject_id = person_id AND condition_start_date <= cohort_start_date;
```

```sql
SELECT *
FROM cohort c
JOIN drug_exposure de ON de.person_id = c.subject_id
JOIN condition_occurrence co ON co.person_id = de.person_id;
```

```sql
SELECT *
FROM cohort c
JOIN cohort_definition cd ON cd.cohort_definition_id = c.cohort_definition_id AND cd.subject_concept_id = 8
JOIN visit_occurrence vo ON vo.visit_occurrence_id = c.subject_id;
```

#### Suggested fix

Add `cohort.subject_id = <clinical>.person_id` to the join, or link the table to another one already tied to `subject_id`. If the cohort's subjects are visits or providers, restrict `cohort_definition.subject_concept_id` accordingly.

#### Sources

- [OMOP CDM v5.4 specification: COHORT](https://ohdsi.github.io/CommonDataModel/cdm54.html#cohort)
- [OMOP CDM v5.4 specification: COHORT_DEFINITION](https://ohdsi.github.io/CommonDataModel/cdm54.html#cohort_definition)
- [The Book of OHDSI, ch. 10: Defining Cohorts, Require prior hypertension](https://ohdsi.github.io/TheBookOfOhdsi/Cohorts.html#require-prior-hypertension)

---

### 6. Concept Join Validation { #joins-concept-join-validation }

**Rule ID:** `joins.concept_join_validation`
**Severity:** ERROR

#### Intent

Every `*_concept_id` column of the CDM v5.4 is a foreign key to `concept.concept_id`. Comparing one with any other CONCEPT column (`concept_name`, `concept_code`, `vocabulary_id`, ...) compares a concept id with an unrelated attribute: PostgreSQL rejects the type mismatch, DuckDB fails on the first value that does not convert, and with a cast to text the join runs and matches nothing.

#### How it works

Every equality and `IN` subquery in every SELECT (the main query, CTE bodies, derived tables, each branch of a set operation) is examined. Operands are resolved by lineage through casts, CTEs, derived tables and set operations, so an alias that names a different source in each scope, or a concept-id column renamed in a CTE, is judged by the base column it reads. A column whose CDM foreign key is `concept.concept_id` (from the field-level specification) compared with another CONCEPT column is reported, with a REPLACE patch to `concept.concept_id`.

Columns of VOCABULARY, DOMAIN, CONCEPT_CLASS and RELATIONSHIP are checked by `joins.concept_metadata_join_validation` and `joins.concept_relationship_relationship_join_validation`; columns of CONCEPT_SYNONYM by `joins.concept_synonym_join_validation`. Joins on `concept.concept_code` without a `vocabulary_id` constraint are checked by `anti_patterns.concept_code_requires_vocabulary_id`.

#### Examples

**Violation patterns:**

```sql
SELECT co.person_id FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_name;
```

```sql
SELECT co.person_id FROM condition_occurrence co
JOIN concept c ON CAST(co.condition_concept_id AS VARCHAR) = c.concept_name;
```

```sql
SELECT co.person_id FROM condition_occurrence co
WHERE co.condition_concept_id IN (SELECT concept_code FROM concept WHERE vocabulary_id = 'SNOMED');
```

**Correct patterns:**

```sql
SELECT co.person_id FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id;
```

```sql
WITH k AS (SELECT concept_id, concept_name FROM concept WHERE domain_id = 'Condition')
SELECT co.person_id FROM condition_occurrence co
JOIN k ON k.concept_id = co.condition_concept_id;
```

#### Suggested fix

Replace any `*_concept_id` join target other than `concept.concept_id` with `concept.concept_id`.

#### Sources

- [OMOP CDM: Data Model Conventions, Fields](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#fields)
- [OMOP CDM v5.4 specification: CONCEPT](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept)
- [The Book of OHDSI, ch. 9: SQL and R, Using the vocabulary when querying](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#using-the-vocabulary-when-querying)

---

### 7. Concept Relationship Requires Relationship ID Filter { #joins-concept-relationship-requires-relationship-id }

**Rule ID:** `joins.concept_relationship_requires_relationship_id`
**Severity:** WARNING

#### Intent

CONCEPT_RELATIONSHIP stores every link between two concepts in the same two columns: 'Maps to', 'Is a', 'Subsumes', attribute links such as 'Has finding site', drug links such as 'RxNorm has ing', and their reverses. Only `relationship_id` says which. A lookup that reads `concept_id_2` for a concept without restricting `relationship_id` returns all of them.

Whether that changes the result depends on the concept looked up, which the SQL does not show. A non-standard source code often has a single standard target, while a standard concept also links to its parents, children and attribute concepts: in Athena v5.0 27-AUG-25, Type 2 diabetes mellitus (201826) has 133 standard condition concepts among its links and one 'Maps to' target. The finding is therefore a WARNING.

#### How it works

A lookup is an instance of `concept_relationship` whose `concept_id_1` / `concept_id_2` values are compared anywhere in the statement, followed through CTEs, derived tables and `IN` subqueries. An instance is reported unless:

- `relationship_id` is restricted for that instance, in its own SELECT or downstream on its projected column, by any non-negated predicate other than `<>`, `IS [NOT] NULL` or a `LIKE` pattern that matches every value (`'%'`). Wrapped forms (`LOWER(cr.relationship_id) = 'maps to'`) and `JOIN relationship USING (relationship_id)` count;
- the instance's `relationship_id` reaches the statement's output: projected by name, by `*` or `cr.*`, or grouped on in the output SELECT, so every row shows its relationship type;
- the lookup is the body of `NOT EXISTS`, which asks whether a concept has any relationship at all.

`EXISTS`, `IN` and join forms of the same lookup get the same verdict. When the unrestricted ids reach a predicate on a clinical `*_concept_id` column, the message says the selected rows can include concepts that are not the intended mapping. When the instance is joined with an `ON` clause, the finding carries an ADD patch for `AND cr.relationship_id = '<relationship>'`. The rule is a WARNING in strict mode too.

#### Examples

**Violation patterns:**

```sql
WITH related AS (
  SELECT cr.concept_id_2 AS concept_id
  FROM concept_relationship cr
  WHERE cr.concept_id_1 = 201826
)
SELECT COUNT(DISTINCT co.person_id)
FROM condition_occurrence co
JOIN related r ON co.condition_concept_id = r.concept_id;
```

```sql
SELECT c.concept_id, c.concept_name
FROM concept_relationship cr
JOIN concept c ON cr.concept_id_2 = c.concept_id
WHERE cr.concept_id_1 = 201826;
```

**Correct patterns:**

```sql
WITH related AS (
  SELECT cr.concept_id_2 AS concept_id
  FROM concept_relationship cr
  WHERE cr.concept_id_1 = 201826
    AND cr.relationship_id = 'Maps to'
)
SELECT COUNT(DISTINCT co.person_id)
FROM condition_occurrence co
JOIN related r ON co.condition_concept_id = r.concept_id;
```

```sql
SELECT cr.relationship_id, c.concept_name
FROM concept_relationship cr
JOIN concept c ON c.concept_id = cr.concept_id_2
WHERE cr.concept_id_1 = 201826;
```

```sql
SELECT c.concept_id
FROM concept c
WHERE NOT EXISTS (SELECT 1 FROM concept_relationship cr WHERE cr.concept_id_1 = c.concept_id);
```

#### Suggested fix

Add `AND cr.relationship_id = '<relationship>'` to the lookup, typically `'Maps to'` for source-to-standard mapping; use `concept_ancestor` for hierarchy. If every relationship type is wanted, project `relationship_id`.

#### Sources

- [OMOP CDM v5.4 specification: CONCEPT_RELATIONSHIP](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept_relationship)
- [OMOP CDM: Data Model Conventions, Concept Relationships](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concept-relationships)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Relationships](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#relationships)

---

### 8. Concept Relationship to Relationship Join Validation { #joins-concept-relationship-relationship-join-validation }

**Rule ID:** `joins.concept_relationship_relationship_join_validation`
**Severity:** ERROR (relationship_id compared with a column that never holds an id); WARNING (compared with relationship_name, other column pairs)

#### Intent

CONCEPT_RELATIONSHIP references RELATIONSHIP through `concept_relationship.relationship_id` (CDM v5.4 foreign key). `relationship.reverse_relationship_id` also holds relationship ids: every concept relationship exists with its mirror image, whose `relationship_id` is the `reverse_relationship_id` of the original, so joining on it reaches the reverse relationship's row on purpose. Comparing `relationship_id` with `relationship_name` runs and returns correct rows wherever the name equals the id, which in Athena v5.0 27-AUG-25 holds for 72 of 722 relationships, and drops the others: a WARNING. Comparing it with a column that never holds a relationship id (`is_hierarchical`, `defines_ancestry`) never pairs the right rows: an ERROR.

#### How it works

Every equality and `IN` subquery in every SELECT is examined, with operands resolved by lineage through casts, `LOWER` / `UPPER` / `TRIM`, CTEs, derived tables and set operations. Between concept_relationship and relationship:

- `relationship_id = relationship_id` and `relationship_id = reverse_relationship_id` are links;
- `relationship_id` compared with `relationship_name` is a **WARNING** whose message gives how many relationships are named by their id, with a REPLACE patch to `relationship.relationship_id`;
- `relationship_id` compared with another non-key column of RELATIONSHIP (`is_hierarchical`, `defines_ancestry`) is an **ERROR**, with the same patch;
- any other pair (`cr.concept_id_1 = r.relationship_concept_id`) is a **WARNING**, unless the same two sources are already linked in that SELECT.

The classification is the one used by `joins.concept_metadata_join_validation`.

#### Examples

**Violation patterns:**

*a column that never holds a relationship id (ERROR)*

```sql
SELECT * FROM concept_relationship cr
JOIN relationship r ON cr.relationship_id = r.is_hierarchical;
```

*the name column: right only for relationships named by their id (WARNING)*

```sql
SELECT * FROM concept_relationship cr
JOIN relationship r ON cr.relationship_id = r.relationship_name;
```

```sql
SELECT * FROM concept_relationship cr
WHERE cr.relationship_id IN (SELECT relationship_name FROM relationship);
```

*another column pair (WARNING)*

```sql
SELECT * FROM concept_relationship cr
JOIN relationship r ON cr.concept_id_1 = r.relationship_concept_id;
```

**Correct patterns:**

```sql
SELECT * FROM concept_relationship cr
JOIN relationship r ON cr.relationship_id = r.relationship_id;
```

```sql
SELECT cr.concept_id_1, cr.concept_id_2, rev.relationship_id AS reverse_id
FROM concept_relationship cr
JOIN relationship rev ON rev.reverse_relationship_id = cr.relationship_id;
```

#### Suggested fix

Replace the join target with `concept_relationship.relationship_id = relationship.relationship_id` (or `relationship.reverse_relationship_id` for the reverse type). Do not join on `relationship_name` or `relationship_concept_id`.

#### Sources

- [OMOP CDM v5.4 specification: CONCEPT_RELATIONSHIP](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept_relationship)
- [OMOP CDM v5.4 specification: RELATIONSHIP](https://ohdsi.github.io/CommonDataModel/cdm54.html#relationship)
- [OMOP CDM: Data Model Conventions, Concept Relationships](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concept-relationships)

---

### 9. Concept Synonym Join Validation { #joins-concept-synonym-join-validation }

**Rule ID:** `joins.concept_synonym_join_validation`
**Severity:** ERROR (WARNING for a text match between synonym and concept names)

#### Intent

`concept_synonym` rows belong to a concept through the foreign key `concept_synonym.concept_id = concept.concept_id` (CDM v5.4). `concept_synonym.language_concept_id = concept.concept_id` is also a valid foreign key: it looks up the synonym's language concept.

#### How it works

Every equality and `IN` subquery in every SELECT is examined, with operands resolved by lineage through casts, `LOWER` / `UPPER` / `TRIM`, CTEs, derived tables and set operations.

- **ERROR:** a `concept_synonym` ↔ `concept` equality on a key column that is not one of those foreign keys (e.g. `cs.language_concept_id = c.vocabulary_id`, `cs.concept_id = c.concept_code`), when that concept source is not also joined on `concept_id` in the same SELECT.
- **WARNING:** a text match between the two tables (`concept_synonym_name` against `concept_name` / `concept_code`, bare or wrapped). It is a legitimate lexical lookup when deliberate (finding the concepts whose synonym equals another concept's name), but it is not the key: in Athena v5.0 27-AUG-25, 183,901 synonym names belong to more than one concept, so used in place of `concept_id` it links unrelated concepts.

A text predicate on a pair already joined on `concept_id` (e.g. `AND cs.concept_synonym_name = c.concept_name`) is a filter and is not reported. `joins.concept_join_validation` leaves concept_synonym columns to this rule.

#### Examples

**Violation patterns:**

```sql
SELECT cs.concept_synonym_name
FROM concept c
JOIN concept_synonym cs ON c.vocabulary_id = cs.language_concept_id;
```

```sql
SELECT cs.concept_synonym_name
FROM concept_synonym cs
WHERE cs.concept_id IN (SELECT concept_code FROM concept WHERE vocabulary_id = 'SNOMED');
```

```sql
SELECT c.concept_id, c.concept_name
FROM concept_synonym cs
JOIN concept c ON cs.concept_synonym_name = c.concept_name;
```

**Correct patterns:**

```sql
SELECT cs.concept_synonym_name
FROM concept c
JOIN concept_synonym cs ON c.concept_id = cs.concept_id;
```

```sql
SELECT cs.concept_synonym_name, lang.concept_name AS language
FROM concept_synonym cs
JOIN concept lang ON lang.concept_id = cs.language_concept_id;
```

#### Suggested fix

Replace the join target with `concept_synonym.concept_id = concept.concept_id`. A name match (`concept_synonym_name = concept_name`) is only a lexical lookup; names are not unique.

#### Sources

- [OMOP CDM v5.4 specification: CONCEPT_SYNONYM](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept_synonym)
- [OMOP CDM: Data Model Conventions, Concept Synonyms](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concept-synonyms)

---

### 10. Concept to Vocabulary, Domain and Concept Class Join Validation { #joins-concept-metadata-join-validation }

**Rule ID:** `joins.concept_metadata_join_validation`
**Severity:** ERROR (key compared with a column that never holds an id); WARNING (key compared with the name column, other column pairs)

#### Intent

CONCEPT references the three metadata tables through foreign keys (CDM v5.4): `concept.vocabulary_id` → VOCABULARY, `concept.domain_id` → DOMAIN, `concept.concept_class_id` → CONCEPT_CLASS. Each metadata table also points back at the concept that represents its row (`vocabulary_concept_id`, `domain_concept_id`, `concept_class_concept_id` → CONCEPT), and other tables reference them too (`source_to_concept_map.target_vocabulary_id`, `cost.cost_domain_id`). A foreign key compared with the referenced table's name column runs and returns correct rows wherever the name equals the id, and drops the others: in Athena v5.0 27-AUG-25 the name equals the id for 2 of 113 vocabularies, 30 of 50 domains (every main clinical domain: Condition, Drug, Procedure, Measurement, Observation, Device, Visit, Specimen, Note) and 208 of 433 concept classes. Whether that loses rows the query needs depends on the data asked for, so it is a WARNING. A foreign key compared with a column that never holds an id (`vocabulary_version`, `vocabulary_reference`) never pairs the right rows and is an ERROR.

This rule merges the earlier vocabulary, concept_class and domain join rules and carries the vocabulary rule's renamed id (old ids in the [CHANGELOG](https://github.com/fastomop/fastSSV/blob/main/CHANGELOG.md) renames table).

#### How it works

Every equality and `IN` subquery in every SELECT is examined. Operands are resolved by lineage through casts, `LOWER` / `UPPER` / `TRIM`, CTEs, derived tables and set operations. For a pair of columns from a metadata table and a table related to it by a foreign key:

- a foreign key and the key it references (or two columns holding the same table's key) is a link and is accepted;
- a foreign key compared with the name column of the table it references (`vocabulary_name`, `domain_name`, `concept_class_name`) is a **WARNING** whose message gives how many rows of that table are named by their id, with a REPLACE patch to the key;
- a foreign key compared with another non-key column of that table (`vocabulary_version`, `vocabulary_reference`), which never holds an id, is an **ERROR**, with a REPLACE patch to the key;
- any other pair (`concept.vocabulary_id = concept_class.concept_class_id`) is a **WARNING**, unless the same two sources are already linked by a foreign key in that SELECT. Such a pair can match by design: vocabularies without concept classes use the vocabulary_id as the class.

#### Examples

**Violation patterns:**

*a column that never holds an id (ERROR)*

```sql
SELECT c.concept_id FROM concept c
JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_version;
```

*the name column: right only where the name equals the id (WARNING)*

```sql
SELECT c.concept_id FROM concept c
JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_name;
```

```sql
SELECT c.concept_id FROM concept c
JOIN concept_class cc ON LOWER(c.concept_class_id) = LOWER(cc.concept_class_name);
```

```sql
SELECT c.concept_id FROM concept c
WHERE c.domain_id IN (SELECT domain_name FROM domain);
```

*another column pair (WARNING)*

```sql
SELECT c.concept_id FROM concept c
JOIN vocabulary v ON c.domain_id = v.vocabulary_id;
```

**Correct patterns:**

```sql
SELECT c.concept_id FROM concept c
JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_id;
```

```sql
SELECT v.vocabulary_id, mc.concept_name
FROM vocabulary v
JOIN concept mc ON mc.concept_id = v.vocabulary_concept_id;
```

```sql
SELECT c.concept_id FROM concept c
JOIN domain d ON c.domain_id = d.domain_id
WHERE d.domain_name LIKE '%Drug%';
```

<!-- extra-examples:start -->
**Warning variant: The concept_class key compared with the class name: right only where the name equals the id.**

```sql
SELECT c.concept_id FROM concept c
JOIN concept_class cc ON c.concept_class_id = cc.concept_class_name;
```

**Warning variant: The domain key compared with the domain name: right for every main clinical domain, not for all.**

```sql
SELECT c.concept_id FROM concept c
JOIN domain d ON c.domain_id = d.domain_name;
```

<!-- extra-examples:end -->

#### Suggested fix

Replace the ON clause with the foreign-key pair: `concept.vocabulary_id = vocabulary.vocabulary_id`, `concept.domain_id = domain.domain_id` or `concept.concept_class_id = concept_class.concept_class_id`.

#### Sources

- [OMOP CDM v5.4 specification: CONCEPT](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept)
- [OMOP CDM v5.4 specification: VOCABULARY](https://ohdsi.github.io/CommonDataModel/cdm54.html#vocabulary)
- [OMOP CDM v5.4 specification: DOMAIN](https://ohdsi.github.io/CommonDataModel/cdm54.html#domain)
- [OMOP CDM v5.4 specification: CONCEPT_CLASS](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept_class)
- [OMOP CDM: Data Model Conventions, Vocabularies](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#vocabularies)

---

### 11. Drug Exposure to Drug Strength Join Validation { #joins-drug-exposure-drug-strength-join-validation }

**Rule ID:** `joins.drug_exposure_drug_strength_join_validation`
**Severity:** ERROR (WARNING for data-dependent keys)

#### Intent

DRUG_STRENGTH holds one row per active ingredient of each active Standard Drug Concept, keyed by `drug_concept_id`.
DRUG_EXPOSURE records the standard drug in `drug_concept_id`. The two tables relate on `drug_concept_id`, directly or
through a table or CTE that carries it (CONCEPT, CONCEPT_ANCESTOR, a CTE projecting `drug_strength.drug_concept_id`).

#### How it works

The rule looks at one SELECT at a time and follows operands through casts, CTEs, derived tables and
`IN (SELECT …)`. Every FROM/JOIN source of the SELECT is a node of the relation, whatever it is: a CTE or derived
table built with JOIN, GROUP BY or DISTINCT relays `drug_concept_id` as well as a base table does. Unqualified columns,
renamed ones included (`JOIN s ON ds_drug = de.drug_concept_id`), are resolved to the source that supplies them, and
the sources of a correlated subquery count too (`EXISTS (SELECT 1 FROM concept c WHERE c.concept_id =
de.drug_concept_id AND c.concept_id = ds.drug_concept_id)`).

- **ERROR**: both tables are read in one FROM list and nothing relates their rows (every exposure pairs with every
  strength row), or the only relation equates columns that hold different kinds of value (`drug_exposure_id =
  drug_concept_id`, `route_concept_id = drug_concept_id`).
- **WARNING**: the only relation is `drug_source_concept_id` = a drug_strength concept, or `drug_concept_id =
  ingredient_concept_id`. A source concept is a standard drug only when the source was coded in RxNorm, and an
  exposure recorded at ingredient level matches every product that contains the ingredient, so the outcome depends on
  the data.

Once a SELECT relates the tables on `drug_concept_id`, further predicates between them are filters and are not
reported.

#### Examples

**Violation patterns:**

```sql
SELECT de.drug_exposure_id, ds.amount_value
FROM drug_exposure de
JOIN drug_strength ds ON de.drug_exposure_id = ds.drug_concept_id;
```

```sql
SELECT de.drug_exposure_id, ds.amount_value
FROM drug_exposure de, drug_strength ds
WHERE ds.ingredient_concept_id = 1125315;
```

**Correct patterns:**

```sql
SELECT de.drug_exposure_id, de.quantity * ds.amount_value AS ingredient_dose
FROM drug_exposure de
JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id;
```

```sql
WITH s AS (
  SELECT drug_concept_id, amount_value FROM drug_strength WHERE ingredient_concept_id = 1125315
)
SELECT de.person_id, SUM(de.quantity * s.amount_value)
FROM drug_exposure de
JOIN s ON s.drug_concept_id = de.drug_concept_id
GROUP BY de.person_id;
```

<!-- extra-examples:start -->
**Warning variant: drug_source_concept_id matches drug_strength only where the source was coded in a standard drug vocabulary.**

```sql
SELECT de.drug_exposure_id, de.quantity * ds.amount_value AS ingredient_dose
FROM drug_exposure de
JOIN drug_strength ds ON de.drug_source_concept_id = ds.drug_concept_id;
```

<!-- extra-examples:end -->

#### Suggested fix

Relate the tables with `drug_exposure.drug_concept_id = drug_strength.drug_concept_id`. A REPLACE patch is attached
when the wrong predicate is written as a plain equality.

#### Sources

- [OMOP CDM v5.4 specification: DRUG_STRENGTH](https://ohdsi.github.io/CommonDataModel/cdm54.html#drug_strength)
- [OMOP CDM: Data Model Conventions, Drug strength](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#drug-strength)
- [OMOP CDM: Drug Dose Calculation](https://ohdsi.github.io/CommonDataModel/drug_dose.html)
- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)

---

### 12. Era to Visit Join Without Date Relation { #joins-era-forbidden-join-validation }

**Rule ID:** `joins.era_forbidden_join_validation`
**Severity:** WARNING

#### Intent

`condition_era`, `drug_era` and `dose_era` carry no visit key: their only foreign keys are `person_id` and concept ids.
A person usually has several eras and several visits, so joining an era table to `visit_occurrence` or
`visit_detail` on `person_id` alone pairs every era of a person with every visit of that person. Rows repeat and
`COUNT(*)`, `SUM` and similar aggregates over the join are inflated.

#### How it works

The rule looks at one SELECT at a time and reports an era table and a visit table whose `person_id` values that
SELECT equates (directly, through `person` or another column, or with `USING (person_id)`) when nothing else relates
the two. Tables wrapped in a CTE or derived table that passes their rows through are judged as the tables. It stays
silent when:

- another comparison relates the era and the visit (a date condition, `OVERLAPS`, or another key), or an expression
  combines an era date with a visit date (`LEAST(...) - GREATEST(...)`, a date difference);
- one side is pinned to a single row by its primary key;
- row duplication cannot change the result: `SELECT DISTINCT`, a `GROUP BY` without aggregates, only
  `COUNT(DISTINCT …)` / `MIN` / `MAX`, or the query of an `IN` / `EXISTS`;
- the SELECT feeds a later step and passes on columns of both tables, and every query of the statement that reads it
  relates an era value to a visit value or de-duplicates. An INSERT or CREATE TABLE AS leaves the statement and is
  not followed.

#### Examples

**Violation patterns:**

```sql
SELECT de.person_id, COUNT(*) AS visits_on_metformin
FROM drug_era de
JOIN visit_occurrence vo ON de.person_id = vo.person_id
WHERE de.drug_concept_id = 1503297
GROUP BY de.person_id;
```

```sql
WITH j AS (
  SELECT de.drug_era_id, vo.visit_occurrence_id
  FROM drug_era de JOIN visit_occurrence vo ON vo.person_id = de.person_id
)
SELECT COUNT(*) FROM j;
```

**Correct patterns:**

```sql
SELECT de.person_id, COUNT(*) AS visits_on_metformin
FROM drug_era de
JOIN visit_occurrence vo
  ON de.person_id = vo.person_id
  AND vo.visit_start_date BETWEEN de.drug_era_start_date AND de.drug_era_end_date
WHERE de.drug_concept_id = 1503297
GROUP BY de.person_id;
```

```sql
SELECT COUNT(*)
FROM drug_era de
JOIN visit_occurrence vo ON vo.person_id = de.person_id
WHERE (vo.visit_start_date, vo.visit_end_date) OVERLAPS (de.drug_era_start_date, de.drug_era_end_date);
```

<!-- extra-examples:start -->
**Warning variant: The person_id link runs through person; the era and the visit detail are still unrelated in time.**

```sql
SELECT ce.condition_concept_id, SUM(vd.visit_detail_end_date - vd.visit_detail_start_date) AS days
FROM condition_era ce
JOIN person p ON ce.person_id = p.person_id
JOIN visit_detail vd ON vd.person_id = p.person_id
GROUP BY ce.condition_concept_id;
```

<!-- extra-examples:end -->

#### Suggested fix

Add a condition relating the visit to the era, e.g. `AND <visit>.visit_start_date BETWEEN <era>.<era>_start_date AND
<era>.<era>_end_date`, in addition to person_id. If only co-occurrence within a person is wanted, use `EXISTS`,
`SELECT DISTINCT` or `COUNT(DISTINCT …)`.

#### Sources

- [OMOP CDM v5.4 specification: DRUG_ERA](https://ohdsi.github.io/CommonDataModel/cdm54.html#drug_era)
- [OMOP CDM v5.4 specification: CONDITION_ERA](https://ohdsi.github.io/CommonDataModel/cdm54.html#condition_era)
- [OMOP CDM v5.4 specification: VISIT_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_occurrence)
- [PostgreSQL documentation: Joined Tables](https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN)

---

### 13. Fact Relationship Polymorphic Join Validation { #joins-fact-relationship-join-validation }

**Rule ID:** `joins.fact_relationship_join_validation`
**Severity:** WARNING

#### Intent

FACT_RELATIONSHIP relates facts stored in any table of the CDM. `fact_id_1` and `fact_id_2` have no foreign key;
`domain_concept_id_1` and `domain_concept_id_2` say which table each id belongs to. A join of `fact_id_N` to one
table's primary key needs a restriction on `domain_concept_id_N` of the same row. Without it, rows about other
tables' facts are matched wherever the ids coincide. Whether such rows exist depends on the data, so the finding is
a warning.

#### How it works

The rule looks for a predicate that equates `fact_id_1` or `fact_id_2` with the primary key of a CDM table
(`=` or `IN (SELECT …)`, through casts, CTEs and derived tables). It then looks for a restriction on the matching
`domain_concept_id_N` of the same fact_relationship rows: in the ON clause that holds the join, in a WHERE clause, in
the ON clause of an inner join, or inside the CTE or derived table the rows come from. A filter in the ON clause of
a LEFT JOIN covers only that join, so one polymorphic key can be resolved against several tables, each in its own
LEFT JOIN.

The CDM does not say which concept stands for which table. Two conventions exist in the vocabulary, and both are
accepted: the Domain concept (`21` = Measurement) and the CDM table concept (`1147330` = measurement). A value the
rule cannot read (a parameter, a subquery, a join to DOMAIN or CONCEPT) is accepted. A second warning is given when
the restriction admits a literal concept that the vocabulary assigns to a different table or domain (`= 19`, or
`IN (21, 27)` for a measurement join).

#### Examples

**Violation patterns:**

```sql
SELECT m.measurement_id, fr.fact_id_2
FROM fact_relationship fr
JOIN measurement m ON fr.fact_id_1 = m.measurement_id;
```

```sql
SELECT p.person_id
FROM fact_relationship fr
JOIN person p ON fr.fact_id_1 = p.person_id;
```

**Correct patterns:**

```sql
SELECT m.measurement_id, fr.fact_id_2
FROM fact_relationship fr
JOIN measurement m ON fr.fact_id_1 = m.measurement_id
WHERE fr.domain_concept_id_1 = 21;
```

```sql
SELECT fr.fact_id_1, COALESCE(m.person_id, o.person_id) AS person_id
FROM fact_relationship fr
LEFT JOIN measurement m ON fr.domain_concept_id_1 = 21 AND fr.fact_id_1 = m.measurement_id
LEFT JOIN observation o ON fr.domain_concept_id_1 = 27 AND fr.fact_id_1 = o.observation_id;
```

<!-- extra-examples:start -->
**Warning variant: The domain filter names another domain (19 is the Domain concept 'Condition', not 'Measurement').**

```sql
SELECT m.measurement_id, fr.fact_id_2
FROM fact_relationship fr
JOIN measurement m ON fr.fact_id_1 = m.measurement_id
WHERE fr.domain_concept_id_1 = 19;
```

<!-- extra-examples:end -->

#### Suggested fix

Add a filter on the matching domain column of the same fact_relationship row, e.g.
`AND fr.domain_concept_id_1 = <concept your CDM uses for the joined table>`. The CDM does not fix the value: ETLs
use either the Domain concept (21 = Measurement) or the CDM table concept (1147330 = measurement); check which one
your fact_relationship holds.

#### Sources

- [OMOP CDM v5.4 specification: FACT_RELATIONSHIP](https://ohdsi.github.io/CommonDataModel/cdm54.html#fact_relationship)
- [OMOP CDM v5.4 specification: DOMAIN](https://ohdsi.github.io/CommonDataModel/cdm54.html#domain)
- [OMOP CDM: Data Model Conventions, Domains](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#domains)

---

### 14. Left Join Then Where On Right Table { #joins-left-join-then-where-on-right-table }

**Rule ID:** `joins.left_join_then_where_on_right_table`
**Severity:** WARNING

#### Intent

A LEFT JOIN keeps every left-side row; rows without a match get NULL in every right-side column. A WHERE conjunct
that can never be true when a right-side column is NULL (a null-rejecting predicate) removes exactly those rows, so the
LEFT JOIN behaves like an INNER JOIN. Whether that is intended depends on the query, so the finding is a warning.

#### How it works

For each SELECT with a LEFT JOIN, every AND-conjunct of its WHERE clause is tested. A conjunct is null-rejecting for a
right-side column when it is a comparison, `IN`, `LIKE`, `BETWEEN` or `IS NOT NULL` on that column, possibly through
functions that return NULL for a NULL argument (`LOWER`, `CAST`, arithmetic, `EXTRACT`, `DATE_TRUNC`, `DATEDIFF`,
`YEAR`, …), or `(predicate) IS TRUE` of such a predicate. Only columns the SELECT reads from a LEFT JOIN's right-side
source count: a qualified alias, or an unqualified name only that source supplies. `IS NULL`, `COALESCE`, `CASE`,
`IS DISTINCT FROM` and an `OR` with another branch keep the NULL-extended rows and are not reported.

#### Examples

**Violation patterns:**

```sql
SELECT p.person_id FROM person p
LEFT JOIN visit_occurrence vo ON p.person_id = vo.person_id
WHERE vo.visit_start_date >= DATE '2023-01-01';
```

```sql
SELECT p.person_id FROM person p
LEFT JOIN visit_occurrence vo ON p.person_id = vo.person_id
WHERE DATE_TRUNC('year', vo.visit_start_date) = DATE '2023-01-01';
```

**Correct patterns:**

```sql
SELECT p.person_id FROM person p
LEFT JOIN visit_occurrence vo
  ON p.person_id = vo.person_id
  AND vo.visit_start_date >= DATE '2023-01-01';
```

```sql
SELECT p.person_id FROM person p
LEFT JOIN death d ON d.person_id = p.person_id
WHERE d.person_id IS NULL;
```

<!-- extra-examples:start -->
**Warning variant: An IS NOT NULL test on the right table keeps exactly the rows an INNER JOIN would.**

```sql
SELECT co.condition_occurrence_id FROM condition_occurrence co
LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
WHERE vo.visit_occurrence_id IS NOT NULL;
```

<!-- extra-examples:end -->

#### Suggested fix

Move the predicate into the JOIN ON clause (`LEFT JOIN ... ON ... AND <right>.<col> <op> <val>`) to keep the
left-outer semantics, or accept that the join is an inner join and write `INNER JOIN`.

#### Sources

- [PostgreSQL documentation: Joined Tables](https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN)
- [PostgreSQL documentation: The WHERE Clause](https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-WHERE)
- [PostgreSQL documentation: Comparison Functions and Operators](https://www.postgresql.org/docs/current/functions-comparison.html)

---

### 15. Maps To Direction { #joins-maps-to-direction }

**Rule ID:** `joins.maps_to_direction`
**Severity:** WARNING

#### Intent

A `'Maps to'` row reads `concept_id_1` (any concept, usually a non-standard source concept) → `concept_id_2` (the standard concept it maps to); `'Mapped from'` is the same link reversed. Matching a `*_source_concept_id` against the standard side of the link (`concept_id_2` of `'Maps to'`, `concept_id_1` of `'Mapped from'`) runs the mapping backwards. Non-standard source concepts never appear on that side, so those rows silently drop out.

#### How it works

The rule fires when a `*_source_concept_id` is equated with the standard-side column of a `concept_relationship` instance restricted to `'Maps to'` or `'Mapped from'`. Column lineage is followed through CTEs and derived tables, and the relationship restriction is read from the `concept_relationship` instance the column actually comes from.

Not flagged:

- a standard `*_concept_id` joined to `concept_id_1` of `'Maps to'`: standard concepts map to themselves, so this is a harmless self-map;
- CTE columns that merely share a source-field name but do not come from a `*_source_concept_id`.

#### Examples

**Violation patterns:**

```sql
SELECT cr.concept_id_1
FROM condition_occurrence co
JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_2
WHERE cr.relationship_id = 'Maps to';
```

```sql
SELECT cr.concept_id_2
FROM condition_occurrence co
JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_1
WHERE cr.relationship_id = 'Mapped from';
```

**Correct patterns:**

```sql
SELECT cr.concept_id_2
FROM condition_occurrence co
JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_1
WHERE cr.relationship_id = 'Maps to';
```

```sql
-- Standard concept self-map: not flagged
SELECT cr.concept_id_2
FROM condition_occurrence co
JOIN concept_relationship cr ON co.condition_concept_id = cr.concept_id_1
WHERE cr.relationship_id = 'Maps to';
```

#### Suggested fix

Match the source concept id to `cr.concept_id_1` for `'Maps to'` (or `cr.concept_id_2` for `'Mapped from'`) and read the standard concept from the other column.

#### Sources

- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Mapping relationships](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptMapping)
- [OMOP CDM: Data Model Conventions, Concept relationships](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concept-relationships)
- [OMOP CDM v5.4 specification: CONCEPT_RELATIONSHIP](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept_relationship)

---

### 16. Observation Period Not Linked on person_id { #joins-observation-period-unlinked }

**Rule ID:** `joins.observation_period_unlinked`
**Severity:** WARNING

#### Intent

`observation_period.person_id` identifies the person the period belongs to. When a SELECT reads observation_period
together with `person` or a person-level table in the same FROM list, only a predicate that equates
`observation_period.person_id` with a value of the other row ties each period to its own person. Without it the join
is a cross product: a date-containment test checks one patient's event against every patient's observation windows,
and rows multiply.

#### How it works

For each SELECT that reads observation_period and a person-level table (tables wrapped in a CTE or derived table that
passes their rows through count as the tables), the rule looks in JOIN ON and WHERE for a link: an equality between
`observation_period.person_id` and a column of another source under any name (`c.subject_id = op.person_id`), through
casts, `op.person_id IN (co.person_id)`, `USING (person_id)` or `NATURAL JOIN` between observation_period and another
source of the same FROM item, or both sides fixed to the same patient by one literal. A SELECT where
observation_period is the only source (a UNION branch, a correlated `EXISTS`) is not checked.

#### Examples

**Violation patterns:**

```sql
SELECT co.person_id
FROM condition_occurrence co, observation_period op
WHERE co.condition_start_date BETWEEN op.observation_period_start_date
  AND op.observation_period_end_date;
```

```sql
SELECT co.person_id
FROM condition_occurrence co JOIN person p USING (person_id), observation_period op
WHERE co.condition_start_date BETWEEN op.observation_period_start_date AND op.observation_period_end_date;
```

**Correct patterns:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN observation_period op ON co.person_id = op.person_id
WHERE co.condition_start_date BETWEEN op.observation_period_start_date
  AND op.observation_period_end_date;
```

```sql
SELECT person_id FROM person NATURAL JOIN observation_period;
```

<!-- extra-examples:start -->
**Warning variant: An explicit JOIN ... ON that tests only dates still matches other patients' observation periods.**

```sql
SELECT de.person_id
FROM drug_exposure de
JOIN observation_period op
  ON de.drug_exposure_start_date >= op.observation_period_start_date
  AND de.drug_exposure_start_date <= op.observation_period_end_date;
```

<!-- extra-examples:end -->

#### Suggested fix

Add `op.person_id = <clinical>.person_id` to the JOIN ON (or WHERE) that brings in observation_period.

#### Sources

- [OMOP CDM v5.4 specification: OBSERVATION_PERIOD](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation_period)
- [The Book of OHDSI, ch. 9: SQL and R, Exposure cohort](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#exposure-cohort)
- [PostgreSQL documentation: Joined Tables](https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN)

---

### 17. Period Join Requires Date Overlap { #joins-period-join-requires-date-overlap }

**Rule ID:** `joins.period_join_requires_date_overlap`
**Severity:** WARNING

#### Intent

A person can have several `observation_period` rows (which never overlap) and several `payer_plan_period` rows (which
can overlap, e.g. medical and drug coverage). Joining an event table to a period table on `person_id` alone pairs
every event with every period of that person: rows repeat and counts, sums and period attributes are wrong. The join
needs a date-containment condition as well.

#### How it works

For each SELECT, the rule finds an event table and a period table whose `person_id` values are equated,
directly, through another table, or with `USING (person_id)`. Tables wrapped in a CTE or derived table that passes
their rows through count as the tables; a wrapper around the period table counts only when it applies no filter.

The join is accepted when the same SELECT bounds the period on both sides around one date: `>=` start and `<=` end,
`BETWEEN start AND end`, offset bounds (`start + INTERVAL '365 days'`), or a non-negative date difference
(`x - start >= 365`, `DATEDIFF(day, start, x) >= 0`). For observation_period, a bound equal to one value (a date or a
per-person `MIN(...)` subquery) also selects one period. For payer_plan_period it does not, because plans can
overlap, and containment keeps the plans in force at the event, which can be more than one.

A SELECT whose result cannot change under duplication (`IN`/`EXISTS` subquery, `SELECT DISTINCT`, only
`COUNT(DISTINCT …)`/`MIN`/`MAX` with no period column in the output) is not reported, unless a predicate compares a
period column with another table without bounding it (a one-sided washout). `person` joined to a period table is
not reported: that join yields one row per period, and counting periods is a legitimate question.

A pair of comparisons that tests one event column against both bounds of the same period alias with the bounds swapped (`BETWEEN op.observation_period_end_date AND op.observation_period_start_date`, or the `>=` / `<=` spellings) is a containment attempt; it is left to `temporal.observation_period_date_range_logic`.

#### Examples

**Violation patterns:**

```sql
SELECT COUNT(*) FROM condition_occurrence co
JOIN observation_period op ON co.person_id = op.person_id;
```

```sql
SELECT ppp.payer_concept_id, SUM(de.quantity)
FROM drug_exposure de
JOIN payer_plan_period ppp ON de.person_id = ppp.person_id
GROUP BY ppp.payer_concept_id;
```

**Correct patterns:**

```sql
SELECT COUNT(*) FROM condition_occurrence co
JOIN observation_period op
  ON co.person_id = op.person_id
  AND co.condition_start_date BETWEEN op.observation_period_start_date
                                  AND op.observation_period_end_date;
```

```sql
SELECT COUNT(*) FROM condition_occurrence co
JOIN observation_period op ON co.person_id = op.person_id
WHERE co.condition_start_date - op.observation_period_start_date >= 365
  AND co.condition_start_date <= op.observation_period_end_date;
```

<!-- extra-examples:start -->
**Warning variant: A one-sided washout bound is met by any earlier period, not only the one containing the event; DISTINCT does not repair that.**

```sql
SELECT DISTINCT co.person_id
FROM condition_occurrence co
JOIN observation_period op ON co.person_id = op.person_id
WHERE op.observation_period_start_date <= co.condition_start_date - INTERVAL '365 days';
```

<!-- extra-examples:end -->

#### Suggested fix

Add `AND <event>.<event_date> BETWEEN p.<period>_start_date AND p.<period>_end_date` (or both `>=` start and `<=` end)
to the period join, in addition to `person_id`. For payer_plan_period this keeps the plans in force at the event; a
person can hold more than one at a time.

#### Sources

- [The Book of OHDSI, ch. 9: SQL and R, Exposure cohort](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#exposure-cohort)
- [OMOP CDM v5.4 specification: OBSERVATION_PERIOD](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation_period)
- [OMOP CDM v5.4 specification: PAYER_PLAN_PERIOD](https://ohdsi.github.io/CommonDataModel/cdm54.html#payer_plan_period)

---

### 18. Person ID Join Validation { #joins-person-id-join-validation }

**Rule ID:** `joins.person_id_join_validation`
**Severity:** ERROR

#### Intent

`person_id` identifies an individual, and every person-level table references PERSON through its own `person_id`. A
predicate that equates a person key with the key of another entity (`visit_occurrence_id`, `location_id`,
`provider_id`, `care_site_id`, …) compares unrelated id sequences and matches unrelated rows on every CDM.

#### How it works

Each operand of an equality or `IN (SELECT …)` in ON, WHERE or HAVING is followed through casts, CTEs and derived
tables to the CDM column it reads, and that column is mapped to the entity it identifies (CDM v5.4 field-level
specification: primary and foreign keys). A column is judged on what it carries, not on its name:
`(SELECT gender_concept_id AS person_id FROM person) a` supplies a concept id under the name `person_id`.

Not judged: `cohort.subject_id`, which holds a person id for a person cohort and can hold a provider, visit or care
site id; the polymorphic keys (`fact_id_1`, `*_event_id`, …); columns the CDM does not define; and two columns of the
same row (a data check, not a join). A person key equated with a concept id is reported by
`anti_patterns.join_key_validation`. This rule also covers a person joined to LOCATION on the wrong key (a merged rule; old id in the [CHANGELOG](https://github.com/fastomop/fastSSV/blob/main/CHANGELOG.md) renames table).

#### Examples

**Violation patterns:**

```sql
SELECT p.person_id, vo.visit_start_date
FROM person p
JOIN visit_occurrence vo ON p.person_id = vo.visit_occurrence_id;
```

```sql
SELECT p.person_id
FROM person p
WHERE p.person_id IN (SELECT vo.visit_occurrence_id FROM visit_occurrence vo);
```

**Correct patterns:**

```sql
SELECT p.person_id, vo.visit_start_date
FROM person p
JOIN visit_occurrence vo ON p.person_id = vo.person_id;
```

```sql
SELECT c.subject_id
FROM cohort c
JOIN person p ON p.person_id = c.subject_id;
```

<!-- extra-examples:start -->
**Error variant: A person's address joined on person_id instead of person.location_id.**

```sql
SELECT p.person_id, l.zip
FROM person p
JOIN location l ON p.person_id = l.location_id;
```

<!-- extra-examples:end -->

#### Suggested fix

Replace the equality with one between two columns that identify the same entity, usually
`<table>.person_id = <other>.person_id` (`person.location_id = location.location_id` for an address). When a column is
read straight from a table, the finding carries a REPLACE patch.

#### Sources

- [OMOP CDM: Data Model Conventions, Fields](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#fields)
- [OMOP CDM v5.4 specification: PERSON](https://ohdsi.github.io/CommonDataModel/cdm54.html#person)
- [OMOP CDM v5.4 specification: LOCATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#location)
- [OMOP CDM v5.4 specification: COHORT](https://ohdsi.github.io/CommonDataModel/cdm54.html#cohort)

---

### 19. Preceding Visit Occurrence Validation { #joins-preceding-visit-occurrence-validation }

**Rule ID:** `joins.preceding_visit_occurrence_validation`
**Severity:** ERROR

#### Intent

`visit_occurrence.preceding_visit_occurrence_id` is a foreign key to `visit_occurrence.visit_occurrence_id`: it finds
the visit that occurred for the person before the given visit. It holds a visit id, so equating it with the key of
another entity (`person_id`, `visit_detail_id`, `condition_occurrence_id`, …) matches unrelated rows on every CDM.

#### How it works

Each operand of an equality or `IN (SELECT …)` in ON, WHERE or HAVING is followed through casts, CTEs and derived
tables to the CDM column it reads. A predicate between `preceding_visit_occurrence_id` and a column that identifies
another entity is reported. Any column that holds a visit id is a valid partner, including
`visit_detail.visit_occurrence_id` and another `preceding_visit_occurrence_id`. The foreign key alone identifies the
prior visit: no person or date condition is required in addition.

#### Examples

**Violation patterns:**

```sql
SELECT vo.visit_occurrence_id FROM visit_occurrence vo
JOIN visit_occurrence prev ON vo.preceding_visit_occurrence_id = prev.person_id;
```

```sql
SELECT vo.visit_occurrence_id
FROM visit_occurrence vo
WHERE vo.preceding_visit_occurrence_id IN (SELECT person_id FROM person);
```

**Correct patterns:**

```sql
SELECT vo.visit_occurrence_id, prev.visit_end_date
FROM visit_occurrence vo
JOIN visit_occurrence prev ON vo.preceding_visit_occurrence_id = prev.visit_occurrence_id;
```

```sql
SELECT vd.visit_detail_id
FROM visit_occurrence vo
JOIN visit_detail vd ON vd.visit_occurrence_id = vo.preceding_visit_occurrence_id;
```

<!-- extra-examples:start -->
**Error variant: A visit detail id is not a visit id; the prior visit is visit_occurrence.visit_occurrence_id.**

```sql
SELECT vo.visit_occurrence_id
FROM visit_occurrence vo
JOIN visit_detail vd ON vo.preceding_visit_occurrence_id = vd.visit_detail_id;
```

<!-- extra-examples:end -->

#### Suggested fix

Replace the equality with `vo.preceding_visit_occurrence_id = prev.visit_occurrence_id`. When the wrong column is read
straight from a table, the finding carries a REPLACE patch.

#### Sources

- [OMOP CDM v5.4 specification: VISIT_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_occurrence)
- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)

---

### 20. Provider Join Validation { #joins-provider-join-validation }

**Rule ID:** `joins.provider_join_validation`
**Severity:** ERROR

#### Intent

PROVIDER is keyed by `provider_id`, and every table that records a provider references it through its own
`provider_id` foreign key. A predicate that equates a provider key with the key of another entity, such as
`care_site_id` or `condition_occurrence_id`, compares unrelated id sequences and matches unrelated rows on every CDM.

#### How it works

Each operand of an equality or `IN (SELECT …)` in ON, WHERE or HAVING is followed through casts, CTEs and derived
tables to the CDM column it reads, and that column is mapped to the entity it identifies (its own table for a
primary key, the referenced table for a foreign key, per the CDM v5.4 field-level specification). The rule reports a
predicate whose two sides identify different entities when one of them is a provider. Two columns that reference the
same entity are a valid join even when neither is `provider_id`: `vo.care_site_id = pr.care_site_id` pairs a visit
with the providers of its care site. Polymorphic keys and columns the CDM does not define are not judged.

Each wrong predicate is reported by one rule: pairs with a person key by `joins.person_id_join_validation`, with a
visit key by `joins.visit_occurrence_id_join_validation`, and provider-to-care_site pairs by
`joins.provider_care_site_join_validation`.

#### Examples

**Violation patterns:**

```sql
SELECT vo.visit_occurrence_id, p.specialty_concept_id
FROM visit_occurrence vo
JOIN provider p ON vo.care_site_id = p.provider_id;
```

```sql
SELECT co.condition_occurrence_id
FROM condition_occurrence co, provider p
WHERE co.condition_occurrence_id = p.provider_id;
```

**Correct patterns:**

```sql
SELECT vo.visit_occurrence_id, p.specialty_concept_id
FROM visit_occurrence vo
JOIN provider p ON vo.provider_id = p.provider_id;
```

```sql
SELECT vo.visit_occurrence_id, pr.provider_id
FROM visit_occurrence vo
JOIN provider pr ON vo.care_site_id = pr.care_site_id;
```

#### Suggested fix

Replace the equality with one between two columns that identify the same entity, usually
`<table>.provider_id = provider.provider_id`. When the wrong column is read straight from a table, the finding
carries a REPLACE patch that swaps it for that table's column holding the other side's key.

#### Sources

- [OMOP CDM: Data Model Conventions, Fields](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#fields)
- [OMOP CDM v5.4 specification: PROVIDER](https://ohdsi.github.io/CommonDataModel/cdm54.html#provider)
- [OMOP CDM v5.4 specification: VISIT_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_occurrence)

---

### 21. Provider to Care Site Join Validation { #joins-provider-care-site-join-validation }

**Rule ID:** `joins.provider_care_site_join_validation`
**Severity:** ERROR

#### Intent

`provider.care_site_id` is the foreign key to CARE_SITE: the site the provider primarily practises in. A predicate
between PROVIDER and CARE_SITE that equates keys of two different entities, such as `p.provider_id =
cs.care_site_id`, compares unrelated id sequences and matches unrelated rows on every CDM.

#### How it works

Each operand of an equality or `IN (SELECT …)` in ON, WHERE or HAVING is followed through casts, CTEs and derived
tables to the CDM column it reads, and mapped to the entity it identifies (CDM v5.4 field-level specification). A
predicate between a provider column and a care_site column whose sides identify different entities is reported.

#### Examples

**Violation patterns:**

```sql
SELECT p.provider_id, cs.care_site_name
FROM provider p
JOIN care_site cs ON p.provider_id = cs.care_site_id;
```

```sql
SELECT p.provider_id, cs.care_site_name
FROM provider p, care_site cs
WHERE p.provider_id = cs.care_site_id;
```

**Correct patterns:**

```sql
SELECT p.provider_id, cs.care_site_name
FROM provider p
JOIN care_site cs ON p.care_site_id = cs.care_site_id;
```

```sql
SELECT p.provider_id
FROM provider p
WHERE p.care_site_id IN (SELECT care_site_id FROM care_site WHERE place_of_service_concept_id = 8756);
```

#### Suggested fix

Replace the equality with `provider.care_site_id = care_site.care_site_id`. When the wrong column is read straight
from a table, the finding carries a REPLACE patch.

#### Sources

- [OMOP CDM v5.4 specification: PROVIDER](https://ohdsi.github.io/CommonDataModel/cdm54.html#provider)
- [OMOP CDM v5.4 specification: CARE_SITE](https://ohdsi.github.io/CommonDataModel/cdm54.html#care_site)

---

### 22. Visit Occurrence ID Join Validation { #joins-visit-occurrence-id-join-validation }

**Rule ID:** `joins.visit_occurrence_id_join_validation`
**Severity:** ERROR

#### Intent

`visit_occurrence_id` identifies a visit. The event tables, VISIT_DETAIL and NOTE reference it through their own
`visit_occurrence_id`, and `visit_occurrence.preceding_visit_occurrence_id` references it too. A predicate that
equates a visit key with the key of another entity (`visit_detail_id`, `condition_occurrence_id`, `care_site_id`,
…) compares unrelated id sequences and matches unrelated rows on every CDM.

#### How it works

Each operand of an equality or `IN (SELECT …)` in ON, WHERE or HAVING is followed through casts, CTEs and derived
tables to the CDM column it reads, and that column is mapped to the entity it identifies (CDM v5.4 field-level
specification: primary and foreign keys). The rule reports a predicate whose sides identify different entities when
one of them is a visit. Keys without a declared target are not judged: the polymorphic event keys
(`cost_event_id`, `fact_id_1`, `episode_event.event_id`, `*_event_id`) and `cohort.subject_id`, which can hold a
visit id for a visit cohort. Pairs with a person key are reported by `joins.person_id_join_validation`, and pairs with
`preceding_visit_occurrence_id` by `joins.preceding_visit_occurrence_validation`.

This rule also covers the mix-up of a visit id and a visit detail id (a merged rule; old id in the [CHANGELOG](https://github.com/fastomop/fastSSV/blob/main/CHANGELOG.md) renames table).

#### Examples

**Violation patterns:**

```sql
SELECT co.condition_concept_id, vd.visit_detail_concept_id
FROM condition_occurrence co
JOIN visit_detail vd ON co.visit_occurrence_id = vd.visit_detail_id;
```

```sql
SELECT vo.visit_concept_id, co.condition_concept_id
FROM visit_occurrence vo
JOIN condition_occurrence co ON vo.visit_occurrence_id = co.condition_occurrence_id;
```

**Correct patterns:**

```sql
SELECT co.condition_concept_id, vd.visit_detail_concept_id
FROM condition_occurrence co
JOIN visit_detail vd ON co.visit_detail_id = vd.visit_detail_id;
```

```sql
SELECT c.cohort_start_date, vo.visit_concept_id
FROM cohort c
JOIN visit_occurrence vo ON c.subject_id = vo.visit_occurrence_id
WHERE c.cohort_definition_id = 7;
```

#### Suggested fix

Replace the equality with one between two columns that identify the same entity:
`<table>.visit_occurrence_id = <other>.visit_occurrence_id`, or `<table>.visit_detail_id =
visit_detail.visit_detail_id` for a visit detail. When the wrong column is read straight from a table, the finding
carries a REPLACE patch (a visit / visit detail mix-up keeps the visit detail key).

#### Sources

- [OMOP CDM: Data Model Conventions, Fields](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#fields)
- [OMOP CDM v5.4 specification: VISIT_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_occurrence)
- [OMOP CDM v5.4 specification: VISIT_DETAIL](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_detail)
- [OMOP CDM v5.4 specification: COHORT](https://ohdsi.github.io/CommonDataModel/cdm54.html#cohort)

---

### 23. Visit Occurrence INNER JOIN Validation { #joins-visit-occurrence-inner-join-validation }

**Rule ID:** `joins.visit_occurrence_inner_join_validation`
**Severity:** WARNING

#### Intent

`visit_occurrence_id` is optional on the CDM v5.4 event tables (condition_occurrence, drug_exposure,
procedure_occurrence, measurement, observation, device_exposure, note). An inner join from an event table to
`visit_occurrence` on `visit_occurrence_id` therefore drops every event without a visit. Whether that is intended
depends on the question, so the finding is a warning.

#### How it works

For each SELECT, the rule finds an event table linked to `visit_occurrence` on `visit_occurrence_id` by an inner
join: `JOIN … ON`, `JOIN … USING (visit_occurrence_id)`, or a comma join linked in WHERE, through casts and through
CTEs or derived tables that pass the tables' rows through. `visit_detail` is not covered: its `visit_occurrence_id`
is required.

It stays silent when the SELECT shows that only visit-linked events are wanted:

- a predicate in ON, WHERE or HAVING that can only be true for a matched visit row (`vo.visit_concept_id = 9201`,
  visit dates, `vo.visit_occurrence_id IS NOT NULL`). Such a predicate is never true for NULL-extended rows, so a
  LEFT JOIN would return the same rows;
- `<event>.visit_occurrence_id IS NOT NULL`;
- a GROUP BY on a visit column.

`vo.<col> IS NULL` does not count: it is true for NULL-extended rows.

#### Examples

**Violation patterns:**

```sql
SELECT co.person_id FROM condition_occurrence co
JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id;
```

```sql
SELECT co.person_id FROM condition_occurrence co
JOIN visit_occurrence vo USING (visit_occurrence_id);
```

**Correct patterns:**

```sql
SELECT co.person_id FROM condition_occurrence co
LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id;
```

```sql
SELECT co.person_id FROM condition_occurrence co
JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
WHERE vo.visit_concept_id = 9201;
```

<!-- extra-examples:start -->
**Warning variant: A comma join linked in WHERE is an inner join too, and drops exposures without a visit.**

```sql
SELECT de.drug_concept_id, COUNT(*)
FROM drug_exposure de, visit_occurrence vo
WHERE de.visit_occurrence_id = vo.visit_occurrence_id
GROUP BY de.drug_concept_id;
```

<!-- extra-examples:end -->

#### Suggested fix

Replace `JOIN visit_occurrence` with `LEFT JOIN visit_occurrence` if events without a recorded visit should be kept;
keep the inner join only when the query is meant to cover visit-linked events, and say so with a visit filter or
`<event>.visit_occurrence_id IS NOT NULL`.

#### Sources

- [OMOP CDM v5.4 specification: CONDITION_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#condition_occurrence)
- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)

---

## Temporal Rules

These rules validate temporal logic, date handling, and observation period anchoring.

---

### 1. Datetime Column With a Date-Only Inclusive Upper Bound { #temporal-datetime-between-date-literal }

**Rule ID:** `temporal.datetime_between_date_literal`
**Severity:** WARNING

#### Intent

BETWEEN includes both ends, and PostgreSQL compares a date with a timestamp as midnight of that day. measurement_datetime BETWEEN '2023-01-01' AND '2023-12-31' (or <= '2023-12-31') therefore keeps 2023-12-31 00:00:00 but drops every later time on 2023-12-31. The same holds for any inclusive upper bound written as a date: <=, a literal on the left with >=, TO_DATE, MAKE_DATE, CAST(... AS DATE). The bounded value is typed by lineage (a CDM *_datetime column through CTEs and renames), so a value already cast to DATE, truncated to the day or formatted as text is not reported. The finding is a WARNING: the CDM conventions set unknown times to 00:00:00, so a source without times loses nothing. Use a half-open interval (>= start AND < the day after the end) or compare CAST(column AS DATE).

#### How it works

The rule looks at every inclusive upper bound: the high end of `BETWEEN` (not `BETWEEN SYMMETRIC`), `x <= bound`
and `bound >= x`, in any clause. It reports the bound when

* `x` is a TIMESTAMP by lineage: a CDM `*_datetime` column (typed by the CDM v5.4 field-level CSV), followed through
  CTEs, derived tables and renames, possibly under MIN / MAX or a cast to TIMESTAMP; and
* the bound is a date literal without a time of day: `'2023-12-31'`, `'20231231'`, `DATE '...'`,
  `CAST('...' AS DATE)`, `TIMESTAMP '2023-12-31'`, `TO_DATE(...)`, `MAKE_DATE(...)`.

A value already reduced to a day (`CAST(x AS DATE)`, `x::date`, `DATE(x)`, `CONVERT(date, x)`, `DATE_TRUNC('day', x)`)
or to text (`TO_CHAR`) is not a timestamp and is not reported; neither are strict `<` bounds or bounds that carry a
time.

#### Examples

**Violation pattern:**

```sql
SELECT *
FROM measurement
WHERE measurement_datetime BETWEEN DATE '2023-01-01' AND DATE '2023-12-31';
```

**Correct pattern:**

```sql
SELECT *
FROM measurement
WHERE measurement_datetime >= TIMESTAMP '2023-01-01 00:00:00'
  AND measurement_datetime <  TIMESTAMP '2024-01-01 00:00:00';
```

<!-- extra-examples:start -->
**Warning variant: The same range written with >= and <=**

```sql
SELECT *
FROM measurement
WHERE measurement_datetime >= '2023-01-01'
  AND measurement_datetime <= '2023-12-31';
```

<!-- extra-examples:end -->

#### Suggested fix

REPLACE: `<datetime_col> BETWEEN '<start>' AND '<end>'` WITH `<datetime_col> >= '<start>' AND <datetime_col> < '<end_plus_one_day>'` (half-open), OR compare the day: `CAST(<datetime_col> AS DATE) BETWEEN '<start>' AND '<end>'`.

#### Sources

- [PostgreSQL documentation: Comparison Functions and Operators, comparison predicates (BETWEEN)](https://www.postgresql.org/docs/current/functions-comparison.html#FUNCTIONS-COMPARISON-PRED-TABLE)
- [PostgreSQL documentation: Date/Time Functions and Operators](https://www.postgresql.org/docs/current/functions-datetime.html)
- [OMOP CDM: Data Model Conventions, Time span of available data](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#time-span-of-available-data)

---

### 2. End Before Start Validation { #temporal-end-before-start-validation }

**Rule ID:** `temporal.end_before_start_validation`
**Severity:** WARNING / ERROR

#### Intent

The OHDSI Data Quality Dashboard pairs each start date with its end date (plausibleStartBeforeEnd) and counts the records whose start is after their end. A filter such as drug_exposure_start_date >= '2023-06-01' AND drug_exposure_end_date < '2023-06-01' admits only those records: the usual cause is a window test with one operator the wrong way round. The finding is a WARNING because the CDM specification does not rule such records out and the dashboard tolerates a share of them, so the result depends on the data. Bounds on a single column that cannot both hold (start >= '2023-06-01' AND start < '2023-01-01', or BETWEEN with the larger value first) are false for every row and are an ERROR. Bounds are read from the AND-ed predicates of one SELECT, per row source, with the literal on either side. Comparing the two columns of a row with each other is the plausibility check itself and is not reported; neither are comparisons between two rows, such as a readmission that starts after the previous visit ended.

#### How it works

The start/end pairs are generated from the Data Quality Dashboard field-level file (every field with
`plausibleStartBeforeEnd = Yes`), so they include procedure_occurrence, the era tables, observation_period,
payer_plan_period and the `*_datetime` pairs. For each SELECT, the rule collects the constant bounds that the AND-ed
WHERE and JOIN ON predicates place on each of those columns, per row source: `>`, `>=`, `<`, `<=`, `=` with the literal
on either side, and `BETWEEN`. Columns are followed through CTEs, derived tables and renames to the CDM column; a cast
of a DATE column to DATE or TIMESTAMP keeps its bound, any other wrapper drops it. Literals may be `'2023-06-01'`,
`'20230601'`, `DATE '...'`, `CAST('...' AS DATE)`, `TIMESTAMP '...'`, `TO_DATE(...)` or `MAKE_DATE(...)`. An untyped
string literal compared with a DATE column is read as a date, as PostgreSQL and DuckDB coerce it: `visit_start_date >=
'2023-06-01 10:00' AND visit_start_date <= '2023-06-01'` keeps the visits of 2023-06-01 and is not reported. A
`TIMESTAMP '...'` literal, a cast of the column to TIMESTAMP, and a `*_datetime` column keep the time of day.

* **ERROR**: the bounds on one column leave no value (`start >= '2023-06-01' AND start < '2023-01-01'`,
  `BETWEEN '2023-12-31' AND '2023-01-01'`). The predicate is false for every row.
* **WARNING**: the lower bound on a start column lies after the upper bound on its end column of the same row source,
  so only records whose end precedes their start qualify. The Data Quality Dashboard counts those as implausible and
  tolerates a share of them, so the result depends on the data.

Not reported: a comparison of the two columns of one row (`visit_end_date < visit_start_date`), which is the
plausibility check itself in any projection; comparisons between two rows (readmission, treatment gaps, cohort
re-entry); bounds on different row sources, in different SELECTs or UNION branches; anything under OR or NOT;
`BETWEEN SYMMETRIC`; non-constant bounds.

#### Examples

**Violation pattern:**

```sql
SELECT drug_exposure_id
FROM drug_exposure
WHERE drug_exposure_start_date >= DATE '2023-06-01'
  AND drug_exposure_end_date < DATE '2023-06-01';
```

**Correct pattern:**

```sql
SELECT drug_exposure_id
FROM drug_exposure
WHERE drug_exposure_start_date <= DATE '2023-06-30'
  AND drug_exposure_end_date >= DATE '2023-06-01';
```

<!-- extra-examples:start -->
**Error variant: Bounds on one column that no date satisfies: the query returns no rows on any data**

```sql
SELECT visit_occurrence_id
FROM visit_occurrence
WHERE visit_start_date >= DATE '2023-06-01'
  AND visit_start_date < DATE '2023-01-01';
```

<!-- extra-examples:end -->

#### Suggested fix

REPLACE: the two bounds WITH an overlap test, `<start_col> <= '<window_end>' AND <end_col> >= '<window_start>'`. To list records whose end precedes their start as a data-quality check, compare the two columns directly: `<end_col> < <start_col>`.

#### Sources

- [OHDSI Data Quality Dashboard: CDM v5.4 field-level checks and thresholds (CSV)](https://raw.githubusercontent.com/OHDSI/DataQualityDashboard/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)

---

### 3. Implausible Date Filter { #temporal-implausible-date-filter }

**Rule ID:** `temporal.implausible_date_filter`
**Severity:** WARNING

#### Intent

The OHDSI Data Quality Dashboard bounds every CDM event date between its plausibleValueLow (1950-01-01; 1850-01-01 for birth_datetime) and the day after the check runs, and the CDM conventions say no record can follow the last observation period. A filter whose admitted range lies entirely after the current date, or entirely before the column's floor, therefore selects nothing, or only records the dashboard reports as errors. The columns and floors are generated from the dashboard's field-level file, so era, observation_period, payer_plan_period and cohort dates are covered too. The usual cause is a reversed comparison operator. Date-shifted research CDMs (for example MIMIC-IV, whose dates fall in 2100-2200) legitimately hold 'future' dates, so the rule is a warning and does not flag far-future calendar literals. End dates are exempt from the future check because they are often derived and can legitimately extend past the extract. Predicates under OR or NOT are treated as data-quality audits and are not flagged.

#### How it works

The columns and their floors are generated from the Data Quality Dashboard field-level file: every date column with
`plausibleValueHigh = DATEADD(dd,1,GETDATE())`, with its `plausibleValueLow` (1950-01-01 for clinical events, eras,
periods and cohort dates; 1850-01-01 for `person.birth_datetime`). Columns are followed through CTEs and renames, and
MIN / MAX and DATE_TRUNC are looked through. A predicate in WHERE, JOIN ON or HAVING is reported when everything it
admits is

* **on or after the current date**, for start and point-in-time dates: `> CURRENT_DATE`, `>= CURRENT_DATE` (today or
  later), `>= CURRENT_DATE + INTERVAL '30 days'`, `BETWEEN CURRENT_DATE AND ...`, `<date> - CURRENT_DATE > 0`,
  `DATEDIFF(day, GETDATE(), <date>) > 0`, `EXTRACT(YEAR FROM <date>) > EXTRACT(YEAR FROM CURRENT_DATE)`, with
  `NOW()`, `GETDATE()`, `LOCALTIMESTAMP`, `(SELECT CURRENT_DATE)` and similar. End dates (columns the dashboard
  requires to follow another date of the same row) are exempt; or
* **before the column's floor**: `< '1950-01-01'`, `<= DATE '1949-12-31'`, `EXTRACT(YEAR FROM ...) < 1950`,
  `BETWEEN` / `IN` / `=` with only earlier values.

Look-back windows (`>= CURRENT_DATE - INTERVAL '30 days'`) and far-future calendar literals are not reported, and
predicates under OR or NOT are treated as data-quality audits.

#### Examples

**Violation pattern:**

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_start_date > CURRENT_DATE;
```

**Correct pattern:**

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_start_date >= CURRENT_DATE - INTERVAL '30 days';
```

<!-- extra-examples:start -->
**Warning variant: Cutoff before the 1950 floor: admits only dates the Data Quality Dashboard treats as implausible**

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_start_date < DATE '1950-01-01';
```

<!-- extra-examples:end -->

#### Suggested fix

REPLACE: `<event_date> > CURRENT_DATE` WITH `<event_date> <= CURRENT_DATE` (or a look-back such as `>= CURRENT_DATE - INTERVAL '30 days'`) if the direction is reversed; REPLACE a cutoff before the plausible floor WITH `<event_date> >= '1950-01-01'` if the direction is reversed.

#### Sources

- [OHDSI Data Quality Dashboard: CDM v5.4 field-level checks and thresholds (CSV)](https://raw.githubusercontent.com/OHDSI/DataQualityDashboard/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [OMOP CDM: Data Model Conventions, Time span of available data](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#time-span-of-available-data)
- [MIMIC-IV v3.1 on PhysioNet: deidentified dates](https://physionet.org/content/mimiciv/3.1/)

---

### 4. Nullable Date Column Without NULL Handling { #temporal-nullable-date-column }

**Rule ID:** `temporal.nullable_date_column`
**Severity:** WARNING

#### Intent

In CDM v5.4 each clinical table has a required date (Required = Yes) and optional companions: the *_datetime columns (including person.birth_datetime and note_nlp.nlp_datetime), measurement_time, drug_exposure.verbatim_end_date, and the end dates of condition_occurrence, procedure_occurrence, device_exposure and episode. drug_exposure_end_date, visit_end_date and visit_detail_end_date are required and are not flagged. Comparing or subtracting an optional column yields NULL for rows where it is missing, so those rows disappear from the filter, join or aggregate without any error. Use the required column, COALESCE to a sensible fallback, or state the exclusion with IS NOT NULL. Calendar-part extraction (EXTRACT(YEAR FROM birth_datetime)) passes NULL through and counts as a use. The end-before-start plausibility check on one row is not flagged.

#### How it works

The optional columns and their required stand-ins are generated from the CDM v5.4 field-level CSV (Required =
No date and datetime columns, plus measurement_time). A column is identified by lineage, through CTEs, derived tables
and renames; a value computed from it (COALESCE, CASE) is no longer the optional column. A use is reported when the
column is

* an operand of a comparison, the tested value or a bound of `BETWEEN`, or the tested value of `IN`, in WHERE /
  JOIN ON / HAVING; or
* an operand of `+` / `-` or of a date-difference / date-add function, anywhere (calendar-part extraction such as
  `EXTRACT(YEAR FROM ...)` is looked through);

and it is not wrapped in COALESCE / NULLIF / IFNULL / ISNULL / NVL / CASE, and the SELECT that uses it does not test
it with `IS [NOT] NULL` in its WHERE / JOIN ON / HAVING (or the aggregate's FILTER clause). A test in another CTE or in
the projection list does not count. The same-row plausibility check `end < start` for a Data Quality Dashboard
start/end pair is not reported: under three-valued logic a NULL end is not before its start.

#### Examples

**Violation pattern:**

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_end_date >= DATE '2023-01-01';
```

**Correct pattern:**

```sql
SELECT person_id
FROM condition_occurrence
WHERE COALESCE(condition_end_date, condition_start_date) >= DATE '2023-01-01';
```

<!-- extra-examples:start -->
**Warning variant: Date arithmetic on optional birth_datetime: age is NULL wherever it is missing (year_of_birth is required)**

```sql
SELECT co.person_id,
       EXTRACT(YEAR FROM co.condition_start_date) - EXTRACT(YEAR FROM p.birth_datetime) AS age
FROM condition_occurrence co
JOIN person p ON co.person_id = p.person_id;
```

<!-- extra-examples:end -->

#### Suggested fix

REPLACE: the optional column WITH its required counterpart (e.g. condition_start_date for condition_start_datetime), OR WRAP it as `COALESCE(<optional_col>, <fallback>)`, OR ADD an explicit `<optional_col> IS NOT NULL` so the exclusion is deliberate.

#### Sources

- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [OMOP CDM v5.4 specification: CONDITION_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#condition_occurrence)
- [PostgreSQL documentation: Comparison Functions and Operators](https://www.postgresql.org/docs/current/functions-comparison.html)

---

### 5. Observation Period Anchoring { #temporal-observation-period-anchoring }

**Rule ID:** `temporal.observation_period_anchoring`
**Severity:** WARNING

#### Intent

The CDM v5.4 OBSERVATION_PERIOD user guide says that events may exist outside every observation period, that the absence of an event outside these spans cannot be read as evidence that it did not happen, and that incidence or prevalence rates should only be calculated over active observation periods. The rule reports exactly those two uses when observation_period is not a source of the SELECT that makes them, of an enclosing SELECT, or of a CTE or derived table they read: an absence test on a clinical event table (NOT EXISTS / NOT IN over an event subquery, LEFT JOIN to an event source filtered on IS NULL, EXCEPT with an event query), and a count of events divided by a count of persons. The same guide allows outside events to be used for inclusion criteria, so selecting or counting recorded events, with or without a calendar window, is not reported. The finding is a WARNING: whether the omission changes the answer depends on how the site derived its observation periods and on the study question.

#### How it works

Two shapes are reported, each only when observation_period is not a FROM / JOIN source of the SELECT that
makes the test, of a SELECT enclosing it, or of a CTE or derived table those read (an observation_period read only in
a scalar subquery does not count):

* **Absence test, per person, on a clinical event table** (any table with a person_id and dates other than PERSON and
  the two period tables, generated from the CDM v5.4 field-level CSV): `NOT EXISTS (...)` whose subquery equates the
  event table's person_id with another column; `x NOT IN (SELECT person_id FROM <event table> ...)`; `LEFT JOIN
  <event source> e ON e.person_id = ...` (or `USING (person_id)`) with `e.<column> IS NULL` as an AND-ed conjunct of
  the WHERE clause; `EXCEPT SELECT person_id FROM <event table>`. Columns are followed through CTEs, derived tables
  and renames. A test not tied to the event's person_id (a foreign-key check on visit_occurrence_id, an uncorrelated
  NOT EXISTS) is not about a person's events and is not reported.
* **Rate**: a division whose numerator is computed from a COUNT or SUM over a clinical event table and whose
  denominator is computed from a count over PERSON (or observation_period), followed through CTE projections.

Plain selections and counts of recorded events, with or without a calendar window, are not reported.

#### Examples

**Violation pattern:**

```sql
SELECT p.person_id
FROM person p
WHERE NOT EXISTS (
  SELECT 1 FROM drug_exposure de
  WHERE de.person_id = p.person_id
    AND de.drug_exposure_start_date >= DATE '2020-01-01'
    AND de.drug_exposure_start_date < DATE '2021-01-01');
```

**Correct pattern:**

```sql
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

<!-- extra-examples:start -->
**Warning variant: Prevalence computed over all persons instead of persons under observation**

```sql
SELECT COUNT(DISTINCT co.person_id) * 1.0 / (SELECT COUNT(*) FROM person) AS prevalence
FROM condition_occurrence co
WHERE co.condition_concept_id = 201826
  AND co.condition_start_date >= DATE '2020-01-01'
  AND co.condition_start_date < DATE '2021-01-01';
```

<!-- extra-examples:end -->

#### Suggested fix

ADD: a join to observation_period that restricts the population (for an absence test) or the events and the denominator (for a rate) to time under observation.

#### Sources

- [OMOP CDM v5.4 specification: OBSERVATION_PERIOD](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation_period)
- [The Book of OHDSI, ch. 4: The Common Data Model, OBSERVATION_PERIOD table](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#observationPeriod)

---

### 6. Observation Period Date Range Logic { #temporal-observation-period-date-range-logic }

**Rule ID:** `temporal.observation_period_date_range_logic`
**Severity:** ERROR

#### Intent

A date lies inside an observation period when observation_period_start_date <= date AND date <= observation_period_end_date. Swapping the BETWEEN bounds, flipping both comparison operators, or joining the two 'outside the period' tests with AND instead of OR yields the reverse: the date must be on or before the period start and on or after the period end. Because a period never starts after it ends, that holds for no row when either operator is strict, and only for one-day periods otherwise. The rule reports only this certain contradiction, on AND-ed predicates of one SELECT that compare the same column with both bounds of the same observation_period row; constant day offsets are taken into account. One-sided tests such as a washout, tests under OR or NOT, and interval tests that compare the bounds with two different columns are not flagged.

#### How it works

The rule reads the AND-ed predicates of each SELECT's WHERE and JOIN ON clauses and turns each into comparisons of the
form `value OP bound + offset`: `BETWEEN` gives two, a day difference compared with a constant (`x - start < 0`,
`DATEDIFF(day, start, x) < 0`) gives the comparison it states, and constant day offsets (`+ 365`, `INTERVAL '30
days'`, `DATEADD(day, ...)`) are kept. A bound is identified by lineage: the observation_period row it is read from,
through CTEs, derived tables and renames; an aggregated bound is not judged. The rule reports a value that is held on
or before `observation_period_start_date` and on or after `observation_period_end_date` of the same row: never
satisfiable when either operator is strict, satisfiable only for one-day periods otherwise (two equalities, which pin
a one-day period on purpose, are not reported). Predicates under OR or NOT, one-sided tests such as a washout,
interval tests against two different columns, and month or year offsets are not reported.

#### Examples

**Violation pattern:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN observation_period op ON co.person_id = op.person_id
WHERE co.condition_start_date
      BETWEEN op.observation_period_end_date AND op.observation_period_start_date;
```

**Correct pattern:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN observation_period op ON co.person_id = op.person_id
WHERE co.condition_start_date
      BETWEEN op.observation_period_start_date AND op.observation_period_end_date;
```

<!-- extra-examples:start -->
**Error variant: Both comparison operators flipped: holds only when the period is a single day**

```sql
SELECT de.person_id
FROM drug_exposure de
JOIN observation_period op ON op.person_id = de.person_id
 AND op.observation_period_start_date >= de.drug_exposure_start_date
 AND op.observation_period_end_date <= de.drug_exposure_start_date;
```

**Error variant: 'Outside the period' tests joined with AND instead of OR: can never hold**

```sql
SELECT m.measurement_id
FROM measurement m
JOIN observation_period op ON op.person_id = m.person_id
WHERE m.measurement_date < op.observation_period_start_date
  AND m.measurement_date > op.observation_period_end_date;
```

<!-- extra-examples:end -->

#### Suggested fix

REPLACE: the reversed bounds WITH `<event_date> BETWEEN op.observation_period_start_date AND op.observation_period_end_date` (the same as `op.observation_period_start_date <= <event_date> AND <event_date> <= op.observation_period_end_date`). To find events outside the period, join the two tests with OR: `<event_date> < op.observation_period_start_date OR <event_date> > op.observation_period_end_date`.

#### Sources

- [OMOP CDM v5.4 specification: OBSERVATION_PERIOD](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation_period)
- [The Book of OHDSI, ch. 10: Defining Cohorts, Require 365 days of prior observation](https://ohdsi.github.io/TheBookOfOhdsi/Cohorts.html#require-365-days-of-prior-observation)
- [OHDSI Data Quality Dashboard: CDM v5.4 field-level checks and thresholds (CSV)](https://raw.githubusercontent.com/OHDSI/DataQualityDashboard/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)

---

### 7. DATE difference compared with INTERVAL { #temporal-date-diff-interval-comparison }

**Rule ID:** `temporal.date_diff_interval_comparison`
**Severity:** ERROR

#### Intent

PostgreSQL defines date - date as an integer number of days and timestamp - timestamp as an interval. There is no comparison operator between an integer and an interval, so a day difference compared with INTERVAL '30 days', with a cast or computed interval, or tested BETWEEN two intervals is rejected before any row is read; DuckDB rejects it in the same way. Operand types are settled by lineage: a column is followed through CTEs and derived tables to its CDM column or to the expression that produces it, so a timestamp that happens to be called first_date is not reported and a date that was renamed is. The rule runs under the postgres and duckdb dialects only; other engines type DATE − DATE differently.

#### How it works

For every comparison (`=`, `<>`, `<`, `<=`, `>`, `>=`) and `BETWEEN`, the rule types both sides. A
side is a day count when it contains a subtraction of two DATE operands and its overall type is integer; the other
side (or a BETWEEN bound) must be an interval: `INTERVAL '30 days'`, `'30 days'::interval`, `CAST(... AS INTERVAL)`,
`n * INTERVAL '1 day'`, `make_interval(...)`. DATE operands are CDM date columns (typed from the CDM v5.4 field-level
CSV), `CURRENT_DATE`, `DATE '...'`, casts to DATE, and `MIN` / `MAX` / `COALESCE` / `GREATEST` / `LEAST` over them.
Columns are followed through CTEs, derived tables, set operations and renames; a projected expression is typed with
the PostgreSQL operator rules (`date + interval` is a timestamp, `timestamp - timestamp` an interval), so a column's
name never decides its type. A column of a table outside the CDM is not judged. The rule runs only under the postgres
and duckdb dialects, where every flagged form was executed and rejected.

#### Examples

**Violation pattern:**

```sql
SELECT COUNT(*)
FROM drug_exposure a
JOIN drug_exposure b ON a.person_id = b.person_id AND a.drug_exposure_id <> b.drug_exposure_id
WHERE ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= INTERVAL '30 days';
```

**Correct pattern:**

```sql
SELECT COUNT(*)
FROM drug_exposure a
JOIN drug_exposure b ON a.person_id = b.person_id AND a.drug_exposure_id <> b.drug_exposure_id
WHERE ABS(a.drug_exposure_start_date - b.drug_exposure_start_date) <= 30;
```

<!-- extra-examples:start -->
**Error variant: Day difference from CURRENT_DATE tested BETWEEN two intervals**

```sql
SELECT COUNT(*)
FROM drug_exposure
WHERE (CURRENT_DATE - drug_exposure_start_date) BETWEEN INTERVAL '0 days' AND INTERVAL '30 days';
```

<!-- extra-examples:end -->

#### Suggested fix

REPLACE: the INTERVAL WITH an integer number of days (e.g. `<= 30`), OR subtract *_datetime / TIMESTAMP values so the difference is an interval.

#### Sources

- [PostgreSQL documentation: Date/Time Functions and Operators, Date/Time Operators](https://www.postgresql.org/docs/current/functions-datetime.html#OPERATORS-DATETIME-TABLE)
- [DuckDB documentation: Date Functions, Date Operators](https://duckdb.org/docs/stable/sql/functions/date.html)

---

## Data Quality Rules

These rules validate data quality, schema compliance, and proper handling of edge cases.

---

### 1. Column Type Validation (SCHEMA Layer) { #data-quality-column-type-validation }

**Rule ID:** `data_quality.column_type_validation`
**Severity:** ERROR on PostgreSQL; ERROR or WARNING on other dialects (see below)

#### Intent

OMOP columns have declared types: `*_id` and `*_concept_id` columns are integers, `*_source_value` columns and vocabulary identifiers such as `domain_id` are varchar, `*_date` columns are dates. PostgreSQL gives a quoted literal the type of the column it is compared with, so `year_of_birth = '1980'` and `condition_start_date >= '2020-01-01'` are valid. It has no operator comparing a string with a number, or a date with a number, and no implicit cast between those families, so `domain_id = 19` and `person_id = person_source_value` fail at plan time. DuckDB rejects a date compared with a number, a literal that does not convert to the column's type, and `<`, `>`, `BETWEEN` between varchar and a number; for `=`, `<>` and `IN` it casts the varchar side, as SQL Server and MySQL do, and fails only when a stored value does not convert. `value_as_string` compared with `<` or `>` against a quoted number runs everywhere and orders text, so `'9' > '100'`.

#### How it works

The rule checks `=`, `<>`, `<`, `<=`, `>`, `>=`, `IN (...)`, `IN (SELECT <one column> ...)` and `BETWEEN` wherever they appear (WHERE, JOIN ... ON, HAVING, CASE, the select list). Columns are resolved through their lineage, through CTEs and derived tables. It flags:

- a varchar column against a numeric literal (`domain_id = 19`, `value_as_string > 100`);
- a date column against a numeric literal or a quoted word that is not a date (`death_date = 'unknown'`);
- a numeric column against a quoted literal that is not a number (`year_of_birth = 'unknown'`). For an integer column, what converts depends on the engine: PostgreSQL's integer input takes an optional sign, digits with underscores and `0x` / `0o` / `0b` prefixes, and rejects `'1e3'` and `'1.5'`; DuckDB parses the string as a number and rounds it, so it accepts `'1e3'` and `'1.5'` but rejects `'0o17'` and a signed `'-0x1F'`. Both syntaxes were checked against the engines, and `data_quality.concept_id_string_comparison` uses the same definition;
- two columns of different type families (`person_id = person_source_value`, `measurement_event_id = specimen_source_id`);
- WARNING: `value_as_string` (a result value stored as text) ordered against a quoted number (`value_as_string > '100'`). Other varchar columns, such as code ranges on `concept_code`, are ordered as text on purpose and are not flagged.

Severity: ERROR on PostgreSQL for the first four; on DuckDB ERROR for the date and unconvertible-literal cases and for `<` / `>` / BETWEEN between varchar and a number; WARNING otherwise (the outcome depends on the data). Not flagged: quoted literals on `*_concept_id` columns (data_quality.concept_id_string_comparison), date formats (data_quality.non_standard_date_literal_format), `note_nlp.offset` (data_quality.note_nlp_offset_is_character_position), template parameters (`'@start_date'`), explicit casts, computed columns and T-SQL `#temp` tables.

`relationship.is_hierarchical` and `relationship.defines_ancestry` are left to `domain_specific.vocabulary_relationship_boolean_comparison`, which knows their value set. This rule owns a number or boolean compared with a varchar code column such as `concept.standard_concept` or `cost.cost_domain_id`, and `note_nlp.term_modifiers` compared with a concept id.

#### Examples

**Violation patterns:**

```sql
SELECT co.condition_concept_id
FROM condition_occurrence co
JOIN person p ON co.person_id = p.person_source_value;
```

```sql
SELECT concept_id, concept_name
FROM concept
WHERE domain_id = 19;
```

```sql
SELECT person_id
FROM person
WHERE year_of_birth = 'unknown';
```

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_start_date >= 2020;
```

```sql
SELECT person_id
FROM observation
WHERE value_as_string > '100';
```

**Correct patterns:**

```sql
SELECT co.condition_concept_id
FROM condition_occurrence co
JOIN person p ON co.person_id = p.person_id;
```

```sql
SELECT person_id
FROM person
WHERE year_of_birth = '1980';
```

```sql
SELECT person_id
FROM observation
WHERE value_as_number > 100;
```

#### Suggested fix

Replace the literal with one of the column's type (quoted text for varchar, a number for numeric columns, `DATE 'YYYY-MM-DD'` for dates), or the column with the one of the matching type (`person_id`, not `person_source_value`; `value_as_number`, not `value_as_string`; `specimen_id`, not `specimen_source_id`). Cast explicitly, and guard against non-numeric rows, when a cross-type comparison is intended.

#### Sources

- [PostgreSQL documentation: Type Conversion, Operators](https://www.postgresql.org/docs/current/typeconv-oper.html)
- [The Book of OHDSI, ch. 9: SQL and R, Implicit casts](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#implicit-casts)
- [OMOP CDM v5.4 specification: PERSON](https://ohdsi.github.io/CommonDataModel/cdm54.html#person)
- [OMOP CDM v5.4 specification: OBSERVATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation)
- [OMOP CDM v5.4 specification: SPECIMEN](https://ohdsi.github.io/CommonDataModel/cdm54.html#specimen)

---

### 2. Concept ID String Comparison { #data-quality-concept-id-string-comparison }

**Rule ID:** `data_quality.concept_id_string_comparison`
**Severity:** WARNING (a value that is not an integer: ERROR on PostgreSQL and DuckDB)

#### Intent

Every column that holds a concept_id is an INTEGER in CDM v5.4. A quoted literal compared with it depends on coercion: PostgreSQL gives the untyped literal the column's type and DuckDB casts it, so `gender_concept_id = '8532'` runs there, but BigQuery does not coerce string literals to numbers ("String literals don't coerce to numeric types"). A literal that is not an integer (`'FEMALE'`, `''`) cannot be converted: PostgreSQL ("invalid input syntax for type integer") and DuckDB ("Could not convert string") reject the query.

#### How it works

The checked columns are `CONCEPT.concept_id` and every CDM v5.4 column with a foreign key to `CONCEPT` (generated from the field-level CSV, so `concept_id_1` and `domain_concept_id_1` are included), when the column's lineage is that CDM column. A value recast to text in a CTE and columns of non-CDM tables are not judged, nor are unrendered SqlRender parameters (`'@gender_concept_ids'`). Comparisons are `=`, `<>`, `<`, `<=`, `>`, `>=`, `IN`, `NOT IN` and `BETWEEN`, in any clause.

- A quoted integer: WARNING (portability). What counts as an integer is the dialect's integer input, shared with `data_quality.column_type_validation`. On PostgreSQL that is an optional sign and surrounding spaces, digits with underscores, and `0x` / `0o` / `0b` prefixes, so `'8_507'` and `'0x1F'` convert. DuckDB parses the string as a number and rounds it, so `'1e3'` and `'1.5'` convert there too, while `'0o17'` and `'-0x1F'` do not.
- A literal that is not an integer: ERROR on PostgreSQL and DuckDB, WARNING on other dialects.

#### Examples

**Violation pattern (ERROR):**

```sql
SELECT person_id
FROM person
WHERE gender_concept_id = 'FEMALE';
```

**Warning patterns:**

```sql
SELECT person_id
FROM person
WHERE gender_concept_id = '8532';
```

```sql
SELECT person_id
FROM person
WHERE gender_concept_id IN ('8532', '8507');
```

**Correct patterns:**

```sql
SELECT person_id
FROM person
WHERE gender_concept_id = 8532;
```

```sql
SELECT person_id
FROM person
WHERE gender_concept_id IN (8532, 8507);
```

```sql
SELECT person_id
FROM person
WHERE CAST(gender_concept_id AS VARCHAR) = '8532';
```

#### Suggested fix

Write the value as an integer literal (`gender_concept_id = 8532`); the finding carries a REPLACE patch for a single quoted integer. To select a concept by name, join `concept` and filter its name or code.

#### Sources

- [OMOP CDM v5.4 specification: PERSON](https://ohdsi.github.io/CommonDataModel/cdm54.html#person)
- [BigQuery documentation: Conversion rules, Literal coercion](https://cloud.google.com/bigquery/docs/reference/standard-sql/conversion_rules#literal_coercion)
- [PostgreSQL documentation: Type Conversion, Operators](https://www.postgresql.org/docs/current/typeconv-oper.html)
- [The Book of OHDSI, ch. 9: SQL and R, Implicit casts](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#implicit-casts)

---

### 3. Episode Requires Concept Filter { #data-quality-episode-requires-concept-filter }

**Rule ID:** `data_quality.episode_requires_concept_filter`
**Severity:** WARNING

#### Intent

CDM v5.4 `episode` holds every kind of episode in one table: disease episodes, treatment regimens, disease extent and so on, told apart by `episode_concept_id`. A query that reads episode rows without restricting that column, and without returning it, mixes all episode types in one result, so "episodes per person" adds disease phases to treatment lines. The CDM specification defines the column but does not require a filter on it, and whether the mix is wrong depends on the data and the question, so the finding is a WARNING.

#### How it works

For each `episode` instance in a SELECT, the rule fires when `episode_concept_id` is neither restricted nor shown.

- **Restricted:** any non-negated `WHERE` / `ON` / `HAVING` predicate on the column (`=`, `IN`, `BETWEEN`, a join to a concept-set table), a filter on a `concept` row joined to it, or a restriction downstream on a projected copy (followed through CTEs and derived tables). `<>`, `NOT IN` and `IS NULL` / `IS NOT NULL` let every type through and do not count.
- **Shown:** the SELECT projects or groups by the column, or projects a column of the joined `concept` row. `SELECT *` does not count.

For each `episode_event` instance, the rule fires when `episode_id` is not used at all: the linked events then cannot be tied to an episode type. When `episode_event` is joined to an unrestricted `episode`, only the `episode` is reported.

Not flagged: a lookup of known rows by primary key (`episode_id = 42`, `episode_id IN (...)`), a read that analyses nothing (`episode` is the only source, there is no `WHERE`, `GROUP BY` or `HAVING`, and the SELECT is a `LIMIT` peek or a whole-table `COUNT(*)`), constant-false schema probes (`WHERE 0 = 1`), and DML / DDL targets. Any other aggregate mixes the types like a row-level read and is reported: `SELECT person_id, COUNT(*) FROM episode GROUP BY person_id` is the "episodes per person" case above. Strict mode does not escalate this rule.

#### Examples

**Violation patterns:**

```sql
SELECT e.person_id, e.episode_start_date
FROM episode e
WHERE e.episode_start_date >= DATE '2020-01-01';
```

```sql
SELECT e.person_id, COUNT(*) AS n_episodes
FROM episode e
GROUP BY e.person_id;
```

```sql
-- procedures linked to episodes of every type
SELECT po.person_id, po.procedure_concept_id
FROM episode_event ee
JOIN procedure_occurrence po ON ee.event_id = po.procedure_occurrence_id
WHERE ee.episode_event_field_concept_id = 1147082;
```

**Correct patterns:**

```sql
SELECT e.person_id, e.episode_start_date
FROM episode e
WHERE e.episode_concept_id = 32533
  AND e.episode_start_date >= DATE '2020-01-01';
```

```sql
-- every type is wanted, and the type is returned with each row
SELECT e.person_id, e.episode_concept_id, e.episode_start_date
FROM episode e
WHERE e.episode_start_date >= DATE '2020-01-01';
```

```sql
SELECT po.person_id, po.procedure_concept_id
FROM episode_event ee
JOIN episode e ON e.episode_id = ee.episode_id
JOIN procedure_occurrence po ON ee.event_id = po.procedure_occurrence_id
WHERE ee.episode_event_field_concept_id = 1147082
  AND e.episode_concept_id = 32531;  -- Treatment Regimen
```

```sql
SELECT e.episode_start_date
FROM episode e
WHERE e.episode_id = 42;
```

#### Suggested fix

Add `episode_concept_id = <id>` (or `IN (…)`) to select one episode type, or add `episode_concept_id` to the SELECT list or GROUP BY if every type is wanted.

#### Sources

- [OMOP CDM v5.4 specification: EPISODE](https://ohdsi.github.io/CommonDataModel/cdm54.html#episode)
- [OMOP CDM v5.4 specification: EPISODE_EVENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#episode_event)

---

### 4. Fact Relationship Requires Relationship Concept Filter { #data-quality-fact-relationship-requires-relationship-concept-filter }

**Rule ID:** `data_quality.fact_relationship_requires_relationship_concept_filter`
**Severity:** WARNING

#### Intent

CDM v5.4 `fact_relationship` records every kind of link between two facts in the same five columns: person to person, care-site hierarchy, drug indication, measurement parent and child, and so on. Each link is stored twice, once per direction, and `relationship_concept_id` says which kind and which direction a row is. A query that reads `fact_id_1` / `fact_id_2` without restricting that column, and without returning it, mixes all of them: "the facts related to this measurement" returns its parent, its children and anything else linked to it. The CDM specification defines the column but does not require a filter on it, so the finding is a WARNING.

#### How it works

For each `fact_relationship` instance in a SELECT, the rule fires when `relationship_concept_id` is neither restricted nor shown.

- **Restricted:** any non-negated `WHERE` / `ON` / `HAVING` predicate on the column (`=`, `IN`, `BETWEEN`, a join to a concept-set table), a filter on a `concept` row joined to it, or a restriction downstream on a projected copy (followed through CTEs and derived tables). `<>`, `NOT IN` and `IS NULL` / `IS NOT NULL` let every type through and do not count.
- **Shown:** the SELECT projects or groups by the column, or projects a column of the joined `concept` row. `SELECT *` does not count.

Pinning `domain_concept_id_1` / `domain_concept_id_2` does not count: two domains can be linked by several relationship types.

Not flagged: a read that analyses nothing (`fact_relationship` is the only source, there is no `WHERE`, `GROUP BY` or `HAVING`, and the SELECT is a `LIMIT` peek or a whole-table `COUNT(*)`), constant-false schema probes (`WHERE FALSE`, `WHERE (0 = 1)`), and DML / DDL targets. `COUNT(*) ... GROUP BY relationship_concept_id` is silent because it shows the type. Any other aggregate (links per fact, the number of links of one fact, an `EXISTS` probe) mixes the relationship types like a row-level read and is reported. Strict mode does not escalate this rule.

#### Examples

**Violation patterns:**

```sql
SELECT fr.fact_id_2 AS child_measurement_id
FROM fact_relationship fr
WHERE fr.domain_concept_id_1 = 21
  AND fr.fact_id_1 = 100;
```

```sql
SELECT *
FROM fact_relationship
WHERE fact_id_1 = 100;
```

**Correct patterns:**

```sql
SELECT fr.fact_id_2 AS child_measurement_id
FROM fact_relationship fr
WHERE fr.domain_concept_id_1 = 21
  AND fr.fact_id_1 = 100
  AND fr.relationship_concept_id = 581436;
```

```sql
-- every type is wanted, and the type is returned with each row
SELECT fr.fact_id_2, fr.relationship_concept_id
FROM fact_relationship fr
WHERE fr.fact_id_1 = 100;
```

```sql
SELECT fr.fact_id_1, fr.fact_id_2
FROM fact_relationship fr
JOIN concept c ON fr.relationship_concept_id = c.concept_id
WHERE c.concept_name = 'Systolic to diastolic blood pressure measurement';
```

#### Suggested fix

Add `relationship_concept_id = <id>` (or `IN (…)`) to select one relationship type, or add `relationship_concept_id` to the SELECT list or GROUP BY if every type is wanted.

#### Sources

- [OMOP CDM v5.4 specification: FACT_RELATIONSHIP](https://ohdsi.github.io/CommonDataModel/cdm54.html#fact_relationship)

---

### 5. Negative Concept ID Validation { #data-quality-negative-concept-id-validation }

**Rule ID:** `data_quality.negative_concept_id_validation`
**Severity:** ERROR (dead branches and dead IN members: WARNING)

#### Intent

The Standardized Vocabularies assign concept_ids from 0 upwards: values "will be reserved from 0 to 2,000,000,000", and ids above that range are for local use. No CDM column that holds a concept_id contains a negative value, so a filter that only a negative id satisfies matches no row.

#### How it works

The checked columns are `CONCEPT.concept_id` and every CDM v5.4 column with a foreign key to `CONCEPT` (generated from the field-level CSV, including `ancestor_concept_id`, `relationship_concept_id`, `concept_id_1`). A column is checked when its lineage, through CTEs, derived tables and casts, is one of them; a sentinel computed in the query (`COALESCE(unit_concept_id, -1)`) and columns of non-CDM tables are not. Flagged predicates are `= -n`, `IN` lists of negative values only, `< 0`, `<= -n`, and `BETWEEN` with a negative upper bound, with literals possibly parenthesised or cast.

- ERROR when the predicate restricts the rows the statement returns: an AND-conjunct of WHERE, HAVING or an inner join's ON, and, inside a subquery, only when that subquery's IN / EXISTS / comparison restricts the enclosing query in turn.
- WARNING when it does not: in an outer join's ON clause it attaches no row of the joined side and the other side's rows are kept; inside NOT EXISTS / NOT IN the empty subquery removes no outer row; under OR, in a CASE or in the select list it is a dead branch. A negative value inside an IN list that also holds valid ids is a WARNING too.
- Not flagged: predicates every row satisfies (`> -1`, `<> -1`, `NOT IN (-1)`) and a predicate directly under NOT.

#### Examples

**Violation patterns:**

```sql
SELECT *
FROM condition_occurrence
WHERE condition_concept_id = -1;
```

```sql
SELECT *
FROM concept_ancestor
WHERE ancestor_concept_id < 0;
```

**Warning patterns:**

```sql
SELECT *
FROM condition_occurrence
WHERE condition_concept_id IN (201820, -1);
```

```sql
SELECT co.person_id, c.concept_name
FROM condition_occurrence co
LEFT JOIN concept c ON c.concept_id = co.condition_concept_id AND c.concept_id = -1;
```

```sql
SELECT p.person_id
FROM person p
WHERE NOT EXISTS (SELECT 1 FROM condition_occurrence co WHERE co.person_id = p.person_id AND co.condition_concept_id = -1);
```

**Correct patterns:**

```sql
SELECT *
FROM condition_occurrence
WHERE condition_concept_id = 201820;
```

```sql
SELECT *
FROM condition_occurrence
WHERE condition_concept_id = 0;
```

```sql
SELECT *
FROM measurement
WHERE COALESCE(unit_concept_id, -1) = -1;
```

#### Suggested fix

Replace the negative literal with the intended concept_id (0 for records without a matching concept). If -1 stands for a missing value, test `IS NULL` or `= 0`.

#### Sources

- [OMOP CDM: Data Model Conventions, Concepts](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concepts)

---

### 6. Note NLP Offset is Character Position { #data-quality-note-nlp-offset-is-character-position }

**Rule ID:** `data_quality.note_nlp_offset_is_character_position`
**Severity:** WARNING

#### Intent

CDM v5.4 stores `note_nlp.offset` ("Character offset of the extracted term in the input note") as `varchar(50)`. Its content is a number, but every ordering operation on the raw column is a string ordering: `'9' > '100'` is true, `ORDER BY offset` puts 100 before 9, and `MAX(offset)` returns `'99'` over `'100'`. These queries run without error and silently return wrong rows.

#### How it works

Flagged on an uncast `note_nlp.offset`:

- `<`, `<=`, `>`, `>=` against a string literal or against another uncast offset (for example "term A appears before term B in the same note");
- `BETWEEN` with string-literal bounds;
- `ORDER BY offset`, including a window `ORDER BY`;
- `MIN(offset)` / `MAX(offset)`, and `GREATEST` / `LEAST` over offsets.

The column is recognised by its lineage: qualified or not, through CTEs, derived tables and renames (`"offset" AS pos`). A CTE or derived table that casts it (`CAST("offset" AS INTEGER) AS "offset"`) yields an integer and is not flagged.

Not flagged:

- equality and `IN`, because string equality of positions is exact;
- comparison with a numeric literal, or arithmetic: PostgreSQL and DuckDB reject `varchar > integer`, so the query fails rather than returning wrong rows (data_quality.column_type_validation reports it);
- any use inside `CAST`, `TRY_CAST`, `CONVERT` or `::int`.

#### Examples

**Violation patterns:**

```sql
SELECT note_nlp_id
FROM note_nlp
WHERE "offset" > '100';
```

```sql
-- term A before term B in the same note, compared as text
SELECT a.note_id
FROM note_nlp a
JOIN note_nlp b ON a.note_id = b.note_id
WHERE a."offset" < b."offset";
```

```sql
SELECT note_id, lexical_variant
FROM note_nlp
ORDER BY "offset";
```

**Correct patterns:**

```sql
SELECT note_nlp_id
FROM note_nlp
WHERE CAST("offset" AS INTEGER) > 100;
```

```sql
SELECT a.note_id
FROM note_nlp a
JOIN note_nlp b ON a.note_id = b.note_id
WHERE CAST(a."offset" AS INTEGER) < CAST(b."offset" AS INTEGER);
```

#### Suggested fix

Wrap `note_nlp.offset` in `CAST(offset AS INTEGER)` wherever it is ordered, ranged or aggregated.

#### Sources

- [OMOP CDM v5.4 specification: NOTE_NLP](https://ohdsi.github.io/CommonDataModel/cdm54.html#note_nlp)
- [The Book of OHDSI, ch. 9: SQL and R, Implicit casts](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#implicit-casts)

---

### 7. Note NLP nlp_date for Temporal Filtering { #data-quality-note-nlp-nlp-date-for-temporal-filtering }

**Rule ID:** `data_quality.note_nlp_nlp_date_for_temporal_filtering`
**Severity:** WARNING

#### Intent

CDM v5.4 defines `note_nlp.nlp_date` / `nlp_datetime` as "the date (and time) of the note processing": when the NLP pipeline ran, not when the note was written. The clinical date lives on `note.note_date` / `note.note_datetime` ("the date the note was recorded"), reached through `note_nlp.note_id = note.note_id`. Filtering NLP findings to a time window with `nlp_date` selects them by processing batch, so re-running the pipeline changes the result.

#### How it works

The rule fires on a time window over `nlp_date` / `nlp_datetime` in `WHERE`, `JOIN ... ON` or `HAVING`: a range comparison (`<`, `<=`, `>`, `>=`, `BETWEEN`) on it, equality on it, or equality on a truncated value (`EXTRACT(YEAR FROM nlp_date) = 2023`, `YEAR(nlp_date) = 2023`). The column is followed through casts, arithmetic, CTEs and derived tables. It does not fire when:

- the same SELECT also filters `note.note_date` / `note_datetime` (the clinical window is applied; an extra `nlp_date` filter picks a processing run on purpose);
- the comparison is equality on the date in a SELECT that also restricts `nlp_system`, which picks one processing run (`nlp_system = 'cTAKES 4.0' AND nlp_date = DATE '2024-03-01'`), or an aggregate over the date (`HAVING MAX(nlp_date) > ...`);
- the other side reads `nlp_date` / `note_date` or is a subquery: the latest run (`nlp_date = (SELECT MAX(nlp_date) FROM note_nlp)`), a processing-lag audit (`nlp_date >= n.note_date`), or an incremental-load watermark.

#### Examples

**Violation patterns:**

```sql
SELECT note_nlp_id
FROM note_nlp
WHERE nlp_date >= DATE '2023-01-01';
```

```sql
SELECT note_nlp_id
FROM note_nlp
WHERE EXTRACT(YEAR FROM nlp_date) = 2023;
```

**Correct patterns:**

```sql
SELECT nnlp.note_nlp_id
FROM note_nlp nnlp
JOIN note n ON nnlp.note_id = n.note_id
WHERE n.note_date >= DATE '2023-01-01';
```

```sql
SELECT note_nlp_id
FROM note_nlp
WHERE nlp_date = (SELECT MAX(nlp_date) FROM note_nlp);
```

#### Suggested fix

Replace `note_nlp.nlp_date <op> <date>` with `note.note_date <op> <date>` after `JOIN note ON note_nlp.note_id = note.note_id`. `nlp_date` is processing time, not clinical event time.

#### Sources

- [OMOP CDM v5.4 specification: NOTE_NLP](https://ohdsi.github.io/CommonDataModel/cdm54.html#note_nlp)
- [OMOP CDM v5.4 specification: NOTE](https://ohdsi.github.io/CommonDataModel/cdm54.html#note)

---

### 8. OMOP Schema Validation { #data-quality-schema-validation }

**Rule ID:** `data_quality.schema_validation`
**Severity:** ERROR (columns missing from COHORT / COHORT_DEFINITION: WARNING)

#### Intent

Every column a query reads from a CDM table must exist in the OMOP CDM v5.4 specification (the field-level CSV: 39 tables). A column that does not exist makes the statement fail on any conformant CDM. Names that CDM v5.4 renamed (`admitting_source_concept_id` became `admitted_from_concept_id`, `visit_detail_parent_id` became `parent_visit_detail_id`), names that only some event tables carry (`procedure_occurrence` has `procedure_date`, not `procedure_start_date`), and columns that live on a neighbouring table (`episode_event` has no `person_id`; `episode` does) are reported with the v5.4 name. The schema is closed: a table that is neither in the specification nor defined in the same input is an error. This is a declared policy, like the write protection. A database may hold results or staging tables FastSSV cannot see, but a query that reads them cannot be validated against the CDM, and SQL that invents a table (`condition`, `drug_concept`) fails.

#### How it works

- **Columns.** Each reference is resolved the way the engine resolves it: a qualified name through the alias of its own SELECT, then enclosing SELECTs (correlated subqueries), and through CTEs and derived tables including `SELECT *`; an unqualified name against every source of its SELECT and of the enclosing ones. It is reported only when every source that could supply the name is known not to. When several CDM tables are in scope and none has the column, the finding names all of them. `JOIN ... USING (col)` names must exist on both sides, and `INSERT INTO <cdm table> (col, ...)` lists are checked.
- **COHORT and COHORT_DEFINITION.** The field-level CSV places these two tables in a separate schema (`databaseSchema` = cohort), not with the CDM or vocabulary tables, and analytic tools create their own tables under those names with columns of their own. A column missing from their CDM v5.4 definition (`cohort_definition.cohort_name`, `cohort.row_id`) is therefore a WARNING saying the table may be a tool's own and the column was not checked. The set of such tables is read from the CSV's `databaseSchema` column.
- **Output aliases** of the same SELECT are not base columns: `SELECT measurement_date AS measurement_start_date ... GROUP BY measurement_start_date` is not flagged.
- **Tables.** A table missing from the specification and not defined in the same input is an ERROR. When its name is one or two edits from a CDM table (one edit for names under seven letters) and it stands where a CDM table is expected (unqualified, or in the schema the statement reads its CDM tables from), the finding names that table; a table removed in v5.4 (`attribute_definition`) is reported as removed. To use a results or scratch table, define it in the same input (CTE, `CREATE TABLE`, `SELECT ... INTO`, temp table).
- **Not checked:** CTE names, derived tables, tables created earlier in the same batch, T-SQL `#temp` tables, the targets of CREATE / DROP / ALTER / ANALYZE / TRUNCATE, and system catalogues. A REPLACE patch is offered only when one replacement name is known, and never for a write target.

#### Examples

**Violation patterns:**

```sql
SELECT person_id, procedure_concept_id
FROM procedure_occurrence
WHERE procedure_start_date >= DATE '2020-01-01';
```

```sql
SELECT visit_occurrence_id
FROM visit_occurrence
WHERE admitting_source_concept_id = 8870;
```

```sql
SELECT ee.event_id, p.year_of_birth
FROM episode_event ee
JOIN person p ON ee.person_id = p.person_id;
```

```sql
SELECT person_id
FROM condition_occurence
WHERE condition_concept_id = 201826;
```

```sql
SELECT attribute_name
FROM attribute_definition;
```

```sql
SELECT analysis_id, count_value
FROM results.analysis_results
WHERE analysis_id = 1;
```

**Warning pattern (column outside the CDM definition of a cohort-schema table, not checked):**

```sql
SELECT cd.cohort_definition_id, cd.cohort_name
FROM cohort_definition cd;
```

**Correct patterns:**

```sql
SELECT person_id, procedure_concept_id
FROM procedure_occurrence
WHERE procedure_date >= DATE '2020-01-01';
```

```sql
SELECT ee.event_id, p.year_of_birth
FROM episode_event ee
JOIN episode e ON ee.episode_id = e.episode_id
JOIN person p ON e.person_id = p.person_id;
```

```sql
SELECT measurement_date AS measurement_start_date, COUNT(*)
FROM measurement
GROUP BY measurement_start_date;
```

#### Suggested fix

Replace the nonexistent column or table with its OMOP CDM v5.4 name; the finding names it when it is known (a v5.4 rename, `<event>_date` for `<event>_start_date`, or the table that holds the column). For a table outside the CDM, no change is needed if it exists in the database.

#### Sources

- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [OHDSI Data Quality Dashboard: CDM v5.4 field-level checks and thresholds (CSV)](https://raw.githubusercontent.com/OHDSI/DataQualityDashboard/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [OMOP CDM v5.4 specification: PROCEDURE_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#procedure_occurrence)
- [OMOP CDM v5.4 specification: EPISODE_EVENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#episode_event)
- [OMOP CDM: Changes from v5.3 to v5.4, VISIT_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54Changes.html#visit_occurrence)
- [OMOP CDM: Changes from v5.3 to v5.4, VISIT_DETAIL](https://ohdsi.github.io/CommonDataModel/cdm54Changes.html#visit_detail)
- [OMOP CDM: Changes from v5.3 to v5.4, Summary](https://ohdsi.github.io/CommonDataModel/cdm54Changes.html#summary)

---

### 9. UNION vs UNION ALL for Clinical Events { #data-quality-union-vs-union-all-clinical-events }

**Rule ID:** `data_quality.union_vs_union_all_clinical_events`
**Severity:** WARNING

#### Intent

`UNION` removes duplicate rows. Two clinical event rows that look identical on the selected columns can still be separate events (two ER visits on the same day, two prescriptions on the same day), because the primary key that distinguishes them is not selected. `UNION` then drops events silently and event counts come out low. `UNION ALL` keeps every row.

#### How it works

The rule warns on a `UNION` (without `ALL`) where some arm returns rows taken one-to-one from a person-level CDM table (every CDM v5.4 table that carries `person_id`, including the era tables, `death` and `observation_period`), directly or through CTEs and derived tables. Arms that are each a single aggregate row or a grouped result over that data, but carry no label column that tells them apart, get their own message: two arms with equal values (equal counts) collapse into one row. Not flagged, because de-duplication cannot change the result or is what the query asks for:

- every arm projects only foreign-key columns by lineage (`person_id`, `visit_occurrence_id`, `*_concept_id`, ...), whatever their output names: the rows name other entities, so duplicates are the same entity (a cohort or concept-set union);
- the UNION feeds only set-style consumers: an enclosing `SELECT DISTINCT`, `IN (…)` / `EXISTS (…)`, or a SELECT whose outputs are `GROUP BY` keys and `COUNT(DISTINCT …)` / `MIN` / `MAX` aggregates;
- every arm is `SELECT DISTINCT`;
- the arms carry literal label columns whose values differ between arms, so no row of one arm can equal a row of another, and every arm that returns event-level rows is itself unique (GROUP BY over its outputs, DISTINCT, or a single aggregate row);
- no arm reads person-level CDM data (vocabulary unions).

#### Examples

**Violation patterns:**

```sql
SELECT person_id, condition_start_date AS event_date
FROM condition_occurrence
UNION
SELECT person_id, drug_exposure_start_date
FROM drug_exposure;
```

```sql
SELECT person_id, condition_start_date AS event_date
FROM condition_occurrence
WHERE condition_concept_id = 201826
UNION
SELECT person_id, drug_exposure_start_date
FROM drug_exposure
WHERE drug_concept_id = 1125315;
```

```sql
-- Equal counts collapse into one unlabelled row
SELECT COUNT(*) AS n FROM condition_occurrence
UNION
SELECT COUNT(*) FROM drug_exposure;
```

**Correct patterns:**

```sql
SELECT person_id, condition_start_date AS event_date
FROM condition_occurrence
UNION ALL
SELECT person_id, drug_exposure_start_date
FROM drug_exposure;
```

```sql
SELECT person_id, condition_start_date AS event_date
FROM condition_occurrence
WHERE condition_concept_id = 201826
UNION ALL
SELECT person_id, drug_exposure_start_date
FROM drug_exposure
WHERE drug_concept_id = 1125315;
```

```sql
-- Labelled single-row arms cannot collide
SELECT 'condition_occurrence' AS cdm_table, COUNT(*) AS n FROM condition_occurrence
UNION
SELECT 'drug_exposure', COUNT(*) FROM drug_exposure;
```

```sql
-- Only COUNT(DISTINCT ...) reads the UNION
SELECT COUNT(DISTINCT person_id)
FROM (
  SELECT person_id, condition_start_date AS d FROM condition_occurrence
  UNION
  SELECT person_id, drug_exposure_start_date FROM drug_exposure
) t;
```

#### Suggested fix

Replace `UNION` with `UNION ALL` when combining clinical event rows. If de-duplication is intended, make it explicit (`SELECT DISTINCT` arms or a labelled, grouped result).

#### Sources

- [PostgreSQL documentation: SELECT, UNION clause](https://www.postgresql.org/docs/current/sql-select.html#SQL-UNION)
- [OMOP CDM v5.4 specification: CONDITION_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#condition_occurrence)

---

### 10. Vocabulary Table Protection { #data-quality-vocabulary-table-protection }

**Rule ID:** `data_quality.vocabulary_table_protection`
**Severity:** ERROR (table in another schema: WARNING)

#### Intent

The Book of OHDSI (4.2.2) states that "the clinical Event and vocabulary tables are in the 'CDM' schema and are considered read-only to the end user or analytic tool", and the CDM v5.4 specification repeats that "the CDM and Vocabulary tables are all read-only". The vocabulary tables are reloaded from an Athena release and every later query reads them, so an analytical statement must not write to, restructure or remove them.

**Why an ERROR.** This is the rule set's one declared exception to reporting an ERROR only for a query that fails or certainly returns wrong rows. The write may succeed, and the statement is not wrong in itself. The reason for the ERROR is scope: FastSSV validates analytics SQL, and the Book of OHDSI treats the CDM schema, vocabulary tables included, as read-only to the end user, so a write to it is outside what an analytic query may do.

#### How it works

Protected tables are those of the specification's vocab schema except `source_to_concept_map`, which the specification recommends "for use in ETL processes to maintain local source codes": `concept`, `concept_ancestor`, `concept_class`, `concept_relationship`, `concept_synonym`, `domain`, `drug_strength`, `relationship`, `vocabulary`. Reported statements are DELETE, UPDATE, INSERT, MERGE, TRUNCATE, DROP TABLE, ALTER TABLE and CREATE OR REPLACE TABLE whose target is one of them.

- ERROR when the name reaches the vocabulary table: unqualified, in the default schema (`public`, `dbo`), or in a schema whose name contains `cdm`, `omop` or `vocab` and does not name a results, scratch or temp schema (the schema logic of `anti_patterns.destructive_operations_on_clinical_tables`).
- WARNING when the table is in another schema (`scratch.concept`, `vocab_scratch.concept`, `cdm_results.concept_ancestor`): it may be a copy that only shares the name.
- Not reported: tables only read (INSERT ... SELECT FROM concept), T-SQL `#temp` tables, names an earlier statement of the batch created unqualified or as TEMP, and INSERTs of local concepts (every concept id above 2,000,000,000, the range the conventions reserve for local use).

#### Examples

**Violation patterns:**

```sql
DELETE FROM concept
WHERE concept_id = 0;
```

```sql
ALTER TABLE concept DROP COLUMN concept_name;
```

**Warning pattern:**

```sql
TRUNCATE TABLE scratch.concept_ancestor;
```

**Correct patterns:**

```sql
SELECT concept_id
FROM concept
WHERE concept_id = 0;
```

```sql
INSERT INTO source_to_concept_map (source_code, source_concept_id, source_vocabulary_id, target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date)
VALUES ('X1', 0, 'MyLocal', 201826, 'SNOMED', DATE '1970-01-01', DATE '2099-12-31');
```

#### Suggested fix

Remove the statement and read the vocabulary instead. Load vocabulary changes from an Athena release, keep local source codes in `source_to_concept_map`, and give local concepts ids above 2,000,000,000.

#### Sources

- [The Book of OHDSI, ch. 4: The Common Data Model, General conventions of schemas](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#general-conventions-of-schemas)
- [OMOP CDM v5.4 specification: COHORT](https://ohdsi.github.io/CommonDataModel/cdm54.html#cohort)
- [OMOP CDM v5.4 specification: SOURCE_TO_CONCEPT_MAP](https://ohdsi.github.io/CommonDataModel/cdm54.html#source_to_concept_map)
- [OMOP CDM: Data Model Conventions, Concepts](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concepts)

---

### 11. Canonical Vocabulary String Value Validation { #data-quality-canonical-string-value-validation }

**Rule ID:** `data_quality.canonical_string_value_validation`
**Severity:** ERROR on PostgreSQL and DuckDB; WARNING on other dialects and for misspelt IDs

#### Intent

`vocabulary_id`, `domain_id` and `concept_class_id` are short case-sensitive IDs (Book of OHDSI, ch. 5). A literal that names one of them with the wrong case matches no row on an engine that compares strings case-sensitively, as PostgreSQL and DuckDB do. SQL Server and MySQL compare case-insensitively under their default collations, so there the filter works but is not portable. Vocabulary IDs generally omit the dashes of the vocabulary's name (`ICD-9-CM` is `ICD9CM`), so a dashed form of a known ID matches no row unless a site has loaded a local ID with that spelling.

#### How it works

The known IDs are every `vocabulary_id`, `domain_id` and `concept_class_id` of one Athena release (named in the generated module `_dq_vocab_ids.py`). The checked columns are the keys of `VOCABULARY`, `DOMAIN` and `CONCEPT_CLASS` and every CDM v5.4 column with a foreign key to one of them (`concept.vocabulary_id`, `concept.domain_id`, `concept.concept_class_id`, `source_to_concept_map.target_vocabulary_id`, `cost.cost_domain_id`). A column is checked when its lineage, through CTEs, derived tables and casts, is one of those columns; a value recomputed on the way (`LOWER(domain_id) AS domain_id`) is not. Comparisons are `=`, `<>`, `IN`, `NOT IN`, `BETWEEN` and `LIKE` without wildcards, in any clause.

- Wrong case of a known ID: ERROR on PostgreSQL and DuckDB, WARNING on other dialects.
- A known ID written with dashes, spaces or underscores it does not have: WARNING.
- The message follows the operator. With `=`, `IN` or `LIKE` the filter matches no row. With `<>`, `NOT IN` or a negated `=`, no row holds the value, so the filter excludes nothing and keeps every row, including the rows of the ID that was meant (`vocabulary_id <> 'snomed'` keeps the SNOMED concepts).
- Not flagged: `ILIKE`, comparisons on `LOWER()` / `UPPER()` of the column, and values that are not a variant of any known ID (local vocabularies).

#### Examples

**Violation patterns:**

```sql
SELECT concept_id
FROM concept
WHERE vocabulary_id = 'snomed';
```

```sql
SELECT concept_id
FROM concept
WHERE domain_id IN ('condition', 'Drug');
```

**Warning pattern:**

```sql
SELECT concept_id
FROM concept
WHERE vocabulary_id = 'ICD-10-CM';
```

**Correct patterns:**

```sql
SELECT concept_id
FROM concept
WHERE vocabulary_id = 'SNOMED';
```

```sql
SELECT concept_id
FROM concept
WHERE LOWER(vocabulary_id) = 'snomed';
```

```sql
SELECT concept_id
FROM concept
WHERE concept_class_id = 'Morph Abnormality';
```

#### Suggested fix

Replace the literal with the ID as the vocabulary spells it (`'snomed'` -> `'SNOMED'`, `'ICD-10-CM'` -> `'ICD10CM'`, `'condition'` -> `'Condition'`); the finding carries a REPLACE patch. To compare case-insensitively on purpose, use `ILIKE` or `LOWER()`.

#### Sources

- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Vocabularies](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#vocabularies)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Domains](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptDomains)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Concept classes](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#concept-classes)
- [The Book of OHDSI, ch. 9: SQL and R, Case sensitivity in string comparisons](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#case-sensitivity-in-string-comparisons)

---

### 12. Free-Text Column Misuse { #data-quality-free-text-column-misuse }

**Rule ID:** `data_quality.free_text_column_misuse`
**Severity:** WARNING

#### Intent

The CDM v5.4 specification keeps varchar columns that are neither source values nor keys: text copied from the source (`condition_occurrence.stop_reason`, `drug_exposure.sig`, `note.note_text`), identifiers issued outside OMOP (`drug_exposure.lot_number`, `provider.npi`, `specimen.specimen_source_id`), address parts (`location.state`, `location.zip`, "handled as strings of up to 9 characters") and NLP annotations (`note_nlp.term_modifiers`, every modifier concatenated in one string). None of them has a mapping into the vocabulary; the concept of a row is in its `*_concept_id` column. Matching one against `concept_name` or `concept_code` returns rows only where two strings happen to coincide, and casting one to a number fails on the first value that is not a number. The query runs either way, so the findings are warnings.

#### How it works

The column list is generated from the field-level CSV (varchar, not `*_source_value`, neither primary nor foreign key; `note_nlp.snippet` and `lexical_variant` are left to domain_specific.note_nlp_snippet_misuse). Columns are resolved through lineage, through CTEs, derived tables, casts and `LOWER` / `UPPER` / `TRIM`. The rule warns on:

- an equality, in any clause or through `IN (SELECT ...)`, between such a column and a text column of `concept` or `concept_synonym`;
- `CAST(<column> AS <numeric type>)` (`TRY_CAST` and `SAFE_CAST` are not flagged; `note_nlp.offset` is a character position and is cast on purpose);
- `note_nlp.term_modifiers` equated with another column.

Text use is not flagged: string equality, `LIKE`, `IS NULL`, joins to non-vocabulary tables. A comparison with a number or with an integer `concept_id` is a type error, reported by data_quality.column_type_validation.

#### Examples

**Violation patterns:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.stop_reason = c.concept_name;
```

```sql
SELECT de.person_id
FROM drug_exposure de
WHERE CAST(de.lot_number AS INTEGER) > 1000;
```

```sql
SELECT a.note_id
FROM note_nlp a
JOIN note_nlp b ON a.note_id = b.note_id AND a.term_modifiers = b.term_modifiers;
```

**Correct patterns:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id;
```

```sql
SELECT location_id
FROM location
WHERE zip = '10001';
```

```sql
SELECT note_id
FROM note_nlp
WHERE term_modifiers LIKE '%negation=false%';
```

#### Suggested fix

Remove the match against the vocabulary and join `concept` on the row's `*_concept_id` column; treat free-text columns as text (`LIKE`, `IS NULL`, string equality); guard any numeric conversion (`TRY_CAST`, or a regex test before the cast).

#### Sources

- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [OMOP CDM v5.4 specification: CONDITION_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#condition_occurrence)
- [OMOP CDM v5.4 specification: LOCATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#location)
- [OMOP CDM v5.4 specification: NOTE_NLP](https://ohdsi.github.io/CommonDataModel/cdm54.html#note_nlp)

---

### 13. Non-Standard Date Literal Format { #data-quality-non-standard-date-literal-format }

**Rule ID:** `data_quality.non_standard_date_literal_format`
**Severity:** WARNING (ERROR on DuckDB where DuckDB rejects the literal)

#### Intent

A string used as a date is read by the engine's date input routine, and only ISO 8601 `YYYY-MM-DD` reads the same everywhere. PostgreSQL reads `'1/8/1999'` as "January 8 in MDY mode; August 1 in DMY mode", and SQL Server reads slash dates by `SET DATEFORMAT`, so the same query selects different days on differently configured servers. PostgreSQL reads `'08-Jan-1999'` the same in every mode, but SQL Server and Oracle read month names in the session language, and a two-digit year is ambiguous (PostgreSQL rejects `'08-Jan-99'` in YMD mode). `'19990108'` (ISO 8601 basic format) reads the same on PostgreSQL ("in any mode") and SQL Server. DuckDB documents ISO 8601 `YYYY-MM-DD` input and reads every date year first: year, month and day separated by `-`, `/` or a space, with a one- or two-digit day (checked on DuckDB 2.0). It rejects the dot, month-name and `YYYYMMDD` forms and a slash date with a four-digit year (`'01/31/2020'`). A slash date whose last field has two digits is accepted when the fields form a valid year-first date, and read as that date: `'01/02/20'` is 0001-02-20.

#### How it works

A literal is judged when the statement uses it as a date: compared (`=`, `<>`, `<`, `>`, `BETWEEN`, `IN`) with a column whose lineage is a CDM date or datetime column, cast to a date type (`DATE '...'`, `CAST('...' AS DATE)`, `::date`, `CONVERT(DATE, '...')` without a style), or passed as a date to `DATEDIFF` / `DATEADD`. Formats reported: slash and dot dates (with or without a time part), `DD-MON-YYYY`, `DD-MON-YY`, and on DuckDB also `YYYYMMDD`.

- ERROR on DuckDB when DuckDB rejects the literal: the query fails.
- WARNING on DuckDB when it accepts the literal as a year-first date (`'01/02/20'`, read as 0001-02-20): the query runs, and only the rows are wrong when another date was meant. The message names the date DuckDB reads.
- WARNING on other dialects: the result depends on server settings or on the engine the query is moved to.
- Not flagged: ISO 8601 (`2020-01-31`, `2020-01-31 00:00:00`, `2020-01-31T00:00:00`), `YYYYMMDD` outside DuckDB, conversions with an explicit format (`TO_DATE('31/01/2020', 'DD/MM/YYYY')`, `CONVERT(DATE, '01/31/2020', 101)`), and text columns.

#### Examples

**Violation patterns:**

```sql
SELECT *
FROM condition_occurrence
WHERE condition_start_date = '01/31/2020';
```

```sql
SELECT person_id
FROM drug_exposure
WHERE drug_exposure_start_date >= '01-JAN-2020';
```

```sql
SELECT *
FROM visit_occurrence
WHERE visit_start_datetime >= '01/31/2020 00:00:00';
```

**Correct patterns:**

```sql
SELECT *
FROM condition_occurrence
WHERE condition_start_date = DATE '2020-01-31';
```

```sql
SELECT *
FROM condition_occurrence
WHERE condition_start_date = TO_DATE('31/01/2020', 'DD/MM/YYYY');
```

#### Suggested fix

Write the date in ISO 8601 (`DATE '2020-01-31'`); the finding carries a REPLACE patch for `DD-MON-YYYY` and `YYYYMMDD` literals, whose meaning is unambiguous. To keep another format, convert it with an explicit format string.

#### Sources

- [PostgreSQL documentation: Date/Time Types, Date input](https://www.postgresql.org/docs/current/datatype-datetime.html#DATATYPE-DATETIME-INPUT-DATES)
- [SQL Server documentation: SET DATEFORMAT](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-dateformat-transact-sql)
- [Oracle Database 19c Reference: NLS_DATE_LANGUAGE](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/NLS_DATE_LANGUAGE.html)
- [DuckDB documentation: Date Types](https://duckdb.org/docs/stable/sql/data_types/date)

---

### 14. Source Value Field Usage { #data-quality-source-value-field-usage }

**Rule ID:** `data_quality.source_value_field_usage`
**Severity:** WARNING

#### Intent

The CDM conventions define `*_source_value` columns as verbatim information from the source data, "typically used in ETL to map to CONCEPT_ID, and not to be used by any standard analytics"; there is no standardization of their content. One clinical idea arrives as different strings at different sites (`'250.00'`, `'25000'`, `'E11.9'`), so an analysis grouped by the source value splits a concept into site-specific buckets that cannot be compared across data sources. The paired `*_concept_id` holds the standard concept and is the column to group by.

#### How it works

The rule flags a SELECT that groups its rows by a CDM `*_source_value` column with a standardized counterpart in the same table: `condition_source_value` → `condition_concept_id`, `drug_source_value` → `drug_concept_id`, `value_source_value` → `value_as_concept_id`, and so on. Grouping covers `GROUP BY` (by expression, ordinal or output alias), `SELECT DISTINCT` and window `PARTITION BY`; the column may be wrapped in `CAST`, `UPPER` / `LOWER` / `TRIM` or `COALESCE` and is followed through CTEs and derived tables.

Reviewing mappings is what the Book of OHDSI recommends ("We can review both the source codes that map to the concepts used in the study, as well as those that don't"), so these are not flagged:

- the same SELECT also groups by the paired `*_concept_id` or `*_source_concept_id`;
- the paired `*_concept_id` of the table instance the source value is read from is restricted: to unmapped rows (`= 0`, `IS NULL`) or to the study's concepts (`= 201826`, `IN (...)`, a concept-set subquery), in the SELECT that reads the table or in the grouping SELECT. A filter on another instance of the same table does not count.

Source values with no standardized counterpart (`person_source_value`, `dose_unit_source_value`, …) and columns of non-CDM tables are never flagged.

#### Examples

**Violation patterns:**

```sql
SELECT condition_source_value, COUNT(*) AS n
FROM condition_occurrence
GROUP BY condition_source_value;
```

```sql
SELECT drug_source_value, COUNT(DISTINCT person_id) AS persons
FROM drug_exposure
GROUP BY drug_source_value;
```

**Correct patterns:**

```sql
SELECT co.condition_concept_id, c.concept_name, COUNT(*) AS n
FROM condition_occurrence co
JOIN concept c ON c.concept_id = co.condition_concept_id
GROUP BY co.condition_concept_id, c.concept_name;
```

```sql
-- mapping audit: source code next to the concepts it maps to
SELECT condition_source_value, condition_source_concept_id, condition_concept_id, COUNT(*) AS n
FROM condition_occurrence
GROUP BY condition_source_value, condition_source_concept_id, condition_concept_id;
```

```sql
-- unmapped source codes
SELECT condition_source_value, COUNT(*) AS n
FROM condition_occurrence
WHERE condition_concept_id = 0
GROUP BY condition_source_value;
```

#### Suggested fix

Replace `GROUP BY <col>_source_value` with `GROUP BY <col>_concept_id` and join `concept` on that id to recover the standard name. If the query audits unmapped source codes, restrict it to `<col>_concept_id = 0`.

#### Sources

- [OMOP CDM: Data Model Conventions, Fields](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#fields)
- [OMOP CDM: Data Model Conventions, Source values, source concept ids, and standard concept ids](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#source-values-source-concept-ids-and-standard-concept-ids)
- [The Book of OHDSI, ch. 4: The Common Data Model, Difference between concepts and source values](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#concepts-Sources)
- [The Book of OHDSI, ch. 15: Data Quality, Checking mappings](https://ohdsi.github.io/TheBookOfOhdsi/DataQuality.html#checking-mappings)

---

## Anti-Pattern Rules

These rules detect common SQL anti-patterns and mistakes when working with OMOP vocabulary tables.

---

### 1. Ambiguous Column Reference { #anti-patterns-ambiguous-column-reference }

**Rule ID:** `anti_patterns.ambiguous_column_reference`
**Severity:** ERROR

#### Intent

PostgreSQL: "The correlation name and separating dot can be omitted if the column name is unique across all the tables being used in the current query." When two or more FROM / JOIN sources of a SELECT expose the same column, an unqualified reference to it cannot be resolved. The database rejects the query (PostgreSQL: `column reference "x" is ambiguous`, SQLSTATE 42702; DuckDB raises the equivalent binder error).

#### How it works

The check is scope-aware. Only the sources of the SELECT that owns the reference are counted. Base CDM tables are resolved through the CDM v5.4 column catalogue. CTEs, derived tables and LATERAL subqueries expose only the columns they project, including `*` expansion and `AS t(col, ...)` lists. Parenthesised join trees contribute each of their tables. A source whose columns cannot be determined is never counted, so every finding is a certain ambiguity.

- `JOIN ... USING (col)` and `NATURAL JOIN` merge the joined columns.
- An ON clause sees only the sources joined so far. This also holds for a correlated subquery inside the ON clause, and for a LATERAL source: the JOIN of the SELECT being judged sets what is visible, not a JOIN inside the subquery.
- A bare `ORDER BY` / `DISTINCT ON` name may name an output column; outside PostgreSQL, select-list aliases are also accepted in GROUP BY, HAVING and WHERE.
- An unqualified column of a subquery that no source of the subquery can supply is resolved in the enclosing SELECT, as the database does.

#### Examples

**Violation patterns:**

```sql
SELECT person_id
FROM condition_occurrence co
JOIN person p ON co.person_id = p.person_id;
```

```sql
SELECT person_id
FROM person p
CROSS JOIN LATERAL (SELECT person_id FROM death d WHERE d.person_id = p.person_id) x;
```

**Correct patterns:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN person p ON co.person_id = p.person_id;
```

```sql
SELECT person_id
FROM condition_occurrence co
JOIN person p USING (person_id);
```

#### Suggested fix

Qualify the column with the alias of the source you mean (e.g. `co.person_id`), or join with `USING (col)` so the joined columns merge into one.

#### Sources

- [PostgreSQL documentation: Value Expressions, column references](https://www.postgresql.org/docs/current/sql-expressions.html#SQL-EXPRESSIONS-COLUMN-REFS)
- [PostgreSQL documentation: Table Expressions, joined tables](https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN)
- [PostgreSQL documentation: Error Codes (42702 ambiguous_column)](https://www.postgresql.org/docs/current/errcodes-appendix.html#ERRCODES-TABLE)

---

### 2. Cartesian Product With Clinical Rows { #anti-patterns-comma-separated-cross-join }

**Rule ID:** `anti_patterns.comma_separated_cross_join`
**Severity:** WARNING

#### Intent

Two sources of one SELECT that no predicate ties together form a Cartesian product: if they hold N and M rows, the result holds N * M. PostgreSQL defines the comma, `CROSS JOIN` and `JOIN ... ON TRUE` as the same operation, so the rule judges the predicates, not the spelling. When one side is a patient-level table, each clinical record is paired with every row of the other side, and the combinations correspond to no recorded event. Usually a `person_id` (or `visit_occurrence_id`) predicate is missing. Whether a product was intended cannot be read from the SQL, so the finding is a WARNING.

#### How it works

For each SELECT, the rule groups its FROM / JOIN sources by the predicates that tie them. These count as ties:

- equality, range and `IN` predicates in WHERE or any ON clause, including function-wrapped columns;
- `USING` and `NATURAL`;
- two columns equated to the same literal;
- a column of an enclosing query, read in a predicate or inside a derived table, which ties the source to the outer row;
- a derived or LATERAL table whose query reads another source.

A predicate under `NOT`, and `<>`, tie nothing; an `OR` ties a source only when every branch ties it to another source. Unqualified columns are attributed to the one source that can supply them (CDM catalogue, CTE and derived-table projections).

The rule warns when two or more groups multiply rows and at least one contains a patient-level source. Patient-level tables have a `person_id` column or refer to a table that has one, per the CDM v5.4 field-level specification. CTEs and derived tables count by the tables they read. These sources multiply nothing:

- a side that returns one row: a primary key equated to a literal, an aggregate query without GROUP BY, or LIMIT 1;
- row generators (`VALUES`, `UNNEST`, `generate_series`);
- CDM_SOURCE and METADATA, which [Instance Metadata Paired With Clinical Rows](#anti-patterns-singleton-metadata-clinical-join) reports.

Two person-level tables left untied (`condition_occurrence` crossed with `drug_exposure`) are reported by [`joins.clinical_person_id_linkage_validation`](#joins-clinical-person-id-linkage-validation) instead, so the pair is reported once; this rule keeps every other pair, such as a clinical table crossed with a vocabulary table.

#### Examples

**Violation patterns:**

```sql
SELECT co.condition_occurrence_id, c.concept_name
FROM condition_occurrence co, concept c
WHERE co.condition_concept_id = 201820;
```

```sql
SELECT co.condition_occurrence_id, c.concept_name
FROM condition_occurrence co
CROSS JOIN concept c
WHERE co.condition_concept_id = 201820;
```

**Correct patterns:**

```sql
SELECT co.condition_occurrence_id, c.concept_name
FROM condition_occurrence co
JOIN concept c
  ON co.condition_concept_id = c.concept_id
WHERE co.condition_concept_id = 201820;
```

```sql
-- two person-level tables: reported by joins.clinical_person_id_linkage_validation, not here
SELECT co.condition_occurrence_id, de.drug_exposure_id
FROM condition_occurrence co, drug_exposure de
WHERE co.condition_concept_id = 201820;
```

```sql
-- one-row side: the total used for a percentage
WITH t AS (SELECT COUNT(*) AS n FROM person)
SELECT p.gender_concept_id, COUNT(*) * 100.0 / MAX(t.n) AS pct
FROM t, person p
GROUP BY p.gender_concept_id;
```

```sql
-- range predicate ties the two sources
SELECT co.condition_occurrence_id
FROM condition_occurrence co, observation_period op
WHERE co.person_id = op.person_id
  AND co.condition_start_date BETWEEN op.observation_period_start_date AND op.observation_period_end_date;
```

#### Suggested fix

Add a predicate tying the sources, typically on `person_id` (or `visit_occurrence_id` when both sides belong to the same visit). Range predicates such as `event_date BETWEEN op.observation_period_start_date AND op.observation_period_end_date` count as ties too.

#### Sources

- [PostgreSQL documentation: Table Expressions, joined tables](https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN)

---

### 3. Concept Code Requires Vocabulary ID { #anti-patterns-concept-code-requires-vocabulary-id }

**Rule ID:** `anti_patterns.concept_code_requires_vocabulary_id`
**Severity:** WARNING (ERROR in strict mode)

#### Intent

The CDM v5.4 specification: "Note that concept codes are not unique across vocabularies." The Book of OHDSI adds that the same code can exist in different vocabularies with completely different meanings. A predicate on `concept.concept_code` without a `vocabulary_id` constraint on the same concept row matches every vocabulary that uses the code: 'E11.9' is a code of ICD10, ICD10CM and OPCS4.

#### How it works

The rule finds predicates on `concept_code`: `=` against a literal or another column (a code join), `IN`, `BETWEEN`, `LIKE` / `ILIKE` / regex / `SIMILAR TO`, and `JOIN concept USING (concept_code)`. They may sit in WHERE, any ON clause or HAVING. The column may be wrapped in a cast, `LOWER` / `UPPER` / `TRIM` or `LEFT` / `SUBSTRING`, and is followed through CTEs, derived tables and renames to the concept table it reads.

The predicate is reported unless the same concept row is constrained by an AND-connected predicate in any SELECT along that lineage. These count as constraints:

- `vocabulary_id =` (a literal or a column), `IN`, or a pattern, through casts, case folding and COALESCE;
- an equality on the same row's `concept_id`, which pins the row.

A predicate under OR or NOT constrains nothing.

The suggested vocabulary comes from the shape of the code. It is given only when exactly one vocabulary of the embedded release (Athena v5.0 27-AUG-25) uses that shape, which is ICD9CM-style `250.00` today. Otherwise the fix lists the vocabularies that share the shape.

#### Examples

**Violation patterns:**

```sql
SELECT c.concept_id
FROM concept c
WHERE c.concept_code = 'E11.9';
```

```sql
SELECT co.condition_occurrence_id, c.concept_id AS source_concept_id
FROM condition_occurrence co
JOIN concept c ON co.condition_source_value = c.concept_code;
```

**Correct patterns:**

```sql
SELECT c.concept_id
FROM concept c
WHERE c.concept_code = 'E11.9'
  AND c.vocabulary_id = 'ICD10CM';
```

```sql
WITH icd AS (SELECT * FROM concept WHERE vocabulary_id = 'ICD10CM')
SELECT icd.concept_id FROM icd WHERE icd.concept_code = 'E11.9';
```

#### Suggested fix

Add `AND <alias>.vocabulary_id = '<vocab>'` (or a join on vocabulary_id) alongside the concept_code predicate. The structured patch inserts it after the predicate, with the vocabulary filled in only when the code shape identifies it.

#### Sources

- [OMOP CDM v5.4 specification: CONCEPT](https://ohdsi.github.io/CommonDataModel/cdm54.html#concept)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Concept codes](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#concept-codes)

---

### 4. Concept Name Lookup Anti-pattern { #anti-patterns-concept-name-lookup }

**Rule ID:** `anti_patterns.concept_name_lookup`
**Severity:** WARNING

#### Intent

`concept_name` is display text, not a key. The CDM identifies a concept by `concept_id`, which "stays the same for the same Concept between releases", or by `concept_code` within a `vocabulary_id`. Nothing makes a name unique: 'Type 2 diabetes mellitus' names five concepts in the Athena release. Vocabulary releases also reword names: the v20260227 release renamed ATC classes such as 'mycophenolic acid; systemic'. A name filter can therefore return a different concept set across sites and releases, and a pattern pulls in every concept whose name contains the text. Exact comparison is case-sensitive in PostgreSQL: gender concept 8507 is named 'MALE', not 'Male'. The same applies to `concept_synonym.concept_synonym_name`.

#### How it works

The rule reports `concept.concept_name` or `concept_synonym.concept_synonym_name` compared with string literals in these forms:

- `=`, `<>`, `IN` / `NOT IN`;
- `LIKE`, `ILIKE`, `SIMILAR TO` and regex matches, including `LIKE ANY (ARRAY[...])` and patterns built by concatenating literals;
- the substring functions `POSITION` / `STRPOS` / `CHARINDEX`, `STARTS_WITH` and `CONTAINS`.

The column is found through case folding, trimming and casts, and resolved through lineage when it is unqualified or renamed in a CTE. Blank literals and match-everything patterns name no concept and are ignored.

#### Examples

**Violation patterns:**

```sql
SELECT c.concept_id
FROM concept c
WHERE c.concept_name = 'Type 2 diabetes mellitus';
```

```sql
SELECT c.concept_id
FROM concept c
WHERE POSITION('diabetes' IN LOWER(c.concept_name)) > 0;
```

**Correct patterns:**

```sql
SELECT c.concept_id
FROM concept c
WHERE c.concept_code = 'E11'
  AND c.vocabulary_id = 'ICD10CM';
```

```sql
SELECT c.concept_name, COUNT(*)
FROM condition_occurrence co
JOIN concept c ON c.concept_id = co.condition_concept_id
GROUP BY c.concept_name;
```

<!-- extra-examples:start -->
**Warning variant: Pattern match on concept_name: pulls in every concept whose name contains the text**

```sql
SELECT c.concept_id
FROM concept c
WHERE LOWER(c.concept_name) LIKE '%diabetes%';
```

<!-- extra-examples:end -->

#### Suggested fix

Replace `WHERE c.concept_name = '<name>'` with `WHERE c.concept_code = '<code>' AND c.vocabulary_id = '<vocab>'`, or with `WHERE c.concept_id = <id>` if the concept_id is known.

#### Sources

- [OMOP CDM: Data Model Conventions, Concepts](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concepts)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Concept names](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#concept-names)
- [The Book of OHDSI, ch. 9: SQL and R, Case sensitivity in string comparisons](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#case-sensitivity-in-string-comparisons)
- [OHDSI Vocabulary release notes, v20260227: concept name changes (ATC, CPT4)](https://github.com/OHDSI/Vocabulary-v5.0/releases/tag/v20260227_1772176757.000000)

---

### 5. Concept Relationship Transitive Misuse { #anti-patterns-concept-relationship-transitive-misuse }

**Rule ID:** `anti_patterns.concept_relationship_transitive_misuse`
**Severity:** WARNING

#### Intent

CDM conventions: "Concept Relationships define direct relationships between Concepts. Indirect relationships through 3rd Concepts are not captured in this table. However, the CONCEPT_ANCESTOR table does this for hierarchical relationships." A chain of 'Is a' / 'Subsumes' lookups returns only the concepts reached in exactly that many steps. It skips the levels in between and everything deeper, and concepts with several parents can be reached at different depths. concept_ancestor covers standard and classification concepts. A non-standard vocabulary's hierarchy (ICD10CM, Read, ...) needs a recursive CTE.

#### How it works

Each concept_relationship instance restricted to a single hierarchical relationship is a hop with a direction:

- 'Subsumes' read from `concept_id_1` to `concept_id_2`, and 'Is a' read from `concept_id_2` to `concept_id_1`, go down the hierarchy;
- the reverse readings go up.

Two hops form a chain when the row one leads to is the row the next starts from. They may be joined directly, through CTEs or IN-subqueries, or through a concept row, since `concept.concept_id` carries the id unchanged. A chain of two or more hops walking one direction is reported once, in the direction anchored to an outside value. These are not reported: chains that change direction (siblings), hops through 'Maps to', and hops inside a recursive CTE, which repeats until the closure is complete.

#### Examples

**Violation pattern:**

```sql
SELECT cr2.concept_id_2
FROM concept_relationship cr1
JOIN concept_relationship cr2 ON cr1.concept_id_2 = cr2.concept_id_1
WHERE cr1.concept_id_1 = 201820
  AND cr1.relationship_id = 'Subsumes'
  AND cr2.relationship_id = 'Subsumes';
```

**Correct patterns:**

```sql
SELECT descendant_concept_id
FROM concept_ancestor
WHERE ancestor_concept_id = 201820
  AND min_levels_of_separation BETWEEN 1 AND 2;
```

```sql
-- siblings: up one level, then down one level
SELECT cr2.concept_id_2
FROM concept_relationship cr1
JOIN concept_relationship cr2 ON cr1.concept_id_2 = cr2.concept_id_1
WHERE cr1.concept_id_1 = 201826
  AND cr1.relationship_id = 'Is a'
  AND cr2.relationship_id = 'Subsumes';
```

#### Suggested fix

Use concept_ancestor (`descendant_concept_id` walking down, `ancestor_concept_id` walking up), filtering `min_levels_of_separation` when a depth limit is intended. For a non-standard vocabulary, which concept_ancestor does not cover, use a recursive CTE.

#### Sources

- [OMOP CDM: Data Model Conventions, Concept relationships](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concept-relationships)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Hierarchy](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptAncestor)
- [OMOP CDM: Data Model Conventions, Concept ancestor](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#concept-ancestor)

---

### 6. Destructive Operations on Clinical Tables { #anti-patterns-destructive-operations-on-clinical-tables }

**Rule ID:** `anti_patterns.destructive_operations_on_clinical_tables`
**Severity:** ERROR (target is the CDM table); WARNING (target in another schema)

#### Intent

The Book of OHDSI: "The clinical Event and vocabulary tables are in the 'CDM' schema and are considered read-only to the end user or analytic tool." Only the Results tables COHORT and COHORT_DEFINITION are meant to be written. An analytical query that deletes, updates, inserts into or restructures a CDM table changes the data for everyone using it.

**Why an ERROR.** This is the rule set's one declared exception to reporting an ERROR only for a query that fails or certainly returns wrong rows. The write may succeed, and the statement is not wrong in itself. The reason for the ERROR is scope: FastSSV validates analytics SQL, and the Book of OHDSI treats the CDM schema as read-only to the end user, so a write to it is outside what an analytic query may do.

#### How it works

The rule looks at DELETE, UPDATE, INSERT, MERGE, TRUNCATE, DROP, ALTER, CREATE OR REPLACE TABLE and SELECT ... INTO, including data-modifying CTEs, and at DROP SCHEMA of a CDM schema. It reports them when the target is a CDM v5.4 table other than COHORT and COHORT_DEFINITION. Vocabulary tables are covered by `data_quality.vocabulary_table_protection`.

- An unqualified target, one in the engine's default schema (`public`, `dbo`), or one in a schema whose name contains `cdm` or `omop` and does not name a results, scratch or temp schema, is taken as the CDM table: ERROR.
- A target in any other schema (`scratch.person`, `results.death`, `cdm_results.drug_era`, `omop_scratch.condition_era`), or in a templated schema (`@results_schema.metadata`), may be a copy that only shares the name: WARNING.
- A T-SQL `#temp` table is not reported, because it is session-local.
- A table the statement itself creates as TEMP (`CREATE OR REPLACE TEMP TABLE death AS …`, `SELECT … INTO TEMP TABLE death …`) is not reported.
- A name that an earlier statement of the same input (or of the same API batch) created unqualified or as TEMP, including by `SELECT … INTO TEMP`, is not reported either. A temporary table hides a permanent table of the same name.

#### Examples

**Violation patterns:**

```sql
DELETE FROM condition_occurrence
WHERE person_id = 1;
```

```sql
MERGE INTO person p
USING staging s ON p.person_id = s.person_id
WHEN MATCHED THEN UPDATE SET year_of_birth = s.yob;
```

**Correct patterns:**

```sql
SELECT *
FROM condition_occurrence
WHERE person_id = 1;
```

```sql
INSERT INTO results.cohort (cohort_definition_id, subject_id, cohort_start_date, cohort_end_date)
SELECT 1, person_id, observation_period_start_date, observation_period_end_date
FROM observation_period;
```

```sql
CREATE TEMP TABLE death AS SELECT DISTINCT person_id FROM cdm.death;
DROP TABLE death;
```

```sql
SELECT * INTO TEMP TABLE death FROM cdm.death;
DELETE FROM death WHERE death_date IS NULL;
```

#### Suggested fix

Remove the statement that writes to the CDM table. Write results to the Results schema (COHORT, COHORT_DEFINITION) or to a scratch or temporary table; data modifications belong in the ETL.

#### Sources

- [The Book of OHDSI, ch. 4: The Common Data Model, General conventions of schemas](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#general-conventions-of-schemas)

---

### 7. Having Without Group By { #anti-patterns-having-without-group-by }

**Rule ID:** `anti_patterns.having_without_group_by`
**Severity:** ERROR (PostgreSQL, DuckDB); WARNING for other dialects

#### Intent

A query with HAVING and no GROUP BY is a grouped query whose rows form one single group. Its SELECT list and HAVING clause may therefore read table columns only inside aggregate functions. A column read outside an aggregate makes PostgreSQL and DuckDB reject the query (`must appear in the GROUP BY clause or be used in an aggregate function`).

The aggregate-only form is valid SQL: it returns one row when the HAVING condition holds and none otherwise. The rule does not report it.

#### How it works

For each SELECT that has HAVING and no GROUP BY, the rule collects the columns that the SELECT list or the HAVING clause reads outside an aggregate. Columns inside scalar subqueries, and columns of an enclosing query, are not counted. A column in an aggregate's `FILTER (WHERE …)` or in an ordered-set aggregate's `WITHIN GROUP (ORDER BY …)` (`mode()`, `percentile_cont`, `percentile_disc`) is aggregated. Every aggregate the dialect defines counts: sqlglot's aggregate classes plus the aggregate function names of the PostgreSQL and DuckDB documentation (DuckDB `list(x)`, `histogram`, `arg_max`, `string_agg`, ...). Outside the `postgres` dialect, an unqualified HAVING name that matches a select-list alias names that aggregate (DuckDB accepts `SELECT count(*) AS n FROM person HAVING n > 0`) and is not counted. A window function does not count as an aggregate, because its arguments must themselves be grouped. If any such column exists, the rule reports it by name. The finding is an ERROR for the `postgres` and `duckdb` dialects, where the rejection was verified, and a WARNING for other dialects, because some engines accept an ungrouped column under a permissive SQL mode and return an arbitrary value for it.

#### Examples

**Violation patterns:**

```sql
SELECT person_id, COUNT(*) AS n_exposures
FROM drug_exposure
HAVING COUNT(*) > 3;
```

```sql
SELECT * FROM condition_occurrence
HAVING COUNT(*) > 5
```

**Correct patterns:**

```sql
SELECT person_id, COUNT(*) AS n_exposures
FROM drug_exposure
GROUP BY person_id
HAVING COUNT(*) > 3;
```

```sql
-- one group: returns the count only when it reaches the threshold
SELECT COUNT(DISTINCT person_id) AS n
FROM condition_occurrence
WHERE condition_concept_id = 201826
HAVING COUNT(DISTINCT person_id) >= 5
```

#### Suggested fix

Add `GROUP BY` for the ungrouped columns before `HAVING`, or wrap the column in an aggregate, or move a row-level condition from `HAVING` to `WHERE`.

#### Sources

- [PostgreSQL documentation: SELECT, HAVING clause](https://www.postgresql.org/docs/current/sql-select.html#SQL-HAVING)
- [PostgreSQL documentation: Aggregate Functions](https://www.postgresql.org/docs/current/functions-aggregate.html)
- [DuckDB documentation: Aggregate Functions](https://duckdb.org/docs/stable/sql/functions/aggregates)

---

### 8. Join Key Validation { #anti-patterns-join-key-validation }

**Rule ID:** `anti_patterns.join_key_validation`
**Severity:** ERROR

#### Intent

The CDM conventions separate two id namespaces. `_ID` columns are "unique identifiers for key entities" (person_id, visit_occurrence_id, provider_id, ...), and `_CONCEPT_ID` columns are foreign keys into the Standardized Vocabularies. An equality between an entity key and a concept id compares numbers from unrelated sequences. Both are integers, so the comparison runs, but every match is a numeric coincidence.

#### How it works

The rule reports `=` and `IN (SELECT ...)` comparisons with an entity key on one side and a concept id on the other. Entity keys are the primary keys of the CDM's non-vocabulary tables and the foreign keys that refer to them. Concept ids are `concept.concept_id` and every column that is a foreign key to CONCEPT. Both lists come from the CDM v5.4 field-level specification. Both sides are traced through casts, CTEs, derived tables and renaming aliases to the base column they read. Columns of tables outside the CDM, and role-named keys such as `cohort.subject_id`, are not judged.

#### Examples

**Violation patterns:**

```sql
SELECT *
FROM person p
JOIN concept c ON p.person_id = c.concept_id;
```

```sql
WITH x AS (SELECT visit_occurrence_id AS vid FROM visit_occurrence)
SELECT * FROM x JOIN concept c ON x.vid = c.concept_id;
```

**Correct patterns:**

```sql
SELECT p.person_id, c.concept_name AS gender
FROM person p
JOIN concept c ON p.gender_concept_id = c.concept_id;
```

```sql
SELECT * FROM cohort ch JOIN person p ON ch.subject_id = p.person_id;
```

#### Suggested fix

Join on a pair the CDM defines: `person_id = person_id` across clinical tables, `<event>.<x>_concept_id = concept.concept_id`, `<clinical>.visit_occurrence_id = visit_occurrence.visit_occurrence_id`.

#### Sources

- [OMOP CDM: Data Model Conventions, Fields](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#fields)
- [The Book of OHDSI, ch. 4: The Common Data Model, General naming conventions of fields](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#general-naming-conventions-of-fields)

---

### 9. Instance Metadata Paired With Clinical Rows { #anti-patterns-singleton-metadata-clinical-join }

**Rule ID:** `anti_patterns.singleton_metadata_clinical_join`
**Severity:** WARNING

#### Intent

CDM_SOURCE and METADATA describe the CDM instance (source, release dates, CDM and vocabulary versions, dataset metadata), not patients. In CDM v5.4 their only foreign keys point to CONCEPT, and no table refers to them, so nothing relates their rows to clinical rows.

When a SELECT reads one of them next to a patient-level table and no predicate ties the two, every clinical row is repeated once per metadata row. That is harmless with a single CDM_SOURCE row, and multiplies counts with more. The specification does not limit either table to one row, so the finding is a WARNING.

#### How it works

The rule uses the same source grouping as [Cartesian Product With Clinical Rows](#anti-patterns-comma-separated-cross-join). It reports a CDM_SOURCE or METADATA source, read directly or through a CTE or derived table, that shares a SELECT with a patient-level source in either of two cases:

- no predicate ties the two, and the metadata source is not provably one row;
- an equality ties an identifier column of the metadata source to an identifier column of the clinical source (`m.metadata_id = p.person_id`). No key relates them, so equal values are a coincidence.

Predicates that compare other values are legitimate and not reported, for example events after `cdm_source.cdm_release_date`. Scalar subqueries and one-row derived tables (an aggregate or LIMIT 1) are not reported either.

An identifier equality that a joins key-pair rule reports as an ERROR (`m.metadata_id = p.person_id` is reported by `joins.person_id_join_validation`) is reported once, by that rule; the finding stays for keys no joins rule judges and for the no-predicate case.

#### Examples

**Violation patterns:**

```sql
SELECT *
FROM person p, cdm_source cs;
```

```sql
SELECT *
FROM metadata m
JOIN person p ON m.metadata_id = p.person_id;
```

**Correct patterns:**

```sql
SELECT *,
       (SELECT cdm_version FROM cdm_source) AS cdm_version
FROM person;
```

```sql
-- data-quality check: events dated after the CDM release
SELECT co.condition_occurrence_id
FROM condition_occurrence co, cdm_source cs
WHERE co.condition_start_date > cs.cdm_release_date;
```

#### Suggested fix

Replace the join with a scalar subquery in the SELECT list, e.g. `(SELECT cdm_version FROM cdm_source) AS cdm_version`. Unlike the join, the subquery raises an error instead of repeating rows if the table holds more than one row.

#### Sources

- [OMOP CDM v5.4 specification: CDM_SOURCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#cdm_source)
- [OMOP CDM v5.4 specification: METADATA](https://ohdsi.github.io/CommonDataModel/cdm54.html#metadata)

---

### 10. No DISTINCT on Primary Key Column { #anti-patterns-no-distinct-on-primary-key-column }

**Rule ID:** `anti_patterns.no_distinct_on_primary_key_column`
**Severity:** WARNING

#### Intent

A primary key is unique within its table. A SELECT over a single CDM table, with no joins, that projects the table's primary key returns unique rows, so DISTINCT removes nothing and only adds a sort or hash step. A redundant DISTINCT never changes the result, so the rule reports only `SELECT DISTINCT` over the key and stays silent on `COUNT(DISTINCT pk)` and `GROUP BY pk`.

#### How it works

The primary keys are those of the CDM v5.4 field-level specification (29 tables, including `concept`, `observation_period`, the era tables and `death.person_id`). The rule reports a `SELECT DISTINCT` whose columns include the primary key of the SELECT's single source. The key may be projected as is, under a cast, or through `*` / `t.*`. The table may be read directly or through CTEs and derived tables that themselves read one source without joins, grouping or set operations.

Nothing is reported when the query joins another source (a join can repeat the key), or for T-SQL `#temp` tables.

#### Examples

**Violation patterns:**

```sql
SELECT DISTINCT condition_occurrence_id
FROM condition_occurrence
WHERE condition_concept_id = 201826;
```

```sql
SELECT DISTINCT I.concept_id
FROM (SELECT concept_id FROM concept WHERE concept_id IN (192671)) AS I;
```

**Correct patterns:**

```sql
SELECT condition_occurrence_id
FROM condition_occurrence
WHERE condition_concept_id = 201826;
```

```sql
-- DISTINCT needed: one person can have many condition records
SELECT DISTINCT p.person_id
FROM person p
JOIN condition_occurrence co ON p.person_id = co.person_id;
```

#### Suggested fix

Remove DISTINCT (the structured patch does this when the statement has one DISTINCT keyword).

#### Sources

- [OMOP CDM v5.4 field-level specification (CSV)](https://raw.githubusercontent.com/OHDSI/CommonDataModel/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [PostgreSQL documentation: SELECT, DISTINCT clause](https://www.postgresql.org/docs/current/sql-select.html#SQL-DISTINCT)

---

### 11. No String Identification { #anti-patterns-no-string-identification }

**Rule ID:** `anti_patterns.no_string_identification`
**Severity:** WARNING

#### Intent

`*_source_value` columns keep the verbatim code or text of the source data. The CDM conventions say they are "generally not to be used by any standardized analytics" and are "provided for convenience and quality assurance (QA) purposes". The values are not harmonised across data sources, so a string filter on one selects whatever a single site happened to record. The table's concept column holds the same fact in a form that is the same everywhere.

#### How it works

The rule looks for a `*_source_value` column compared with a string literal: `=`, `<>`, `IN` / `NOT IN`, `BETWEEN`, `LIKE`, `ILIKE`, `SIMILAR TO` and regex matches. The column may be wrapped in `LOWER`, `UPPER`, `TRIM`, `LEFT`, `SUBSTRING` or a cast, and is followed through CTEs, derived tables and renaming aliases to the base column.

It reports only source-value columns whose table has a concept column for the same fact, taken from the CDM v5.4 field-level specification. Most are `<x>_concept_id` / `<x>_source_concept_id`. The `value_source_value` columns map to `value_as_concept_id`, and `note_source_value` maps to `note_class_concept_id`.

It does not report:

- identifiers with no concept counterpart (`person_source_value`, `provider_source_value`, `care_site_source_value`, `location_source_value`, `family_source_value`, `drug_exposure.dose_unit_source_value`);
- blank-literal checks (`= ''`);
- a SELECT that filters the paired concept column to 0, where the source value is the only information an unmapped record carries.

The finding is a WARNING: the sources discourage the practice, they do not make it an error.

#### Examples

**Violation patterns:**

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_source_value LIKE '%diabetes%';
```

```sql
SELECT person_id
FROM condition_occurrence
WHERE LOWER(condition_source_value) IN ('e11.9', 'e11.8');
```

**Correct patterns:**

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_concept_id = 201820;
```

```sql
-- linking back to the source record: person_source_value has no concept counterpart
SELECT person_id FROM person WHERE person_source_value = 'MRN-000123';
```

```sql
-- QA of unmapped records
SELECT condition_source_value, COUNT(*)
FROM condition_occurrence
WHERE condition_concept_id = 0 AND condition_source_value LIKE 'E11%'
GROUP BY condition_source_value;
```

#### Suggested fix

Replace the string filter with a filter on the paired concept column: the standard `<x>_concept_id` for analytics, or `<x>_source_concept_id` when the analysis is specific to the source vocabulary. The structured patch rewrites `col = '<text>'` / `col LIKE '<text>'` to `<concept column> = <expected_concept_id>` when the column is read directly from its table.

#### Sources

- [OMOP CDM: Data Model Conventions, Source values, source concept ids and standard concept ids](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#source-values-source-concept-ids-and-standard-concept-ids)
- [The Book of OHDSI, ch. 4: The Common Data Model, General naming conventions of fields](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#general-naming-conventions-of-fields)

---

### 12. Type Concept ID Not For Clinical Filtering { #anti-patterns-type-concept-id-misuse }

**Rule ID:** `anti_patterns.type_concept_id_misuse`
**Severity:** ERROR (every compared id is a known non-Type concept); WARNING otherwise

#### Intent

`*_type_concept_id` columns record the provenance of a record (EHR, claim, registry, ...), not its clinical meaning. The CDM v5.4 field-level specification gives them the foreign-key domain 'Type Concept'. A comparison with a concept id outside that domain therefore matches no record of a conformant CDM. Typically a clinical concept was written into the provenance column instead of the primary `*_concept_id` column.

#### How it works

The rule finds `=`, `IN`, `= ANY(ARRAY[...])` and `BETWEEN` comparisons of a type column with integer literals. The column may sit on either side, under a cast or parentheses, or behind a CTE or derived-table rename. The comparison may appear in WHERE, inner or outer join ON clauses, HAVING, CASE or FILTER. Negations exclude nothing and are not reported. Comparisons with a column, a subquery or an expression cannot be judged statically. The type columns are those the specification gives the 'Type Concept' domain, including `period_type_concept_id`, plus `cost_type_concept_id`.

Each compared id is looked up in the embedded vocabulary release (Athena v5.0 27-AUG-25).

- **ERROR**: every id is a concept of that release outside the 'Type Concept' domain. Concept ids are persistent across releases, so the comparison can never hold.
- **WARNING**:
  - an IN list that mixes Type Concepts with other ids;
  - an id the release does not contain (a newer or local concept);
  - a dead branch of an OR;
  - `cost_type_concept_id`, whose domain the specification leaves open;
  - a Type Concept of another table's legacy family, e.g. an 'Observation Type' in `condition_type_concept_id`. The conventions say Type Concepts "have to be chosen correctly" for the target table but nothing enforces it.

#### Examples

**Violation patterns:**

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_type_concept_id = 201826;
```

```sql
-- WARNING: 201826 never matches, 32817 still filters
SELECT person_id
FROM condition_occurrence
WHERE condition_type_concept_id IN (32817, 201826);
```

```sql
-- WARNING: 38000280 is an Observation Type
SELECT person_id
FROM condition_occurrence
WHERE condition_concept_id = 201826
  AND condition_type_concept_id = 38000280;
```

**Correct patterns:**

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_concept_id = 201820;
```

```sql
-- provenance filter with a Type Concept
SELECT person_id
FROM condition_occurrence
WHERE condition_concept_id = 201826
  AND condition_type_concept_id = 32817;
```

#### Suggested fix

Move the clinical concept to the primary column: `<x>_type_concept_id = <id>` becomes `<x>_concept_id = <id>`. The structured patch does this when every compared id is a known non-Type concept. For a foreign-family Type Concept, use the table's own family or a standard 'Type Concept' vocabulary id (e.g. 32817 'EHR', 32810 'Claim').

#### Sources

- [OMOP CDM: Data Model Conventions, Type Concepts](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#type-concepts)
- [The Book of OHDSI, ch. 4: The Common Data Model, General naming conventions of fields](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#general-naming-conventions-of-fields)

---

### 13. CTE Shadows OMOP CDM Table { #anti-patterns-cte-shadows-omop-table }

**Rule ID:** `anti_patterns.cte_shadows_omop_table`
**Severity:** WARNING

#### Intent

PostgreSQL: "the WITH query hides any real table of the same name for the purposes of the primary query. If necessary, you can refer to a real table of the same name by schema-qualifying the table's name." A CTE named after a CDM table (`WITH concept AS (...)`) changes what every unqualified reference to that table in the statement means. A reader, a later edit, or a fix suggested by another rule (`JOIN concept c ON ...`) reaches the CTE, which lacks the table's columns, instead of the CDM table.

#### How it works

The rule warns once per name for every CTE, at any depth, whose name is one of the 39 tables of the CDM v5.4 specification. Derived-table aliases (`FROM (SELECT ...) AS concept`) are not reported: they are visible only as a qualifier inside their own SELECT and hide no table.

#### Examples

**Violation patterns:**

```sql
WITH concept AS (
    SELECT 4112343 AS concept_id UNION ALL SELECT 201826
)
SELECT co.person_id
FROM condition_occurrence co
JOIN concept ON co.condition_concept_id = concept.concept_id;
```

```sql
WITH person AS (SELECT * FROM person WHERE year_of_birth > 1980)
SELECT COUNT(*) FROM person;
```

**Correct patterns:**

```sql
WITH my_concepts AS (
    SELECT 4112343 AS concept_id UNION ALL SELECT 201826
)
SELECT co.person_id
FROM condition_occurrence co
JOIN my_concepts ON co.condition_concept_id = my_concepts.concept_id;
```

```sql
-- a derived-table alias hides no table
SELECT co.person_id
FROM condition_occurrence co
JOIN (SELECT concept_id FROM concept WHERE vocabulary_id = 'SNOMED') AS snomed
  ON co.condition_concept_id = snomed.concept_id;
```

#### Suggested fix

Rename the CTE to a name that is not a CDM table (e.g. `WITH my_concepts AS ...` instead of `concept`). If renaming is impractical, schema-qualify every reference to the real table (`cdm.concept`).

#### Sources

- [PostgreSQL documentation: SELECT, FROM clause (WITH query names)](https://www.postgresql.org/docs/current/sql-select.html#SQL-FROM)

---

### 14. LIMIT Without ORDER BY { #anti-patterns-limit-without-order-by }

**Rule ID:** `anti_patterns.limit_without_order_by`
**Severity:** WARNING

#### Intent

PostgreSQL: "When using LIMIT, it is important to use an ORDER BY clause that constrains the result rows into a unique order. Otherwise you will get an unpredictable subset of the query's rows." SQL Server gives the same advice for TOP. A limited query without ORDER BY can return different rows on different runs, plans or engines. When it feeds an enclosing query (a CTE, a derived table, an `IN` list), the enclosing result is arbitrary too.

#### How it works

Every SELECT and set operation that declares a row limit is checked: LIMIT, TOP, FETCH FIRST, OFFSET, and Oracle `WHERE ROWNUM <= n`. It is reported when it has no ORDER BY. The rule stays silent when the result cannot depend on which rows are kept:

- the query of an `EXISTS (...)`;
- a limited derived table or CTE whose only reader counts its rows (`SELECT COUNT(*) FROM (... LIMIT 1) q`);
- a query that returns at most one row by construction: no FROM clause, an aggregate without GROUP BY (or with `GROUP BY ()`), or a FROM list whose sources are all one row (a primary key equated to a literal, an aggregate or LIMIT 1 subquery);
- a set operation whose arms are all one-row aggregates with a LIMIT no smaller than the arm count;
- `LIMIT 0`, `LIMIT ALL`, `LIMIT NULL`, `OFFSET 0`, and the Oracle idiom of ROWNUM over an ordered subquery.

When `concept_standardization.multiple_maps_to_targets` reports the same LIMIT (a `'Maps to'` lookup cut to one target), the validator keeps only that finding, whose message gives the OMOP reason; `ORDER BY` alone would not fix it.

#### Examples

**Violation patterns:**

```sql
SELECT person_id, condition_concept_id
FROM condition_occurrence
LIMIT 100;
```

```sql
WITH sample_patients AS (
    SELECT person_id
    FROM condition_occurrence
    WHERE condition_concept_id = 201826
    LIMIT 100
)
SELECT COUNT(*)
FROM drug_exposure de
JOIN sample_patients sp ON de.person_id = sp.person_id;
```

**Correct patterns:**

```sql
SELECT person_id, condition_concept_id
FROM condition_occurrence
ORDER BY condition_occurrence_id
LIMIT 100;
```

```sql
-- one row by construction
SELECT COUNT(DISTINCT person_id) FROM condition_occurrence LIMIT 1000;
```

```sql
-- only the row count of the limited subquery is read
SELECT COUNT(*) AS n FROM (SELECT person.* FROM person LIMIT 1) q01;
```

#### Suggested fix

Add `ORDER BY <stable_key>` before the row-limiting clause, using the table's primary key (e.g. `ORDER BY condition_occurrence_id`) or a composite key that identifies a row.

#### Sources

- [PostgreSQL documentation: LIMIT and OFFSET](https://www.postgresql.org/docs/current/queries-limit.html)
- [SQL Server documentation: TOP (Transact-SQL), best practices](https://learn.microsoft.com/en-us/sql/t-sql/queries/top-transact-sql#best-practices)

---

### 15. NULL Comparison Must Use IS NULL / IS NOT NULL { #anti-patterns-null-comparison-operator }

**Rule ID:** `anti_patterns.null_comparison_operator`
**Severity:** ERROR (PostgreSQL, DuckDB); WARNING for a bare `= NULL` on PostgreSQL and for other dialects

#### Intent

PostgreSQL: "Ordinary comparison operators yield null (signifying 'unknown'), not true or false, when either input is null." A predicate that compares with the NULL keyword never holds, so a WHERE, ON or HAVING filter built on it removes every row, and a CASE branch built on it is never taken. `IS NULL` and `IS NOT NULL` test for null.

#### How it works

The rule reports comparisons with the NULL keyword. NULL may be bare, parenthesised or cast, and on either side. The forms covered are:

- `=`, `<>` / `!=`, `<`, `<=`, `>`, `>=`;
- `IN (NULL)`, when every member is NULL;
- `NOT IN (..., NULL, ...)`, because a NULL member makes NOT IN never true;
- `BETWEEN` with a NULL bound;
- `CASE x WHEN NULL`, which compares `x = NULL`.

A negated BETWEEN with one NULL bound is different. `x BETWEEN NULL AND y` is false where `x > y` and unknown elsewhere, so `x NOT BETWEEN NULL AND y` (or `NOT (x BETWEEN NULL AND y)`) is true exactly where `x > y` (`x < y` for `x NOT BETWEEN y AND NULL`) and unknown for every other row. It does not "never hold", so it is always a WARNING whose message states that result.

Severity depends on the dialect:

- PostgreSQL and DuckDB: ERROR, where the outcome was verified. The exception is a bare `x = NULL` on PostgreSQL, which the `transform_null_equals` setting rewrites to `x IS NULL`; that form is a WARNING.
- Other dialects: WARNING, because engine settings such as SQL Server `ANSI_NULLS OFF` change NULL comparisons.

The rule does not report:

- assignments: `UPDATE ... SET col = NULL`, MERGE / ON CONFLICT updates, `SET @var = NULL`, T-SQL `SELECT @var = NULL`;
- named arguments of table functions, such as DuckDB `read_csv(..., nullstr = NULL)`;
- the string `'NULL'`;
- an IN list that still holds a non-null member.

#### Examples

**Violation patterns:**

```sql
SELECT person_id
FROM death
WHERE death_date <> NULL;
```

```sql
SELECT person_id
FROM death
WHERE cause_concept_id NOT IN (1, NULL);
```

**Correct patterns:**

```sql
SELECT person_id
FROM death
WHERE death_date IS NOT NULL;
```

```sql
UPDATE my_results SET flag = NULL WHERE flag IS NULL;
```

<!-- extra-examples:start -->
**Warning variant: Bare `= NULL` on PostgreSQL: transform_null_equals would rewrite it to IS NULL**

```sql
SELECT person_id
FROM death
WHERE death_date = NULL;
```

<!-- extra-examples:end -->

#### Suggested fix

Replace `<col> = NULL` with `<col> IS NULL`, and `<col> <> NULL` / `<col> != NULL` with `<col> IS NOT NULL` (the structured patch does this). Remove NULL from a NOT IN list, and write `CASE WHEN <col> IS NULL THEN ...` instead of `CASE <col> WHEN NULL`.

#### Sources

- [PostgreSQL documentation: Comparison Functions and Operators](https://www.postgresql.org/docs/current/functions-comparison.html)

---

## Domain-Specific Rules

These rules validate domain-specific constraints for individual OMOP CDM tables and clinical domains.

---

### COST Domain

### 1. Cost Event ID Polymorphic Resolution { #domain-specific-cost-event-id-polymorphic-resolution }

**Rule ID:** `domain_specific.cost_event_id_polymorphic_resolution`
**Severity:** ERROR (restriction rules out the matched table's domain) / WARNING (no restriction, extra domains)

#### Intent

CDM v5.4 defines `cost.cost_event_id` as "a foreign key identifier to the event (e.g. Measurement, Procedure, Visit, Drug Exposure, etc) record for which cost data are recorded" and `cost.cost_domain_id` as the domain "from which the corresponding table can be inferred". The same integer can therefore denote a drug exposure in one cost row and a visit in another; only `cost_domain_id` says which.

| table matched with `cost_event_id` | `cost_domain_id` of its costs |
|---|---|
| `drug_exposure.drug_exposure_id` | `'Drug'` |
| `procedure_occurrence.procedure_occurrence_id` | `'Procedure'` |
| `visit_occurrence.visit_occurrence_id`, `visit_detail.visit_detail_id` | `'Visit'` |
| `device_exposure.device_exposure_id` | `'Device'` |
| `measurement.measurement_id` | `'Measurement'` |
| `observation.observation_id` | `'Observation'` |
| `condition_occurrence.condition_occurrence_id` | `'Condition'` |
| `specimen.specimen_id` | `'Specimen'` |

The domain of each table is the domain its concept field accepts in the CDM v5.4 field-level specification (or the DOMAIN row named after the table).

#### How it works

For every comparison that matches `cost_event_id` with a column, an `IN` subquery or a literal (in WHERE, JOIN ON, HAVING or USING), the rule collects the `cost_domain_id` restrictions that hold for the same cost row. Both columns are traced through CTEs, derived tables and casts, so a restriction inside a CTE counts for the join outside it. A restriction on another read of `cost` (another alias, another UNION branch, a subquery) does not count; one under an OR counts only inside its own operand; one in a LEFT / RIGHT / FULL join's ON counts only for that join.

- **ERROR**: the key is matched with an event table and the restriction rules out that table's domain (`= 'Procedure'` with `drug_exposure`, `NOT (cost_domain_id = 'Drug')`, `LOWER(cost_domain_id) = 'Drug'`). On any CDM every returned row pairs a cost with an unrelated event that shares its id, or no row is returned.
- **WARNING**: no restriction. The result is wrong only where the cost table holds costs of several domains.
- **WARNING**: an `IN` list that admits the right domain and others.

A domain id that differs from the right one only in case (`'drug'`) is reported by [`data_quality.canonical_string_value_validation`](#data-quality-canonical-string-value-validation), and a number compared with `cost_domain_id` by [`data_quality.column_type_validation`](#data-quality-column-type-validation); this rule treats both as restrictions it cannot judge. A restriction whose value the SQL does not show (a parameter, a subquery, a domain chosen through a join to `domain` or `concept`) is accepted. A cast literal (`CAST('Drug' AS TEXT)`) compares as the literal. NULL tests on `cost_event_id` do not interpret the key and are not reported.

#### Examples

**Violation patterns:**

*the restriction names another domain (ERROR)*

```sql
SELECT c.cost_id, de.drug_concept_id
FROM cost c
JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
WHERE c.cost_domain_id = 'Procedure';
```

*no restriction (WARNING)*

```sql
SELECT c.cost_id, de.drug_concept_id
FROM cost c
JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id;
```

**Correct patterns:**

```sql
SELECT c.cost_id, de.drug_concept_id
FROM cost c
JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
WHERE c.cost_domain_id = 'Drug';
```

*one LEFT JOIN per domain*

```sql
SELECT c.cost_id, COALESCE(de.person_id, po.person_id) AS person_id
FROM cost c
LEFT JOIN drug_exposure de
  ON c.cost_domain_id = 'Drug' AND c.cost_event_id = de.drug_exposure_id
LEFT JOIN procedure_occurrence po
  ON c.cost_domain_id = 'Procedure' AND c.cost_event_id = po.procedure_occurrence_id;
```

*restriction inside the CTE that reads cost*

```sql
WITH drug_costs AS (
  SELECT cost_id, cost_event_id FROM cost WHERE cost_domain_id = 'Drug'
)
SELECT dc.cost_id, de.drug_concept_id
FROM drug_costs dc
JOIN drug_exposure de ON dc.cost_event_id = de.drug_exposure_id;
```

#### Suggested fix

ADD (or correct) a restriction on the same cost row: `c.cost_domain_id = '<Domain>'` with the case-exact domain_id of the table matched with `cost_event_id` ('Drug' for drug_exposure, 'Procedure' for procedure_occurrence, 'Visit' for visit_occurrence and visit_detail, 'Device' for device_exposure, …).

#### Sources

- [OMOP CDM v5.4 specification: COST](https://ohdsi.github.io/CommonDataModel/cdm54.html#cost)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Domains](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptDomains)
- [PostgreSQL documentation: Nondeterministic Collations](https://www.postgresql.org/docs/current/collation.html#COLLATION-NONDETERMINISTIC)
- [DuckDB documentation: Collations](https://duckdb.org/docs/stable/sql/expressions/collations)

---

### 2. Cost Currency Concept ID For Multi-Currency { #domain-specific-cost-currency-concept-id }

**Rule ID:** `domain_specific.cost_currency_concept_id`
**Severity:** WARNING

#### Intent

CDM v5.4 defines `cost.currency_concept_id` as "a foreign key identifier to the concept representing the 3-letter code used to delineate international currencies, such as USD for US Dollar". Every amount on a cost row is in that row's currency, so an aggregate that combines amounts of several rows (sum, mean, minimum, maximum, variance, percentile) mixes currencies wherever the rows hold more than one. Whether they do depends on the data, so this is a warning.

The amounts are the float fields of COST: `total_charge`, `total_cost`, `total_paid`, `paid_by_payer`, `paid_by_patient`, `paid_patient_copay`, `paid_patient_coinsurance`, `paid_patient_deductible`, `paid_by_primary`, `paid_ingredient_cost`, `paid_dispensing_fee`, `amount_allowed`.

#### How it works

An aggregate over an amount, or over a value computed from one in a CTE or derived table, in the projection, HAVING or ORDER BY, is reported unless the cost row it reads has its currency fixed or grouped, in the aggregating SELECT or in a CTE / derived table it reads from:

- `currency_concept_id` equal to one literal, parameter or scalar subquery, or `IN` a one-element list, as a condition that holds for every row (not under OR or NOT);
- `currency_concept_id` in GROUP BY (also by position, `GROUP BY 1`) or in the window's PARTITION BY;
- the same applied to a column of the concept row equi-joined with `currency_concept_id` (`cur.concept_code = 'USD'`, `GROUP BY cur.concept_name`).

COUNT, ARRAY_AGG and STRING_AGG keep values apart and are not reported. A multi-value `IN`, `<>` and two currencies joined by OR do not fix the currency. Aggregates fed by the same cost read are reported once.

#### Examples

**Violation patterns:**

```sql
SELECT SUM(total_paid) AS paid_total
FROM cost;
```

*aggregated outside the CTE that reads cost*

```sql
WITH drug_costs AS (
  SELECT c.cost_event_id, c.total_paid
  FROM cost c
  WHERE c.cost_domain_id = 'Drug'
)
SELECT AVG(total_paid) AS mean_paid
FROM drug_costs;
```

*two currencies*

```sql
SELECT SUM(total_paid) FROM cost
WHERE currency_concept_id = 44818668 OR currency_concept_id = 44818568;
```

**Correct patterns:**

```sql
SELECT currency_concept_id, SUM(total_paid) AS paid_total
FROM cost
GROUP BY currency_concept_id;
```

*one currency, chosen through the concept table*

```sql
SELECT SUM(c.total_paid) AS paid_usd
FROM cost c
JOIN concept cur ON cur.concept_id = c.currency_concept_id
WHERE cur.vocabulary_id = 'Currency' AND cur.concept_code = 'USD';
```

#### Suggested fix

ADD: `GROUP BY c.currency_concept_id` (each result row is then in one currency; rows without a currency form their own group) OR a filter to one currency, `c.currency_concept_id = <currency concept_id>` (44818668 is the US dollar), on the cost rows being aggregated.

#### Sources

- [OMOP CDM v5.4 specification: COST](https://ohdsi.github.io/CommonDataModel/cdm54.html#cost)

---

### CROSS-DOMAIN

### 3. Event Rows Counted as Persons { #domain-specific-event-rows-counted-as-persons }

**Rule ID:** `domain_specific.event_rows_counted_as_persons`
**Severity:** WARNING

#### Intent

The CDM is person-centric: every clinical event table links to PERSON, and a person can have many rows in it. CDM v5.4 says so explicitly for periods ("one or more OBSERVATION_PERIOD records may be present", "a Person can have multiple, overlapping, Payer_Plan_Periods") and for cohorts (a subject "can have multiple, discrete records in the cohort table per cohort_definition_id"). `COUNT(*)` over such rows counts rows; presented as a count of people it overstates the number of people whenever someone has several rows. Whether anyone does depends on the data, so this is a warning.

The tables covered are those whose person key (`person_id`, or `subject_id` in COHORT) is not their primary key in the CDM v5.4 field-level specification: the event, era, period, episode and cohort tables.

#### How it works

A SELECT is reported when all of these hold:

1. it counts rows: `COUNT(*)`, `COUNT(col)`, `SUM(1)`, a windowed count not partitioned by a person key, or `COUNT(DISTINCT …)` of anything but a person key;
2. the count is presented as a count of people: the head noun of its alias is a person noun (`num_persons`, `patients_with_drug`, `overall_patients`, `patients_exposed`), or it counts the person key itself (`COUNT(person_id)`) and the alias does not name something else;
3. its rows are event rows: its FROM / JOIN list reads one of those tables directly, or through a CTE or derived table that neither aggregates, groups, deduplicates nor limits its rows;
4. it does not group by a person key.

The head noun is the alias's last word once counter words (`n`, `num`, `count`, `total`) are dropped, trailing participles are dropped and the phrase is cut at its first preposition: `patient_visits` names visits and `person_days` names days, so both are left alone. A count labelled outside the derived table that computes it (`SELECT t.c AS num_patients FROM (SELECT COUNT(*) AS c FROM …) t`) is followed.

Two reads do not raise the grain. An observation_period confined to the one period containing a date (start `<=` date `<=` end, `BETWEEN`, or non-negative date differences) is one row per person, because a person's observation periods "will not overlap". payer_plan_period is never treated this way, since its periods may overlap. A table filtered to `<primary key> IN (SELECT MIN(<primary key>) … GROUP BY person_id)` keeps one row per person.

#### Examples

**Violation patterns:**

```sql
SELECT drug_concept_id, COUNT(person_id) AS num_persons
FROM drug_exposure
GROUP BY drug_concept_id;
```

*the alias names patients; the rows are exposures*

```sql
SELECT drug_concept_id, COUNT(*) AS patients_with_drug
FROM drug_exposure
GROUP BY drug_concept_id;
```

*a subject can have several records per cohort*

```sql
SELECT COUNT(*) AS num_subjects FROM cohort WHERE cohort_definition_id = 1;
```

**Correct patterns:**

```sql
SELECT drug_concept_id, COUNT(DISTINCT person_id) AS num_persons
FROM drug_exposure
GROUP BY drug_concept_id;
```

*one row per person before counting*

```sql
WITH f AS (
  SELECT person_id, MIN(drug_exposure_start_date) AS first_date
  FROM drug_exposure GROUP BY person_id
)
SELECT COUNT(*) AS persons FROM f;
```

*the observation period that contains the index date*

```sql
WITH su AS (
  SELECT person_id, MIN(drug_exposure_start_date) AS index_date
  FROM drug_exposure GROUP BY person_id
)
SELECT COUNT(*) AS persons
FROM su
JOIN observation_period op ON su.person_id = op.person_id
WHERE su.index_date BETWEEN op.observation_period_start_date AND op.observation_period_end_date;
```

*counting records on purpose*

```sql
SELECT person_id, COUNT(*) AS patient_visits FROM visit_occurrence GROUP BY person_id;
```

#### Suggested fix

REPLACE: `COUNT(*)` / `COUNT(person_id)` WITH `COUNT(DISTINCT person_id)` (`subject_id` for cohort), or first reduce the rows to one per person (GROUP BY person_id in a subquery) before counting.

#### Sources

- [PostgreSQL: Value Expressions, aggregate expressions (count(*) vs count(distinct ...))](https://www.postgresql.org/docs/current/sql-expressions.html#SYNTAX-AGGREGATES)
- [The Book of OHDSI, ch. 4: The Common Data Model, General conventions of the model (person-centric event tables)](https://ohdsi.github.io/TheBookOfOhdsi/CommonDataModel.html#model-Conv)
- [OMOP CDM v5.4 specification: OBSERVATION_PERIOD](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation_period)
- [OMOP CDM v5.4 specification: PAYER_PLAN_PERIOD](https://ohdsi.github.io/CommonDataModel/cdm54.html#payer_plan_period)
- [OMOP CDM v5.4 specification: COHORT](https://ohdsi.github.io/CommonDataModel/cdm54.html#cohort)

---

### 4. Numeric Value Without Unit Constraint { #domain-specific-numeric-value-without-unit-constraint }

**Rule ID:** `domain_specific.numeric_value_without_unit_constraint`
**Severity:** WARNING

#### Intent

CDM v5.4: "At present, there isn't a prescribed unit for individual measurements" and "There is currently no recommended unit for individual observation concepts". The same concept can therefore carry values in different units (HbA1c in % or mmol/mol, glucose in mg/dL or mmol/L), and an aggregate or a fixed cut-off computed across them mixes scales. `dose_era.dose_value` has the same structure.

#### How it works

The rule reports an aggregate (AVG, SUM, MIN, MAX, STDDEV, VARIANCE, MEDIAN, percentiles) of `measurement.value_as_number`, `observation.value_as_number` or `dose_era.dose_value`, or a comparison of the value with a non-zero numeric literal (in a filter or a CASE condition), unless the unit of the same row is constrained. A constraint is `unit_concept_id = <id>`, an `IN` list or subquery (the Unit domain has several concepts for one scale), `unit_concept_id IS NULL` (no unit), `GROUP BY` / `PARTITION BY unit_concept_id`, a join to a `concept` row that is filtered or grouped, or a unit test in the same CASE condition or aggregate. Rows are followed through casts, CTEs and derived tables, so a constraint on another alias, another UNION branch or another table's unit does not count, and equating two rows' units fixes neither. Comparisons with 0 are not reported. At most one finding per table and statement.

#### Examples

**Violation patterns:**

```sql
SELECT AVG(m.value_as_number) FROM measurement m
WHERE m.measurement_concept_id = 3004410;
```

*threshold with no unit*

```sql
SELECT DISTINCT m.person_id FROM measurement m
WHERE m.measurement_concept_id = 3004410
  AND m.value_as_number > 7;
```

*the unit is constrained on another row*

```sql
SELECT a.person_id
FROM measurement a JOIN measurement b ON a.person_id = b.person_id
WHERE a.value_as_number > 7 AND b.unit_concept_id = 8554;
```

**Correct patterns:**

```sql
SELECT AVG(m.value_as_number) FROM measurement m
WHERE m.measurement_concept_id = 3004410
  AND m.unit_concept_id = 8554;
```

*one aggregate per unit*

```sql
SELECT unit_concept_id, AVG(value_as_number)
FROM measurement
WHERE measurement_concept_id = 3004410
GROUP BY unit_concept_id;
```

*a quantity without unit*

```sql
SELECT person_id FROM measurement
WHERE measurement_concept_id = 3022217
  AND value_as_number > 3.0
  AND unit_concept_id IS NULL;
```

#### Suggested fix

ADD: `AND m.unit_concept_id = <unit_concept_id>` (or IN (...) of concepts for the same scale), or `AND m.unit_concept_id IS NULL` when the quantity has no unit; OR GROUP BY m.unit_concept_id so each aggregate is in one unit; OR convert explicitly with CASE WHEN m.unit_concept_id = ... THEN ... END before aggregating.

#### Sources

- [OMOP CDM v5.4 specification: MEASUREMENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#measurement)
- [OMOP CDM v5.4 specification: OBSERVATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation)
- [OMOP CDM v5.4 specification: DOSE_ERA](https://ohdsi.github.io/CommonDataModel/cdm54.html#dose_era)

---

### 5. Value-As Columns Combined With AND { #domain-specific-value-as-columns-conjunction }

**Rule ID:** `domain_specific.value_as_columns_conjunction`
**Severity:** WARNING

#### Intent

`measurement` and `observation` store a result in the `value_as_*` column matching its type: `value_as_number`, `value_as_concept_id` and, on observation, `value_as_string`. CDM v5.4 allows both `value_as_number` and `value_as_concept_id` to be filled when the source gives both, but many ETLs store one value per result. AND-ing value conditions on two of these columns of the same row keeps only rows where both are populated, which on many databases is few or none.

#### How it works

The rule collects value conditions (comparisons, `IN`, `BETWEEN`, `LIKE`) on the `value_as_*` columns from the WHERE, HAVING and JOIN ON clauses of every SELECT, follows each column to the table row it reads (through casts, CTEs and derived tables), and reports a row with conditions on two different value columns. Two rows of a self-join stay apart. Not value conditions: a conjunct that tests several value columns (an OR, a CASE), a conjunct that also accepts the column being NULL, NULL tests, conditions under NOT, and joins of `value_as_concept_id` to `concept.concept_id`.

#### Examples

**Violation patterns:**

```sql
SELECT person_id FROM measurement
WHERE value_as_number > 140 AND value_as_concept_id = 4126681;
```

*split between JOIN ON and WHERE*

```sql
SELECT m.person_id FROM person p
JOIN measurement m ON m.person_id = p.person_id AND m.value_as_number > 140
WHERE m.value_as_concept_id = 4126681;
```

**Correct patterns:**

```sql
SELECT person_id FROM measurement
WHERE value_as_number > 140 OR value_as_concept_id = 4126681;
```

*the coded value may be absent*

```sql
SELECT person_id FROM measurement
WHERE value_as_number > 140
  AND (value_as_concept_id IS NULL OR value_as_concept_id = 0);
```

*two different rows*

```sql
SELECT a.person_id
FROM measurement a JOIN measurement b ON a.person_id = b.person_id
WHERE a.value_as_number > 140 AND b.value_as_concept_id = 4126681;
```

#### Suggested fix

REPLACE: `value_as_number <op> <n> AND value_as_concept_id = <id>` WITH the single value_as_* column that matches how the result is recorded, OR use `OR` (in parentheses) to accept either representation.

#### Sources

- [OMOP CDM v5.4 specification: MEASUREMENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#measurement)
- [OMOP CDM v5.4 specification: OBSERVATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation)

---

### 6. Event-Field Polymorphic Resolution { #domain-specific-event-field-polymorphic-resolution }

**Rule ID:** `domain_specific.event_field_polymorphic_resolution`
**Severity:** ERROR (field concept names another table) / WARNING (no restriction, unknown or extra field concepts)

#### Intent

CDM v5.4 has four keys that hold "the primary key of the linked record" in whichever table a sibling field concept names ("the CONCEPT_ID that identifies which table the primary key of the linked record came from"):

| key | field concept column |
|---|---|
| `measurement.measurement_event_id` | `meas_event_field_concept_id` |
| `observation.observation_event_id` | `obs_event_field_concept_id` |
| `note.note_event_id` | `note_event_field_concept_id` |
| `episode_event.event_id` | `episode_event_field_concept_id` |

The field concepts are the CDM vocabulary's 'Field' concepts named `<table>.<primary key>`: `condition_occurrence.condition_occurrence_id` is 1147127 (and 1147663), `visit_occurrence.visit_occurrence_id` 1147070, `drug_exposure.drug_exposure_id` 1147094. A measurement linked to visit 5 and the condition_occurrence with id 5 are unrelated.

#### How it works

For every comparison that matches one of the keys with a column, an `IN` subquery or a literal (in WHERE, JOIN ON, HAVING or USING), the rule collects the restrictions of the field concept that hold for the same row. Both columns are traced through CTEs, derived tables and casts; a restriction on another read of the table does not count, one under an OR counts only inside its own operand, and one in an outer join's ON counts only for that join. The restriction parser is shared with `domain_specific.cost_event_id_polymorphic_resolution`.

- **ERROR**: the key is matched with a table's primary key and the field concept is restricted to the field concept of another table's primary key, or excludes every field concept of the matched table. Every returned row pairs a record with an unrelated row that shares its id.
- **WARNING**: no restriction (wrong only where the column links records of several tables); `IS [NOT] NULL` on the field concept is not a restriction, because it does not say which table the key points into.
- **WARNING**: a restriction to an id that is not the CDM field concept of any primary key, or a list that also admits other tables.

A restriction in either operand order, through a cast, as an `IN` list, a parameter, a scalar subquery, or a field concept chosen by name through a join to `concept`, is recognised. NULL tests on the key itself are not reported.

#### Examples

**Violation patterns:**

*the field concept names visit_occurrence (ERROR)*

```sql
SELECT m.measurement_concept_id, m.value_as_number, co.condition_concept_id
FROM measurement m
JOIN condition_occurrence co
  ON m.measurement_event_id = co.condition_occurrence_id
 AND m.meas_event_field_concept_id = 1147070  -- visit_occurrence.visit_occurrence_id
;
```

*no restriction (WARNING)*

```sql
SELECT e.episode_id, co.condition_concept_id
FROM episode_event ee
JOIN episode e ON ee.episode_id = e.episode_id
JOIN condition_occurrence co ON ee.event_id = co.condition_occurrence_id;
```

*IS NOT NULL does not choose a table (WARNING)*

```sql
SELECT m.measurement_id, co.condition_concept_id
FROM measurement m
JOIN condition_occurrence co ON m.measurement_event_id = co.condition_occurrence_id
WHERE m.meas_event_field_concept_id IS NOT NULL;
```

**Correct patterns:**

```sql
SELECT m.measurement_concept_id, m.value_as_number, co.condition_concept_id
FROM measurement m
JOIN condition_occurrence co
  ON m.measurement_event_id = co.condition_occurrence_id
 AND m.meas_event_field_concept_id = 1147127  -- condition_occurrence.condition_occurrence_id
;
```

```sql
SELECT m.measurement_concept_id, m.value_as_number, co.condition_concept_id
FROM measurement m
JOIN condition_occurrence co
  ON m.measurement_event_id = co.condition_occurrence_id
 AND 1147127 = m.meas_event_field_concept_id;
```

*field concept chosen by name*

```sql
SELECT m.value_as_number
FROM measurement m
JOIN concept f ON f.concept_id = m.meas_event_field_concept_id
JOIN condition_occurrence co ON m.measurement_event_id = co.condition_occurrence_id
WHERE f.concept_name = 'condition_occurrence.condition_occurrence_id';
```

*profiling the link column*

```sql
SELECT COUNT(*) FROM measurement WHERE measurement_event_id IS NOT NULL;
```

#### Suggested fix

ADD (or correct) a restriction on the same row: `<x>_event_field_concept_id = <id>`, where `<id>` is the CDM 'Field' concept named `<table>.<primary key>` of the table matched with the key (condition_occurrence: 1147127, visit_occurrence: 1147070, drug_exposure: 1147094, procedure_occurrence: 1147082, measurement: 1147138). Look it up with `SELECT concept_id FROM concept WHERE vocabulary_id = 'CDM' AND concept_class_id = 'Field' AND concept_name = '<table>.<primary key>'`.

#### Sources

- [OMOP CDM v5.4 specification: MEASUREMENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#measurement)
- [OMOP CDM v5.4 specification: OBSERVATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation)
- [OMOP CDM v5.4 specification: NOTE](https://ohdsi.github.io/CommonDataModel/cdm54.html#note)
- [OMOP CDM v5.4 specification: EPISODE_EVENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#episode_event)

---

### DRUG Domain

### 7. Drug Era Concept Class Validation { #domain-specific-drug-era-concept-class-validation }

**Rule ID:** `domain_specific.drug_era_concept_class_validation`
**Severity:** ERROR (filter admits no Ingredient) / WARNING (filter excludes Ingredient, sits in an outer join's ON, or reaches the era through an OR branch)

#### Intent

CDM v5.4 says of `drug_era.drug_concept_id`: "The drug_concept_id should conform to the concept class 'ingredient' as the drug_era is an era of time where a person is exposed to a particular drug ingredient." The CDM v5.4 field-level specification sets fkClass = Ingredient for `drug_era.drug_concept_id` and `dose_era.drug_concept_id`, and the Data Quality Dashboard checks it with threshold 0. On a conformant CDM a filter on the concept of these fields that keeps only Clinical Drug, Branded Drug or another product class returns nothing. `drug_strength.ingredient_concept_id` is not checked: the specification leaves its fkClass empty, and the vocabulary's drug_strength rows also point at Precise Ingredient concepts.

#### How it works

The rule follows the `concept` row that is matched with one of those fields: a join, a comma join, an `IN` subquery, an `EXISTS`, a CTE or a derived table, through casts. It then judges the `concept_class_id` restrictions that hold for that same concept row (an `=`, `IN`, `LIKE` / `ILIKE`, `<>` or `NOT IN`).

- **ERROR**: the restriction admits no Ingredient (`= 'Clinical Drug'`, `IN ('Branded Drug', 'Clinical Drug')`, `LIKE 'Branded%'`). The query returns no row of the era table.
- **WARNING**: the restriction excludes Ingredient (`<> 'Ingredient'`, `NOT IN ('Ingredient')`). Nothing is left on a conformant CDM, so the shape is useful only as a conformance check.
- **WARNING**: a restriction that admits no Ingredient in a LEFT / RIGHT / FULL join's ON: the concept is never attached and its columns are always NULL.
- **WARNING**: a restriction that admits no Ingredient on a concept matched with the era field only in one branch of an OR (`de.drug_concept_id IN (SELECT concept_id FROM concept WHERE concept_class_id = 'Clinical Drug') OR de.drug_concept_id = 1`, or `ON c.concept_id = de.drug_concept_id OR ...`): that branch selects nothing, but the other branches still return rows.

A list that contains 'Ingredient' is accepted. A class filter on another concept read (a reused alias in another scope, the concept of a drug_exposure row, descendants reached through `concept_ancestor`) or under an OR does not count. A literal that differs from 'Ingredient' only in case is reported by `data_quality.canonical_string_value_validation`, which checks the casing of every vocabulary id.

#### Examples

**Violation patterns:**

*product class on the era's concept (ERROR)*

```sql
SELECT de.person_id FROM drug_era de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.concept_class_id = 'Branded Drug';
```

*through an IN subquery (ERROR)*

```sql
SELECT person_id FROM drug_era
WHERE drug_concept_id IN (
  SELECT concept_id FROM concept WHERE concept_class_id = 'Clinical Drug'
);
```

*excluding Ingredient (WARNING)*

```sql
SELECT de.person_id FROM drug_era de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.concept_class_id <> 'Ingredient';
```

**Correct patterns:**

```sql
SELECT de.person_id FROM drug_era de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.concept_class_id = 'Ingredient';
```

*eras of the ingredients of a product*

```sql
SELECT DISTINCT de.person_id
FROM drug_era de
JOIN concept_ancestor ca ON ca.ancestor_concept_id = de.drug_concept_id
JOIN concept c ON c.concept_id = ca.descendant_concept_id
WHERE c.concept_class_id = 'Clinical Drug';
```

#### Suggested fix

drug_era holds ingredient concepts only. For product-level classes (Clinical Drug, Branded Drug, Clinical Drug Form, …) query drug_exposure instead; to keep the eras of a product's ingredients, map the product to its ingredient through concept_ancestor (ancestor_concept_id = drug_era.drug_concept_id). Drop a `concept_class_id <> 'Ingredient'` filter unless the query is a conformance check.

#### Sources

- [OMOP CDM v5.4 specification: DRUG_ERA](https://ohdsi.github.io/CommonDataModel/cdm54.html#drug_era)
- [The Book of OHDSI, ch. 9: SQL and R, Exposure cohort](https://ohdsi.github.io/TheBookOfOhdsi/SqlAndR.html#exposure-cohort)
- [OHDSI Data Quality Dashboard: CDM v5.4 field-level checks and thresholds (CSV)](https://raw.githubusercontent.com/OHDSI/DataQualityDashboard/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)

---

### 8. Drug Exposure Implausible Value Filter { #domain-specific-drug-exposure-implausible-value-filter }

**Rule ID:** `domain_specific.drug_exposure_implausible_value_filter`
**Severity:** WARNING

#### Intent

CDM v5.4 says of `drug_exposure.days_supply`: "Negative values are not allowed" and "High values (>365 days) should be investigated." The OHDSI Data Quality Dashboard thresholds in the CDM v5.4 field-level specification go further: they flag a `days_supply` outside 1 to 365 and a `quantity` below 0.0000001 as implausible. A filter that admits only such values keeps only the rows those checks flag. The result is typically empty or made of data-quality outliers, which is exactly what a deliberate data-quality query wants, so this is a warning.

#### How it works

The rule evaluates every WHERE, HAVING and inner JOIN ON condition as a whole, with three-valued logic, so NOT, AND and OR are honoured. It reports a condition that admits no value in the plausible range of `days_supply` or `quantity`. The column is followed through casts, CTEs and derived tables and may carry a constant offset (`days_supply - 1 < 0`). Predicates on other columns are unknown, which can only make the rule fire less.

A condition that still admits a plausible value (`days_supply > 0`, `quantity >= 1`, `NOT (quantity <= 0)`, `days_supply IN (0, 30)`) is not reported. Neither is a condition that removes rows instead of selecting them: under `NOT EXISTS` / `NOT IN`, in the right branch of EXCEPT, or in an outer join's ON.

#### Examples

**Violation patterns:**

```sql
SELECT person_id FROM drug_exposure
WHERE days_supply = 0;
```

```sql
SELECT person_id FROM drug_exposure
WHERE quantity <= 0;
```

```sql
SELECT person_id FROM drug_exposure
WHERE days_supply NOT BETWEEN 1 AND 365;
```

**Correct patterns:**

```sql
SELECT person_id FROM drug_exposure
WHERE days_supply > 0;
```

*excluding the persons who have an implausible record*

```sql
SELECT p.person_id FROM person p
WHERE NOT EXISTS (
  SELECT 1 FROM drug_exposure de
  WHERE de.person_id = p.person_id AND de.days_supply > 365
);
```

#### Suggested fix

REPLACE: a filter such as `days_supply = 0`, `days_supply > 365` or `quantity <= 0` WITH one that keeps plausible rows (`days_supply BETWEEN 1 AND 365`, `quantity > 0`). If the query is a deliberate data-quality check for out-of-range values, this warning can be ignored.

#### Sources

- [OHDSI Data Quality Dashboard: CDM v5.4 field-level checks and thresholds (CSV)](https://raw.githubusercontent.com/OHDSI/DataQualityDashboard/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)
- [OMOP CDM v5.4 specification: DRUG_EXPOSURE](https://ohdsi.github.io/CommonDataModel/cdm54.html#drug_exposure)

---

### 9. Drug Exposure Quantity Misuse { #domain-specific-drug-exposure-quantity-misuse }

**Rule ID:** `domain_specific.drug_exposure_quantity_misuse`
**Severity:** WARNING

#### Intent

CDM v5.4 defines `drug_exposure.quantity` as an amount, not a duration: "For clinical drugs with fixed dose forms (tablets etc.) the quantity is the number of units/tablets/capsules prescribed or dispensed", and for liquids and injections it is an amount of ingredient. The drug-dose page uses it the same way ("Ingredient dose = quantity x amount_value"). Adding it to a date as a number of days produces an end date that has nothing to do with the exposure: 30 tablets taken twice a day last 15 days, and 160 mg of an injection is not 160 days.

#### How it works

The rule reports `drug_exposure.quantity` used as a number of days: an argument of DATEADD / DATE_ADD / DATEDIFF / DATE_SUB / TIMESTAMPDIFF and similar functions, an operand of `+` / `-` together with a `*_date` / `*_datetime` column, or the value of an INTERVAL. The column is followed through casts, CTEs and derived tables, so a renamed `quantity` is still recognised. Dividing quantity by a duration (units per day) and multiplying it by a strength (dose) are not reported.

#### Examples

**Violation patterns:**

*PostgreSQL / DuckDB (quantity is a float, so the date arithmetic needs a cast)*

```sql
SELECT person_id,
       drug_exposure_start_date + CAST(quantity AS INTEGER) AS end_date
FROM drug_exposure;
```

*SQL Server*

```sql
SELECT DATEADD(day, quantity, drug_exposure_start_date) AS end_date
FROM drug_exposure;
```

**Correct patterns:**

```sql
SELECT person_id,
       drug_exposure_start_date + days_supply AS end_date
FROM drug_exposure;
```

*units per day of the exposure*

```sql
SELECT quantity / NULLIF(drug_exposure_end_date - drug_exposure_start_date + 1, 0) AS units_per_day
FROM drug_exposure;
```

#### Suggested fix

REPLACE: `<start_date> + de.quantity` (or quantity-as-duration arithmetic) WITH `de.drug_exposure_start_date + de.days_supply`, OR use date_diff(de.drug_exposure_end_date, de.drug_exposure_start_date).

#### Sources

- [OMOP CDM v5.4 specification: DRUG_EXPOSURE](https://ohdsi.github.io/CommonDataModel/cdm54.html#drug_exposure)
- [OMOP CDM: Drug Dose Calculation](https://ohdsi.github.io/CommonDataModel/drug_dose.html)

---

### 10. Drug Strength Completeness (Amount vs Concentration) { #domain-specific-drug-strength-numerator-denominator-for-concentration }

**Rule ID:** `domain_specific.drug_strength_numerator_denominator_for_concentration`
**Severity:** WARNING

#### Intent

The CDM v5.4 conventions say: "Ingredient strength information is provided either as absolute amount (usually for solid formulations) or as concentration (usually for liquid formulations)." An absolute amount is stored in `amount_value` / `amount_unit_concept_id`; a concentration in `numerator_value` / `numerator_unit_concept_id` over `denominator_unit_concept_id`, with `amount_value` empty. The drug-dose page adds that an inhaler's strength may be in the numerator, and the vocabulary stores rates (transdermal systems, some extended-release forms) the same way. A query that reads only `amount_value` misses all of these drugs. An analysis may be meant for fixed-dose forms only, so this is a warning.

#### How it works

Each read of `drug_strength` (one FROM / JOIN entry, followed through CTEs, derived tables and casts) is judged on its own:

- `amount_value` used (projected, computed with, aggregated, grouped or ordered on) while `numerator_value` of the same read is never used: the value is NULL for concentration and rate rows.
- `amount_value` filtered (a comparison, `IN`, `BETWEEN`, `LIKE` or `IS NOT NULL` in WHERE, JOIN ON or HAVING) while no condition on the same read uses `numerator_value`: the filter drops those rows, whatever is projected.

A read restricted to the amount model (`numerator_value IS NULL`, `numerator_unit_concept_id IS NULL` or `denominator_unit_concept_id IS NULL`) states that intent and is not reported, nor is `amount_value IS NULL`, which selects the concentration rows. `numerator_value` used on another read of the table (another UNION branch, another alias) does not count.

#### Examples

**Violation patterns:**

```sql
SELECT drug_concept_id, amount_value FROM drug_strength
WHERE amount_value > 500;
```

*a dose total: concentration rows add nothing*

```sql
SELECT de.person_id, SUM(de.quantity * ds.amount_value) AS total_dose
FROM drug_exposure de
JOIN drug_strength ds ON ds.drug_concept_id = de.drug_concept_id
GROUP BY de.person_id;
```

**Correct patterns:**

```sql
SELECT drug_concept_id,
       COALESCE(amount_value, numerator_value) AS strength_value,
       COALESCE(amount_unit_concept_id, numerator_unit_concept_id) AS strength_unit_concept_id,
       denominator_value, denominator_unit_concept_id
FROM drug_strength;
```

*fixed-dose forms only, stated*

```sql
SELECT ds.drug_concept_id FROM drug_strength ds
WHERE ds.amount_value = 500 AND ds.numerator_value IS NULL;
```

#### Suggested fix

REPLACE: `amount_value` WITH `COALESCE(amount_value, numerator_value)` and carry its unit, `COALESCE(amount_unit_concept_id, numerator_unit_concept_id)`, together with denominator_value / denominator_unit_concept_id: a concentration's numerator is per denominator unit (e.g. 125 mg per 5 mL), so compare or compute with the units in view. If only fixed-dose forms are meant, say so with `numerator_value IS NULL`.

#### Sources

- [OMOP CDM: Data Model Conventions, Drug strength](https://ohdsi.github.io/CommonDataModel/dataModelConventions.html#drug-strength)
- [OMOP CDM: Drug Dose Calculation](https://ohdsi.github.io/CommonDataModel/drug_dose.html)
- [OMOP CDM v5.4 specification: DRUG_STRENGTH](https://ohdsi.github.io/CommonDataModel/cdm54.html#drug_strength)

---

### MEASUREMENT Domain

### 11. Measurement Operator Concept Validation { #domain-specific-measurement-operator-concept-validation }

**Rule ID:** `domain_specific.measurement_operator_concept_validation`
**Severity:** ERROR (row filter) / WARNING (CASE, OR or outer-join branch)

#### Intent

CDM v5.4 restricts `measurement.operator_concept_id` to the 'Meas Value Operator' domain: "Operators are <, <=, =, >=, > and these concepts belong to the 'Meas Value Operator' domain." A test for a concept id outside that domain can never be true on a conformant CDM.

#### How it works

The rule judges `operator_concept_id = <id>` and `operator_concept_id IN (<ids>)`, with the column followed through casts, CTEs and derived tables to `measurement`. It reports a test whose ids are all outside the domain (the domain's seven standard concepts, generated from the vocabulary, are accepted).

- As a row filter (a conjunct of WHERE, HAVING or an inner join's ON) the query returns no rows: **ERROR**.
- In a CASE, under OR or in an outer join's ON, that branch is dead: **WARNING**.

0 (an unmapped source operator), a list that contains a valid operator or 0, `<>`, `NOT IN` and ordering tests such as `> 0` are satisfiable and are not reported.

#### Examples

**Violation patterns:**

*8554 is the unit 'percent', not an operator (ERROR)*

```sql
SELECT person_id, value_as_number FROM measurement
WHERE measurement_concept_id = 3004410
  AND operator_concept_id = 8554;
```

*through a derived table (ERROR)*

```sql
SELECT d.person_id
FROM (SELECT person_id, operator_concept_id FROM measurement) d
WHERE d.operator_concept_id IN (8554, 201826);
```

*the CASE branch is never taken (WARNING)*

```sql
SELECT SUM(CASE WHEN operator_concept_id = 8554 THEN 1 ELSE 0 END) AS n_below_limit
FROM measurement;
```

**Correct patterns:**

*4171756 is '<'*

```sql
SELECT person_id, value_as_number FROM measurement
WHERE measurement_concept_id = 3004410
  AND operator_concept_id = 4171756;
```

*exact results: no operator, unmapped, or '='*

```sql
SELECT person_id FROM measurement
WHERE value_as_number > 7
  AND (operator_concept_id IS NULL OR operator_concept_id IN (0, 4172703));
```

*an exclusion*

```sql
SELECT person_id FROM measurement
WHERE operator_concept_id NOT IN (4171756, 4171754);
```

#### Suggested fix

REPLACE: the concept id WITH an operator concept: 4171756 (<), 4171754 (<=), 4172703 (=), 4171755 (>=), 4172704 (>). Use `operator_concept_id IS NULL OR operator_concept_id IN (0, 4172703)` for results recorded as an exact value.

#### Sources

- [OMOP CDM v5.4 specification: MEASUREMENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#measurement)
- [OHDSI Data Quality Dashboard: CDM v5.4 field-level checks and thresholds (CSV)](https://raw.githubusercontent.com/OHDSI/DataQualityDashboard/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)

---

### 12. Measurement Range Low/High Validation { #domain-specific-measurement-range-low-high-validation }

**Rule ID:** `domain_specific.measurement_range_low_high_validation`
**Severity:** WARNING

#### Intent

CDM v5.4 stores the lower and upper limit of normal provided by the source in `measurement.range_low` and `range_high`. A row whose lower limit is above its upper limit is a data error. A filter that holds only for such rows returns exactly those rows: what a data-quality audit wants, and not a clinical criterion. The CDM does not forbid the rows, so the rule is a warning.

#### How it works

The rule reports a row filter (a conjunct of WHERE, HAVING or an inner join's ON, outside NOT / NOT EXISTS / EXCEPT) that can hold only when `range_low > range_high` on one measurement row:

- a strict comparison of the two columns of the same row, in either operand order or as `range_low - range_high > 0`;
- literal bounds on the two columns of the same row that leave no room, such as `range_low >= 150 AND range_high < 100`.

Columns are followed through casts, CTEs and derived tables, and bounds are collected per row, so a self-join, UNION branches or a subquery that constrain different rows are not combined. `range_low >= range_high` is not reported, because a source may give one reference value as both limits.

#### Examples

**Violation patterns:**

```sql
SELECT person_id, value_as_number FROM measurement
WHERE measurement_concept_id = 3004410
  AND range_low > range_high;
```

*literal bounds that require an inverted range*

```sql
SELECT person_id FROM measurement
WHERE range_low >= 150
  AND range_high < 100;
```

**Correct patterns:**

```sql
SELECT person_id, value_as_number FROM measurement
WHERE measurement_concept_id = 3004410
  AND value_as_number > range_high;
```

*out-of-range results*

```sql
SELECT * FROM measurement
WHERE value_as_number < range_low
   OR value_as_number > range_high;
```

*overlapping bounds*

```sql
SELECT * FROM measurement
WHERE range_low >= 50
  AND range_high <= 200;
```

*bounds on two different rows*

```sql
SELECT a.person_id
FROM measurement a JOIN measurement b ON a.person_id = b.person_id
WHERE a.range_low > 100 AND b.range_high < 50;
```

#### Suggested fix

REMOVE: the filter, unless the query audits reference ranges on purpose. To find abnormal results compare the value with the range: `value_as_number < range_low OR value_as_number > range_high`.

#### Sources

- [OMOP CDM v5.4 specification: MEASUREMENT](https://ohdsi.github.io/CommonDataModel/cdm54.html#measurement)

---

### NOTE Domain

### 13. Note NLP Snippet Misuse { #domain-specific-note-nlp-snippet-misuse }

**Rule ID:** `domain_specific.note_nlp_snippet_misuse`
**Severity:** WARNING

#### Intent

CDM v5.4 defines `note_nlp.snippet` as "a small window of text surrounding the term" and `note_nlp.lexical_variant` as "raw text extracted from the NLP tool". Both are free text kept for context; the coded result of the extraction is `note_nlp_concept_id`. Selecting findings by matching the strings misses spelling, abbreviation and casing variants and catches substrings.

#### How it works

In WHERE or JOIN ON, the rule reports a snippet / lexical_variant column (through casts, LOWER / UPPER / TRIM, CTEs and derived tables) that is compared with `=` / `IN` to non-empty text, matched with LIKE / ILIKE (also `LIKE ANY`), a regular expression or SIMILAR TO against a real pattern, searched with POSITION / STRPOS / CHARINDEX, or equated to `concept.concept_name` / `concept_synonym.concept_synonym_name`, unless the same note_nlp row is also selected by `note_nlp_concept_id`. Matching the text of rows already selected by concept (a negation or context cue) or of unmapped rows (`note_nlp_concept_id = 0`) is not reported. Projections, presence checks (`IS NOT NULL`, `<> ''`, `LIKE '%'`), lengths and CASE logic are not reported.

#### Examples

**Violation patterns:**

```sql
SELECT note_nlp_id FROM note_nlp
WHERE snippet LIKE '%diabetes%';
```

*text joined to concept names*

```sql
SELECT n.note_id, c.concept_id
FROM note_nlp n
JOIN concept c ON LOWER(n.lexical_variant) = LOWER(c.concept_name);
```

*substring search*

```sql
SELECT note_nlp_id FROM note_nlp
WHERE POSITION('diabetes' IN snippet) > 0;
```

**Correct patterns:**

```sql
SELECT note_nlp_id, snippet FROM note_nlp
WHERE note_nlp_concept_id = 201820;
```

*a context cue on rows selected by concept*

```sql
SELECT note_id FROM note_nlp
WHERE note_nlp_concept_id = 201820
  AND snippet NOT ILIKE '%family history%';
```

*unmapped rows*

```sql
SELECT lexical_variant, COUNT(*) FROM note_nlp
WHERE note_nlp_concept_id = 0
  AND lexical_variant ILIKE '%covid%'
GROUP BY lexical_variant;
```

#### Suggested fix

REPLACE: text matches on note_nlp.snippet / note_nlp.lexical_variant WITH `WHERE note_nlp.note_nlp_concept_id = <id>` (or IN (...)). Keep the text columns in SELECT for context, or as an extra filter on rows already selected by concept.

#### Sources

- [OMOP CDM v5.4 specification: NOTE_NLP](https://ohdsi.github.io/CommonDataModel/cdm54.html#note_nlp)

---

### OBSERVATION Domain

### 14. Observation Value As Concept Confusion { #domain-specific-observation-value-as-concept-confusion }

**Rule ID:** `domain_specific.observation_value_as_concept_confusion`
**Severity:** WARNING

#### Intent

In CDM v5.4 a source code that carries two clinical ideas is split into `observation_concept_id` (its 'Maps to' target, the question) and `value_as_concept_id` (its 'Maps to value' target, the answer). A filter that requires the same concept in both columns of one row matches only rows where question and answer coincide, which usually means the concept was put in the wrong column. The CDM allows such rows, so the rule is a warning.

#### How it works

For each observation row (followed through casts, CTEs and derived tables), the rule combines the row filters (conjuncts of WHERE, HAVING or an inner join's ON, outside NOT). It reports when both columns are restricted to the same set of literal concept ids, or when the two columns are equated. Overlapping but different lists, conditions on two different rows, OR branches and 0 (unmapped) are not reported. Concepts that some source concept maps to with both 'Maps to' and 'Maps to value' in the vocabulary (for example 903657 'Cigarette smoker') are not reported either, because a conformant ETL writes them to both columns.

#### Examples

**Violation patterns:**

```sql
SELECT person_id FROM observation
WHERE observation_concept_id = 134057
  AND value_as_concept_id = 134057;
```

*the two columns of one row equated*

```sql
SELECT person_id FROM observation
WHERE observation_concept_id = value_as_concept_id;
```

**Correct patterns:**

*Z82.4: 'Family history of clinical finding' with value 'Disorder of cardiovascular system'*

```sql
SELECT person_id FROM observation
WHERE observation_concept_id = 4167217
  AND value_as_concept_id = 134057;
```

*two different rows*

```sql
SELECT o1.person_id
FROM observation o1 JOIN observation o2 ON o1.person_id = o2.person_id
WHERE o1.observation_concept_id = 134057
  AND o2.value_as_concept_id = 134057;
```

*a concept the vocabulary maps to as question and as answer*

```sql
SELECT person_id FROM observation
WHERE observation_concept_id = 903657
  AND value_as_concept_id = 903657;
```

#### Suggested fix

REPLACE: one of the two concept ids, so that observation_concept_id holds what was observed (the 'Maps to' target) and value_as_concept_id holds its value (the 'Maps to value' target); keep the filter only when auditing rows where the two coincide.

#### Sources

- [OMOP CDM v5.4 specification: OBSERVATION](https://ohdsi.github.io/CommonDataModel/cdm54.html#observation)
- [The Book of OHDSI, ch. 5: Standardized Vocabularies, Mapping relationships ('Maps to value')](https://ohdsi.github.io/TheBookOfOhdsi/StandardizedVocabularies.html#conceptMapping)

---

### PERSON Domain

### 15. Person Birth Field Validation { #domain-specific-person-birth-field-validation }

**Rule ID:** `domain_specific.person_birth_field_validation`
**Severity:** ERROR (inverted BETWEEN) / WARNING (month or day out of range, impossible month/day pair, year before 1850)

#### Intent

CDM v5.4 fills `month_of_birth` and `day_of_birth` from a precise date of birth, and the OHDSI Data Quality Dashboard (DQD) checks the birth fields against plausible ranges: month 1-12, day 1-31, year from 1850. A row filter that admits no value in those ranges can match rows only in non-conformant data. The DQD thresholds tolerate a share of violating rows, so the CDM does not guarantee that none exist, and DQD's own check query selects exactly those rows: such a filter is a WARNING. An inverted `BETWEEN` is empty for any data and is an ERROR.

#### How it works

The rule computes the values a row filter admits (`=`, `IN`, `BETWEEN`, or a one-sided comparison; any literal, quoted or cast) on `person.year_of_birth`, `month_of_birth`, `day_of_birth`, or a year, month or day extracted from `birth_datetime` (`EXTRACT`, `DATE_PART`, `YEAR()`, `MONTH()`, `DAY()`). Columns are followed through CTEs and derived tables. Only conjuncts of WHERE, HAVING or an inner join's ON count, outside NOT / NOT EXISTS / EXCEPT.

- an inverted `BETWEEN` on any birth field (lower bound above upper bound): **ERROR**, empty for any data.
- month or day with no value in 1-12 / 1-31, or a month/day pair that never occurs (30 February): **WARNING**, rows can match only in non-conformant data.
- year_of_birth only before 1850: **WARNING**, DQD treats such years as implausible, not impossible.

There is no upper bound on year_of_birth: DQD derives it from the current date, and date-shifted databases store later years.

#### Examples

**Violation patterns:**

*inverted BETWEEN (ERROR)*

```sql
SELECT person_id FROM person
WHERE year_of_birth BETWEEN 2000 AND 1990;
```

*month out of range: only non-conformant rows match (WARNING)*

```sql
SELECT person_id FROM person
WHERE month_of_birth = 13;
```

*one-sided, same set (WARNING)*

```sql
SELECT person_id FROM person
WHERE month_of_birth > 12;
```

*day extracted from birth_datetime (WARNING)*

```sql
SELECT person_id FROM person
WHERE EXTRACT(DAY FROM birth_datetime) = 32;
```

*a date that does not exist (WARNING)*

```sql
SELECT person_id FROM person
WHERE month_of_birth = 2 AND day_of_birth = 30;
```

*only years before 1850 (WARNING)*

```sql
SELECT person_id FROM person
WHERE year_of_birth < 1800;
```

**Correct patterns:**

```sql
SELECT person_id FROM person
WHERE month_of_birth = 12;
```

```sql
SELECT person_id FROM person
WHERE year_of_birth BETWEEN 1950 AND 2000;
```

*a future year on a date-shifted CDM*

```sql
SELECT COUNT(*) FROM person
WHERE year_of_birth = 2092;
```

*a value audit under OR*

```sql
SELECT COUNT(*) FROM person
WHERE month_of_birth = 0 OR month_of_birth IS NULL;
```

<!-- extra-examples:start -->
**Warning variant: month 0 is outside 1-12 like any other non-month: only non-conformant rows match**

```sql
SELECT person_id FROM person
WHERE month_of_birth = 0;
```

<!-- extra-examples:end -->

#### Suggested fix

REPLACE: the literal WITH a value in range: month_of_birth 1-12, day_of_birth 1-31, year_of_birth from 1850; for an inverted BETWEEN, put the lower bound first.

#### Sources

- [OMOP CDM v5.4 specification: PERSON](https://ohdsi.github.io/CommonDataModel/cdm54.html#person)
- [OHDSI Data Quality Dashboard: CDM v5.4 field-level checks and thresholds (CSV)](https://raw.githubusercontent.com/OHDSI/DataQualityDashboard/main/inst/csv/OMOP_CDMv5.4_Field_Level.csv)

---

### VISIT Domain

### 16. Visit Detail Dates Within Parent Visit { #domain-specific-visit-detail-dates-within-parent-visit }

**Rule ID:** `domain_specific.visit_detail_dates_within_parent_visit`
**Severity:** WARNING

#### Intent

CDM v5.4 puts in `visit_detail.visit_occurrence_id` the visit that subsumes the detail, so a visit_detail is expected to lie inside its parent visit, and it may share the visit's dates. A filter that keeps only visit_detail rows dated strictly outside the visit they are linked to selects misnested records: ETL defects, or an audit of them.

#### How it works

The rule reports a row filter (a conjunct of WHERE or an inner join's ON, outside NOT / NOT EXISTS / EXCEPT) that can hold only when a visit_detail starts or ends before its parent visit starts, or after it ends. The visit_detail and visit_occurrence rows must be linked on `visit_occurrence_id` (directly, through another table, or by `USING` / NATURAL joins), followed through casts, CTEs and derived tables. Strict `<` / `>` count, as do differences (`vd.visit_detail_start_date - vo.visit_start_date < 0`) and offsets that widen the gap; `<=` / `>=` count only with a widening offset, since equal dates are allowed. Dates are compared with dates and datetimes with datetimes.

#### Examples

**Violation patterns:**

```sql
SELECT vd.visit_detail_id FROM visit_detail vd
JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
WHERE vd.visit_detail_start_date < vo.visit_start_date;
```

*datetimes*

```sql
SELECT vd.visit_detail_id FROM visit_detail vd
JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
WHERE vd.visit_detail_end_datetime > vo.visit_end_datetime;
```

**Correct patterns:**

```sql
SELECT vd.visit_detail_id FROM visit_detail vd
JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
WHERE vd.visit_detail_start_date = vo.visit_start_date;
```

*same-day admission*

```sql
SELECT vd.visit_detail_id FROM visit_detail vd
JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
WHERE vd.visit_detail_start_date <= vo.visit_start_date;
```

*an audit that counts both kinds under OR*

```sql
SELECT COUNT(*) FROM visit_detail vd
JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
WHERE vd.visit_detail_start_date < vo.visit_start_date
   OR vd.visit_detail_end_date > vo.visit_end_date;
```

#### Suggested fix

REPLACE: the outside-the-visit comparison WITH the nesting it violates, `vd.visit_detail_start_date >= vo.visit_start_date AND vd.visit_detail_end_date <= vo.visit_end_date`, or remove it. Keep it only when auditing misnested records on purpose.

#### Sources

- [OMOP CDM v5.4 specification: VISIT_DETAIL](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_detail)

---

### 17. Visit Detail Visit Occurrence Linkage { #domain-specific-visit-detail-visit-occurrence-reference }

**Rule ID:** `domain_specific.visit_detail_visit_occurrence_reference`
**Severity:** WARNING

#### Intent

CDM v5.4 links every `visit_detail` row to the visit that subsumes it through the required foreign key `visit_detail.visit_occurrence_id`. A SELECT that combines `visit_detail` and `visit_occurrence` rows of one person without equating `visit_occurrence_id` pairs each detail with every visit of the person. That is a mistake unless the query is about the person's other visits, so the rule is a warning.

#### How it works

Each SELECT is judged on its own. Rows are identified by lineage (through casts, CTEs, derived tables and correlated references), and equalities are followed through other tables' columns (`vd.person_id = c.subject_id = vo.person_id`). The rule fires when the two rows are tied by `person_id` and their `visit_occurrence_id` values are never equated. It is silent when:

- the visit ids are equated, directly, through `CAST`, `USING` or another table;
- both rows are fixed by an event row's `visit_detail_id` and `visit_occurrence_id`;
- the visit_detail is also joined to its own parent visit;
- a row filter excludes the parent visit: `<>` on the visit ids, `preceding_visit_occurrence_id = vd.visit_occurrence_id`, or a date comparison that places the visit after the detail ends or before it starts (`vo.visit_start_date > vd.visit_detail_end_date`, `vo.visit_start_date BETWEEN vd.visit_detail_end_date AND …`), which the parent satisfies at most on a shared boundary day;
- duplicated rows cannot change the result (`IN` / `EXISTS`, `DISTINCT`, only `COUNT(DISTINCT …)` / `MIN` / `MAX`, or one row kept per `ROW_NUMBER()` partition) and only one of the two rows is read beyond `person_id`;
- both rows are independent outer-joined look-ups of a third row and the query keeps one row per partition (`ROW_NUMBER() … = 1`).

Joins on a non-person key cross persons and are left to the person-linkage and primary-key join rules.

#### Examples

**Violation patterns:**

```sql
SELECT vd.visit_detail_id, vd.visit_detail_concept_id, vo.visit_concept_id
FROM visit_detail vd
JOIN visit_occurrence vo ON vd.person_id = vo.person_id;
```

*details of any person with an inpatient visit, not details inside one*

```sql
SELECT DISTINCT vd.visit_detail_id
FROM visit_detail vd
JOIN visit_occurrence vo ON vd.person_id = vo.person_id
WHERE vo.visit_concept_id = 9201;
```

**Correct patterns:**

```sql
SELECT vd.visit_detail_id, vd.visit_detail_concept_id, vo.visit_concept_id
FROM visit_detail vd
JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id;
```

*later visits of the person (readmission)*

```sql
SELECT vd.visit_detail_id, vo.visit_occurrence_id
FROM visit_detail vd
JOIN visit_occurrence vo
  ON vd.person_id = vo.person_id
 AND vo.visit_start_date > vd.visit_detail_end_date;
```

*existence only*

```sql
SELECT vd.visit_detail_id
FROM visit_detail vd
WHERE EXISTS (SELECT 1 FROM visit_occurrence vo WHERE vo.person_id = vd.person_id);
```

#### Suggested fix

ADD: `vd.visit_occurrence_id = vo.visit_occurrence_id` to the join, or exclude the parent visit explicitly (`vd.visit_occurrence_id <> vo.visit_occurrence_id`) when the other visits of the person are intended.

#### Sources

- [OMOP CDM v5.4 specification: VISIT_DETAIL](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_detail)

---

### 18. Visit Length-of-Stay Arithmetic { #domain-specific-visit-length-of-stay-arithmetic }

**Rule ID:** `domain_specific.visit_length_of_stay_arithmetic`
**Severity:** WARNING

#### Intent

CDM v5.4: "The Visit duration, or 'length of stay', is defined as VISIT_END_DATE - VISIT_START_DATE. For all Visits this is <1 day, except Inpatient Visits and Non-hospital institution Visits." Averaging length of stay over all visit types mixes many same-day zeros into the result, and requiring two or more days of stay on outpatient or ER visits keeps only records that break the convention.

#### How it works

Length of stay is `visit_end_date - visit_start_date`, `DATEDIFF(day, …)` or `DATE_DIFF(…)` of one visit row, followed through casts, CTEs and derived tables.

- **Diluted average:** LOS aggregated with AVG, MEDIAN, percentiles, STDDEV, VARIANCE or MIN (inside the aggregate, or projected by a CTE / derived table and aggregated outside) over visit types that mix multi-day and same-day visits. Not reported when the visit row is restricted to the inpatient hierarchy (9201, 262, 42898160 and descendants), restricted in a way that cannot be read statically (a subquery, a join to concept, `<>`, a care_site place-of-service filter), restricted to visits that end after they start, or when the aggregate is grouped or partitioned by the row's visit_concept_id. SUM is not checked.
- **Multi-day threshold on same-day visits:** the visit row is restricted to the outpatient / ER hierarchy and a row filter requires two or more days of stay on dates (`>= 2`, `> 1`, `BETWEEN 5 AND 10`, `visit_end_date > visit_start_date + 1`). A one-day difference is not reported.

Restrictions and filters count only for the visit row the LOS is computed from; a filter on another row (a subquery, another alias) does not. The concept sets are generated from the vocabulary.

#### Examples

**Violation patterns:**

```sql
SELECT AVG(visit_end_date - visit_start_date) AS avg_los
FROM visit_occurrence;
```

*LOS computed in a derived table*

```sql
SELECT AVG(los)
FROM (SELECT visit_end_date - visit_start_date AS los FROM visit_occurrence) t;
```

*multi-day filter on outpatient visits*

```sql
SELECT visit_occurrence_id FROM visit_occurrence
WHERE visit_concept_id = 9202
  AND visit_end_date - visit_start_date >= 2;
```

**Correct patterns:**

```sql
SELECT AVG(visit_end_date - visit_start_date) AS avg_los
FROM visit_occurrence
WHERE visit_concept_id = 9201;
```

*stratified by visit type*

```sql
SELECT visit_concept_id, AVG(visit_end_date - visit_start_date)
FROM visit_occurrence
GROUP BY visit_concept_id;
```

*an ER visit crossing midnight*

```sql
SELECT visit_occurrence_id FROM visit_occurrence
WHERE visit_concept_id = 9203
  AND visit_end_date - visit_start_date >= 1;
```

#### Suggested fix

ADD: a visit_concept_id restriction to inpatient-like visits before aggregating LOS, e.g. `vo.visit_concept_id IN (SELECT descendant_concept_id FROM concept_ancestor WHERE ancestor_concept_id IN (9201, 262, 42898160))`, OR GROUP BY visit_concept_id; for a multi-day LOS filter, REPLACE the outpatient / ER visit_concept_id with the inpatient one.

#### Sources

- [OMOP CDM v5.4 specification: VISIT_OCCURRENCE](https://ohdsi.github.io/CommonDataModel/cdm54.html#visit_occurrence)

---

### VOCABULARY Domain

### 19. Relationship Flag Comparison { #domain-specific-vocabulary-relationship-boolean-comparison }

**Rule ID:** `domain_specific.vocabulary_relationship_boolean_comparison`
**Severity:** ERROR

#### Intent

`relationship.is_hierarchical` and `relationship.defines_ancestry` are `varchar(1)` columns holding `'1'` or `'0'` (OMOP CDM v5.4). Despite their yes/no meaning they are strings, not booleans. Any other value never matches a row, and in PostgreSQL and BigQuery an integer or boolean operand is a type error.

#### How it works

The rule checks comparisons on the two flag columns, resolved through lineage so a derived column that reuses the name is not checked; `LOWER`, `UPPER` and `TRIM` around the column are looked through. A CTE or derived table that projects the flag through a cast (`is_hierarchical::int AS is_hierarchical`) ends the check.

- In every dialect: a string other than `'1'` / `'0'` (`'true'`, `'Y'`, `'yes'`, `''`), an integer other than 1 / 0, or a `LIKE` pattern that neither `'1'` nor `'0'` matches is reported, since the flag never holds it: `=` / `IN` / `LIKE` match no rows, `<>` / `NOT IN` filter nothing.
- In PostgreSQL and BigQuery: an integer (`= 1`) or boolean (`= TRUE`, `IS TRUE`) operand, or the column used by itself as a condition (`WHERE is_hierarchical`), is reported. Neither engine casts between a string column and an integer or boolean implicitly. Engines that coerce (DuckDB, SQL Server) accept `= 1`, and the rule does not flag it there.

#### Examples

**Violation patterns:**

```sql
SELECT relationship_id FROM relationship
WHERE is_hierarchical = 'yes';
```

*no row holds 'true'*

```sql
SELECT relationship_id FROM relationship WHERE LOWER(is_hierarchical) = 'true';
```

*no such value*

```sql
SELECT relationship_id FROM relationship WHERE defines_ancestry = 2;
```

*PostgreSQL: operator does not exist: character varying = integer*

```sql
SELECT relationship_id FROM relationship WHERE is_hierarchical = 1;
```

*PostgreSQL: argument of IS TRUE must be type boolean*

```sql
SELECT relationship_id FROM relationship WHERE is_hierarchical IS TRUE;
```

*the varchar flag used directly as a condition*

```sql
SELECT relationship_id FROM relationship
WHERE is_hierarchical;
```

**Correct patterns:**

```sql
SELECT relationship_id FROM relationship WHERE is_hierarchical = '1';
```

```sql
SELECT relationship_id FROM relationship WHERE defines_ancestry IN ('0', '1');
```

*an explicit cast*

```sql
SELECT relationship_id FROM relationship WHERE CAST(is_hierarchical AS INTEGER) = 1;
```

<!-- extra-examples:start -->
**Error variant: integer operand: a type error in PostgreSQL and BigQuery**

```sql
SELECT relationship_id FROM relationship
WHERE defines_ancestry = 1;
```

<!-- extra-examples:end -->

#### Suggested fix

REPLACE: the comparison value WITH the string '1' (true) or '0' (false), e.g. `is_hierarchical = '1'`.

#### Sources

- [OMOP CDM v5.4 specification: RELATIONSHIP](https://ohdsi.github.io/CommonDataModel/cdm54.html#relationship)
- [PostgreSQL 8.3 release notes: non-character types are no longer cast to text implicitly](https://www.postgresql.org/docs/8.3/release-8-3.html)
- [PostgreSQL: SELECT, WHERE clause](https://www.postgresql.org/docs/current/sql-select.html#SQL-WHERE)
- [BigQuery: Conversion rules, comparison of casting and coercion](https://cloud.google.com/bigquery/docs/reference/standard-sql/conversion_rules#comparison_chart)

---

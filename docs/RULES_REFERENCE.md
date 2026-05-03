# FastSSV Rules Reference

This document provides comprehensive documentation for FastSSV's validation rules.

**Current registry: 154 rules across 6 categories.**

- **anti_patterns**: 20 rules
- **concept_standardization**: 18 rules
- **data_quality**: 22 rules
- **domain_specific**: 48 rules
- **joins**: 36 rules
- **temporal**: 10 rules

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
| `concept_standardization.concept_ancestor_cross_domain` | [Concept Ancestor Cross-Domain Validation](#concept-standardization-concept-ancestor-cross-domain) | ERROR | concept_standardization |
| `concept_standardization.concept_ancestor_max_levels_misuse` | [Concept Ancestor Max Levels Misuse](#concept-standardization-concept-ancestor-max-levels-misuse) | ERROR | concept_standardization |
| `concept_standardization.concept_ancestor_rollup_direction` | [Concept Ancestor Rollup Direction](#concept-standardization-concept-ancestor-rollup-direction) | ERROR | concept_standardization |
| `concept_standardization.concept_ancestor_self_include_redundancy` | [Concept Ancestor Self-Include Redundancy](#concept-standardization-concept-ancestor-self-include-redundancy) | WARNING | concept_standardization |
| `concept_standardization.concept_class_id_ingredient_for_drug_grouping` | [Concept Class ID Ingredient for Drug Grouping](#concept-standardization-concept-class-id-ingredient-for-drug-grouping) | WARNING | concept_standardization |
| `concept_standardization.concept_domain_validation` | [Concept Domain ID Matches Target Table](#concept-standardization-concept-domain-validation) | WARNING | concept_standardization |
| `concept_standardization.concept_synonym_language_concept_id` | [Concept Synonym Language Concept ID](#concept-standardization-concept-synonym-language-concept-id) | WARNING | concept_standardization |
| `concept_standardization.domain_vocabulary_validation` | [Domain Vocabulary Validation (VOCAB_022-025)](#concept-standardization-domain-vocabulary-validation) | WARNING | concept_standardization |
| `concept_standardization.era_table_standard_concepts` | [Era Tables Use Standard Concepts Only](#concept-standardization-era-table-standard-concepts) | ERROR | concept_standardization |
| `concept_standardization.invalid_reason_enforcement` | [Invalid Reason Enforcement (strict mode)](#concept-standardization-invalid-reason-enforcement) | WARNING (strict-mode-only) | concept_standardization |
| `concept_standardization.maps_to_target_standard_validation` | [Maps To Target Standard Validation](#concept-standardization-maps-to-target-standard-validation) | WARNING | concept_standardization |
| `concept_standardization.multiple_maps_to_targets` | [Multiple Maps To Targets Not Handled](#concept-standardization-multiple-maps-to-targets) | WARNING | concept_standardization |
| `concept_standardization.source_concept_id_standard_filter` | [Source Concept ID Should Not Filter Standard Concepts](#concept-standardization-source-concept-id-standard-filter) | WARNING | concept_standardization |
| `concept_standardization.source_concept_id_warning` | [Source Concept ID Not For Analytical Filtering](#concept-standardization-source-concept-id-warning) | WARNING | concept_standardization |
| `concept_standardization.source_to_concept_map_validation` | [Source to Concept Map Validation](#concept-standardization-source-to-concept-map-validation) | WARNING | concept_standardization |
| `concept_standardization.standard_concept_enforcement` | [Standard Concept Enforcement](#concept-standardization-standard-concept-enforcement) | WARNING | concept_standardization |
| `concept_standardization.standard_concept_value_validation` | [Standard Concept Value Validation](#concept-standardization-standard-concept-value-validation) | ERROR | concept_standardization |
| `concept_standardization.unit_vocabulary_validation` | [Unit Vocabulary Validation](#concept-standardization-unit-vocabulary-validation) | WARNING | concept_standardization |
| `joins.care_site_id_join_validation` | [Care Site ID Join Validation](#joins-care-site-id-join-validation) | ERROR | joins |
| `joins.care_site_join_validation` | [Care Site Join Path Validation](#joins-care-site-join-validation) | WARNING | joins |
| `joins.care_site_location_join_validation` | [Care Site to Location Join Validation](#joins-care-site-location-join-validation) | ERROR | joins |
| `joins.clinical_person_id_linkage_validation` | [Clinical Tables Require Person ID Linkage](#joins-clinical-person-id-linkage-validation) | ERROR | joins |
| `joins.clinical_pk_cross_join_validation` | [Clinical Primary Key Join Validation](#joins-clinical-pk-cross-join-validation) | ERROR | joins |
| `joins.clinical_visit_detail_join_validation` | [Clinical to Visit Detail Join Validation](#joins-clinical-visit-detail-join-validation) | ERROR | joins |
| `joins.cohort_clinical_join_validation` | [Cohort to Clinical Table Join Validation](#joins-cohort-clinical-join-validation) | ERROR | joins |
| `joins.concept_alias_reuse_validation` | [Concept Alias Reuse Validation](#joins-concept-alias-reuse-validation) | ERROR | joins |
| `joins.concept_ancestor_name_resolution` | [Concept Ancestor Name Resolution Validation](#joins-concept-ancestor-name-resolution) | ERROR | joins |
| `joins.concept_concept_class_join_validation` | [Concept to Concept Class Join Validation](#joins-concept-concept-class-join-validation) | ERROR | joins |
| `joins.concept_domain_join_validation` | [Concept to Domain Join Validation](#joins-concept-domain-join-validation) | ERROR | joins |
| `joins.concept_join_validation` | [Concept Join Validation](#joins-concept-join-validation) | ERROR | joins |
| `joins.concept_relationship_concept_join_validation` | [Concept Relationship to Concept Join Validation](#joins-concept-relationship-concept-join-validation) | ERROR | joins |
| `joins.concept_relationship_relationship_join_validation` | [Concept Relationship to Relationship Join Validation](#joins-concept-relationship-relationship-join-validation) | ERROR | joins |
| `joins.concept_relationship_requires_relationship_id` | [Concept Relationship Requires Relationship ID Filter](#joins-concept-relationship-requires-relationship-id) | WARNING | joins |
| `joins.concept_synonym_join_validation` | [Concept Synonym Join Validation](#joins-concept-synonym-join-validation) | ERROR | joins |
| `joins.concept_vocabulary_join_validation` | [Concept to Vocabulary Join Validation](#joins-concept-vocabulary-join-validation) | ERROR | joins |
| `joins.cost_table_domain_validation` | [Cost Table Domain Validation](#joins-cost-table-domain-validation) | WARNING | joins |
| `joins.death_visit_occurrence_join_validation` | [Death to Visit Occurrence Join Validation](#joins-death-visit-occurrence-join-validation) | ERROR | joins |
| `joins.drug_exposure_drug_strength_join_validation` | [Drug Exposure to Drug Strength Join Validation](#joins-drug-exposure-drug-strength-join-validation) | ERROR | joins |
| `joins.era_forbidden_join_validation` | [Era Table Forbidden Join Validation](#joins-era-forbidden-join-validation) | ERROR | joins |
| `joins.fact_relationship_join_validation` | [Fact Relationship Polymorphic Join Validation](#joins-fact-relationship-join-validation) | ERROR | joins |
| `joins.join_path_validation` | [Join Path Validation](#joins-join-path-validation) | WARNING | joins |
| `joins.left_join_then_where_on_right_table` | [Left Join Then Where On Right Table](#joins-left-join-then-where-on-right-table) | WARNING | joins |
| `joins.maps_to_direction` | [Maps To Direction](#joins-maps-to-direction) | WARNING | joins |
| `joins.note_nlp_note_join_validation` | [Note NLP to Note Join Validation](#joins-note-nlp-note-join-validation) | ERROR | joins |
| `joins.observation_period_join_validation`{ #joins-observation-period-join-validation } | [Observation Period Join Requires Date Overlap](#joins-observation-period-join-validation) | WARNING | joins |
| `joins.payer_plan_period_join_validation` | [Payer Plan Period Join Validation](#joins-payer-plan-period-join-validation) | WARNING | joins |
| `joins.person_id_join_validation` | [Person ID Join Validation](#joins-person-id-join-validation) | ERROR | joins |
| `joins.person_location_join_validation` | [Person to Location Join Validation](#joins-person-location-join-validation) | ERROR | joins |
| `joins.preceding_visit_occurrence_validation` | [Preceding Visit Occurrence Validation](#joins-preceding-visit-occurrence-validation) | ERROR | joins |
| `joins.provider_care_site_join_validation` | [Provider to Care Site Join Validation](#joins-provider-care-site-join-validation) | ERROR | joins |
| `joins.provider_join_validation` | [Provider Join Validation](#joins-provider-join-validation) | ERROR | joins |
| `joins.visit_detail_join_validation` | [Visit Detail Join Validation](#joins-visit-detail-join-validation) | WARNING | joins |
| `joins.visit_occurrence_id_join_validation` | [Visit Occurrence ID Join Validation](#joins-visit-occurrence-id-join-validation) | ERROR | joins |
| `joins.visit_occurrence_inner_join_validation` | [Visit Occurrence INNER JOIN Validation](#joins-visit-occurrence-inner-join-validation) | WARNING | joins |
| `temporal.clinical_event_date_in_future_validation` | [Clinical Event Date Should Not Be In Future](#temporal-clinical-event-date-in-future-validation) | WARNING | temporal |
| `temporal.datetime_between_date_literal` | [Datetime BETWEEN with Date Literal](#temporal-datetime-between-date-literal) | WARNING | temporal |
| `temporal.death_date_before_birth_validation` | [Death Date Before Birth Validation](#temporal-death-date-before-birth-validation) | ERROR | temporal |
| `temporal.death_date_in_future_validation` | [Death Date In Future Validation](#temporal-death-date-in-future-validation) | WARNING | temporal |
| `temporal.end_before_start_validation` | [End Before Start Validation](#temporal-end-before-start-validation) | ERROR | temporal |
| `temporal.future_information_leakage` | [Unbounded Follow-up Window (Future Information Leakage)](#temporal-future-information-leakage) | WARNING | temporal |
| `temporal.nullable_end_date_null_handling` | [Nullable End Date NULL Handling](#temporal-nullable-end-date-null-handling) | WARNING | temporal |
| `temporal.observation_period_anchoring` | [Observation Period Anchoring](#temporal-observation-period-anchoring) | WARNING | temporal |
| `temporal.observation_period_date_range_logic` | [Observation Period Date Range Logic](#temporal-observation-period-date-range-logic) | ERROR | temporal |
| `temporal.required_date_column_validation` | [Required Date Column Validation](#temporal-required-date-column-validation) | WARNING | temporal |
| `data_quality.canonical_string_value_validation`{ #data-quality-canonical-string-value-validation } | [Canonical Vocabulary String Value Validation](#data-quality-canonical-string-value-validation) | ERROR | data_quality |
| `data_quality.clinical_event_date_before_1900_validation` | [Clinical Event Date Should Not Be Before 1900](#data-quality-clinical-event-date-before-1900-validation) | WARNING | data_quality |
| `data_quality.column_type_validation` | [Column Type Validation (SCHEMA Layer)](#data-quality-column-type-validation) | ERROR | data_quality |
| `data_quality.concept_id_string_comparison` | [Concept ID String Comparison](#data-quality-concept-id-string-comparison) | WARNING | data_quality |
| `data_quality.concept_name_whitespace` | [Concept Name Whitespace](#data-quality-concept-name-whitespace) | WARNING | data_quality |
| `data_quality.episode_requires_concept_filter` | [Episode Requires Concept Filter](#data-quality-episode-requires-concept-filter) | ERROR | data_quality |
| `data_quality.fact_relationship_no_self_reference` | [Fact Relationship No Self-Reference](#data-quality-fact-relationship-no-self-reference) | WARNING | data_quality |
| `data_quality.fact_relationship_requires_relationship_concept_filter` | [Fact Relationship Requires Relationship Concept Filter](#data-quality-fact-relationship-requires-relationship-concept-filter) | ERROR | data_quality |
| `data_quality.fact_relationship_valid_concepts` | [Fact Relationship Valid Concepts](#data-quality-fact-relationship-valid-concepts) | WARNING | data_quality |
| `data_quality.free_text_column_misuse`{ #data-quality-free-text-column-misuse } | [Free-Text Column Misuse](#data-quality-free-text-column-misuse) | ERROR | data_quality |
| `data_quality.incorrect_percentile_calculation`{ #data-quality-incorrect-percentile-calculation } | [Incorrect Percentile Calculation](#data-quality-incorrect-percentile-calculation) | ERROR | data_quality |
| `data_quality.negative_concept_id_validation` | [Negative Concept ID Validation](#data-quality-negative-concept-id-validation) | ERROR | data_quality |
| `data_quality.non_standard_date_literal_format`{ #data-quality-non-standard-date-literal-format } | [Non-Standard Date Literal Format](#data-quality-non-standard-date-literal-format) | WARNING | data_quality |
| `data_quality.note_nlp_nlp_date_for_temporal_filtering` | [Note NLP nlp_date for Temporal Filtering](#data-quality-note-nlp-nlp-date-for-temporal-filtering) | WARNING | data_quality |
| `data_quality.note_nlp_offset_is_character_position` | [Note NLP Offset is Character Position](#data-quality-note-nlp-offset-is-character-position) | WARNING | data_quality |
| `data_quality.schema_validation` | [OMOP Schema Validation](#data-quality-schema-validation) | ERROR | data_quality |
| `data_quality.source_value_field_usage`{ #data-quality-source-value-field-usage } | [Source Value Field Usage](#data-quality-source-value-field-usage) | WARNING | data_quality |
| `data_quality.standard_concept_null_handling` | [Standard Concept NULL Handling](#data-quality-standard-concept-null-handling) | WARNING | data_quality |
| `data_quality.union_concept_id_domain_indicator` | [Union Concept ID Domain Indicator](#data-quality-union-concept-id-domain-indicator) | WARNING | data_quality |
| `data_quality.union_vs_union_all_clinical_events` | [UNION vs UNION ALL for Clinical Events](#data-quality-union-vs-union-all-clinical-events) | WARNING | data_quality |
| `data_quality.unmapped_concept_handling` | [Unmapped Concept Handling](#data-quality-unmapped-concept-handling) | WARNING | data_quality |
| `data_quality.vocabulary_table_protection` | [Vocabulary Table Protection](#data-quality-vocabulary-table-protection) | ERROR | data_quality |
| `anti_patterns.ambiguous_column_reference` | [Ambiguous Column Reference](#anti-patterns-ambiguous-column-reference) | WARNING | anti_patterns |
| `anti_patterns.attribute_definition_invalid_join` | [Attribute Definition Invalid Join](#anti-patterns-attribute-definition-invalid-join) | ERROR | anti_patterns |
| `anti_patterns.comma_separated_cross_join` | [Comma-Separated Cross Join](#anti-patterns-comma-separated-cross-join) | ERROR | anti_patterns |
| `anti_patterns.concept_ancestor_mixed_with_concept_relationship_redundantly` | [Concept Ancestor Mixed with Concept Relationship Redundantly](#anti-patterns-concept-ancestor-mixed-with-concept-relationship-redundantly) | WARNING | anti_patterns |
| `anti_patterns.concept_code_requires_vocabulary_id` | [Concept Code Requires Vocabulary ID](#anti-patterns-concept-code-requires-vocabulary-id) | WARNING | anti_patterns |
| `anti_patterns.concept_name_lookup` | [Concept Name Lookup Anti-pattern](#anti-patterns-concept-name-lookup) | WARNING | anti_patterns |
| `anti_patterns.concept_relationship_transitive_misuse` | [Concept Relationship Transitive Misuse](#anti-patterns-concept-relationship-transitive-misuse) | WARNING | anti_patterns |
| `anti_patterns.destructive_operations_on_clinical_tables` | [Destructive Operations on Clinical Tables](#anti-patterns-destructive-operations-on-clinical-tables) | ERROR | anti_patterns |
| `anti_patterns.duplicate_column_alias`{ #anti-patterns-duplicate-column-alias } | [Duplicate Column Alias](#anti-patterns-duplicate-column-alias) | WARNING | anti_patterns |
| `anti_patterns.having_without_group_by` | [Having Without Group By](#anti-patterns-having-without-group-by) | ERROR | anti_patterns |
| `anti_patterns.join_key_validation` | [Join Key Validation](#anti-patterns-join-key-validation) | ERROR | anti_patterns |
| `anti_patterns.limit_without_order_by`{ #anti-patterns-limit-without-order-by } | [LIMIT Without ORDER BY](#anti-patterns-limit-without-order-by) | WARNING | anti_patterns |
| `anti_patterns.no_distinct_on_primary_key_column` | [No DISTINCT on Primary Key Column](#anti-patterns-no-distinct-on-primary-key-column) | WARNING | anti_patterns |
| `anti_patterns.no_string_identification` | [No String Identification](#anti-patterns-no-string-identification) | ERROR | anti_patterns |
| `anti_patterns.null_comparison_operator`{ #anti-patterns-null-comparison-operator } | [NULL Comparison Must Use IS NULL / IS NOT NULL](#anti-patterns-null-comparison-operator) | ERROR | anti_patterns |
| `anti_patterns.singleton_metadata_clinical_join` | [Singleton Metadata Joined to Clinical Table](#anti-patterns-singleton-metadata-clinical-join) | ERROR | anti_patterns |
| `anti_patterns.standard_concept_or_with_classification` | [Standard Concept OR with Classification](#anti-patterns-standard-concept-or-with-classification) | WARNING | anti_patterns |
| `anti_patterns.top_as_synthetic_data`{ #anti-patterns-top-as-synthetic-data } | [TOP/LIMIT for Synthetic Data Generation](#anti-patterns-top-as-synthetic-data) | WARNING | anti_patterns |
| `anti_patterns.type_concept_id_domain_filter` | [Type Concept ID Domain Filter](#anti-patterns-type-concept-id-domain-filter) | WARNING | anti_patterns |
| `anti_patterns.type_concept_id_misuse` | [Type Concept ID Not For Clinical Filtering](#anti-patterns-type-concept-id-misuse) | ERROR | anti_patterns |
| `domain_specific.cdm_v53_to_v54_column_renames`{ #domain-specific-cdm-v53-to-v54-column-renames } | [CDM v5.3 to v5.4 Column Renames](#domain-specific-cdm-v53-to-v54-column-renames) | ERROR | domain_specific |
| `domain_specific.cohort_definition_syntax_not_executable_sql` | [Cohort Definition Syntax Not Executable SQL](#domain-specific-cohort-definition-syntax-not-executable-sql) | ERROR | domain_specific |
| `domain_specific.condition_occurrence_cardinality_validation` | [Condition Occurrence Cardinality Risk](#domain-specific-condition-occurrence-cardinality-validation) | WARNING | domain_specific |
| `domain_specific.condition_visit_hierarchy_validation` | [Condition Occurrence Visit Hierarchy Validation](#domain-specific-condition-visit-hierarchy-validation) | ERROR | domain_specific |
| `domain_specific.cost_currency_concept_id`{ #domain-specific-cost-currency-concept-id } | [Cost Currency Concept ID For Multi-Currency](#domain-specific-cost-currency-concept-id) | WARNING | domain_specific |
| `domain_specific.cost_event_id_polymorphic_resolution`{ #domain-specific-cost-event-id-polymorphic-resolution } | [Cost Event ID Polymorphic Resolution](#domain-specific-cost-event-id-polymorphic-resolution) | ERROR | domain_specific |
| `domain_specific.cost_paid_ingredient_cost_drug_specific`{ #domain-specific-cost-paid-ingredient-cost-drug-specific } | [Cost Drug-Specific Columns Require Domain Filter](#domain-specific-cost-paid-ingredient-cost-drug-specific) | WARNING | domain_specific |
| `domain_specific.cost_payer_plan_period_id_join` | [Cost Payer Plan Period ID Join](#domain-specific-cost-payer-plan-period-id-join) | ERROR | domain_specific |
| `domain_specific.death_cause_source_concept_validation` | [Death Cause Source Concept Not For Analytical Filtering](#domain-specific-death-cause-source-concept-validation) | ERROR | domain_specific |
| `domain_specific.death_join_to_person_not_to_clinical_event` | [Death Join to Person Not to Clinical Event](#domain-specific-death-join-to-person-not-to-clinical-event) | ERROR | domain_specific |
| `domain_specific.dose_era_cross_unit_comparison`{ #domain-specific-dose-era-cross-unit-comparison } | [Dose Era Cross-Unit Comparison](#domain-specific-dose-era-cross-unit-comparison) | WARNING | domain_specific |
| `domain_specific.drug_days_supply_validation` | [Drug Days Supply Validation](#domain-specific-drug-days-supply-validation) | WARNING | domain_specific |
| `domain_specific.drug_era_concept_class_validation` | [Drug Era Concept Class Validation](#domain-specific-drug-era-concept-class-validation) | ERROR | domain_specific |
| `domain_specific.drug_exposure_cardinality_validation` | [Drug Exposure Cardinality Awareness](#domain-specific-drug-exposure-cardinality-validation) | WARNING | domain_specific |
| `domain_specific.drug_exposure_quantity_misuse` | [Drug Exposure Quantity Misuse](#domain-specific-drug-exposure-quantity-misuse) | WARNING | domain_specific |
| `domain_specific.drug_exposure_sig_parsing` | [Drug Exposure Sig Parsing](#domain-specific-drug-exposure-sig-parsing) | WARNING | domain_specific |
| `domain_specific.drug_quantity_validation` | [Drug Quantity Validation](#domain-specific-drug-quantity-validation) | WARNING | domain_specific |
| `domain_specific.drug_strength_numerator_denominator_for_concentration` | [Drug Strength Completeness (Amount vs Concentration)](#domain-specific-drug-strength-numerator-denominator-for-concentration) | WARNING | domain_specific |
| `domain_specific.drug_strength_validity_filter` | [Drug Strength Validity Filter](#domain-specific-drug-strength-validity-filter) | WARNING | domain_specific |
| `domain_specific.episode_event_no_person_id` | [Episode Event No Person ID](#domain-specific-episode-event-no-person-id) | ERROR | domain_specific |
| `domain_specific.episode_parent_id_self_join` | [Episode Parent ID Self Join](#domain-specific-episode-parent-id-self-join) | ERROR | domain_specific |
| `domain_specific.event_cardinality_validation`{ #domain-specific-event-cardinality-validation } | [Event Cardinality Risk](#domain-specific-event-cardinality-validation) | WARNING | domain_specific |
| `domain_specific.event_date_column_correctness`{ #domain-specific-event-date-column-correctness } | [Event Date Column Correctness](#domain-specific-event-date-column-correctness) | ERROR | domain_specific |
| `domain_specific.event_field_polymorphic_resolution`{ #domain-specific-event-field-polymorphic-resolution } | [Event-Field Polymorphic Resolution](#domain-specific-event-field-polymorphic-resolution) | ERROR | domain_specific |
| `domain_specific.location_history_entity_id_requires_domain_id` | [Location History Entity ID Requires Domain ID](#domain-specific-location-history-entity-id-requires-domain-id) | ERROR | domain_specific |
| `domain_specific.measurement_cross_unit_comparison` | [Measurement Cross-Unit Comparison](#domain-specific-measurement-cross-unit-comparison) | WARNING | domain_specific |
| `domain_specific.measurement_duplicate_detection` | [Measurement Duplicate Detection](#domain-specific-measurement-duplicate-detection) | WARNING | domain_specific |
| `domain_specific.measurement_operator_concept_validation` | [Measurement Operator Concept Validation](#domain-specific-measurement-operator-concept-validation) | ERROR | domain_specific |
| `domain_specific.measurement_range_low_high_validation` | [Measurement Range Low/High Validation](#domain-specific-measurement-range-low-high-validation) | ERROR | domain_specific |
| `domain_specific.measurement_unit_validation` | [Measurement Unit Validation](#domain-specific-measurement-unit-validation) | WARNING | domain_specific |
| `domain_specific.measurement_value_as_number_and_concept_validation` | [Measurement Value Representation Consistency](#domain-specific-measurement-value-as-number-and-concept-validation) | ERROR | domain_specific |
| `domain_specific.note.note_nlp_snippet_misuse` | [Note NLP Snippet Misuse](#domain-specific-note-note-nlp-snippet-misuse) | WARNING | domain_specific |
| `domain_specific.observation_value_as_columns_mutually_contextual` | [Observation Value As Columns Mutually Contextual](#domain-specific-observation-value-as-columns-mutually-contextual) | WARNING | domain_specific |
| `domain_specific.observation_value_as_concept_confusion` | [Observation Value As Concept Confusion](#domain-specific-observation-value-as-concept-confusion) | ERROR | domain_specific |
| `domain_specific.observation_value_as_string_numeric_comparison` | [Observation Value As String Numeric Comparison](#domain-specific-observation-value-as-string-numeric-comparison) | ERROR | domain_specific |
| `domain_specific.person_birth_field_validation` | [Person Birth Field Validation](#domain-specific-person-birth-field-validation) | ERROR | domain_specific |
| `domain_specific.procedure_occurrence_quantity_semantics` | [Procedure Occurrence Quantity Semantics](#domain-specific-procedure-occurrence-quantity-semantics) | WARNING | domain_specific |
| `domain_specific.specimen_source_id_not_specimen_id` | [Specimen Source ID Not Specimen ID](#domain-specific-specimen-source-id-not-specimen-id) | ERROR | domain_specific |
| `domain_specific.visit_detail_admitted_discharged_domain` | [Visit Detail Admitted/Discharged Domain Validation](#domain-specific-visit-detail-admitted-discharged-domain) | WARNING | domain_specific |
| `domain_specific.visit_detail_dates_within_parent_visit` | [Visit Detail Dates Within Parent Visit](#domain-specific-visit-detail-dates-within-parent-visit) | WARNING | domain_specific |
| `domain_specific.visit_detail_has_no_preceding_visit_occurrence_id` | [Visit Detail Has No Preceding Visit Occurrence ID](#domain-specific-visit-detail-has-no-preceding-visit-occurrence-id) | ERROR | domain_specific |
| `domain_specific.visit_detail_visit_occurrence_reference` | [Visit Detail Visit Occurrence Reference](#domain-specific-visit-detail-visit-occurrence-reference) | ERROR | domain_specific |
| `domain_specific.visit_event_temporal_validation` | [Visit Event Temporal Validation](#domain-specific-visit-event-temporal-validation) | WARNING | domain_specific |
| `domain_specific.visit_length_of_stay_arithmetic`{ #domain-specific-visit-length-of-stay-arithmetic } | [Visit Length-of-Stay Arithmetic](#domain-specific-visit-length-of-stay-arithmetic) | WARNING | domain_specific |
| `domain_specific.visit_occurrence_type_domain`{ #domain-specific-visit-occurrence-type-domain } | [Visit Occurrence Type Concept Domain Validation](#domain-specific-visit-occurrence-type-domain) | ERROR | domain_specific |
| `domain_specific.visit_outpatient_same_day_validation` | [Visit Outpatient Same-Day Validation](#domain-specific-visit-outpatient-same-day-validation) | WARNING | domain_specific |
| `domain_specific.vocabulary.relationship_boolean_comparison` | [Relationship Boolean Comparison](#domain-specific-vocabulary-relationship-boolean-comparison) | ERROR | domain_specific |
| `domain_specific.year_of_birth_age_arithmetic`{ #domain-specific-year-of-birth-age-arithmetic } | [Person Year-of-Birth Age Arithmetic](#domain-specific-year-of-birth-age-arithmetic) | WARNING | domain_specific |


---

## Severity Levels

**ERROR** — the SQL logic is analytically incorrect and will produce wrong results (wrong cohort, wrong counts, wrong associations). Treat as a blocker.

**WARNING** — the SQL may produce incorrect results depending on data quality or study intent. Treat as a mandatory review item before publishing results.

---

## Concept Standardization Rules

These rules validate whether SQL queries correctly use OMOP standard concepts, vocabulary hierarchies, and concept mappings.

---

### 1. Concept Ancestor Cross-Domain Validation { #concept-standardization-concept-ancestor-cross-domain }

**Rule ID:** `concept_standardization.concept_ancestor_cross_domain`
**Severity:** WARNING

#### Intent

The concept_ancestor table represents hierarchical relationships within domains:
    - A Condition ancestor has only Condition descendants
    - A Drug ancestor has only Drug descendants
    - A Procedure ancestor has only Procedure descendants

    Cross-domain relationships exist in concept_relationship (e.g., 'Has indication'),
    NOT in concept_ancestor.

    Filtering descendant concepts by a different domain_id than the ancestor's
    domain will always return zero results.

Common mistake scenarios:
    1. Trying to find drugs to treat a condition via concept_ancestor
       (should use concept_relationship with 'Has indication')

    2. Mixing domains when expanding hierarchies
       (e.g., drug ancestor with procedure descendants)

    3. Misunderstanding OMOP's domain architecture

#### How it works

This rule analyzes the SQL query to identify concept ancestor cross-domain validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT ca.descendant_concept_id
    FROM concept_ancestor ca
    JOIN concept c ON ca.descendant_concept_id = c.concept_id
    WHERE ca.ancestor_concept_id = 201820  -- Condition: Diabetes
      AND c.domain_id = 'Drug'             -- ERROR: No Drug descendants!
```

**Correct patterns:**

```sql
SELECT cr.concept_id_2 AS drug_concept_id
    FROM concept_relationship cr
    WHERE cr.concept_id_1 = 201820
      AND cr.relationship_id = 'Has indication'
```

#### Suggested fix

Match descendant domain_id to the ancestor's domain, or use concept_relationship for cross-domain relationships.

---

### 2. Concept Ancestor Max Levels Misuse { #concept-standardization-concept-ancestor-max-levels-misuse }

**Rule ID:** `concept_standardization.concept_ancestor_max_levels_misuse`
**Severity:** WARNING

#### Intent

The concept_ancestor table tracks hierarchical relationships with two distance columns:
    - min_levels_of_separation: The shortest path from ancestor to descendant
    - max_levels_of_separation: The LONGEST path from ancestor to descendant

    Due to multiple inheritance (a concept can have multiple parents), there can be
    multiple paths between an ancestor and descendant. The max_levels_of_separation
    represents the longest of these paths.

    Common misconception:
    - Users think max_levels_of_separation = 1 returns "direct children"
    - This is WRONG because a concept with multiple paths might have:
      * min_levels_of_separation = 1 (direct child via one path)
      * max_levels_of_separation = 3 (via a longer alternate path)

    Using max_levels_of_separation = 1 will MISS concepts that have direct
    relationships but also have longer alternate paths.

Correct usage:
    - Use min_levels_of_separation = 1 for direct parent-child relationships
    - Use max_levels_of_separation <= N to limit maximum depth to explore
    - Use max_levels_of_separation >= N to find distant relationships only

#### How it works

This rule analyzes the SQL query to identify concept ancestor max levels misuse patterns.

#### Examples

**Violation patterns:**

```sql
SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
      AND max_levels_of_separation = 1  -- WRONG: misses multi-path children
```

**Correct patterns:**

```sql
SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
      AND min_levels_of_separation = 1
```

```sql
SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
      AND max_levels_of_separation <= 2
```

#### Suggested fix

Use min_levels_of_separation = 1 for direct relationships, or max_levels_of_separation <= N to limit hierarchy depth.

---

### 3. Concept Ancestor Rollup Direction { #concept-standardization-concept-ancestor-rollup-direction }

**Rule ID:** `concept_standardization.concept_ancestor_rollup_direction`
**Severity:** ERROR

#### Intent

concept_ancestor represents hierarchical relationships:
    - descendant_concept_id: The more specific child concept (e.g., "Type 2 Diabetes")
    - ancestor_concept_id: The more general parent concept (e.g., "Diabetes Mellitus")

    Patient records contain specific diagnoses (descendants), not parent concepts.
    To roll up to parent concepts, you must:
    1. Join clinical table's concept_id to descendant_concept_id
    2. Filter on ancestor_concept_id

    Swapping this reverses the hierarchy, causing:
    - Missed child concepts
    - Incorrect aggregations
    - Potentially zero results

#### How it works

This rule analyzes the SQL query to identify concept ancestor rollup direction patterns.

#### Examples

**Violation patterns:**

```sql
SELECT ca.descendant_concept_id, COUNT(*)
    FROM condition_occurrence co
    JOIN concept_ancestor ca
      ON co.condition_concept_id = ca.ancestor_concept_id  -- WRONG!
    WHERE ca.descendant_concept_id = 201820
    GROUP BY ca.descendant_concept_id
```

**Correct patterns:**

```sql
SELECT ca.ancestor_concept_id, COUNT(*)
    FROM condition_occurrence co
    JOIN concept_ancestor ca
      ON co.condition_concept_id = ca.descendant_concept_id  -- Correct
    WHERE ca.ancestor_concept_id = 201820
    GROUP BY ca.ancestor_concept_id
```

#### Suggested fix

Join clinical concept_id to concept_ancestor.descendant_concept_id and filter on concept_ancestor.ancestor_concept_id.

---

### 4. Concept Ancestor Self-Include Redundancy { #concept-standardization-concept-ancestor-self-include-redundancy }

**Rule ID:** `concept_standardization.concept_ancestor_self_include_redundancy`
**Severity:** WARNING

#### Intent

The concept_ancestor table includes self-referencing rows where:
    - ancestor_concept_id = descendant_concept_id
    - min_levels_of_separation = 0
    - max_levels_of_separation = 0

    This means every concept is its own ancestor at distance 0.

    When building concept sets, the anchor concept is AUTOMATICALLY included
    in concept_ancestor results. Queries that explicitly add the anchor concept
    AND query concept_ancestor will duplicate the anchor.

#### How it works

This rule analyzes the SQL query to identify concept ancestor self-include redundancy patterns.

#### Examples

**Violation patterns:**

```sql
with explicit anchor (duplicates 201820)
    SELECT concept_id FROM concept WHERE concept_id = 201820
    UNION
    SELECT descendant_concept_id FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
```

```sql
with explicit anchor and concept_ancestor
    SELECT DISTINCT c.concept_id
    FROM concept c
    LEFT JOIN concept_ancestor ca
      ON c.concept_id = ca.descendant_concept_id
      AND ca.ancestor_concept_id = 201820
    WHERE c.concept_id = 201820 OR ca.ancestor_concept_id IS NOT NULL
```

**Correct patterns:**

```sql
SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
```

```sql
SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
      AND min_levels_of_separation > 0
```

#### Common scenarios

- UNION with explicit anchor: Include concept_id = X and also query
- OR condition mixing direct concept_id and concept_ancestor
- IN clause with explicit IDs and concept_ancestor subquery

#### Suggested fix

Use concept_ancestor alone, or filter with min_levels_of_separation > 0.

---

### 5. Concept Class ID Ingredient for Drug Grouping { #concept-standardization-concept-class-id-ingredient-for-drug-grouping }

**Rule ID:** `concept_standardization.concept_class_id_ingredient_for_drug_grouping`
**Severity:** WARNING

#### Intent

OMOP drug concepts exist in a specificity hierarchy:

    Ingredient (most general - active substance)
        ↓
    Clinical Drug Form (dose form + ingredient)
        ↓
    Clinical Drug (formulation without brand)
        ↓
    Branded Drug (brand name + formulation)
        ↓
    Marketed Product (most specific - commercial package)

    When analysts want to group by active ingredient (e.g., "all Metformin prescriptions"),
    but filter by concept_class_id = 'Clinical Drug' or 'Branded Drug', they're grouping
    at the wrong level of granularity.

    Issues with wrong concept_class_id:
    1. Clinical Drug: Groups by formulation (500mg tablet vs 850mg tablet counted separately)
    2. Branded Drug: Groups by brand (Glucophage vs Fortamet vs generic counted separately)
    3. Missing data: Fails to capture all forms of the active ingredient

    This leads to:
    - Undercounting drug exposure (split across formulations/brands)
    - Incorrect prevalence estimates
    - Misleading comparative effectiveness analyses
    - Fragmented ingredient-level statistics

#### How it works

This rule analyzes the SQL query to identify concept class id ingredient for drug grouping patterns.

#### Examples

**Violation patterns:**

```sql
SELECT c.concept_name AS ingredient, COUNT(*) AS patient_count
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Clinical Drug'
    GROUP BY c.concept_name
```

```sql
SELECT c.concept_name AS active_substance,
           COUNT(DISTINCT de.person_id) AS patients
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Branded Drug'
    GROUP BY c.concept_name
```

**Correct patterns:**

```sql
SELECT c.concept_name AS ingredient, COUNT(*) AS patient_count
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Ingredient'
    GROUP BY c.concept_name
```

```sql
SELECT ing.concept_name AS ingredient,
           COUNT(DISTINCT de.person_id) AS patients
    FROM drug_exposure de
    JOIN concept_ancestor ca
      ON de.drug_concept_id = ca.descendant_concept_id
    JOIN concept ing
      ON ca.ancestor_concept_id = ing.concept_id
    WHERE ing.concept_class_id = 'Ingredient'
    GROUP BY ing.concept_name
```

#### Suggested fix

Use concept_class_id = 'Ingredient' or use concept_ancestor to roll up drug products to ingredients.

---

### 6. Concept Domain ID Matches Target Table { #concept-standardization-concept-domain-validation }

**Rule ID:** `concept_standardization.concept_domain_validation`
**Severity:** ERROR

#### Intent

Merged rule combining domain_segregation and concept_domain_validation.

#### How it works

This rule analyzes the SQL query to identify concept domain id matches target table patterns.

#### Examples

#### Suggested fix

Add or correct concept.domain_id filter to match expected domain.

---


### 8. Concept Synonym Language Concept ID { #concept-standardization-concept-synonym-language-concept-id }

**Rule ID:** `concept_standardization.concept_synonym_language_concept_id`
**Severity:** WARNING

#### Intent

The concept_synonym table stores synonym names in multiple languages:
    - English (language_concept_id = 4180186)
    - Spanish, German, French, etc. (other language_concept_id values)

    When searching for synonyms by name (LIKE '%heart attack%'), you may
    inadvertently retrieve synonyms in multiple languages if you don't
    filter by language_concept_id.

Common mistake:
    Developers search concept_synonym_name without considering language,
    leading to unexpected multilingual results.

#### How it works

This rule analyzes the SQL query to identify concept synonym language concept id patterns.

#### Examples

**Violation patterns:**

```sql
SELECT concept_id FROM concept_synonym
    WHERE concept_synonym_name LIKE '%heart attack%'
```

```sql
SELECT * FROM concept_synonym
    WHERE concept_synonym_name ILIKE '%diabetes%'
```

**Correct patterns:**

```sql
SELECT concept_id FROM concept_synonym
    WHERE concept_synonym_name LIKE '%heart attack%'
    AND language_concept_id = 4180186  -- English
```

```sql
SELECT * FROM concept_synonym
    WHERE concept_synonym_name LIKE '%myocardial infarction%'
    AND language_concept_id IN (4180186, 4175777)  -- English and Spanish
```

#### Suggested fix

Add: AND language_concept_id = 4180186 (English), unless multilingual results are intended.

---

### 9. Domain Vocabulary Validation (VOCAB_022-025) { #concept-standardization-domain-vocabulary-validation }

**Rule ID:** `concept_standardization.domain_vocabulary_validation`
**Severity:** WARNING

#### Intent

Each OMOP clinical domain has specific standard vocabularies:

    - Conditions: SNOMED (not ICD10CM, ICD9CM, ICD10, ICD9)
    - Drugs: RxNorm, RxNorm Extension (not NDC, ATC, GCN_SEQNO, SPL)
    - Procedures: SNOMED, CPT4, HCPCS (not ICD10PCS, ICD9Proc, OPCS4)
    - Measurements: LOINC, SNOMED (not CPT4 when standard_concept = 'S')

    Standard *_concept_id columns (condition_concept_id, drug_concept_id, etc.)
    ALWAYS reference standard concepts. If you filter by source vocabulary_id
    values, you'll get zero results because standard concept IDs don't belong
    to those vocabularies.

#### How it works

This rule analyzes the SQL query to identify domain vocabulary validation (vocab_022-025) patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'ICD10CM'
```

```sql
SELECT *
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'NDC'
```

**Correct patterns:**

```sql
SELECT *
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'SNOMED'
```

```sql
SELECT *
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.vocabulary_id IN ('RxNorm', 'RxNorm Extension')
```

#### Common scenarios

- Joining condition_concept_id to concept and filtering vocabulary_id = 'ICD10CM'
- Joining drug_concept_id to concept and filtering vocabulary_id = 'NDC'
- Joining procedure_concept_id to concept and filtering vocabulary_id = 'ICD10PCS'

#### Suggested fix

Use the correct standard vocabulary for the domain, or use *_source_concept_id for source vocabulary filtering.

---

### 10. Era Tables Use Standard Concepts Only { #concept-standardization-era-table-standard-concepts }

**Rule ID:** `concept_standardization.era_table_standard_concepts`
**Severity:** ERROR

#### Intent

Era tables contain only standard concepts. Filtering for non-standard concepts will always return 0 rows.

#### How it works

This rule analyzes the SQL query to identify era tables use standard concepts only patterns.

#### Examples

#### Suggested fix

Remove filters for non-standard concepts. Era tables only contain standard concepts (standard_concept = 'S').

---


### 12. Invalid Reason Enforcement { #concept-standardization-invalid-reason-enforcement }

**Rule ID:** `concept_standardization.invalid_reason_enforcement`
**Severity:** WARNING (strict-mode-only — silent in default mode)

#### Intent

Vocabulary tables (`concept`, `concept_relationship`) carry an `invalid_reason` column that marks retired/superseded entries (`'D'` = deprecated, `'U'` = upgraded, `NULL` = currently valid). Derived vocabulary tables (`concept_ancestor`, `concept_synonym`, `drug_strength`, `source_to_concept_map`) reference concept IDs but lack their own `invalid_reason` column. Queries that omit `invalid_reason` filtering can silently include retired concepts in cohort definitions or analytic outputs.

This rule is **gated behind strict mode**: silent in default mode, fires as WARNING under `--strict` (CLI) or `strict=True` (API). Real-world OMOP queries on the concept table almost always omit this filter, so firing in default mode would dilute every other rule.

#### How it works

The rule fires when:

- A vocabulary table with `invalid_reason` (`concept`, `concept_relationship`) appears in scope, is being used as a *source* (filtered by `vocabulary_id` / `domain_id` / `relationship_id` / `standard_concept` / `concept_name` / `concept_code` / `concept_class_id`), and no `invalid_reason` predicate is asserted; OR
- A derived vocabulary table is used as a source for cohort selection. Source-usage detection (for `concept_ancestor` specifically) recognizes any of:
  - **Primary FROM:** `FROM concept_ancestor WHERE …`.
  - **Direct JOIN:** `JOIN concept_ancestor ca ON <clinical>.<concept_id_col> = ca.descendant_concept_id` (or `ca.ancestor_concept_id`).
  - **Chained JOIN through `concept`:** `JOIN concept c ON … JOIN concept_ancestor ca ON c.concept_id = ca.descendant_concept_id` (or `ca.ancestor_concept_id`).
  - **Multi-ancestor IN-list:** `WHERE ca.ancestor_concept_id IN (a, b, c, …)` of literals.

  All four are detected by a single signal: a literal predicate (`=` or `IN (…)` of literals) on `concept_ancestor.{ancestor,descendant}_concept_id` in WHERE / JOIN-ON. That signal is form-agnostic and reliably distinguishes cohort-source usage from lookup-decoration. Lookup-shape JOINs (e.g. `FROM concept c JOIN concept_ancestor ca ON ca.ancestor_concept_id = c.concept_id WHERE c.concept_id = 192671` — concept_ancestor's hierarchy columns are equated against another column rather than a literal) correctly stay silent.

The rule is suppressed when any of the following is asserted in the query:
- `invalid_reason IS NULL` / `IS NOT NULL` / `= '...'` / `IN (...)` / `NOT IN (...)`.
- A date-validity check using both `valid_start_date` and `valid_end_date`.
- `standard_concept = 'S'` (or `IN ('S', …)`) — standard concepts are nearly always valid, so the additional `invalid_reason` check would be belt-and-suspenders noise.
- Lookup-join shape: vocabulary table is joined to another vocabulary table and pinned by specific `concept_id` literals.

#### Examples

**Violation patterns** (strict mode):

```sql
-- Primary FROM on derived vocabulary table
SELECT descendant_concept_id FROM concept_ancestor WHERE ancestor_concept_id = 201826;
```

```sql
-- Direct-JOIN cohort idiom: clinical fact table JOIN concept_ancestor on concept_id linkage
SELECT de.person_id
FROM drug_exposure de
JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 43027253;
```

```sql
-- Chained-JOIN cohort idiom (clinical → concept → concept_ancestor)
SELECT co.person_id, c.concept_name
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
JOIN concept_ancestor ca ON c.concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 320128;
```

```sql
-- IN-subquery cohort idiom (semantically equivalent to the JOIN forms)
SELECT de.person_id
FROM drug_exposure de
WHERE de.drug_concept_id IN (
    SELECT descendant_concept_id FROM concept_ancestor WHERE ancestor_concept_id = 43027253
);
```

**Correct patterns:**

```sql
-- JOIN concept and filter invalid_reason
SELECT de.person_id
FROM drug_exposure de
JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
JOIN concept c ON c.concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 43027253
  AND c.invalid_reason IS NULL;
```

```sql
-- standard_concept = 'S' is sufficient — standard concepts are nearly always valid
SELECT concept_id FROM concept WHERE vocabulary_id = 'SNOMED' AND standard_concept = 'S';
```

#### Suggested fix

`ADD: JOIN concept c ON c.concept_id = <table>.<concept_id_col>` and `WHERE c.invalid_reason IS NULL` to exclude deprecated concepts. For queries on `concept` / `concept_relationship` directly, just add `AND invalid_reason IS NULL` to the WHERE clause.

---

### 13. Maps To Target Standard Validation { #concept-standardization-maps-to-target-standard-validation }

**Rule ID:** `concept_standardization.maps_to_target_standard_validation`
**Severity:** ERROR

#### Intent

'Maps to' relationships map source concepts to standard concepts, but:
    1. Mapping chains can exist (A → B → C) where intermediate concepts are not final
    2. Some concept_id_2 targets may have standard_concept = NULL (deprecated)
    3. Data quality issues may result in non-standard targets

    Without validating that concept_id_2 is actually standard (standard_concept = 'S'),
    queries may return:
    - Deprecated concepts
    - Intermediate non-standard concepts
    - Invalid mappings

#### How it works

This rule analyzes the SQL query to identify maps to target standard validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT cr.concept_id_2
    FROM concept_relationship cr
    WHERE cr.concept_id_1 = 44836914
      AND cr.relationship_id = 'Maps to'
      AND cr.invalid_reason IS NULL
```

**Correct patterns:**

```sql
SELECT cr.concept_id_2
    FROM concept_relationship cr
    JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
    WHERE cr.relationship_id = 'Maps to'
      AND cr.invalid_reason IS NULL
      AND c2.standard_concept = 'S'
```

#### Suggested fix

Join concept_relationship.concept_id_2 to concept.concept_id and add filter: concept.standard_concept = 'S'

---

### 14. Multiple Maps To Targets Not Handled { #concept-standardization-multiple-maps-to-targets }

**Rule ID:** `concept_standardization.multiple_maps_to_targets`
**Severity:** WARNING

#### Intent

The concept_relationship table with relationship_id = 'Maps to' is a
    one-to-many relationship:
    - A source concept can map to multiple standard concepts
    - Assuming only one mapping exists leads to:
      * Duplicate rows (when joining without DISTINCT)
      * Arbitrary/incomplete results (when using scalar subqueries)
      * Missing mappings (when using LIMIT 1)

#### How it works

This rule analyzes the SQL query to identify multiple maps to targets not handled patterns.

#### Examples

**Violation patterns:**

```sql
SELECT (
      SELECT concept_id_2
      FROM concept_relationship
      WHERE concept_id_1 = c.concept_id
        AND relationship_id = 'Maps to'
    ) AS standard_id
    FROM concept c
```

```sql
without DISTINCT (duplicates rows)
    SELECT de.*, cr.concept_id_2
    FROM drug_exposure de
    JOIN concept_relationship cr ON de.drug_concept_id = cr.concept_id_1
    WHERE cr.relationship_id = 'Maps to'
```

**Correct patterns:**

```sql
SELECT DISTINCT de.drug_exposure_id, cr.concept_id_2
    FROM drug_exposure de
    JOIN concept_relationship cr ON de.drug_concept_id = cr.concept_id_1
    WHERE cr.relationship_id = 'Maps to'
```

```sql
SELECT de.drug_exposure_id,
           ARRAY_AGG(cr.concept_id_2) AS mapped_concepts
    FROM drug_exposure de
    JOIN concept_relationship cr ON de.drug_concept_id = cr.concept_id_1
    WHERE cr.relationship_id = 'Maps to'
    GROUP BY de.drug_exposure_id
```

#### Common scenarios

- Scalar subquery assuming single result
- JOIN without DISTINCT or GROUP BY
- Using LIMIT 1 to force single result

#### Suggested fix

Use DISTINCT, GROUP BY with aggregation (e.g., ARRAY_AGG), or explicitly handle multiple mappings.

---

### 15. Source Concept ID Not For Analytical Filtering { #concept-standardization-source-concept-id-warning }

**Rule ID:** `concept_standardization.source_concept_id_warning`
**Severity:** WARNING

#### Intent

OMOP semantic rule OMOP_022: The *_source_concept_id columns store the original source vocabulary concept. For standard analytical queries and cohort identification, use the primary *_concept_id (standard concept) rather than *_source_concept_id.

#### How it works

Valid uses of source_concept_id:
  - Data quality checks
  - ETL validation / mapping verification
  - Source code exploration
  - Provenance tracking

Invalid use (cohort identification):
  - SELECT person_id FROM condition_occurrence WHERE condition_source_concept_id = 123

Correct approach:
  - SELECT person_id FROM condition_occurrence WHERE condition_concept_id = 456

#### Examples

#### Suggested fix

Replace *_source_concept_id with corresponding standard *_concept_id column. If this is for ETL validation or source exploration, this warning can be ignored.

---

### 16. Source Concept ID Should Not Filter Standard Concepts { #concept-standardization-source-concept-id-standard-filter }

**Rule ID:** `concept_standardization.source_concept_id_standard_filter`
**Severity:** WARNING

#### Intent

In OMOP CDM, clinical domain tables have two types of concept ID columns:

    1. Standard concept IDs (e.g., condition_concept_id):
       - Reference standard concepts for analytics
       - Should have standard_concept = 'S'

    2. Source concept IDs (e.g., condition_source_concept_id):
       - Reference the original source vocabulary codes (ICD-10, CPT, etc.)
       - Are intentionally NON-standard (standard_concept IS NULL or 'C')
       - Preserve the original coding system used in the source data

    Filtering standard_concept = 'S' when joining on *_source_concept_id is
    semantically wrong and will typically return zero results.

#### How it works

This rule analyzes the SQL query to identify source concept id should not filter standard concepts patterns.

#### Examples

**Violation patterns:**

```sql
SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c
      ON co.condition_source_concept_id = c.concept_id
    WHERE c.standard_concept = 'S'
```

```sql
SELECT c.concept_name
    FROM drug_exposure de
    JOIN concept c
      ON de.drug_source_concept_id = c.concept_id
      AND c.standard_concept = 'S'
```

**Correct patterns:**

```sql
SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c
      ON co.condition_source_concept_id = c.concept_id
```

```sql
SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c
      ON co.condition_concept_id = c.concept_id
    WHERE c.standard_concept = 'S'
```

#### Common scenarios

- Joining source_concept_id to concept with standard_concept = 'S' filter
- Misunderstanding the dual concept ID design
- Applying standard concept filters to source concept joins

#### Suggested fix

Remove standard_concept = 'S' filter when joining source concept IDs, or use the standard *_concept_id column instead.

---

### 17. Source to Concept Map Validation { #concept-standardization-source-to-concept-map-validation }

**Rule ID:** `concept_standardization.source_to_concept_map_validation`
**Severity:** WARNING

#### Intent

source_to_concept_map contains mappings from many source vocabularies.
    The same source_code can exist in multiple vocabularies with different meanings.

    Example: Code "250" exists in:
    - ICD-9-CM: Diabetes mellitus
    - ICD-10-CM: Different condition
    - Local hospital codes: Something else entirely

    Without source_vocabulary_id filter, you get ALL mappings for "250",
    leading to incorrect concept assignments.

#### How it works

This rule analyzes the SQL query to identify source to concept map validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT target_concept_id
    FROM source_to_concept_map
    WHERE source_code = '250.00'
```

```sql
from ALL vocabularies!
```

**Correct patterns:**

```sql
SELECT target_concept_id
    FROM source_to_concept_map
    WHERE source_code = '250.00'
      AND source_vocabulary_id = 'ICD9CM'
```

#### Suggested fix

Add source_vocabulary_id filter alongside source_code.

---

### 18. Standard Concept Enforcement { #concept-standardization-standard-concept-enforcement }

**Rule ID:** `concept_standardization.standard_concept_enforcement`
**Severity:** WARNING (escalates to ERROR in strict mode)

#### Intent

When a query reads from a STANDARD OMOP concept field (e.g. `condition_concept_id`, `drug_concept_id`), it must establish that the concept IDs in scope are actually standard concepts. Standard fields *can* hold non-standard values when ETL assumptions break, so cohort definitions without standard-concept enforcement risk silently mixing in classification-only concepts (`'C'`), invalid entries, or legacy mappings.

#### How it works

The rule fires when a query references a known-standard concept field and *none* of the following enforcement signals are present:

1. **Explicit standard-concept filter.** A predicate of the form `concept.standard_concept = 'S'` (or `IN ('S')`) is asserted in `WHERE` or `JOIN ON`.
2. **`Maps to` resolution.** The query joins `concept_relationship` with `relationship_id = 'Maps to'`, indicating the user is resolving source concepts to standard concepts at query time.
3. **Specific concept-id literal filter.** The query restricts the standard field to specific concept IDs via `= <id>` or `IN (<id>, …)` — the user has chosen specific concepts, presumably with knowledge of their standardness.
4. **Hierarchy-based filter via `concept_ancestor`.** The query restricts the standard field via a direct reference to `concept_ancestor`'s hierarchy. Three equivalent forms are recognized:
   - **Subquery form:** `<col> IN (SELECT descendant_concept_id FROM concept_ancestor [WHERE …])` (or `ancestor_concept_id`).
   - **Direct JOIN:** `JOIN concept_ancestor ca ON <clinical>.<concept_id_col> = ca.descendant_concept_id` (or `ca.ancestor_concept_id`). The more common idiom in OHDSI cohort SQL — avoids a correlated subquery and produces better optimizer plans.
   - **Chained JOIN via `concept`:** `JOIN concept c ON <clinical>.<concept_id_col> = c.concept_id JOIN concept_ancestor ca ON c.concept_id = ca.descendant_concept_id` (or `ca.ancestor_concept_id`). Users adopt this shape when they also want to project columns from `concept` (e.g. `concept_name`) in the SELECT list. The intermediate `concept` JOIN is a relay; the standard-concept guarantee is transitive through the chain.

   By OMOP CDM definition, `concept_ancestor` is a hierarchy over Standard Concepts only — both `ancestor_concept_id` and `descendant_concept_id` are guaranteed-standard. Feeding rows from `concept_ancestor` into a `*_concept_id` slot (via any of the three forms above) transitively guarantees the standard-concept property, so an additional `standard_concept = 'S'` filter would be redundant.

The `concept_ancestor` suppression is **scope-limited to direct references**. CTE-indirected patterns (`WITH cte AS (SELECT descendant_concept_id FROM concept_ancestor …) SELECT … WHERE col IN (SELECT concept_id FROM cte)`) still fire, because the rule does not currently inline CTEs to verify the indirected guarantee.

#### Examples

**Violation patterns:**

```sql
-- No enforcement of any kind
SELECT condition_concept_id FROM condition_occurrence;
```

```sql
-- Joins concept but does not filter standard_concept
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.vocabulary_id = 'SNOMED';
```

**Correct patterns:**

```sql
-- Explicit standard_concept = 'S' filter
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
WHERE c.vocabulary_id = 'SNOMED'
  AND c.standard_concept = 'S';
```

```sql
-- Hierarchy-based filter via concept_ancestor — subquery form
SELECT de.person_id
FROM drug_exposure de
WHERE de.drug_concept_id IN (
    SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 35416207
);
```

```sql
-- Hierarchy-based filter via concept_ancestor — direct JOIN form
SELECT de.person_id
FROM drug_exposure de
JOIN concept_ancestor ca
  ON de.drug_concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 35416207;
```

```sql
-- Hierarchy-based filter via concept_ancestor — chained JOIN through concept
-- (when also projecting concept's columns in SELECT)
SELECT co.person_id, c.concept_name
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id
JOIN concept_ancestor ca ON c.concept_id = ca.descendant_concept_id
WHERE ca.ancestor_concept_id = 320128;
```

```sql
-- Specific concept ID literals
SELECT * FROM condition_occurrence
WHERE condition_concept_id IN (201826, 201820);
```

#### Suggested fix

`ADD: \`JOIN concept c ON c.concept_id = <table>.<concept_id_col>\` AND \`WHERE c.standard_concept = 'S'\`` to filter to standard concepts. (Skip if the query already restricts via `concept_ancestor` or specific concept IDs — the rule should not fire in those cases.)

---

### 19. Standard Concept Value Validation { #concept-standardization-standard-concept-value-validation }

**Rule ID:** `concept_standardization.standard_concept_value_validation`
**Severity:** ERROR

#### Intent

OMOP semantic rule OMOP_037: The standard_concept column only accepts 'S' (Standard), 'C' (Classification), or NULL (non-standard). Filtering with other values like 'Y', 'N', 1, 0 is incorrect.

#### How it works

This rule analyzes the SQL query to identify standard concept value validation patterns.

#### Examples

#### Suggested fix

Use 'S' for standard, 'C' for classification, or NULL for non-standard concepts.

---

### 20. Unit Vocabulary Validation { #concept-standardization-unit-vocabulary-validation }

**Rule ID:** `concept_standardization.unit_vocabulary_validation`
**Severity:** WARNING

#### Intent

In OMOP CDM, all standard unit concepts use the UCUM (Unified Code for Units
    of Measure) vocabulary. Unit concept columns (*_unit_concept_id) reference
    standard concepts from the UCUM vocabulary.

    When queries join unit_concept_id to the concept table and filter by
    vocabulary_id != 'UCUM', they may:
    - Return non-standard units
    - Return zero results
    - Miss the intended unit concepts

    Affected columns:
    - measurement.unit_concept_id
    - observation.unit_concept_id
    - drug_strength.amount_unit_concept_id
    - drug_strength.numerator_unit_concept_id
    - drug_strength.denominator_unit_concept_id
    - specimen.unit_concept_id
    - dose_era.unit_concept_id

#### How it works

This rule analyzes the SQL query to identify unit vocabulary validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM measurement m
    JOIN concept c ON m.unit_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'SNOMED'
```

```sql
SELECT *
    FROM observation o
    JOIN concept c
      ON o.unit_concept_id = c.concept_id
      AND c.vocabulary_id = 'LOINC'
```

**Correct patterns:**

```sql
SELECT *
    FROM measurement m
    JOIN concept c ON m.unit_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'UCUM'
```

```sql
SELECT *
    FROM measurement m
    JOIN concept c ON m.unit_concept_id = c.concept_id
    WHERE c.domain_id = 'Unit'
```

#### Suggested fix

Use vocabulary_id = 'UCUM' for unit concept lookups, or remove the vocabulary_id filter entirely.

---

## Join Validation Rules

These rules validate that table joins follow the OMOP CDM schema foreign key relationships.

---

### 1. Care Site ID Join Validation { #joins-care-site-id-join-validation }

**Rule ID:** `joins.care_site_id_join_validation`
**Severity:** ERROR

#### Intent

Clinical tables have care_site_id (foreign key to care_site.care_site_id).
    Joining on other columns (e.g., care_site_id to location_id) is semantically
    incorrect and produces wrong results.

#### How it works

This rule analyzes the SQL query to identify care site id join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM visit_occurrence vo
    JOIN care_site cs ON vo.care_site_id = cs.location_id
```

**Correct patterns:**

```sql
SELECT * FROM visit_occurrence vo
    JOIN care_site cs ON vo.care_site_id = cs.care_site_id
```

#### Suggested fix

Join using care_site_id on both sides: table.care_site_id = care_site.care_site_id

---

### 2. Care Site Join Path Validation { #joins-care-site-join-validation }

**Rule ID:** `joins.care_site_join_validation`
**Severity:** WARNING

#### Intent

Clinical tables have care_site_id (foreign key to care_site.care_site_id).
    Direct joins to location.location_id bypass the care_site intermediary and are
    semantically incorrect (comparing care_site_id with location_id).

    Exception: person.location_id is valid - it represents the person's home address.

#### How it works

This rule analyzes the SQL query to identify care site join path validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM visit_occurrence vo
    JOIN location l ON vo.care_site_id = l.location_id
```

**Correct patterns:**

```sql
SELECT * FROM visit_occurrence vo
    JOIN care_site cs ON vo.care_site_id = cs.care_site_id
    JOIN location l ON cs.location_id = l.location_id
```

#### Suggested fix

Use: clinical → care_site → location join path.

---

### 3. Care Site to Location Join Validation { #joins-care-site-location-join-validation }

**Rule ID:** `joins.care_site_location_join_validation`
**Severity:** ERROR

#### Intent

care_site has location_id (foreign key to location.location_id) to identify
    the geographic location of the care site. Joining on other columns (e.g.,
    care_site_id to location_id) is semantically incorrect and produces wrong results.

#### How it works

This rule analyzes the SQL query to identify care site to location join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM care_site cs
    JOIN location l ON cs.care_site_id = l.location_id
```

**Correct patterns:**

```sql
SELECT * FROM care_site cs
    JOIN location l ON cs.location_id = l.location_id
```

#### Suggested fix

Join care_site to location using location_id: care_site.location_id = location.location_id

---

### 4. Clinical Primary Key Join Validation { #joins-clinical-pk-cross-join-validation }

**Rule ID:** `joins.clinical_pk_cross_join_validation`
**Severity:** ERROR

#### Intent

Each clinical event table has its own independent primary key sequence:
    - condition_occurrence_id = 123 → A specific condition event
    - drug_exposure_id = 123 → A specific drug exposure event
    - procedure_occurrence_id = 123 → A specific procedure event

    These are completely unrelated - they just happen to have overlapping numeric
    values. Joining them is always semantically meaningless.

#### How it works

This rule analyzes the SQL query to identify clinical primary key join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM condition_occurrence co
    JOIN drug_exposure de ON co.condition_occurrence_id = de.drug_exposure_id
```

```sql
with independent ID sequences!
```

**Correct patterns:**

```sql
SELECT * FROM condition_occurrence co
    JOIN drug_exposure de ON co.person_id = de.person_id
      AND co.visit_occurrence_id = de.visit_occurrence_id
```

#### Suggested fix

Do not join clinical event primary keys. Use shared foreign keys such as person_id or visit_occurrence_id instead.

---

### 5. Clinical Tables Require Person ID Linkage { #joins-clinical-person-id-linkage-validation }

**Rule ID:** `joins.clinical_person_id_linkage_validation`
**Severity:** ERROR

#### Intent

Joining clinical tables without person_id linkage can match data from different
    patients, leading to completely invalid results. For example:

    - Patient A has condition_start_date = '2020-01-15'
    - Patient B has drug_exposure_start_date = '2020-01-15'

    Joining on dates alone would incorrectly associate Patient A's condition with
    Patient B's drug exposure.

Clinical tables that require person_id linkage:
    - condition_occurrence
    - drug_exposure
    - procedure_occurrence
    - measurement
    - observation
    - visit_occurrence
    - visit_detail
    - death
    - person

#### How it works

This rule analyzes the SQL query to identify clinical tables require person id linkage patterns.

#### Examples

**Violation patterns:**

```sql
SELECT co.condition_concept_id, de.drug_concept_id
    FROM condition_occurrence co
    JOIN drug_exposure de ON co.condition_start_date = de.drug_exposure_start_date
```

**Correct patterns:**

```sql
SELECT co.condition_concept_id, de.drug_concept_id
    FROM condition_occurrence co
    JOIN drug_exposure de ON co.person_id = de.person_id
```

```sql
SELECT co.condition_concept_id, de.drug_concept_id
    FROM condition_occurrence co
    JOIN person p ON co.person_id = p.person_id
    JOIN drug_exposure de ON p.person_id = de.person_id
```

#### Suggested fix

Add joins on person_id between clinical tables.

---

### 6. Clinical to Visit Detail Join Validation { #joins-clinical-visit-detail-join-validation }

**Rule ID:** `joins.clinical_visit_detail_join_validation`
**Severity:** ERROR

#### Intent

Clinical tables have both visit_occurrence_id and visit_detail_id columns.
    These represent different ID spaces:
    - visit_occurrence_id: Links to parent visit (visit_occurrence table)
    - visit_detail_id: Links to detailed sub-visit (visit_detail table)

    Both are integers, so joining visit_occurrence_id to visit_detail_id produces
    NO TYPE ERROR, but randomly matches unrelated records where IDs happen to be
    equal. This silently corrupts analytical results.

#### How it works

This rule analyzes the SQL query to identify clinical to visit detail join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM measurement m
    JOIN visit_detail vd ON m.visit_occurrence_id = vd.visit_detail_id
```

**Correct patterns:**

```sql
SELECT * FROM measurement m
    JOIN visit_detail vd ON m.visit_detail_id = vd.visit_detail_id
```

#### Suggested fix

None

---

### 7. Cohort to Clinical Table Join Validation { #joins-cohort-clinical-join-validation }

**Rule ID:** `joins.cohort_clinical_join_validation`
**Severity:** ERROR

#### Intent

The cohort table is a RESULTS table with unique naming:
    - Uses subject_id (not person_id) to identify patients
    - This is the ONLY table in OMOP CDM that uses subject_id
    - All clinical tables use person_id for patient identity

    The ONLY valid join is:
    cohort.subject_id = clinical_table.person_id

#### How it works

This rule analyzes the SQL query to identify cohort to clinical table join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM cohort c
    JOIN condition_occurrence co ON c.subject_id = co.condition_occurrence_id
```

**Correct patterns:**

```sql
SELECT
      c.subject_id,
      co.condition_occurrence_id,
      co.condition_concept_id
    FROM cohort c
    JOIN condition_occurrence co ON c.subject_id = co.person_id
    WHERE c.cohort_definition_id = 123
      AND co.condition_start_date >= c.cohort_start_date
      AND co.condition_start_date <= c.cohort_end_date
```

#### Common scenarios

- Joining subject_id to primary keys (condition_occurrence_id, etc.)
- Structurally invalid (patient ID ≠ event ID)
- Using person_id from cohort table

#### Suggested fix

Use: cohort.subject_id = clinical.person_id or cohort.subject_id = person.person_id and person.person_id = clinical.person_id

---

### 8. Concept Alias Reuse Validation { #joins-concept-alias-reuse-validation }

**Rule ID:** `joins.concept_alias_reuse_validation`
**Severity:** ERROR

#### Intent

OMOP clinical tables have both standard concept_id columns (e.g., condition_concept_id)
    and source concept_id columns (e.g., condition_source_concept_id). When you need to
    join to concept for BOTH, you must use separate aliases. Reusing the same alias causes:

    1. Ambiguous references: Which c.concept_id does the ON clause refer to?
    2. Last-join-wins: Second JOIN overwrites/conflicts with first JOIN
    3. Wrong data returned: You get source when you wanted standard (or vice versa)
    4. Silent errors: SQL doesn't error, but results are semantically incorrect

#### How it works

This rule analyzes the SQL query to identify concept alias reuse validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    JOIN concept c ON co.condition_source_concept_id = c.concept_id
```

**Correct patterns:**

```sql
SELECT c1.concept_name AS standard_name, c2.concept_name AS source_name
    FROM condition_occurrence co
    JOIN concept c1 ON co.condition_concept_id = c1.concept_id
    JOIN concept c2 ON co.condition_source_concept_id = c2.concept_id
```

#### Suggested fix

None

---

### 9. Concept Ancestor Name Resolution Validation { #joins-concept-ancestor-name-resolution }

**Rule ID:** `joins.concept_ancestor_name_resolution`
**Severity:** ERROR

#### Intent

concept_ancestor has two concept_id columns:
    - ancestor_concept_id: The parent/higher-level concept
    - descendant_concept_id: The child/lower-level concept

    When joining to the concept table to retrieve concept_name, the join column
    determines WHICH concept's name you get.

#### How it works

This rule analyzes the SQL query to identify concept ancestor name resolution validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT c.concept_name AS descendant_name
    FROM concept_ancestor ca
    JOIN concept c ON ca.ancestor_concept_id = c.concept_id
```

**Correct patterns:**

```sql
SELECT c.concept_name AS descendant_name
    FROM concept_ancestor ca
    JOIN concept c ON ca.descendant_concept_id = c.concept_id
    WHERE ca.ancestor_concept_id = 201826
```

#### Common scenarios

- Aliasing as "descendant_name" but joining on ancestor_concept_id
- Returns the parent concept's name, not the descendant's
- Aliasing as "ancestor_name" but joining on descendant_concept_id

#### Suggested fix

Use descendant_concept_id for descendant values and ancestor_concept_id for ancestor values.

---

### 10. Concept Join Validation { #joins-concept-join-validation }

**Rule ID:** `joins.concept_join_validation`
**Severity:** ERROR

#### Intent

The concept table has a primary key (concept_id) and several descriptive columns
    (concept_name, concept_code, vocabulary_id, domain_id, etc.). Joining on
    descriptive columns instead of the primary key causes:

    1. Non-unique matches: concept_name is not unique, causing cartesian joins
    2. String matching issues: Case sensitivity, trailing spaces, encoding
    3. Performance: String joins are much slower than integer joins
    4. Semantic incorrectness: Foreign keys should reference primary keys

#### How it works

This rule analyzes the SQL query to identify concept join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM drug_exposure de
    JOIN concept c ON de.drug_source_value = c.concept_name
```

**Correct patterns:**

```sql
SELECT * FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
```

#### Suggested fix

None

---


### 12. Concept Relationship Requires Relationship ID Filter { #joins-concept-relationship-requires-relationship-id }

**Rule ID:** `joins.concept_relationship_requires_relationship_id`
**Severity:** ERROR

#### Intent

Each use of concept_relationship must filter on relationship_id to avoid cross-product joins across multiple relationship types.

#### How it works

This rule analyzes the SQL query to identify concept relationship requires relationship id filter patterns.

#### Examples

#### Suggested fix

Add a filter on relationship_id for each concept_relationship alias. Example: cr.relationship_id = 'Maps to'

---

### 13. Concept Relationship to Concept Join Validation { #joins-concept-relationship-concept-join-validation }

**Rule ID:** `joins.concept_relationship_concept_join_validation`
**Severity:** ERROR

#### Intent

concept_relationship has two concept_id columns:
    - concept_id_1: The source/origin concept (what you're mapping FROM)
    - concept_id_2: The target/destination concept (what you're mapping TO)

    When joining to the concept table twice to retrieve names for both concepts,
    developers often swap the join columns, causing:
    - The "source" alias to actually show the target concept's name
    - The "target" alias to actually show the source concept's name
    - Completely reversed mapping semantics

#### How it works

This rule analyzes the SQL query to identify concept relationship to concept join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT
      c_source.concept_name AS source_name,
      c_target.concept_name AS target_name
    FROM concept_relationship cr
    JOIN concept c_source ON cr.concept_id_2 = c_source.concept_id
    JOIN concept c_target ON cr.concept_id_1 = c_target.concept_id
```

**Correct patterns:**

```sql
SELECT
      c_source.concept_name AS source_name,
      c_target.concept_name AS target_name
    FROM concept_relationship cr
    JOIN concept c_source ON cr.concept_id_1 = c_source.concept_id
    JOIN concept c_target ON cr.concept_id_2 = c_target.concept_id
```

#### Common scenarios

- Aliasing as "c_source" but joining on concept_id_2
- Returns the target concept's name, not the source
- Aliasing as "c_target" but joining on concept_id_1

#### Suggested fix

Use concept_id_1 for source concepts and concept_id_2 for target concepts.

---

### 14. Concept Relationship to Relationship Join Validation { #joins-concept-relationship-relationship-join-validation }

**Rule ID:** `joins.concept_relationship_relationship_join_validation`
**Severity:** ERROR

#### Intent

The relationship table is a reference table in OMOP that describes relationship types
    (e.g., 'Maps to', 'Subsumes', 'Is a', 'Has form'). The concept_relationship table
    references relationship via the relationship_id column (VARCHAR FK to
    relationship.relationship_id).

#### How it works

This rule analyzes the SQL query to identify concept relationship to relationship join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM concept_relationship cr
    JOIN relationship r ON cr.concept_id_1 = r.relationship_concept_id
```

**Correct patterns:**

```sql
SELECT * FROM concept_relationship cr
    JOIN relationship r ON cr.relationship_id = r.relationship_id
```

#### Common scenarios

- Joining concept_id_1 or concept_id_2 (INTEGER) to relationship_concept_id (INTEGER)
- Type matches but semantics are wrong
- concept_id_1/2 are the concepts being related, not the relationship type

#### Suggested fix

Use: concept_relationship.relationship_id = relationship.relationship_id

---

### 15. Concept Synonym Join Validation { #joins-concept-synonym-join-validation }

**Rule ID:** `joins.concept_synonym_join_validation`
**Severity:** ERROR

#### Intent

The concept_synonym table provides alternative names for concepts:
    - concept.concept_id = 123 → "Type 2 diabetes mellitus" (primary name)
    - concept_synonym.concept_id = 123, concept_synonym_name = "Diabetes mellitus type 2"
    - concept_synonym.concept_id = 123, concept_synonym_name = "T2DM"

    Joining on name strings is unreliable because:
    1. Names are not unique identifiers
    2. Synonyms may match concept names from different concepts
    3. String matching is error-prone (case sensitivity, whitespace)

#### How it works

This rule analyzes the SQL query to identify concept synonym join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM concept_synonym cs
    JOIN concept c ON cs.concept_synonym_name = c.concept_name
```

**Correct patterns:**

```sql
SELECT * FROM concept_synonym cs
    JOIN concept c ON cs.concept_id = c.concept_id
```

#### Suggested fix

Join concept_synonym to concept using concept_id: concept_synonym.concept_id = concept.concept_id

---

### 16. Concept to Concept Class Join Validation { #joins-concept-concept-class-join-validation }

**Rule ID:** `joins.concept_concept_class_join_validation`
**Severity:** ERROR

#### Intent

The concept_class table is a reference table in OMOP that describes concept classes
    (e.g., 'Clinical Drug', 'Ingredient', 'Procedure', 'Clinical Finding'). The concept
    table references concept_class via the concept_class_id column (VARCHAR FK to
    concept_class.concept_class_id).

#### How it works

This rule analyzes the SQL query to identify concept to concept class join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM concept c
    JOIN concept_class cc ON c.concept_id = cc.concept_class_concept_id
```

**Correct patterns:**

```sql
SELECT * FROM concept c
    JOIN concept_class cc ON c.concept_class_id = cc.concept_class_id
```

#### Common scenarios

- Joining concept_id (INTEGER) to concept_class_concept_id (INTEGER)
- Type matches but semantics are wrong
- Joining concept_class_id to concept_class_name

#### Suggested fix

None

---

### 17. Concept to Domain Join Validation { #joins-concept-domain-join-validation }

**Rule ID:** `joins.concept_domain_join_validation`
**Severity:** ERROR

#### Intent

The domain table is a reference table in OMOP that describes domains
    (e.g., 'Condition', 'Drug', 'Procedure', 'Measurement'). The concept table
    references domain via the domain_id column (VARCHAR FK to domain.domain_id).

#### How it works

This rule analyzes the SQL query to identify concept to domain join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM concept c
    JOIN domain d ON c.concept_id = d.domain_concept_id
```

**Correct patterns:**

```sql
SELECT * FROM concept c
    JOIN domain d ON c.domain_id = d.domain_id
```

#### Common scenarios

- Joining concept_id (INTEGER) to domain_concept_id (INTEGER)
- Type matches but semantics are wrong
- Joining domain_id to domain_name

#### Suggested fix

None

---

### 18. Concept to Vocabulary Join Validation { #joins-concept-vocabulary-join-validation }

**Rule ID:** `joins.concept_vocabulary_join_validation`
**Severity:** ERROR

#### Intent

The vocabulary table is a reference table in OMOP that describes vocabularies
    (e.g., 'SNOMED', 'ICD10CM', 'RxNorm'). The concept table references vocabulary
    via the vocabulary_id column (VARCHAR FK to vocabulary.vocabulary_id).

#### How it works

This rule analyzes the SQL query to identify concept to vocabulary join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM concept c
    JOIN vocabulary v ON c.concept_id = v.vocabulary_concept_id
```

**Correct patterns:**

```sql
SELECT * FROM concept c
    JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_id
```

#### Common scenarios

- Joining concept_id (INTEGER) to vocabulary_concept_id (INTEGER)
- Type matches but semantics are wrong
- Joining vocabulary_id to vocabulary_name

#### Suggested fix

None

---

### 19. Cost Table Domain Validation { #joins-cost-table-domain-validation }

**Rule ID:** `joins.cost_table_domain_validation`
**Severity:** ERROR

#### Intent

Without the domain filter, a join can produce incorrect results because
    cost_event_id is a polymorphic foreign key that can reference different tables.

    Example: drug_exposure_id = 123 and procedure_occurrence_id = 123 are DIFFERENT
    events, but without cost_domain_id filter, both would match cost_event_id = 123.

#### How it works

This rule analyzes the SQL query to identify cost table domain validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM cost c
    JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
```

**Correct patterns:**

```sql
SELECT * FROM cost c
    JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
    WHERE c.cost_domain_id = 'Drug'
```

#### Suggested fix

Add cost.cost_domain_id = '<domain>' matching the joined clinical table.

---

### 20. Death to Visit Occurrence Join Validation { #joins-death-visit-occurrence-join-validation }

**Rule ID:** `joins.death_visit_occurrence_join_validation`
**Severity:** ERROR

#### Intent

The death table has a unique structure with person_id as both primary key
    and the ONLY foreign key to other clinical tables. It has NO visit_occurrence_id,
    provider_id, or care_site_id columns.

    The ONLY valid join is:
    death.person_id = visit_occurrence.person_id

#### How it works

This rule analyzes the SQL query to identify death to visit occurrence join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM death d
    JOIN visit_occurrence vo ON d.death_date = vo.visit_end_date
```

**Correct patterns:**

```sql
SELECT
      d.person_id,
      vo.visit_occurrence_id,
      d.death_date,
      vo.visit_end_date
    FROM death d
    JOIN visit_occurrence vo ON d.person_id = vo.person_id
    WHERE d.death_date = vo.visit_end_date  -- Temporal filter in WHERE
```

#### Common scenarios

- Temporal joins using dates (death_date = visit_end_date)
- Structurally invalid even if temporally meaningful
- Temporal correlations should use WHERE clause, not JOIN ON

#### Suggested fix

Use: death.person_id = visit_occurrence.person_id

---

### 21. Drug Exposure to Drug Strength Join Validation { #joins-drug-exposure-drug-strength-join-validation }

**Rule ID:** `joins.drug_exposure_drug_strength_join_validation`
**Severity:** ERROR

#### Intent

The drug_strength table is a vocabulary table that contains strength information
    for drug formulations. It has NO clinical event columns like drug_exposure_id,
    person_id, or visit_occurrence_id.

    The ONLY valid join is:
    drug_exposure.drug_concept_id = drug_strength.drug_concept_id

#### How it works

This rule analyzes the SQL query to identify drug exposure to drug strength join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM drug_exposure de
    JOIN drug_strength ds ON de.drug_exposure_id = ds.drug_concept_id
```

**Correct patterns:**

```sql
SELECT
      de.drug_exposure_id,
      ds.amount_value,
      ds.amount_unit_concept_id
    FROM drug_exposure de
    JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id
    WHERE ds.invalid_reason IS NULL
```

#### Common scenarios

- Joining drug_exposure_id to drug_concept_id
- drug_strength has no drug_exposure_id column
- Joining person_id to drug_concept_id

#### Suggested fix

Use: drug_exposure.drug_concept_id = drug_strength.drug_concept_id

---

### 22. Era Table Forbidden Join Validation { #joins-era-forbidden-join-validation }

**Rule ID:** `joins.era_forbidden_join_validation`
**Severity:** ERROR

#### Intent

Era tables are DERIVED/AGGREGATED tables built from event tables:
    - condition_era is derived from condition_occurrence
    - drug_era is derived from drug_exposure
    - dose_era is derived from drug_exposure

    They represent continuous time periods, not discrete clinical events.
    Era tables have NO foreign keys to visit, provider, or care_site.

    They ONLY have:
    - person_id (FK to person)
    - *_concept_id (FK to concept)

    Any join to visit/provider/care_site is semantically impossible.

#### How it works

This rule analyzes the SQL query to identify era table forbidden join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM drug_era de
    JOIN visit_occurrence vo ON de.person_id = vo.person_id
```

**Correct patterns:**

```sql
SELECT *
    FROM drug_exposure de  -- NOT drug_era
    JOIN visit_occurrence vo ON de.visit_occurrence_id = vo.visit_occurrence_id
```

```sql
SELECT *
    FROM drug_era de
    JOIN person p ON de.person_id = p.person_id
    JOIN concept c ON de.drug_concept_id = c.concept_id
```

#### Suggested fix

Do not join era tables with visit/provider/care_site. Use event tables (condition_occurrence, drug_exposure) for visit-level analysis.

---

### 23. Fact Relationship Polymorphic Join Validation { #joins-fact-relationship-join-validation }

**Rule ID:** `joins.fact_relationship_join_validation`
**Severity:** ERROR

#### Intent

fact_relationship links ANY two facts across the entire OMOP CDM.
    Without domain filtering, ID values collide across tables:
    - measurement_id = 123 (in measurement table)
    - condition_occurrence_id = 123 (in condition_occurrence table)
    - procedure_occurrence_id = 123 (in procedure_occurrence table)

    These are COMPLETELY DIFFERENT clinical events!

#### How it works

This rule analyzes the SQL query to identify fact relationship polymorphic join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM fact_relationship fr
    JOIN measurement m ON fr.fact_id_1 = m.measurement_id
```

**Correct patterns:**

```sql
SELECT * FROM fact_relationship fr
    JOIN measurement m ON fr.fact_id_1 = m.measurement_id
    WHERE fr.domain_concept_id_1 = 21  -- 21 = Measurement domain
```

#### Suggested fix

Add domain filter matching the clinical table, e.g.: WHERE domain_concept_id_1 = <correct_domain_id>

---

### 24. Join Path Validation { #joins-join-path-validation }

**Rule ID:** `joins.join_path_validation`
**Severity:** WARNING

#### Intent

When a query JOINs `concept` (or `concept_relationship`) to a clinical fact table, the join's ON-clause must connect them via `concept_id` (not `domain_id`, `vocabulary_id`, or any other column). A wrong-key join produces an implicit cross-join or silently empty results with no database error.

#### How it works

The rule fires when:

1. The query references a clinical fact table's standard concept field (`condition_concept_id`, `drug_concept_id`, etc.), AND
2. The query references `concept` or `concept_relationship`, AND
3. None of the recognized linkage patterns are present:
   - Direct JOIN: `clinical.<x>_concept_id = concept.concept_id`.
   - Implicit (comma) join with the same equality in WHERE.
   - Indirect bridge through a CTE that selects a `concept_id` column from a vocabulary source.
   - JOIN with unqualified `concept_id` in the ON clause (when concept is the join target).

**Suppression for subquery-only usage.** When `concept` (or `concept_relationship`) appears *only* inside a `Subquery` node — never in the outer query's FROM/JOIN list — the rule suppresses. Examples of suppressed shapes:

```sql
-- Scalar lookup
WHERE p.gender_concept_id = (SELECT concept_id FROM concept WHERE concept_name = 'Female' AND domain_id = 'Gender')

-- IN-subquery filter
WHERE de.drug_concept_id IN (SELECT concept_id FROM concept WHERE vocabulary_id = 'RxNorm' AND standard_concept = 'S')

-- EXISTS subquery
WHERE EXISTS (SELECT 1 FROM concept c WHERE c.concept_id = p.gender_concept_id)
```

In these shapes there's no JOIN to validate; the relevant data-quality concerns (deprecated concepts, trailing whitespace in `concept_name`, ambiguous multi-row lookups) are covered by other rules: `invalid_reason_enforcement`, `concept_name_whitespace`, and `standard_concept_enforcement`. Suppressing here avoids issuing a join-shaped warning on a non-join query.

#### Examples

**Violation pattern** (wrong join key):

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.domain_id;  -- wrong: should be c.concept_id
```

**Correct pattern:**

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN concept c ON co.condition_concept_id = c.concept_id;
```

#### Suggested fix

`JOIN concept ON <clinical_table>.<x>_concept_id = concept.concept_id` — connect the clinical fact's concept_id slot to `concept.concept_id` directly.

---

### 25. Left Join Then Where On Right Table { #joins-left-join-then-where-on-right-table }

**Rule ID:** `joins.left_join_then_where_on_right_table`
**Severity:** WARNING

#### Intent

LEFT JOIN semantics:
    - Returns ALL rows from the left table
    - Matched rows from right table have values
    - Unmatched rows from right table have NULL values

    When a WHERE clause filters on a right table column with non-NULL conditions:
    - Rows where right table column is NULL are filtered out
    - This defeats the purpose of LEFT JOIN
    - Effectively converts LEFT JOIN to INNER JOIN
    - Developer likely intended INNER JOIN or should move filter to JOIN ON

    This is a common SQL anti-pattern that produces unexpected results.

#### How it works

This rule analyzes the SQL query to identify left join then where on right table patterns.

#### Examples

**Violation patterns:**

```sql
SELECT co.* FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_concept_id = 9201
```

```sql
SELECT p.* FROM person p
    LEFT JOIN location l ON p.location_id = l.location_id
    WHERE l.state = 'CA' AND l.city = 'Los Angeles'
```

**Correct patterns:**

```sql
SELECT co.* FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_concept_id = 9201
```

```sql
SELECT co.* FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_occurrence_id IS NULL
```

#### Common scenarios

- Using LEFT JOIN when INNER JOIN is needed
- Filtering right table columns in WHERE instead of JOIN ON
- Not understanding LEFT JOIN NULL behavior

#### Suggested fix

Use INNER JOIN or move filter into JOIN ON clause.

---

### 26. Maps To Direction { #joins-maps-to-direction }

**Rule ID:** `joins.maps_to_direction`
**Severity:** WARNING

#### Intent

OMOP semantic rule: Verify that 'Maps to' relationship is used in the correct direction: - concept_id_1 should be the source concept - concept_id_2 should be the standard concept

#### How it works

This rule analyzes the SQL query to identify maps to direction patterns.

#### Examples

#### Suggested fix

Use concept_id_1 for source, concept_id_2 for standard concept

---

### 27. Note NLP to Note Join Validation { #joins-note-nlp-note-join-validation }

**Rule ID:** `joins.note_nlp_note_join_validation`
**Severity:** ERROR

#### Intent

The note_nlp table contains NLP-extracted entities from clinical notes.
    It's a vocabulary-like extension table that has NO direct patient context
    columns (no person_id, visit_occurrence_id, provider_id).

    The ONLY valid join is:
    note_nlp.note_id = note.note_id

#### How it works

This rule analyzes the SQL query to identify note nlp to note join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM note_nlp nn
    JOIN note n ON nn.note_nlp_id = n.note_id
```

**Correct patterns:**

```sql
SELECT
      nn.note_nlp_id,
      nn.lexical_variant,
      n.note_text,
      n.person_id
    FROM note_nlp nn
    JOIN note n ON nn.note_id = n.note_id
    WHERE nn.note_nlp_concept_id = 4329847
```

#### Common scenarios

- Joining note_nlp_id (PK) to note_id (FK)
- Semantically backwards (like joining person_id to drug_concept_id)
- Trying to join note_nlp directly to person/visit

#### Suggested fix

Use: note_nlp.note_id = note.note_id

---

### 28. Payer Plan Period Join Validation { #joins-payer-plan-period-join-validation }

**Rule ID:** `joins.payer_plan_period_join_validation`
**Severity:** WARNING

#### Intent

A patient can have multiple insurance coverage periods over time:
    - person_id = 12345, coverage from 2020-01-01 to 2020-12-31
    - person_id = 12345, coverage from 2021-01-01 to 2022-06-30
    - person_id = 12345, coverage from 2022-07-01 to 2024-12-31

    If you join only on person_id, a drug exposure on 2021-06-15 will match
    ALL THREE insurance periods, not just the active one!

#### How it works

This rule analyzes the SQL query to identify payer plan period join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM drug_exposure de
    JOIN payer_plan_period pp ON de.person_id = pp.person_id
```

**Correct patterns:**

```sql
SELECT * FROM drug_exposure de
    JOIN payer_plan_period pp
      ON de.person_id = pp.person_id
      AND de.drug_exposure_start_date BETWEEN
          pp.payer_plan_period_start_date AND pp.payer_plan_period_end_date
```

#### Suggested fix

Add temporal overlap: clinical_date BETWEEN payer_plan_period_start_date AND payer_plan_period_end_date

---

### 29. Person ID Join Validation { #joins-person-id-join-validation }

**Rule ID:** `joins.person_id_join_validation`
**Severity:** ERROR

#### Intent

Even if numeric values overlap (person_id=123, visit_occurrence_id=123 both exist),
    they represent completely different entities:
    - person_id = 123 → Patient identifier
    - visit_occurrence_id = 123 → Visit identifier

    These are unrelated despite having the same number.

#### How it works

This rule analyzes the SQL query to identify person id join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.person_id = vo.visit_occurrence_id
```

**Correct patterns:**

```sql
SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.person_id = vo.person_id
```

#### Suggested fix

Join person_id only with person_id. If linking tables, use the correct foreign key (e.g., visit_occurrence_id).

---

### 30. Person to Location Join Validation { #joins-person-location-join-validation }

**Rule ID:** `joins.person_location_join_validation`
**Severity:** ERROR

#### Intent

person has location_id (foreign key to location.location_id) to identify
    the patient's home address. Joining on other columns (e.g., person_id to
    location_id) is semantically incorrect and produces wrong results.

#### How it works

This rule analyzes the SQL query to identify person to location join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM person p
    JOIN location l ON p.person_id = l.location_id
```

**Correct patterns:**

```sql
SELECT * FROM person p
    JOIN location l ON p.location_id = l.location_id
```

#### Suggested fix

Join person to location using location_id: person.location_id = location.location_id

---

### 31. Preceding Visit Occurrence Validation { #joins-preceding-visit-occurrence-validation }

**Rule ID:** `joins.preceding_visit_occurrence_validation`
**Severity:** ERROR

#### Intent

preceding_visit_occurrence_id is a self-referential foreign key that links
    to the previous visit for a patient. It MUST join to visit_occurrence.visit_occurrence_id.

    Patient visit chain example:
    - Visit ID 1: ER visit (preceding_visit_occurrence_id = NULL)
    - Visit ID 2: Inpatient (preceding_visit_occurrence_id = 1)
    - Visit ID 3: Follow-up (preceding_visit_occurrence_id = 2)

#### How it works

This rule analyzes the SQL query to identify preceding visit occurrence validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM visit_occurrence vo
    JOIN visit_detail vd ON vo.preceding_visit_occurrence_id = vd.visit_detail_id
```

```sql
SELECT * FROM visit_occurrence v1
    JOIN visit_occurrence v2 ON v1.preceding_visit_occurrence_id = v2.person_id
```

**Correct patterns:**

```sql
SELECT v1.*, v2.visit_start_date AS prior_visit_date
    FROM visit_occurrence v1
    JOIN visit_occurrence v2
      ON v1.preceding_visit_occurrence_id = v2.visit_occurrence_id
```

#### Common scenarios

- Joining to a different table (visit_detail, person, etc.)
- Joining to the wrong column in visit_occurrence

#### Suggested fix

Join visit_occurrence to itself using preceding_visit_occurrence_id = visit_occurrence_id with separate aliases.

---

### 32. Provider Join Validation { #joins-provider-join-validation }

**Rule ID:** `joins.provider_join_validation`
**Severity:** ERROR

#### Intent

Clinical tables have provider_id (foreign key to provider.provider_id).
    Joining on other columns (e.g., person_id to provider_id) is semantically
    incorrect and produces wrong results.

#### How it works

This rule analyzes the SQL query to identify provider join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM condition_occurrence co
    JOIN provider p ON co.person_id = p.provider_id
```

**Correct patterns:**

```sql
SELECT * FROM condition_occurrence co
    JOIN provider p ON co.provider_id = p.provider_id
```

#### Suggested fix

Join clinical tables to provider using provider_id: clinical_table.provider_id = provider.provider_id

---

### 33. Provider to Care Site Join Validation { #joins-provider-care-site-join-validation }

**Rule ID:** `joins.provider_care_site_join_validation`
**Severity:** ERROR

#### Intent

provider has care_site_id (foreign key to care_site.care_site_id) to identify
    the provider's practice location. Joining on other columns (e.g., provider_id
    to care_site_id) is semantically incorrect and produces wrong results.

#### How it works

This rule analyzes the SQL query to identify provider to care site join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM provider p
    JOIN care_site cs ON p.provider_id = cs.care_site_id
```

**Correct patterns:**

```sql
SELECT * FROM provider p
    JOIN care_site cs ON p.care_site_id = cs.care_site_id
```

#### Suggested fix

Join provider to care_site using care_site_id: provider.care_site_id = care_site.care_site_id

---

### 34. Visit Detail Join Validation { #joins-visit-detail-join-validation }

**Rule ID:** `joins.visit_detail_join_validation`
**Severity:** WARNING

#### Intent

OMOP semantic rule OMOP_034: visit_detail records are nested within visit_occurrence. Queries using visit_detail should join to visit_occurrence via visit_detail.visit_occurrence_id = visit_occurrence.visit_occurrence_id, not via person_id alone.

#### How it works

This rule analyzes the SQL query to identify visit detail join validation patterns.

#### Examples

**Correct patterns:**

```sql
FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
```

```sql
FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.person_id = vo.person_id
```

#### Suggested fix

Join using: vd.visit_occurrence_id = vo.visit_occurrence_id

---

### 35. Visit Occurrence ID Join Validation { #joins-visit-occurrence-id-join-validation }

**Rule ID:** `joins.visit_occurrence_id_join_validation`
**Severity:** ERROR

#### Intent

Even if numeric values overlap (visit_occurrence_id=123, person_id=123 both exist),
    they represent completely different entities:
    - visit_occurrence_id = 123 → A specific visit/encounter
    - person_id = 123 → A patient identifier

    These are unrelated despite having the same number.

#### How it works

This rule analyzes the SQL query to identify visit occurrence id join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM drug_exposure de
    JOIN visit_occurrence vo ON de.visit_occurrence_id = vo.person_id
```

**Correct patterns:**

```sql
SELECT * FROM drug_exposure de
    JOIN visit_occurrence vo ON de.visit_occurrence_id = vo.visit_occurrence_id
```

#### Suggested fix

Join visit_occurrence_id only with visit_occurrence_id. If linking across domains, use the correct foreign key (e.g., person_id).

---

### 36. Visit Occurrence INNER JOIN Validation { #joins-visit-occurrence-inner-join-validation }

**Rule ID:** `joins.visit_occurrence_inner_join_validation`
**Severity:** WARNING

#### Intent

INNER JOIN filters out rows where visit_occurrence_id IS NULL.
    This silently excludes:
    - Outpatient prescriptions
    - External lab results
    - Historical diagnoses
    - Telemedicine events
    - Claims data without encounter mapping

#### How it works

This rule analyzes the SQL query to identify visit occurrence inner join validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT co.*
    FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
```

**Correct patterns:**

```sql
SELECT co.*
    FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
```

#### Suggested fix

Use LEFT JOIN to preserve all events, or explicitly filter visit_occurrence_id to indicate intentional restriction.

---

## Temporal Rules

These rules validate temporal logic, date handling, and observation period anchoring.

---

### 1. Clinical Event Date Should Not Be In Future { #temporal-clinical-event-date-in-future-validation }

**Rule ID:** `temporal.clinical_event_date_in_future_validation`
**Severity:** WARNING

#### Intent

Detects filtering logic that implies clinical event dates occur in the future. Future event dates may indicate data quality issues or incorrect query logic.

#### How it works

This rule analyzes the SQL query to identify clinical event date should not be in future patterns.

#### Examples

#### Suggested fix

Use date filters consistent with past or present events. Avoid filtering for future clinical dates unless explicitly intended.

---

### 2. Datetime BETWEEN with Date Literal { #temporal-datetime-between-date-literal }

**Rule ID:** `temporal.datetime_between_date_literal`
**Severity:** WARNING

#### Intent

BETWEEN with datetime columns and date literals excludes non-midnight times
    on the end date:

    WHERE measurement_datetime BETWEEN '2023-01-01' AND '2023-01-31'
    -- '2023-01-31' is interpreted as '2023-01-31 00:00:00'
    -- Excludes: '2023-01-31 08:30:00', '2023-01-31 23:59:59', etc.
    -- SILENT DATA LOSS!

    This is a common mistake that's hard to catch because:
    - Query executes without error
    - Returns results (just incomplete)
    - Easy to miss in testing

OMOP Context:
    OMOP CDM has parallel DATE and DATETIME columns:

    Datetime columns (affected):
    - condition_start_datetime, condition_end_datetime
    - drug_exposure_start_datetime, drug_exposure_end_datetime
    - measurement_datetime
    - observation_datetime
    - visit_start_datetime, visit_end_datetime
    - procedure_datetime
    - device_exposure_start_datetime, device_exposure_end_datetime

    Date columns (safe with BETWEEN):
    - condition_start_date, drug_exposure_start_date, etc.

#### How it works

This rule analyzes the SQL query to identify datetime between with date literal patterns.

#### Examples

#### Suggested fix

Use >= start AND < next_day, or include time in end literal, or use *_date column instead.

---

### 3. Death Date Before Birth Validation { #temporal-death-date-before-birth-validation }

**Rule ID:** `temporal.death_date_before_birth_validation`
**Severity:** ERROR

#### Intent

A person cannot die before they are born. Queries filtering for death_date
    before birth_datetime (or death year before year_of_birth) represent:
    - Data quality issues (incorrect dates)
    - Logic errors in the query
    - Incorrect join conditions

#### How it works

This rule analyzes the SQL query to identify death date before birth validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
    WHERE d.death_date < p.birth_datetime
```

```sql
SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
    WHERE YEAR(d.death_date) < p.year_of_birth
```

**Correct patterns:**

```sql
SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
    WHERE d.death_date >= p.birth_datetime
```

```sql
SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
```

#### Suggested fix

Ensure death_date >= birth_datetime

---

### 4. Death Date In Future Validation { #temporal-death-date-in-future-validation }

**Rule ID:** `temporal.death_date_in_future_validation`
**Severity:** WARNING

#### Intent

Death dates in the future are impossible and represent:
    - Data quality issues (incorrect death dates)
    - Data entry errors (wrong year, wrong century)
    - Logic errors in the query (wrong comparison operator)

#### How it works

This rule analyzes the SQL query to identify death date in future validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM death WHERE death_date > CURRENT_DATE
```

```sql
SELECT * FROM death WHERE death_date > '2030-01-01'
```

**Correct patterns:**

```sql
SELECT * FROM death WHERE death_date <= CURRENT_DATE
```

```sql
SELECT * FROM death WHERE death_date BETWEEN '2020-01-01' AND '2023-12-31'
```

#### Suggested fix

Ensure death_date <= CURRENT_DATE

---

### 5. End Before Start Validation { #temporal-end-before-start-validation }

**Rule ID:** `temporal.end_before_start_validation`
**Severity:** ERROR

#### Intent

Many OMOP tables have start_date and end_date columns representing
    temporal events. A query with static date filters that forces the end
    date to be before the start date is logically impossible and indicates
    a WHERE clause error.

Covered Tables and Column Pairs:
    condition_occurrence:
        - condition_start_date, condition_end_date (CLIN_011, OMOP_551)

    drug_exposure:
        - drug_exposure_start_date, drug_exposure_end_date (OMOP_551)

    visit_occurrence:
        - visit_start_date, visit_end_date (OMOP_052, OMOP_551)

    visit_detail:
        - visit_detail_start_date, visit_detail_end_date (CLIN_045)

    cohort:
        - cohort_start_date, cohort_end_date (OMOP_529)

    episode:
        - episode_start_date, episode_end_date (OMOP_244)

Example Violations:
    -- ERROR: Start must be after June, but end must be before January
    WHERE condition_start_date > '2023-06-01'
      AND condition_end_date < '2023-01-01'

    -- ERROR: Start >= June 1, but end < June 1
    WHERE drug_exposure_start_date >= '2023-06-01'
      AND drug_exposure_end_date < '2023-06-01'

    -- ERROR: Start = June 15, but end = May 1
    WHERE visit_start_date = '2023-06-15'
      AND visit_end_date = '2023-05-01'

Valid Patterns (no violation):
    -- OK: Overlapping range is possible
    WHERE condition_start_date > '2023-01-01'
      AND condition_end_date < '2023-12-31'

    -- OK: Could start and end on same day
    WHERE visit_start_date >= '2023-06-01'
      AND visit_end_date >= '2023-06-01'

    -- OK: Dynamic comparison (not static date literals)
    WHERE condition_end_date < condition_start_date + INTERVAL '30 days'

#### How it works

OMOP semantic rules CLIN_011, CLIN_045, OMOP_052, OMOP_244, OMOP_529, OMOP_551:
Detects logically impossible date constraints where static filters force
end_date < start_date for the same record

#### Examples

#### Suggested fix

Ensure start_date <= end_date in filters

---

### 6. Unbounded Follow-up Window (Future Information Leakage) { #temporal-future-information-leakage }

**Rule ID:** `temporal.future_information_leakage`
**Severity:** WARNING

#### Intent

When a query compares date columns across two different clinical event tables (e.g. `co.condition_start_date > de.drug_exposure_start_date`), the *later* event must be bounded by `observation_period_end_date`. Without that bound, the comparison reaches beyond the patient's observed follow-up window — producing immortal-time bias and similar follow-up-window errors in cohort analyses.

The rule is named "future information leakage" for historical reasons; the underlying problem is a missing right-censoring/follow-up-window bound, not ML-style look-ahead leakage.

#### How it works

The rule looks for inequality comparisons (`<`, `<=`, `>`, `>=`) in `WHERE` / `JOIN ON` clauses where:

1. Both sides are columns from clinical fact tables (different tables on each side).
2. Both columns are date or datetime columns.
3. No upper-bound predicate against `observation_period_end_date` exists anywhere in the same query (BETWEEN ... AND `observation_period_end_date` also satisfies the bound).

**Suppression contract.** If `observation_period` is *not joined at all*, this rule stays silent and defers to `temporal.observation_period_anchoring`. The anchoring rule already fires for the same root cause and ships a coherent fix that introduces the JOIN. Firing both would double-count and would emit a patch referencing an `op.` alias the query never defines.

When `observation_period` IS joined but no upper bound is asserted, this rule fires with a self-contained patch: it resolves the actual alias the query uses for `observation_period` (e.g. `op`, or the bare table name when no alias is given) and substitutes it directly into the `ADD` patch — no `<op>` placeholder.

#### Examples

**Violation pattern** (`observation_period` joined, no upper bound):

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN drug_exposure de ON co.person_id = de.person_id
JOIN observation_period op ON co.person_id = op.person_id
WHERE co.condition_start_date > de.drug_exposure_start_date
```

**Correct pattern** (later event bounded by follow-up window):

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN drug_exposure de ON co.person_id = de.person_id
JOIN observation_period op ON co.person_id = op.person_id
WHERE co.condition_start_date > de.drug_exposure_start_date
  AND co.condition_start_date <= op.observation_period_end_date
```

**Suppressed pattern** (`observation_period` not joined — handled by `observation_period_anchoring` instead, no duplicate violation here):

```sql
SELECT co.person_id
FROM condition_occurrence co
JOIN drug_exposure de ON co.person_id = de.person_id
WHERE co.condition_start_date > de.drug_exposure_start_date
```

#### Suggested fix

`ADD: AND <later_qual>.<later_col> <= <op_alias>.observation_period_end_date` — bounds the later event by the patient's observed follow-up window. The rule resolves `<op_alias>` from the query's actual JOIN list, so the emitted patch is directly applyable.

---

### 7. Nullable End Date NULL Handling { #temporal-nullable-end-date-null-handling }

**Rule ID:** `temporal.nullable_end_date_null_handling`
**Severity:** WARNING

#### Intent

End date columns are frequently NULL because:
    - Ongoing/chronic conditions without resolution
    - Drug exposures with unknown end dates
    - Single-point-in-time procedures
    - Incomplete/ongoing visits

    NULL in date arithmetic returns NULL, silently excluding rows from aggregations.

Example impact:
    SELECT AVG(DATEDIFF(day, start_date, end_date))
    FROM condition_occurrence
    -- Returns NULL for rows with NULL end_date
    -- Aggregation excludes these rows → biased results

#### How it works

This rule analyzes the SQL query to identify nullable end date null handling patterns.

#### Examples

**Violation patterns:**

```sql
SELECT DATEDIFF(day, procedure_date, procedure_end_date) AS duration
    FROM procedure_occurrence
```

**Correct patterns:**

```sql
with fallback
    SELECT DATEDIFF(day, drug_exposure_start_date,
                    COALESCE(drug_exposure_end_date, CURRENT_DATE))
    FROM drug_exposure
```

```sql
SELECT DATEDIFF(day, visit_start_date, visit_end_date)
    FROM visit_occurrence
    WHERE visit_end_date IS NOT NULL
```

#### Suggested fix

Use COALESCE(end_date, fallback) or filter IS NOT NULL

---

### 8. Observation Period Anchoring { #temporal-observation-period-anchoring }

**Rule ID:** `temporal.observation_period_anchoring`
**Severity:** ERROR

#### Intent

OMOP semantic rule: Queries with temporal constraints (washout, follow-up, event windows) MUST join to observation_period on person_id.

#### How it works

This rule analyzes the SQL query to identify observation period anchoring patterns.

#### Examples

#### Suggested fix

JOIN observation_period op ON clinical_table.person_id = op.person_id AND clinical_table.date BETWEEN op.observation_period_start_date AND op.observation_period_end_date

---

### 9. Observation Period Date Range Logic { #temporal-observation-period-date-range-logic }

**Rule ID:** `temporal.observation_period_date_range_logic`
**Severity:** ERROR

#### Intent

OMOP semantic rule OMOP_033: When using observation_period to validate patient enrollment, the clinical event date must fall BETWEEN observation_period_start_date AND observation_period_end_date.

#### How it works

This rule analyzes the SQL query to identify observation period date range logic patterns.

#### Examples

#### Suggested fix

Use: event_date BETWEEN op.observation_period_start_date AND op.observation_period_end_date.

---

### 10. Required Date Column Validation { #temporal-required-date-column-validation }

**Rule ID:** `temporal.required_date_column_validation`
**Severity:** WARNING

#### Intent

Many clinical tables have multiple temporal columns with different nullability:
    - A required date column (NOT NULL, always populated)
    - Optional datetime columns (may be NULL)
    - Optional end date columns (may be NULL for ongoing events)

    Using nullable columns for temporal filtering can silently exclude records
    where those columns are NULL, leading to incomplete result sets.

Covered Tables and Columns:
    condition_occurrence:
        - condition_start_date: Required (NOT NULL)
        - condition_start_datetime: Optional (nullable)
        - condition_end_date: Optional (nullable, often NULL for ongoing conditions)

    drug_exposure:
        - drug_exposure_start_date: Required (NOT NULL)
        - drug_exposure_start_datetime: Optional (nullable)
        - drug_exposure_end_date: Optional (nullable)

    measurement:
        - measurement_date: Required (NOT NULL)
        - measurement_datetime: Optional (nullable)
        - measurement_time: Optional (nullable)

    observation:
        - observation_date: Required (NOT NULL)
        - observation_datetime: Optional (nullable)

Example impact:
    -- BAD: Uses nullable column
    SELECT COUNT(*) FROM drug_exposure
    WHERE drug_exposure_end_date BETWEEN '2023-01-01' AND '2023-12-31'
    -- May exclude records where end_date is NULL

    -- GOOD: Uses required column
    SELECT COUNT(*) FROM drug_exposure
    WHERE drug_exposure_start_date BETWEEN '2023-01-01' AND '2023-12-31'
    -- Includes all records (start_date is always populated)

Correct patterns (no violation):
    -- Use required date column
    WHERE condition_start_date BETWEEN '2023-01-01' AND '2023-12-31'

    -- Or use COALESCE if datetime precision is needed
    WHERE COALESCE(condition_start_datetime, condition_start_date) > '2023-01-01'

    -- Or explicitly handle NULLs
    WHERE condition_start_datetime > '2023-01-01'
      AND condition_start_datetime IS NOT NULL

#### How it works

This rule analyzes the SQL query to identify required date column validation patterns.

#### Examples

#### Suggested fix

Use required date columns, COALESCE, or explicit IS NOT NULL checks

---

## Data Quality Rules

These rules validate data quality, schema compliance, and proper handling of edge cases.

---

### 1. Clinical Event Date Should Not Be Before 1900 { #data-quality-clinical-event-date-before-1900-validation }

**Rule ID:** `data_quality.clinical_event_date_before_1900_validation`
**Severity:** WARNING

#### Intent

Clinical event dates before 1900 are implausible and represent:
    - Data quality issues (incorrect event dates)
    - Data entry errors (wrong year, wrong century)
    - Logic errors in the query (accidentally filtering for ancient dates)

Clinical event tables covered:
    - condition_occurrence (condition_start_date, condition_end_date, etc.)
    - drug_exposure (drug_exposure_start_date, drug_exposure_end_date, etc.)
    - procedure_occurrence (procedure_date, procedure_datetime)
    - measurement (measurement_date, measurement_datetime)
    - observation (observation_date, observation_datetime)
    - visit_occurrence (visit_start_date, visit_end_date, etc.)
    - visit_detail (visit_detail_start_date, visit_detail_end_date, etc.)
    - device_exposure (device_exposure_start_date, device_exposure_end_date, etc.)
    - specimen (specimen_date, specimen_datetime)
    - note (note_date, note_datetime)
    - episode (episode_start_date, episode_end_date, etc.)

#### How it works

This rule analyzes the SQL query to identify clinical event date should not be before 1900 patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM condition_occurrence WHERE condition_start_date < '1900-01-01'
    SELECT * FROM drug_exposure WHERE drug_exposure_start_date <= '1899-12-31'
    SELECT * FROM procedure_occurrence WHERE YEAR(procedure_date) < 1900
    SELECT * FROM measurement WHERE measurement_date BETWEEN '1850-01-01' AND '1899-12-31'
    SELECT * FROM observation WHERE observation_date IN ('1880-01-01', '1890-01-01')
```

**Correct patterns:**

```sql
SELECT * FROM condition_occurrence WHERE condition_start_date >= '1900-01-01'
    SELECT * FROM drug_exposure WHERE drug_exposure_start_date BETWEEN '1950-01-01' AND '2023-12-31'
    SELECT * FROM procedure_occurrence WHERE YEAR(procedure_date) >= 1900
```

#### Suggested fix

Use realistic date ranges (>= 1900-01-01) unless intentionally analyzing historical placeholders.

---

### 2. Column Type Validation { #data-quality-column-type-validation }

**Rule ID:** `data_quality.column_type_validation`
**Severity:** ERROR

#### Intent

OMOP semantic rules OMOP_004, OMOP_005, OMOP_024, OMOP_025, OMOP_026: Validates that column data types are compatible in JOIN conditions and WHERE clauses.

#### How it works

This rule analyzes the SQL query to identify column type validation patterns.

#### Examples

#### Suggested fix

Ensure column types are compatible. Use proper literals or CAST explicitly if needed.

---

### 4. Concept ID String Comparison { #data-quality-concept-id-string-comparison }

**Rule ID:** `data_quality.concept_id_string_comparison`
**Severity:** WARNING

#### Intent

All columns ending in `_concept_id` store integer values representing OMOP concepts.
    Comparing these columns with quoted string literals forces the database to perform
    implicit type conversion, which:

    - Degrades query performance (string-to-integer conversion for every row)
    - May fail on some database engines with strict type checking
    - Indicates sloppy coding practices
    - Can produce unexpected results depending on database collation/casting rules

#### How it works

This rule analyzes the SQL query to identify concept id string comparison patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM condition_occurrence WHERE condition_concept_id = '201826'
```

```sql
SELECT * FROM measurement WHERE measurement_concept_id IN ('3004249', '3012888')
```

**Correct patterns:**

```sql
SELECT * FROM condition_occurrence WHERE condition_concept_id = 201826
```

```sql
SELECT * FROM measurement WHERE measurement_concept_id IN (3004249, 3012888)
```

#### Suggested fix

Replace string literals with integer literals: concept_id = 201826 instead of concept_id = '201826'

---

### 5. Concept Name Whitespace { #data-quality-concept-name-whitespace }

**Rule ID:** `data_quality.concept_name_whitespace`
**Severity:** WARNING

#### Intent

OMOP vocabulary data sometimes contains concept names with trailing whitespace:
    - 'Type 2 diabetes mellitus ' (note the trailing space)
    - 'Metformin  ' (multiple trailing spaces)

    When queries use exact equality (=) without trimming, they may fail to match:
    - concept_name = 'Metformin' won't match 'Metformin  ' (with trailing spaces)
    - This causes silent failures - no error, just missing results

    This is particularly problematic because:
    - Users don't expect whitespace in concept names
    - The mismatch is invisible in most query tools
    - Data quality varies across vocabulary versions

#### How it works

This rule analyzes the SQL query to identify concept name whitespace patterns.

#### Examples

**Violation patterns:**

```sql
without TRIM
    SELECT concept_id
    FROM concept
    WHERE concept_name = 'Type 2 diabetes mellitus'
      AND standard_concept = 'S'
```

```sql
with trailing whitespace
```

**Correct patterns:**

```sql
SELECT concept_id
    FROM concept
    WHERE TRIM(concept_name) = 'Type 2 diabetes mellitus'
      AND standard_concept = 'S'
```

```sql
SELECT concept_id
    FROM concept
    WHERE RTRIM(concept_name) = 'Type 2 diabetes mellitus'
      AND standard_concept = 'S'
```

#### Suggested fix

Use TRIM(concept_name) = 'value' or RTRIM(concept_name) = 'value', or use LIKE for safer matching.

---

### 9. Episode Requires Concept Filter { #data-quality-episode-requires-concept-filter }

**Rule ID:** `data_quality.episode_requires_concept_filter`
**Severity:** WARNING

#### Intent

The episode table in OMOP CDM represents aggregated clinical events spanning
    multiple dates (e.g., treatment regimens, disease episodes, hospitalizations).
    The episode_concept_id column defines the TYPE of episode being tracked.

    Querying the episode or episode_event tables without filtering by
    episode_concept_id can lead to:
    - Poor query performance (scanning all episode types)
    - Semantic ambiguity (unclear query intent)
    - Logical errors (mixing incompatible episode types)

Common episode types include:
    - Disease Episode (concept_id 32533)
    - Treatment Episode
    - Hospitalization Episode
    - Drug Era Episode

#### How it works

This rule analyzes the SQL query to identify episode requires concept filter patterns.

#### Examples

**Violation patterns:**

```sql
SELECT p.person_id, e.episode_start_date
    FROM episode e
    JOIN person p ON e.person_id = p.person_id;
```

```sql
SELECT *
    FROM episode e
    WHERE e.episode_start_date > '2020-01-01';
```

**Correct patterns:**

```sql
SELECT p.person_id, e.episode_start_date
    FROM episode e
    JOIN person p ON e.person_id = p.person_id
    WHERE e.episode_concept_id = 32533;  -- Disease Episode
```

```sql
SELECT *
    FROM episode e
    WHERE e.episode_concept_id IN (32533, 32534, 32535)
      AND e.episode_start_date > '2020-01-01';
```

#### Suggested fix

Add a filter on episode_concept_id (e.g., WHERE episode_concept_id = <id>) or join to concept with appropriate filtering.

---

### 10. Fact Relationship No Self-Reference { #data-quality-fact-relationship-no-self-reference }

**Rule ID:** `data_quality.fact_relationship_no_self_reference`
**Severity:** WARNING

#### Intent

The fact_relationship table links two clinical events together via a relationship.
    In most cases, linking an event to itself doesn't make semantic sense:

    - A measurement "preceded by" itself
    - A condition "followed by" itself
    - A procedure "causally related to" itself

    While there might be extremely rare valid cases, queries that explicitly
    filter for or create self-referential relationships typically indicate:
    - Data quality issues
    - Logic errors in ETL processes
    - Incorrect query logic

#### How it works

This rule analyzes the SQL query to identify fact relationship no self-reference patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM fact_relationship
    WHERE fact_id_1 = fact_id_2;
```

```sql
SELECT *
    FROM fact_relationship fr
    JOIN measurement m1 ON fr.fact_id_1 = m1.measurement_id
    JOIN measurement m2 ON fr.fact_id_2 = m2.measurement_id
    WHERE m1.measurement_id = m2.measurement_id;
```

**Correct patterns:**

```sql
SELECT * FROM fact_relationship
    WHERE fact_id_1 = 100
      AND fact_id_2 = 200;
```

```sql
SELECT *
    FROM fact_relationship fr
    JOIN measurement m1 ON fr.fact_id_1 = m1.measurement_id
    JOIN measurement m2 ON fr.fact_id_2 = m2.measurement_id
    WHERE m1.measurement_id != m2.measurement_id;
```

#### Suggested fix

Remove self-referential filters (fact_id_1 = fact_id_2). If intentional, verify this is a valid use case.

---

### 11. Fact Relationship Requires Relationship Concept Filter { #data-quality-fact-relationship-requires-relationship-concept-filter }

**Rule ID:** `data_quality.fact_relationship_requires_relationship_concept_filter`
**Severity:** ERROR

#### Intent

The fact_relationship table links facts across different domain tables using
    relationship types defined by relationship_concept_id. Querying fact_relationship
    without filtering by relationship_concept_id can lead to:
    - Poor query performance (scanning all relationship types)
    - Semantic ambiguity (unclear query intent)
    - Logical errors (mixing incompatible relationship types)

Common relationship types include:
    - "Has temporal context" (concept_id 44818790)
    - "Preceded by" (concept_id 44818783)
    - "Followed by" (concept_id 44818784)
    - "Causally related to" (concept_id 44818888)

#### How it works

This rule analyzes the SQL query to identify fact relationship requires relationship concept filter patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM fact_relationship
    WHERE fact_id_1 = 100;
```

```sql
SELECT *
    FROM fact_relationship
    WHERE domain_concept_id_1 = 19;
```

**Correct patterns:**

```sql
SELECT * FROM fact_relationship
    WHERE relationship_concept_id = 44818790  -- "Has temporal context"
      AND fact_id_1 = 100;
```

```sql
SELECT *
    FROM fact_relationship
    WHERE relationship_concept_id IN (44818783, 44818784)
      AND fact_id_1 = 100;
```

#### Suggested fix

Add a filter on relationship_concept_id (e.g., WHERE relationship_concept_id = <id>) or join to concept with proper filtering.

---

### 12. Fact Relationship Valid Concepts { #data-quality-fact-relationship-valid-concepts }

**Rule ID:** `data_quality.fact_relationship_valid_concepts`
**Severity:** WARNING

#### Intent

The fact_relationship table contains three concept_id columns that reference
    the concept table:
    - domain_concept_id_1: Domain of the first fact
    - domain_concept_id_2: Domain of the second fact
    - relationship_concept_id: Type of relationship between facts

    When joining to the concept table to validate or filter these concept IDs,
    queries must check invalid_reason to ensure only valid (current) concepts
    are used. Invalid concepts may represent deprecated relationships or domains.

#### How it works

This rule analyzes the SQL query to identify fact relationship valid concepts patterns.

#### Examples

**Violation patterns:**

```sql
SELECT fr.*
    FROM fact_relationship fr
    JOIN concept c ON fr.relationship_concept_id = c.concept_id;
```

```sql
without invalid_reason
    SELECT fr.*
    FROM fact_relationship fr
    JOIN concept d1 ON fr.domain_concept_id_1 = d1.concept_id
    WHERE d1.domain_id = 'Condition';
```

**Correct patterns:**

```sql
SELECT fr.*
    FROM fact_relationship fr
    JOIN concept c ON fr.relationship_concept_id = c.concept_id
    WHERE c.invalid_reason IS NULL;
```

```sql
with invalid_reason
    SELECT fr.*
    FROM fact_relationship fr
    JOIN concept d1 ON fr.domain_concept_id_1 = d1.concept_id
    JOIN concept d2 ON fr.domain_concept_id_2 = d2.concept_id
    JOIN concept r ON fr.relationship_concept_id = r.concept_id
    WHERE d1.invalid_reason IS NULL
      AND d2.invalid_reason IS NULL
      AND r.invalid_reason IS NULL;
```

#### Suggested fix

Add '<alias>.invalid_reason IS NULL' for each concept join to ensure only valid concepts are used.

---

### 14. Negative Concept ID Validation { #data-quality-negative-concept-id-validation }

**Rule ID:** `data_quality.negative_concept_id_validation`
**Severity:** ERROR

#### Intent

OMOP concept_id values range from 0 (unmapped) to positive integers.
    Negative values are not allowed and will never return results.

#### How it works

This rule analyzes the SQL query to identify negative concept id validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM condition_occurrence WHERE condition_concept_id = -1
```

**Correct patterns:**

```sql
SELECT * FROM condition_occurrence WHERE condition_concept_id = 201826
```

#### Common scenarios

- Using -1 as a sentinel/null value
- Typos or sign errors
- Copy-paste from non-OMOP systems

#### Suggested fix

Use valid non-negative concept_id values. Use 0 for unmapped.

---

### 15. Note NLP Offset is Character Position { #data-quality-note-nlp-offset-is-character-position }

**Rule ID:** `data_quality.note_nlp_offset_is_character_position`
**Severity:** WARNING

#### Intent

The note_nlp.offset column stores character positions as VARCHAR, not INTEGER.
    Developers often mistakenly treat it as a numeric column because:
    1. Positions are conceptually numeric
    2. Most OMOP CDM position/ID fields are integers
    3. The name "offset" suggests a numeric value

    This leads to:
    - String vs numeric comparison semantics (e.g., '9' > '100' is true)
    - Using it in JOINs (semantically incorrect - it's a position, not a key)
    - Arithmetic operations without proper casting

#### How it works

This rule analyzes the SQL query to identify note nlp offset is character position patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM note_nlp
    WHERE offset > 100
```

**Correct patterns:**

```sql
SELECT *
    FROM note_nlp
    WHERE CAST(offset AS INT) > 100
```

#### Common scenarios

- Direct numeric comparisons: WHERE offset > 100
- Using in JOIN conditions: JOIN ... ON nn.offset = ...
- BETWEEN without CAST: WHERE offset BETWEEN 50 AND 200

#### Suggested fix

Use CAST(offset AS INT) or CONVERT(INT, offset) for numeric usage. Avoid using offset in JOIN conditions.

---

### 17. Note NLP nlp_date for Temporal Filtering { #data-quality-note-nlp-nlp-date-for-temporal-filtering }

**Rule ID:** `data_quality.note_nlp_nlp_date_for_temporal_filtering`
**Severity:** WARNING

#### Intent

The note_nlp.nlp_date column stores when the NLP processing was performed,
    NOT when the clinical event occurred. This is a critical semantic distinction:

    - nlp_date: Processing timestamp (e.g., when cTAKES ran on the note)
    - note.note_date: Actual clinical date of the note/event

    Using nlp_date for temporal filtering produces incorrect results because:
    1. NLP processing often happens in batches, long after the clinical event
    2. Re-running NLP changes nlp_date but not the clinical date
    3. The same note processed twice would have different nlp_date values
    4. Cohorts defined by nlp_date are non-reproducible across NLP runs

#### How it works

This rule analyzes the SQL query to identify note nlp nlp_date for temporal filtering patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM note_nlp
    WHERE note_nlp_concept_id = 201826
      AND nlp_date BETWEEN '2023-01-01' AND '2023-12-31'
```

```sql
from notes written in 2023!
```

**Correct patterns:**

```sql
SELECT nn.*
    FROM note_nlp nn
    JOIN note n ON nn.note_id = n.note_id
    WHERE nn.note_nlp_concept_id = 201826
      AND n.note_date BETWEEN '2023-01-01' AND '2023-12-31'
```

```sql
from notes written in 2023
```

#### Common scenarios

- WHERE nlp_date BETWEEN '2023-01-01' AND '2023-12-31'
- WHERE nlp_date > '2023-01-01'
- Using nlp_date for cohort entry date ranges

#### Suggested fix

Join to note table and filter using note.note_date instead.

---

### 18. Schema Validation { #data-quality-schema-validation }

**Rule ID:** `data_quality.schema_validation`
**Severity:** ERROR

#### Intent

OMOP CDM schema validation: Validates that columns referenced in SQL queries exist in the OMOP CDM schema. Catches common errors like using concept_ancestor columns on concept_relationship table.

#### How it works

This rule analyzes the SQL query to identify schema validation patterns.

#### Examples

#### Suggested fix

Check OMOP CDM documentation for correct column names

---

### 19. Standard Concept NULL Handling { #data-quality-standard-concept-null-handling }

**Rule ID:** `data_quality.standard_concept_null_handling`
**Severity:** WARNING

#### Intent

In OMOP CDM, concept.standard_concept has three possible values:
    - 'S' = Standard concept
    - 'C' = Classification concept
    - NULL = Non-standard concept

#### How it works

This rule analyzes the SQL query to identify standard concept null handling patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM concept
    WHERE standard_concept = NULL  -- WRONG: use IS NULL
```

```sql
SELECT * FROM concept
    WHERE standard_concept = ''  -- WRONG: empty string invalid
```

**Correct patterns:**

```sql
SELECT * FROM concept
    WHERE standard_concept IS NULL  -- Non-standard concepts
```

```sql
SELECT * FROM concept
    WHERE standard_concept = 'S'  -- Standard concepts only
```

#### Common scenarios

- Using standard_concept = NULL instead of IS NULL
- Using standard_concept = '' (empty string - no concepts have this value)
- Using standard_concept = 'N' (this value doesn't exist - non-standard is NULL)

#### Suggested fix

Use IS NULL for non-standard concepts, IS NOT NULL for standard/classification, or filter explicitly for valid values ('S', 'C').

---

### 20. UNION vs UNION ALL for Clinical Events { #data-quality-union-vs-union-all-clinical-events }

**Rule ID:** `data_quality.union_vs_union_all_clinical_events`
**Severity:** WARNING

#### Intent

UNION removes duplicates by sorting and deduplicating results. For clinical
    event data, this is almost always wrong because:

    1. Legitimate duplicates: Two events that look identical are still separate
       - Two ER visits on same day (morning: chest pain, evening: injury)
       - Two drug prescriptions on same day (different physicians/episodes)
       - Multiple measurements on same day (repeated tests, different encounters)
       - Two procedures on same day (staged surgeries, emergency + planned)

    2. Silent data loss: UNION drops events without warning or error

    3. Incorrect counts: Event counting becomes inaccurate
       - Patient had 5 visits, but UNION shows 3
       - Cost analysis missing events

    4. Performance: UNION is slower (must sort and deduplicate)

    Clinical events have unique primary keys (condition_occurrence_id,
    drug_exposure_id, etc.) that make them distinct even if other columns
    appear identical. UNION operates on selected columns only, not primary keys.

#### How it works

This rule analyzes the SQL query to identify union vs union all for clinical events patterns.

#### Examples

**Violation patterns:**

```sql
SELECT person_id, condition_start_date AS event_date
    FROM condition_occurrence
    WHERE condition_concept_id = 201826
    UNION
    SELECT person_id, drug_exposure_start_date
    FROM drug_exposure
    WHERE drug_concept_id = 1125315
```

```sql
SELECT person_id, visit_start_date
    FROM visit_occurrence
    WHERE visit_concept_id = 9203
    UNION
    SELECT person_id, visit_start_date
    FROM visit_detail
    WHERE visit_detail_concept_id = 9201
```

**Correct patterns:**

```sql
SELECT person_id, condition_start_date AS event_date
    FROM condition_occurrence
    WHERE condition_concept_id = 201826
    UNION ALL
    SELECT person_id, drug_exposure_start_date
    FROM drug_exposure
    WHERE drug_concept_id = 1125315
```

#### Common scenarios

- Using UNION by default (learned from non-temporal data)
- Assuming duplicates are data quality issues (they're not for events)
- Not understanding that identical appearance ≠ same event

#### Suggested fix

Replace UNION with UNION ALL to preserve all clinical events. If deduplication is intentional, document it explicitly.

---

### 21. Union Concept ID Domain Indicator { #data-quality-union-concept-id-domain-indicator }

**Rule ID:** `data_quality.union_concept_id_domain_indicator`
**Severity:** WARNING

#### Intent

Each OMOP domain has its own concept_id column:
    - condition_occurrence.condition_concept_id
    - drug_exposure.drug_concept_id
    - procedure_occurrence.procedure_concept_id
    - measurement.measurement_concept_id
    - observation.observation_concept_id

    UNION queries that mix these without domain labels create ambiguous results.

Example impact:
    SELECT condition_concept_id AS concept_id
    FROM condition_occurrence
    UNION ALL
    SELECT drug_concept_id AS concept_id
    FROM drug_exposure
    -- Returns: [201826, 1545958, 313217, ...]
    -- Which are conditions? Which are drugs? UNKNOWN!
    -- Results are uninterpretable without domain context

#### How it works

This rule analyzes the SQL query to identify union concept id domain indicator patterns.

#### Examples

**Violation patterns:**

```sql
SELECT condition_concept_id AS concept_id
    FROM condition_occurrence
    UNION ALL
    SELECT drug_concept_id AS concept_id
    FROM drug_exposure
```

**Correct patterns:**

```sql
SELECT 'Condition' AS domain, condition_concept_id AS concept_id
    FROM condition_occurrence
    UNION ALL
    SELECT 'Drug' AS domain, drug_concept_id AS concept_id
    FROM drug_exposure
```

```sql
from concept table
    SELECT c.domain_id, co.condition_concept_id AS concept_id
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    UNION ALL
    SELECT c.domain_id, de.drug_concept_id AS concept_id
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
```

#### Suggested fix

Add a domain indicator column to each SELECT, e.g.: SELECT 'Condition' AS domain, condition_concept_id FROM ... UNION ...

---

### 22. Unmapped Concept Handling { #data-quality-unmapped-concept-handling }

**Rule ID:** `data_quality.unmapped_concept_handling`
**Severity:** WARNING

#### Intent

OMOP semantic rule: When filtering clinical tables by specific *_concept_id values, warn if concept_id = 0 (unmapped records) is not explicitly handled.

#### How it works

This rule analyzes the SQL query to identify unmapped concept handling patterns.

#### Examples

#### Suggested fix

Add: column > 0 to explicitly exclude unmapped, or handle them separately

---

### 24. Vocabulary Table Protection { #data-quality-vocabulary-table-protection }

**Rule ID:** `data_quality.vocabulary_table_protection`
**Severity:** ERROR

#### Intent

Vocabulary tables contain standardized reference data that all analytical queries
    depend on. Modifying these tables can:
    - Break all downstream queries that reference affected concepts
    - Corrupt the vocabulary structure
    - Require a full vocabulary reload to recover

#### How it works

This rule analyzes the SQL query to identify vocabulary table protection patterns.

#### Examples

#### Common scenarios

- DELETE FROM concept WHERE concept_id = 0
- UPDATE concept SET concept_name = 'xyz' WHERE ...
- INSERT INTO vocabulary VALUES (...)

#### Suggested fix

Do not modify vocabulary tables. Use them as read-only reference data. Vocabulary updates should be performed via official OHDSI releases.

---

## Anti-Pattern Rules

These rules detect common SQL anti-patterns and mistakes when working with OMOP vocabulary tables.

---

### 1. Ambiguous Column Reference { #anti-patterns-ambiguous-column-reference }

**Rule ID:** `anti_patterns.ambiguous_column_reference`
**Severity:** WARNING

#### Intent

Unqualified column references in multi-table queries create ambiguity:

    SELECT person_id, condition_concept_id
    FROM condition_occurrence co
    JOIN person p ON co.person_id = p.person_id
    WHERE person_id = 12345
    -- WRONG: Which person_id? co.person_id or p.person_id?

    Common ambiguous columns in OMOP:
    - person_id: In nearly every clinical table + person table
    - provider_id: In clinical event tables + provider table
    - care_site_id: In clinical events, visit_occurrence, care_site, person
    - visit_occurrence_id: In clinical events + visit_occurrence
    - visit_detail_id: In clinical events + visit_detail

    This causes:
    1. SQL errors: Database rejects query due to ambiguous column
    2. Unpredictable behavior: Database picks wrong table's column
    3. Silent bugs: Query executes but returns wrong data

#### How it works

This rule analyzes the SQL query to identify ambiguous column reference patterns.

#### Examples

**Violation patterns:**

```sql
SELECT person_id, condition_concept_id
    FROM condition_occurrence co
    JOIN person p ON co.person_id = p.person_id
    WHERE person_id = 12345
```

```sql
SELECT provider_id, COUNT(*)
    FROM drug_exposure de
    JOIN provider pr ON de.provider_id = pr.provider_id
    GROUP BY provider_id
```

**Correct patterns:**

```sql
SELECT co.person_id, co.condition_concept_id
    FROM condition_occurrence co
    JOIN person p ON co.person_id = p.person_id
    WHERE co.person_id = 12345
```

```sql
with table alias
```

#### Common scenarios

- Forgetting to add table prefix in WHERE clause
- Copying column names to SELECT without qualification
- Using unqualified columns in GROUP BY / ORDER BY

#### Suggested fix

Qualify the column with a table name or alias (e.g., co.person_id). Always use explicit qualifiers in multi-table queries.

---

### 2. Attribute Definition Invalid Join { #anti-patterns-attribute-definition-invalid-join }

**Rule ID:** `anti_patterns.attribute_definition_invalid_join`
**Severity:** ERROR

#### Intent

attribute_definition is a metadata table with:
    - Single column: attribute_definition_id (primary key)
    - No foreign keys: edges = {} in CDM schema
    - No semantic relationships to clinical or vocabulary data
    - Legacy/optional status: May not be used in modern CDM implementations

    Any JOIN involving attribute_definition is semantically incorrect because there
    are no valid foreign key relationships. The table cannot be meaningfully joined
    to person, condition_occurrence, concept, or any other OMOP table.

#### How it works

This rule analyzes the SQL query to identify attribute definition invalid join patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM attribute_definition ad
    JOIN person p ON ad.attribute_definition_id = p.person_id
```

```sql
with clinical tables
    SELECT * FROM attribute_definition ad, condition_occurrence co
```

**Correct patterns:**

```sql
SELECT * FROM attribute_definition
```

```sql
with filter
    SELECT * FROM attribute_definition
    WHERE attribute_definition_id = 123
```

#### Common scenarios

- Assuming attribute_definition_id links to other tables
- Trying to cross-reference with clinical event IDs
- Attempting to join to vocabulary tables

#### Suggested fix

Query attribute_definition standalone, or remove it from the query. This table has no valid join paths to clinical or vocabulary tables.

---

### 4. Comma-Separated Cross Join { #anti-patterns-comma-separated-cross-join }

**Rule ID:** `anti_patterns.comma_separated_cross_join`
**Severity:** ERROR

#### Intent

Comma-separated FROM clauses without proper join conditions create
    Cartesian products (cross joins):

    SELECT * FROM condition_occurrence, drug_exposure
    WHERE condition_concept_id = 201826
    -- WRONG: No join condition! Creates 10M × 50M = 500 BILLION rows!

    Each clinical table in OMOP has millions of rows:
    - condition_occurrence: ~10M rows
    - drug_exposure: ~50M rows
    - measurement: ~100M rows
    - observation: ~50M rows

    Without a join condition (co.person_id = de.person_id), the query
    creates every possible combination of rows from both tables.

    This causes:
    - Out of memory errors
    - Database crashes
    - Production system locks
    - Hours of wasted compute time

#### How it works

This rule analyzes the SQL query to identify comma-separated cross join patterns.

#### Examples

**Violation patterns:**

```sql
SELECT *
    FROM condition_occurrence, drug_exposure
    WHERE condition_concept_id = 201826
```

```sql
SELECT co.person_id, de.drug_concept_id
    FROM condition_occurrence co, drug_exposure de, measurement m
    WHERE co.condition_concept_id = 201826
      AND de.drug_concept_id = 1545999
```

**Correct patterns:**

```sql
SELECT *
    FROM condition_occurrence co
    JOIN drug_exposure de ON co.person_id = de.person_id
    WHERE co.condition_concept_id = 201826
```

```sql
SELECT *
    FROM condition_occurrence co, drug_exposure de
    WHERE co.person_id = de.person_id
      AND co.condition_concept_id = 201826
```

#### Common scenarios

- Forgot to add WHERE join condition
- Should have used JOIN...ON instead of comma syntax
- Accidentally omitted join predicate in WHERE clause

#### Suggested fix

Use explicit JOIN...ON syntax, or add a WHERE clause with column-to-column equality to join the tables (e.g., WHERE co.person_id = de.person_id).

---

### 5. Concept Ancestor Mixed with Concept Relationship Redundantly { #anti-patterns-concept-ancestor-mixed-with-concept-relationship-redundantly }

**Rule ID:** `anti_patterns.concept_ancestor_mixed_with_concept_relationship_redundantly`
**Severity:** WARNING

#### Intent

The concept_ancestor table is a pre-computed transitive closure table
    that contains ALL hierarchical relationships across all levels:
    - Direct parent-child relationships (1 hop)
    - Grandparent-grandchild relationships (2 hops)
    - All deeper ancestor-descendant relationships (N hops)

    This table is automatically built by traversing the concept_relationship
    table and following all hierarchical relationships where:
    - relationship_id = 'Is a', 'Subsumes', or other relationships
    - relationship.defines_ancestry = 1

    When a query already uses concept_ancestor for hierarchy traversal,
    also joining concept_relationship with hierarchical relationship_id
    filters is redundant because:
    1. concept_ancestor already contains this information
    2. Mixing both tables may cause duplicate rows
    3. It may produce incorrect counts or aggregations
    4. It adds unnecessary complexity and performance overhead

#### How it works

This rule analyzes the SQL query to identify concept ancestor mixed with concept relationship redundantly patterns.

#### Examples

**Violation patterns:**

```sql
SELECT DISTINCT ca.descendant_concept_id
    FROM concept_ancestor ca
    JOIN concept_relationship cr
      ON ca.descendant_concept_id = cr.concept_id_1
    WHERE ca.ancestor_concept_id = 201820
      AND cr.relationship_id = 'Is a'
```

```sql
SELECT c.concept_name
    FROM concept c
    JOIN concept_ancestor ca ON c.concept_id = ca.descendant_concept_id
    JOIN concept_relationship cr ON c.concept_id = cr.concept_id_1
    WHERE ca.ancestor_concept_id = 4329847
      AND cr.relationship_id = 'Subsumes'
```

**Correct patterns:**

```sql
SELECT DISTINCT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
```

```sql
SELECT cr.concept_id_2
    FROM concept_ancestor ca
    JOIN concept c ON ca.descendant_concept_id = c.concept_id
    JOIN concept_relationship cr ON c.concept_id = cr.concept_id_1
    WHERE ca.ancestor_concept_id = 201820
      AND cr.relationship_id = 'RxNorm has dose form'
```

#### Suggested fix

Use concept_ancestor alone for hierarchical traversal. Only use concept_relationship for non-hierarchical relationships.

---

### 7. Concept Code Requires Vocabulary ID { #anti-patterns-concept-code-requires-vocabulary-id }

**Rule ID:** `anti_patterns.concept_code_requires_vocabulary_id`
**Severity:** ERROR

#### Intent

OMOP vocabulary rule: concept_code is unique only within a vocabulary. Any filter on concept_code must also include a vocabulary_id filter in the same scope, otherwise the query may silently match unintended concepts from other vocabularies.

#### How it works

This rule analyzes the SQL query to identify concept code requires vocabulary id patterns.

#### Examples

#### Suggested fix

Add a vocabulary_id filter alongside concept_code

---

### 9. Concept Name Lookup Anti-pattern { #anti-patterns-concept-name-lookup }

**Rule ID:** `anti_patterns.concept_name_lookup`
**Severity:** WARNING

#### Intent

OMOP vocabulary rule: Filtering by concept_name is an anti-pattern because: 1. Concept names are not guaranteed to be unique (multiple concepts can share a name) 2. Concept names can change across vocabulary versions, breaking queries silently 3. Concept names may have variations (spelling, abbreviations, etc.)

#### How it works

This rule analyzes the SQL query to identify concept name lookup anti-pattern patterns.

#### Examples

#### Suggested fix

Use concept_code + vocabulary_id instead: WHERE c.concept_code = '...' AND c.vocabulary_id = '...', or use concept_id directly if known

---


### 11. Concept Relationship Transitive Misuse { #anti-patterns-concept-relationship-transitive-misuse }

**Rule ID:** `anti_patterns.concept_relationship_transitive_misuse`
**Severity:** WARNING

#### Intent

concept_relationship contains direct relationships only:
    - Concept A "Subsumes" Concept B (one hop)
    - Concept B "Subsumes" Concept C (one hop)

    To find all descendants of A (both B and C), users sometimes chain joins:
    cr1 → cr2 → cr3 (each hop requires another join)

    Issues with manual chaining:
    1. Incomplete: Only gets descendants at specific depth (e.g., exactly 3 hops)
    2. Fragile: Misses concepts with multiple inheritance paths
    3. Performance: Multiple self-joins are slow
    4. Complexity: Hard to maintain and understand

    The concept_ancestor table pre-computes ALL transitive hierarchical paths
    and is optimized for hierarchy traversal queries.

#### How it works

This rule analyzes the SQL query to identify concept relationship transitive misuse patterns.

#### Examples

**Violation patterns:**

```sql
SELECT cr3.concept_id_2
    FROM concept_relationship cr1
    JOIN concept_relationship cr2
      ON cr1.concept_id_2 = cr2.concept_id_1
    JOIN concept_relationship cr3
      ON cr2.concept_id_2 = cr3.concept_id_1
    WHERE cr1.concept_id_1 = 201820
      AND cr1.relationship_id = 'Subsumes'
      AND cr2.relationship_id = 'Subsumes'
      AND cr3.relationship_id = 'Subsumes'
```

**Correct patterns:**

```sql
SELECT ca.descendant_concept_id
    FROM concept_ancestor ca
    WHERE ca.ancestor_concept_id = 201820
      AND ca.min_levels_of_separation >= 1
```

```sql
within depth 3
    SELECT ca.descendant_concept_id
    FROM concept_ancestor ca
    WHERE ca.ancestor_concept_id = 201820
      AND ca.min_levels_of_separation BETWEEN 1 AND 3
```

#### Suggested fix

Use concept_ancestor table instead for transitive hierarchy traversal.

---

### 12. Destructive Operations on Clinical Tables { #anti-patterns-destructive-operations-on-clinical-tables }

**Rule ID:** `anti_patterns.destructive_operations_on_clinical_tables`
**Severity:** ERROR

#### Intent

Analysts may accidentally run destructive operations on production data:

    DELETE FROM measurement WHERE measurement_date < '2010-01-01'
    -- Just deleted thousands of historical measurements!

    UPDATE condition_occurrence SET condition_end_date = condition_start_date
    -- Modified production patient data without governance!

    TRUNCATE TABLE drug_exposure
    -- Deleted ALL drug exposure records!

    DROP TABLE visit_occurrence
    -- DISASTER! Lost all visit data!

Protected Tables (patient-level data):
    - condition_occurrence
    - drug_exposure
    - procedure_occurrence
    - measurement
    - observation
    - visit_occurrence
    - visit_detail
    - death
    - person

#### How it works

This rule analyzes the SQL query to identify destructive operations on clinical tables patterns.

#### Examples

**Violation patterns:**

```sql
DELETE FROM measurement WHERE person_id = 12345
    UPDATE condition_occurrence SET condition_concept_id = 201826
    INSERT INTO drug_exposure VALUES (...)
    TRUNCATE TABLE visit_occurrence
    DROP TABLE procedure_occurrence
    ALTER TABLE observation ADD COLUMN custom_field VARCHAR(100)
```

#### Suggested fix

Use SELECT for analysis. Perform data modifications only via controlled ETL pipelines or governed workflows.

---

### 14. Having Without Group By { #anti-patterns-having-without-group-by }

**Rule ID:** `anti_patterns.having_without_group_by`
**Severity:** ERROR

#### Intent

In SQL, HAVING without GROUP BY treats the entire result set as a single group.
    While syntactically valid in some databases (MySQL, PostgreSQL), this pattern
    is almost always a mistake in OMOP queries because:

    - HAVING is meant to filter aggregated groups created by GROUP BY
    - Without GROUP BY, you should use WHERE instead for filtering
    - This indicates the developer forgot to add GROUP BY
    - Results in unexpected behavior where aggregate functions apply to entire table

Why this is wrong:
    The intent is usually to group by some column and filter those groups,
    but the developer forgot the GROUP BY clause. This produces incorrect results
    where the HAVING condition applies to the entire dataset as one group.

#### How it works

This rule analyzes the SQL query to identify having without group by patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM condition_occurrence
    HAVING COUNT(*) > 5
```

```sql
without GROUP BY - filters entire table as one group
```

**Correct patterns:**

```sql
SELECT condition_concept_id, COUNT(*)
    FROM condition_occurrence
    GROUP BY condition_concept_id
    HAVING COUNT(*) > 5
```

```sql
with GROUP BY to filter aggregated groups
```

#### Suggested fix

Add GROUP BY clause to aggregate data, or use WHERE clause for non-aggregated filtering.

---

### 15. Join Key Validation { #anti-patterns-join-key-validation }

**Rule ID:** `anti_patterns.join_key_validation`
**Severity:** ERROR

#### Intent

Joining tables on incompatible keys creates meaningless results:

    SELECT * FROM person p
    JOIN concept c ON p.person_id = c.concept_id
    -- WRONG: person_id and concept_id are different entity types!

#### How it works

Validates that JOIN conditions use correct foreign key relationships in OMOP queries

#### Examples

**Violation patterns:**

```sql
SELECT * FROM person p
    JOIN concept c ON p.person_id = c.concept_id
```

```sql
SELECT * FROM condition_occurrence co
    JOIN provider pr ON co.person_id = pr.provider_id
```

**Correct patterns:**

```sql
SELECT * FROM person p
    JOIN condition_occurrence co ON p.person_id = co.person_id
```

```sql
SELECT * FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
```

#### Common scenarios

- Joining person_id to concept_id (person IDs vs concept IDs)
- Joining visit_occurrence_id to concept_id (visit IDs vs concept IDs)
- Joining provider_id to concept_id (provider IDs vs concept IDs)

#### Suggested fix

Ensure JOIN conditions use correct foreign key relationships. For OMOP: person_id ↔ person_id, *_concept_id ↔ concept.concept_id.

---

### 16. Singleton Metadata Joined to Clinical Table { #anti-patterns-singleton-metadata-clinical-join }

**Rule ID:** `anti_patterns.singleton_metadata_clinical_join`
**Severity:** ERROR

#### Intent

The metadata table is a metadata table with no primary key and no foreign key
    relationships to clinical data. It stores CDM instance metadata such as:
    - ETL provenance information
    - Data characterization results
    - CDM instance-level metrics

    It has no relationship to patient-level clinical data.

    Joining metadata to clinical tables (person, condition_occurrence, etc.)
    is semantically incorrect and indicates confusion about the table's purpose.

Why this is wrong:
    - metadata has no person_id or any FK to clinical tables
    - metadata_id does NOT link to clinical event IDs
    - The table stores instance-level metadata, not patient data
    - Joining creates meaningless results

#### How it works

This rule analyzes the SQL query to identify metadata clinical join patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM metadata m
    JOIN person p ON m.metadata_id = p.person_id
```

```sql
SELECT * FROM metadata m
    JOIN condition_occurrence co ON m.metadata_concept_id = co.condition_concept_id
```

**Correct patterns:**

```sql
SELECT name, value_as_string
    FROM metadata
    WHERE metadata_concept_id = 0
```

```sql
SELECT *
    FROM metadata
    WHERE name = 'CDM Version'
```

#### Suggested fix

Query the metadata table standalone to retrieve CDM instance information. Do not join it to clinical tables like person, condition_occurrence, or drug_exposure.

---

### 17. No DISTINCT on Primary Key Column { #anti-patterns-no-distinct-on-primary-key-column }

**Rule ID:** `anti_patterns.no_distinct_on_primary_key_column`
**Severity:** WARNING

#### Intent

Primary key columns in OMOP CDM tables are unique by definition:
    - condition_occurrence_id, drug_exposure_id, procedure_occurrence_id, etc.

    Using DISTINCT on these columns is either:
    1. Redundant: If querying a single table without joins
    2. Hiding a join problem: If joins introduce duplicates (Cartesian product)

    The presence of DISTINCT on a primary key suggests:
    - Misunderstanding of data uniqueness
    - Missing or incorrect join conditions
    - Unnecessary performance overhead

#### How it works

This rule analyzes the SQL query to identify no distinct on primary key column patterns.

#### Examples

**Violation patterns:**

```sql
SELECT DISTINCT condition_occurrence_id
    FROM condition_occurrence
    WHERE condition_concept_id = 201826;
```

```sql
SELECT DISTINCT de.drug_exposure_id
    FROM drug_exposure de, person p;  -- Missing join condition
```

**Correct patterns:**

```sql
SELECT condition_occurrence_id
    FROM condition_occurrence
    WHERE condition_concept_id = 201826;
```

```sql
SELECT DISTINCT person_id
    FROM condition_occurrence
    WHERE condition_concept_id = 201826;
```

#### Suggested fix

Remove DISTINCT when selecting only primary key columns. If joins are present, review join conditions for unintended duplicates.

---

### 18. No String Identification { #anti-patterns-no-string-identification }

**Rule ID:** `anti_patterns.no_string_identification`
**Severity:** ERROR

#### Intent

OMOP vocabulary rule: Do NOT identify clinical concepts using string matching on *_source_value columns. Use *_concept_id instead.

#### How it works

This rule analyzes the SQL query to identify no string identification patterns.

#### Examples

#### Suggested fix

Use *_concept_id or *_source_concept_id instead of string matching

---

### 19. Standard Concept OR with Classification { #anti-patterns-standard-concept-or-with-classification }

**Rule ID:** `anti_patterns.standard_concept_or_with_classification`
**Severity:** WARNING

#### Intent

The standard_concept column has three values:
    - 'S': Standard concept (for use in clinical data)
    - 'C': Classification concept (hierarchical grouping only)
    - NULL: Non-standard concept (deprecated, source-specific)

    Classification concepts ('C') are high-level groupings and should NOT be used
    in clinical table *_concept_id fields. They're meant for vocabulary hierarchy
    and navigation, not patient data.

    Using OR logic to include both 'S' and 'C' dilutes data quality by mixing
    clinical concepts with non-clinical hierarchy concepts.

Common mistake scenarios:
    1. Concept set building with overly permissive filters
       (standard_concept = 'S' OR standard_concept = 'C')

    2. Using IN clause with both values
       standard_concept IN ('S', 'C')

    3. Misunderstanding that 'C' concepts are not for clinical use

#### How it works

This rule analyzes the SQL query to identify standard concept or with classification patterns.

#### Examples

**Violation patterns:**

```sql
SELECT concept_id, concept_name
    FROM concept
    WHERE concept_name LIKE '%diabetes%'
      AND (standard_concept = 'S' OR standard_concept = 'C')
```

#### Suggested fix

Use standard_concept = 'S' for clinical queries. Use 'C' only for vocabulary hierarchy analysis.

---

### 20. Type Concept ID Domain Filter { #anti-patterns-type-concept-id-domain-filter }

**Rule ID:** `anti_patterns.type_concept_id_domain_filter`
**Severity:** WARNING

#### Intent

Type concept columns (*_type_concept_id) reference concepts that describe the
    provenance or type of a clinical record:
    - condition_type_concept_id: EHR record, Insurance claim, etc.
    - drug_type_concept_id: Prescription written, Dispensed in pharmacy, etc.

    These type concepts have domain_id = 'Type Concept', not clinical domains like
    'Condition' or 'Drug'. When queries join type_concept_id to concept and filter
    by clinical domains, they return zero results.

#### How it works

This rule analyzes the SQL query to identify type concept id domain filter patterns.

#### Examples

**Violation patterns:**

```sql
SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c ON co.condition_type_concept_id = c.concept_id
    WHERE c.domain_id = 'Condition'
```

```sql
with Drug domain
    SELECT c.concept_name
    FROM drug_exposure de
    JOIN concept c ON de.drug_type_concept_id = c.concept_id
    WHERE c.domain_id = 'Drug'
```

**Correct patterns:**

```sql
SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c ON co.condition_type_concept_id = c.concept_id
    WHERE c.domain_id = 'Type Concept'
```

```sql
SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c ON co.condition_type_concept_id = c.concept_id
```

#### Suggested fix

Use domain_id = 'Type Concept' or remove the domain_id filter.

---

### 21. Type Concept ID Not For Clinical Filtering { #anti-patterns-type-concept-id-misuse }

**Rule ID:** `anti_patterns.type_concept_id_misuse`
**Severity:** ERROR

#### Intent

OMOP semantic rule (OMOP_014): The *_type_concept_id columns (e.g., condition_type_concept_id, drug_type_concept_id) represent the provenance of the record (e.g., EHR, claim, patient-reported), not clinical categories. Do not use them to filter for clinical subtypes.

#### How it works

This rule analyzes the SQL query to identify type concept id not for clinical filtering patterns.

#### Examples

#### Suggested fix

Use the primary concept_id column (e.g., condition_concept_id) for clinical filtering. type_concept_id should only be used to understand data source/provenance.

---

## Domain-Specific Rules

These rules validate domain-specific constraints for individual OMOP CDM tables and clinical domains.

---

### COHORT Domain

### 1. Cohort Definition Syntax Not Executable SQL { #domain-specific-cohort-definition-syntax-not-executable-sql }

**Rule ID:** `domain_specific.cohort_definition_syntax_not_executable_sql`
**Severity:** ERROR

#### Intent

cohort_definition.cohort_definition_syntax is a VARCHAR column that stores
    cohort logic metadata, typically in JSON or OHDSI cohort expression format.

    It is NOT executable SQL code.

#### How it works

This rule analyzes the SQL query to identify cohort definition syntax not executable sql patterns.

#### Examples

**Violation patterns:**

```sql
SELECT cohort_definition_syntax
    FROM cohort_definition
    WHERE cohort_definition_syntax LIKE '%SELECT%condition_occurrence%'
```

```sql
SELECT *
    FROM cohort_definition
    WHERE cohort_definition_syntax LIKE '%drug_exposure%'
```

**Correct patterns:**

```sql
SELECT cohort_definition_id, cohort_definition_name
    FROM cohort_definition
    WHERE cohort_definition_name LIKE '%diabetes%'
```

```sql
SELECT cohort_definition_syntax
    FROM cohort_definition
    WHERE cohort_definition_id = 123
```

#### Common scenarios

- Filtering with SQL keywords to identify cohort logic:
- Filtering with OMOP table names:
- Attempting to execute it as dynamic SQL (less common in static queries)

#### Suggested fix

Use cohort_definition_name or cohort_definition_id for filtering. cohort_definition_syntax should only be retrieved, not filtered with string patterns.

---

### CONDITION Domain

### 1. Condition Occurrence Cardinality Risk { #domain-specific-condition-occurrence-cardinality-validation }

**Rule ID:** `domain_specific.condition_occurrence_cardinality_validation`
**Severity:** WARNING

#### Intent

Joining person to condition_occurrence without aggregation can produce
    multiple rows per person, leading to incorrect counts or analysis. For example:

    - Patient A has 3 condition_occurrence records for diabetes (across 3 visits)
    - Query joins person to condition_occurrence: SELECT p.person_id, co.condition_start_date
    - Result: 3 rows for Patient A instead of 1
    - Counting rows gives "3 patients" when only 1 patient exists

Detection heuristics:
    - Query joins person to condition_occurrence on person_id
    - No GROUP BY clause present
    - No DISTINCT in SELECT
    - No aggregation functions (COUNT, MIN, MAX, etc.)

#### How it works

This rule analyzes the SQL query to identify condition occurrence cardinality risk patterns.

#### Examples

**Violation patterns:**

```sql
SELECT p.person_id, co.condition_start_date
    FROM person p
    JOIN condition_occurrence co ON p.person_id = co.person_id
    WHERE co.condition_concept_id = 201826
```

```sql
without aggregation
```

**Correct patterns:**

```sql
SELECT co.person_id, MIN(co.condition_start_date) AS first_diagnosis
    FROM condition_occurrence co
    WHERE co.condition_concept_id = 201826
    GROUP BY co.person_id
```

```sql
SELECT DISTINCT p.person_id
    FROM person p
    JOIN condition_occurrence co ON p.person_id = co.person_id
```

#### Suggested fix

Use GROUP BY person_id, DISTINCT, or condition_era to avoid duplicate rows per person.

---

### 2. Condition Occurrence Visit Hierarchy Validation { #domain-specific-condition-visit-hierarchy-validation }

**Rule ID:** `domain_specific.condition_visit_hierarchy_validation`
**Severity:** ERROR

#### Intent

visit_detail records are nested within visit_occurrence records. A condition can
    be linked to a visit_detail, and that visit_detail belongs to a visit_occurrence.

    If a query joins condition_occurrence to visit_detail but then tries to access
    visit_occurrence columns without properly joining to visit_occurrence, it will
    either fail (if the table isn't in FROM/JOIN) or produce incorrect results.

Example violation:
    -- BAD: References vo.visit_start_date without joining visit_occurrence
    SELECT co.*, vo.visit_start_date
    FROM condition_occurrence co
    JOIN visit_detail vd ON co.visit_detail_id = vd.visit_detail_id
    -- ERROR: vo referenced but not joined

Correct pattern:
    -- GOOD: Properly joins through visit_occurrence
    SELECT co.*, vo.visit_start_date
    FROM condition_occurrence co
    JOIN visit_detail vd ON co.visit_detail_id = vd.visit_detail_id
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id

#### How it works

This rule analyzes the SQL query to identify condition occurrence visit hierarchy validation patterns.

#### Examples

**Correct patterns:**

```sql
SELECT co.*, vo.visit_start_date
    FROM condition_occurrence co
    JOIN visit_detail vd ON co.visit_detail_id = vd.visit_detail_id
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
```

#### Suggested fix

Add: JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id

---

### COST Domain

### 1. Cost Payer Plan Period ID Join { #domain-specific-cost-payer-plan-period-id-join }

**Rule ID:** `domain_specific.cost_payer_plan_period_id_join`
**Severity:** ERROR

#### Intent

The cost table has a payer_plan_period_id column (INTEGER FK) that references
    payer_plan_period.payer_plan_period_id (INTEGER PK). This is the correct join key.

#### How it works

This rule analyzes the SQL query to identify cost payer plan period id join patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM cost c
    JOIN payer_plan_period pp ON c.cost_event_id = pp.payer_plan_period_id
```

```sql
SELECT * FROM cost c
    JOIN payer_plan_period pp ON c.person_id = pp.person_id
```

**Correct patterns:**

```sql
SELECT * FROM cost c
    JOIN payer_plan_period pp ON c.payer_plan_period_id = pp.payer_plan_period_id
```

```sql
SELECT * FROM cost c
    JOIN payer_plan_period pp
      ON c.payer_plan_period_id = pp.payer_plan_period_id
      AND c.person_id = pp.person_id
```

#### Common scenarios

- Joining cost.person_id = payer_plan_period.person_id
- Both tables have person_id, but this will match ALL payer periods for that person
- A person can have multiple insurance periods over time

#### Suggested fix

Use: cost.payer_plan_period_id = payer_plan_period.payer_plan_period_id

---

### DEATH Domain

### 1. Death Cause Source Concept Not For Analytical Filtering { #domain-specific-death-cause-source-concept-validation }

**Rule ID:** `domain_specific.death_cause_source_concept_validation`
**Severity:** ERROR

#### Intent

Using death_cause_source_concept_id in WHERE clauses or JOINs is incorrect for
    analytical work because:
    - It represents source/local vocabulary codes, not standardized OMOP concepts
    - Analytical queries should use standardized concepts for reproducibility
    - Source concepts are intended for ETL validation, mapping verification, or provenance tracking

#### How it works

OMOP semantic rule CLIN_052:
Validates that death.cause_source_concept_id is not used for analytical filtering

#### Examples

**Violation patterns:**

```sql
SELECT * FROM death WHERE cause_source_concept_id = 123
```

```sql
SELECT d.* FROM death d
    WHERE d.cause_source_concept_id IN (456, 789)
```

**Correct patterns:**

```sql
SELECT * FROM death WHERE cause_concept_id = 123
    SELECT d.* FROM death d
    WHERE d.cause_concept_id IN (456, 789)
```

#### Suggested fix

Replace with death.cause_concept_id

---

### 2. Death Join to Person Not to Clinical Event { #domain-specific-death-join-to-person-not-to-clinical-event }

**Rule ID:** `domain_specific.death_join_to_person_not_to_clinical_event`
**Severity:** ERROR

#### Intent

The death table has a minimal schema:
    - person_id (FK to person)
    - death_date
    - death_datetime
    - death_type_concept_id
    - death_cause_concept_id
    - death_cause_source_value
    - death_cause_source_concept_id

    It does NOT have foreign keys to clinical event tables like:
    - visit_occurrence_id
    - condition_occurrence_id
    - drug_exposure_id
    - procedure_occurrence_id

    The only valid join from death to other tables is via person_id.

Why this is wrong:
    Developers sometimes mistakenly try to join death directly to clinical event
    tables using incorrect column mappings, such as:
    - death.person_id = visit_occurrence.visit_occurrence_id (wrong types)
    - death.death_type_concept_id = condition_occurrence.condition_concept_id (semantically wrong)

    This produces incorrect results or errors.

#### How it works

This rule analyzes the SQL query to identify death join to person not to clinical event patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM death d
    JOIN visit_occurrence vo ON d.person_id = vo.visit_occurrence_id
```

```sql
SELECT * FROM death d
    JOIN condition_occurrence co ON d.death_cause_concept_id = co.condition_occurrence_id
```

**Correct patterns:**

```sql
SELECT * FROM death d
    JOIN visit_occurrence vo ON d.person_id = vo.person_id
```

```sql
SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
    JOIN condition_occurrence co ON p.person_id = co.person_id
```

#### Suggested fix

Ensure JOIN includes: death.person_id = <clinical_table>.person_id

---

### DRUG Domain

### 1. Drug Days Supply Validation { #domain-specific-drug-days-supply-validation }

**Rule ID:** `domain_specific.drug_days_supply_validation`
**Severity:** WARNING

#### Intent

days_supply in the drug_exposure table should contain plausible values.
    Values <= 0 or > 365 indicate data quality issues or query logic errors.

#### How it works

This rule analyzes the SQL query to identify drug days supply validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM drug_exposure WHERE days_supply = -30
```

```sql
SELECT * FROM drug_exposure WHERE days_supply = 0
```

**Correct patterns:**

```sql
SELECT * FROM drug_exposure WHERE days_supply = 30
    SELECT * FROM drug_exposure WHERE days_supply BETWEEN 1 AND 90
    SELECT * FROM drug_exposure WHERE days_supply IN (7, 14, 30, 90)
```

#### Common scenarios

- days_supply = -30 (negative value)
- days_supply = 0 (zero value)
- days_supply = 500 (unrealistically long supply)

#### Suggested fix

None

---

### 2. Drug Era Concept Class Validation { #domain-specific-drug-era-concept-class-validation }

**Rule ID:** `domain_specific.drug_era_concept_class_validation`
**Severity:** ERROR

#### Intent

drug_era is a derived table that aggregates drug exposures at the Ingredient level.
    It only contains concepts where concept_class_id = 'Ingredient'.

    Filtering for other RxNorm concept classes will return no data:
    - 'Clinical Drug Form' (e.g., "Acetaminophen 500 MG Oral Tablet")
    - 'Clinical Drug' (e.g., "Acetaminophen 500 MG")
    - 'Branded Drug' (e.g., "Tylenol 500 MG")

#### How it works

This rule analyzes the SQL query to identify drug era concept class validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT de.*
    FROM drug_era de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Clinical Drug'
```

**Correct patterns:**

```sql
SELECT de.*
    FROM drug_era de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Ingredient'
```

#### Suggested fix

Use concept_class_id = 'Ingredient' or remove the filter.

---

### 3. Drug Exposure Cardinality Awareness { #domain-specific-drug-exposure-cardinality-validation }

**Rule ID:** `domain_specific.drug_exposure_cardinality_validation`
**Severity:** WARNING

#### Intent

Counting rows in drug_exposure without awareness of multiple records per person
    per drug can produce misleading statistics. For example:

    - Patient A has 3 drug_exposure records for metformin (3 refills)
    - Query: SELECT drug_concept_id, COUNT(*) FROM drug_exposure GROUP BY drug_concept_id
    - Result: exposure_count = 3 for metformin
    - Misleading: This counts prescription fills, not unique patients

    Common mistake: Using COUNT(*) when you want to count unique patients.

Detection patterns:
    - Query uses COUNT(*) or COUNT(column) on drug_exposure table
    - COUNT does NOT use DISTINCT person_id
    - Suggests using COUNT(DISTINCT person_id) or drug_era table

#### How it works

This rule analyzes the SQL query to identify drug exposure cardinality awareness patterns.

#### Examples

**Violation patterns:**

```sql
SELECT drug_concept_id, COUNT(*) AS exposure_count
    FROM drug_exposure
    GROUP BY drug_concept_id
```

**Correct patterns:**

```sql
SELECT drug_concept_id, COUNT(DISTINCT person_id) AS patient_count
    FROM drug_exposure
    WHERE drug_concept_id != 0
    GROUP BY drug_concept_id
```

```sql
SELECT drug_concept_id, COUNT(*) AS era_count
    FROM drug_era
    GROUP BY drug_concept_id
```

#### Suggested fix

Use COUNT(DISTINCT person_id) for patient counts or use drug_era for consolidated exposure periods.

---

### 4. Drug Exposure Quantity Misuse { #domain-specific-drug-exposure-quantity-misuse }

**Rule ID:** `domain_specific.drug_exposure_quantity_misuse`
**Severity:** WARNING

#### Intent

Quantity is the NUMBER of units dispensed (pills, ml, etc.), not days.
    Using it in date arithmetic leads to incorrect duration calculations.

#### How it works

This rule analyzes the SQL query to identify drug exposure quantity misuse patterns.

#### Examples

**Violation patterns:**

```sql
SELECT DATEADD(day, quantity, drug_exposure_start_date) AS end_date
    FROM drug_exposure
```

**Correct patterns:**

```sql
SELECT DATEADD(day, days_supply, drug_exposure_start_date) AS end_date
    FROM drug_exposure
```

#### Common scenarios

- DATEADD(day, quantity, start_date) -- 30 tablets ≠ 30 days
- DATEDIFF(day, quantity, end_date)
- start_date + INTERVAL quantity DAY

#### Suggested fix

Use days_supply or date differences instead of quantity.

---

### 5. Drug Exposure Sig Parsing { #domain-specific-drug-exposure-sig-parsing }

**Rule ID:** `domain_specific.drug_exposure_sig_parsing`
**Severity:** WARNING

#### Intent

The sig field contains unstructured free-text that varies widely across sites:
    - "1 tab bid"
    - "take one tablet twice daily"
    - "Take 1-2 tablets by mouth every 4-6 hours as needed"

    Parsing this for structured dosing data is unreliable and error-prone.
    The drug_strength table provides standardized, structured dosing information.

#### How it works

This rule analyzes the SQL query to identify drug exposure sig parsing patterns.

#### Examples

**Violation patterns:**

```sql
SELECT CAST(SUBSTRING(sig, 1, CHARINDEX(' ', sig)) AS INT) AS dose
    FROM drug_exposure
```

**Correct patterns:**

```sql
SELECT ds.amount_value, ds.amount_unit_concept_id
    FROM drug_exposure de
    JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id
    WHERE ds.invalid_reason IS NULL
```

#### Common scenarios

- SUBSTRING(sig, 1, CHARINDEX(' ', sig)) to extract dose
- REGEXP_SUBSTR(sig, '[0-9]+') to extract numbers
- PATINDEX('%[0-9]%', sig) to find numeric positions

#### Suggested fix

Join drug_strength to obtain standardized dose fields (amount_value, numerator_value, denominator_value) instead of parsing sig.

---

### 6. Drug Quantity Validation { #domain-specific-drug-quantity-validation }

**Rule ID:** `domain_specific.drug_quantity_validation`
**Severity:** WARNING

#### Intent

quantity in the drug_exposure table represents the amount dispensed (e.g., 30 tablets).
    Negative values are never valid and indicate data quality issues or query logic errors.

#### How it works

This rule analyzes the SQL query to identify drug quantity validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM drug_exposure WHERE quantity = -10
```

```sql
SELECT * FROM drug_exposure WHERE quantity < 0
```

**Correct patterns:**

```sql
SELECT * FROM drug_exposure WHERE quantity = 30
    SELECT * FROM drug_exposure WHERE quantity > 0
    SELECT * FROM drug_exposure WHERE quantity BETWEEN 1 AND 100
```

#### Common scenarios

- quantity = -10 (negative value)
- quantity < 0 (filtering for negatives)
- Using negative as sentinel/null value

#### Suggested fix

Ensure quantity >= 0

---

### 7. Drug Strength Completeness (Amount vs Concentration) { #domain-specific-drug-strength-numerator-denominator-for-concentration }

**Rule ID:** `domain_specific.drug_strength_numerator_denominator_for_concentration`
**Severity:** WARNING

#### Intent

The drug_strength table has two different models for representing strength:

    1. Simple amount model (solid formulations):
       - amount_value + amount_unit_concept_id
       - Example: 500mg tablet → amount_value = 500, amount_unit_concept_id = mg

    2. Concentration model (liquid/injectable formulations):
       - numerator_value + numerator_unit_concept_id (active ingredient)
       - denominator_value + denominator_unit_concept_id (solution volume)
       - Example: 500mg/5mL injection → numerator_value = 500, denominator_value = 5
       - **amount_value is NULL** for these drugs

    Critical issue: Queries that only check amount_value completely miss
    liquid/injectable formulations, which use the numerator/denominator model.

#### How it works

This rule analyzes the SQL query to identify drug strength completeness (amount vs concentration) patterns.

#### Examples

**Violation patterns:**

```sql
SELECT ds.amount_value, ds.amount_unit_concept_id
    FROM drug_strength ds
    WHERE ds.drug_concept_id = 19078461
```

**Correct patterns:**

```sql
SELECT
      COALESCE(ds.amount_value, ds.numerator_value) AS dose_value,
      COALESCE(ds.amount_unit_concept_id, ds.numerator_unit_concept_id) AS dose_unit,
      ds.denominator_value,
      ds.denominator_unit_concept_id
    FROM drug_strength ds
    WHERE ds.drug_concept_id = 19078461
```

#### Common scenarios

- SELECT amount_value without numerator_value
- WHERE amount_value > X (excludes all concentration drugs)
- WHERE amount_value IS NOT NULL (excludes all concentration drugs)

#### Suggested fix

Use COALESCE(amount_value, numerator_value) to include both formulations. Include denominator_value for concentration context when relevant.

---

### 8. Drug Strength Validity Filter { #domain-specific-drug-strength-validity-filter }

**Rule ID:** `domain_specific.drug_strength_validity_filter`
**Severity:** WARNING

#### Intent

drug_strength is a vocabulary table with temporal validity:
    - Same drug_concept_id may have multiple strength records over time
    - Formulations change (new strengths, discontinued strengths)
    - invalid_reason IS NULL indicates currently valid records

    Querying without validity filters returns BOTH current AND historical strengths
    → incorrect calculations, duplicate results

Example impact:
    -- Returns 3 rows: old formulation (100mg), current (150mg), deprecated (200mg)
    SELECT drug_concept_id, amount_value, amount_unit_concept_id
    FROM drug_strength
    WHERE drug_concept_id = 19078461
    -- Which strength is correct? Calculation uses wrong value!

#### How it works

This rule analyzes the SQL query to identify drug strength validity filter patterns.

#### Examples

**Violation patterns:**

```sql
SELECT amount_value
    FROM drug_strength
    WHERE drug_concept_id = 123
```

#### Suggested fix

Add 'invalid_reason IS NULL' OR 'CURRENT_DATE BETWEEN valid_start_date AND valid_end_date'.

---

### EPISODE Domain

### 1. Episode Parent ID Self Join { #domain-specific-episode-parent-id-self-join }

**Rule ID:** `domain_specific.episode_parent_id_self_join`
**Severity:** ERROR

#### Intent

The episode table supports hierarchical nesting where an episode can have a
    parent episode. For example:
    - Parent episode: "Hospital admission" (episode_id = 100)
    - Child episode: "ICU stay" (episode_id = 101, episode_parent_id = 100)
    - Child episode: "Surgery during admission" (episode_id = 102, episode_parent_id = 100)

    The episode_parent_id column (INTEGER) is a self-referential foreign key that
    should ONLY join to episode.episode_id in the same table.

#### How it works

This rule analyzes the SQL query to identify episode parent id self join patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM episode e
    JOIN condition_occurrence co ON e.episode_parent_id = co.condition_occurrence_id
```

```sql
SELECT * FROM episode e
    JOIN visit_occurrence vo ON e.episode_parent_id = vo.visit_occurrence_id
```

**Correct patterns:**

```sql
SELECT child.*, parent.episode_concept_id AS parent_type
    FROM episode child
    JOIN episode parent ON child.episode_parent_id = parent.episode_id
```

```sql
SELECT e1.*, e2.*, e3.*
    FROM episode e1
    LEFT JOIN episode e2 ON e1.episode_parent_id = e2.episode_id
    LEFT JOIN episode e3 ON e2.episode_parent_id = e3.episode_id
```

#### Common scenarios

- Joining episode_parent_id to other clinical tables
- episode.episode_parent_id = condition_occurrence.condition_occurrence_id
- episode.episode_parent_id = visit_occurrence.visit_occurrence_id

#### Suggested fix

Use: FROM episode child JOIN episode parent ON child.episode_parent_id = parent.episode_id

---

### LOCATION Domain

### 1. Location History Entity ID Requires Domain ID { #domain-specific-location-history-entity-id-requires-domain-id }

**Rule ID:** `domain_specific.location_history_entity_id_requires_domain_id`
**Severity:** ERROR

#### Intent

The location_history table tracks location changes for different entity types
    using a polymorphic foreign key pattern:

    - entity_id: Polymorphic FK to person_id, provider_id, or care_site_id
    - domain_id: Discriminator identifying which table entity_id refers to
      - 'Person' → entity_id refers to person.person_id
      - 'Provider' → entity_id refers to provider.provider_id
      - 'Care Site' → entity_id refers to care_site.care_site_id

    Without filtering on domain_id, joins on entity_id are ambiguous because:
    - Integer IDs can collide across tables (person_id=123, provider_id=123)
    - Query may incorrectly match entities from wrong domain
    - Results will include mixed entity types

#### How it works

This rule analyzes the SQL query to identify location history entity id requires domain id patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM location_history lh
    JOIN person p ON lh.entity_id = p.person_id
```

```sql
SELECT * FROM location_history lh
    JOIN provider pr ON lh.entity_id = pr.provider_id
```

**Correct patterns:**

```sql
SELECT * FROM location_history lh
    JOIN person p ON lh.entity_id = p.person_id
    WHERE lh.domain_id = 'Person'
```

```sql
SELECT * FROM location_history lh
    JOIN person p ON lh.entity_id = p.person_id
      AND lh.domain_id = 'Person'
```

#### Common scenarios

- Joining location_history.entity_id to person.person_id without domain_id filter
- Joining location_history.entity_id to provider.provider_id without domain_id filter
- Joining location_history.entity_id to care_site.care_site_id without domain_id filter

#### Suggested fix

Add WHERE location_history.domain_id = '<Domain>' matching the joined table.

---

### MEASUREMENT Domain

### 1. Measurement Cross-Unit Comparison { #domain-specific-measurement-cross-unit-comparison }

**Rule ID:** `domain_specific.measurement_cross_unit_comparison`
**Severity:** WARNING

#### Intent

The measurement table can store the same clinical concept (e.g., blood glucose,
    HbA1c) in different units across sites or time periods:

    Blood glucose: 5.5 (mmol/L) vs 100 (mg/dL)
    HbA1c: 7.0 (%) vs 53 (mmol/mol)

    Performing aggregations (AVG, SUM, MIN, MAX) or arithmetic operations across
    measurements with different units produces meaningless results:

    AVG(5.5 mmol/L, 100 mg/dL) = 52.75 (meaningless!)

#### How it works

This rule analyzes the SQL query to identify measurement cross-unit comparison patterns.

#### Examples

**Violation patterns:**

```sql
without constraining unit
    SELECT AVG(value_as_number) AS avg_glucose
    FROM measurement
    WHERE measurement_concept_id = 3004410
```

```sql
SELECT person_id, AVG(value_as_number)
    FROM measurement
    WHERE measurement_concept_id = 3004410
      AND unit_concept_id IN (8753, 8840)  -- mmol/L and mg/dL
    GROUP BY person_id
```

**Correct patterns:**

```sql
SELECT AVG(value_as_number) AS avg_glucose_mmol
    FROM measurement
    WHERE measurement_concept_id = 3004410
      AND unit_concept_id = 8753  -- mmol/L only
```

```sql
SELECT
      unit_concept_id,
      AVG(value_as_number) AS avg_value
    FROM measurement
    WHERE measurement_concept_id = 3004410
    GROUP BY unit_concept_id
```

#### Suggested fix

Add unit_concept_id constraint (e.g., = <unit>) or group by unit. Alternatively, convert values to a common unit before aggregation.

---

### 2. Measurement Duplicate Detection { #domain-specific-measurement-duplicate-detection }

**Rule ID:** `domain_specific.measurement_duplicate_detection`
**Severity:** WARNING

#### Intent

The measurement table can contain duplicate records with the same:
    - person_id
    - measurement_concept_id
    - measurement_date

    These duplicates can occur from:
    - ETL processing errors (same source record loaded multiple times)
    - Data quality issues (duplicate submissions from source systems)
    - Integration artifacts (same measurement from different data feeds)
    - Multiple precision levels (e.g., 5.5 and 5.52 for same measurement)

    Unlike drug_exposure (where refills are expected) or condition_occurrence
    (where recurring diagnoses are expected), duplicate measurements at the
    same date typically indicate data quality issues rather than clinical reality.

Detection patterns:
    Query performs aggregations or counts on measurement table without:
    - Grouping by natural key (person_id, measurement_concept_id, measurement_date)
    - Using DISTINCT
    - Using deduplication logic (ROW_NUMBER, etc.)

Violation example:
    -- BAD: Counts may include duplicates
    SELECT person_id, AVG(value_as_number) AS avg_glucose
    FROM measurement
    WHERE measurement_concept_id = 3004410
    GROUP BY person_id
    -- If person has 2 identical measurements on same date, average is skewed

Correct patterns:
    -- GOOD: Group by natural key to handle duplicates
    SELECT person_id, measurement_date, measurement_concept_id,
           AVG(value_as_number) AS avg_value
    FROM measurement
    WHERE measurement_concept_id = 3004410
    GROUP BY person_id, measurement_date, measurement_concept_id

    -- GOOD: Use ROW_NUMBER to deduplicate
    WITH ranked AS (
      SELECT *,
        ROW_NUMBER() OVER (
          PARTITION BY person_id, measurement_concept_id, measurement_date
          ORDER BY measurement_datetime NULLS LAST
        ) AS rn
      FROM measurement
    )
    SELECT * FROM ranked WHERE rn = 1

    -- GOOD: Use DISTINCT
    SELECT DISTINCT person_id, measurement_concept_id, measurement_date
    FROM measurement

#### How it works

This rule analyzes the SQL query to identify measurement duplicate detection patterns.

#### Examples

**Correct patterns:**

```sql
SELECT person_id, measurement_date, measurement_concept_id,
           AVG(value_as_number) AS avg_value
    FROM measurement
    WHERE measurement_concept_id = 3004410
    GROUP BY person_id, measurement_date, measurement_concept_id
```

```sql
WITH ranked AS (
      SELECT *,
        ROW_NUMBER() OVER (
          PARTITION BY person_id, measurement_concept_id, measurement_date
          ORDER BY measurement_datetime NULLS LAST
        ) AS rn
      FROM measurement
    )
    SELECT * FROM ranked WHERE rn = 1
```

#### Suggested fix

Group by natural key (person_id, measurement_concept_id, measurement_date), use DISTINCT, or apply explicit deduplication logic like ROW_NUMBER() OVER (PARTITION BY person_id, measurement_concept_id, measurement_date).

---

### 3. Measurement Operator Concept Validation { #domain-specific-measurement-operator-concept-validation }

**Rule ID:** `domain_specific.measurement_operator_concept_validation`
**Severity:** ERROR

#### Intent

operator_concept_id indicates the comparison operator for value_as_number.
    Only 5 specific concept_ids are valid operators in OMOP CDM.
    Using any other concept_id is incorrect and will cause data integrity issues.

#### How it works

This rule analyzes the SQL query to identify measurement operator concept validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM measurement WHERE operator_concept_id = 201826
```

```sql
SELECT * FROM measurement WHERE operator_concept_id = 999999
```

**Correct patterns:**

```sql
SELECT * FROM measurement WHERE operator_concept_id = 4172704  -- >
    SELECT * FROM measurement WHERE operator_concept_id = 4171756  -- <
    SELECT * FROM measurement WHERE operator_concept_id = 4171755  -- =
    SELECT * FROM measurement WHERE operator_concept_id IN (4171754, 4172703)  -- <=, >=
```

#### Common scenarios

- Using random concept_ids as operators
- Using measurement concept_ids instead of operator concept_ids
- Hardcoding invalid operator values

#### Suggested fix

Use one of the valid operator concept_ids

---

### 4. Measurement Range Low/High Validation { #domain-specific-measurement-range-low-high-validation }

**Rule ID:** `domain_specific.measurement_range_low_high_validation`
**Severity:** ERROR

#### Intent

In the measurement table, range_low and range_high represent the normal
    reference range for a measurement. By definition, range_low must be ≤ range_high.
    A query that filters for range_low > range_high is logically impossible
    unless it's a data quality check.

#### How it works

OMOP semantic rule CLIN_027:
Detects logically impossible range constraints where range_low > range_high

#### Examples

**Violation patterns:**

```sql
SELECT * FROM measurement
    WHERE range_low > range_high
      AND value_as_number > range_high
```

```sql
SELECT * FROM measurement
    WHERE range_low >= 150
      AND range_high < 100
```

**Correct patterns:**

```sql
SELECT * FROM measurement
    WHERE range_low > range_high
```

```sql
SELECT * FROM measurement
    WHERE value_as_number < range_low
       OR value_as_number > range_high
```

#### Common scenarios

- Direct comparison: WHERE range_low > range_high (as business logic filter)
- Static contradictions: WHERE range_low > 100 AND range_high < 50
- Swapped column usage in filters

#### Suggested fix

Ensure range_low <= range_high

---

### 5. Measurement Unit Validation { #domain-specific-measurement-unit-validation }

**Rule ID:** `domain_specific.measurement_unit_validation`
**Severity:** WARNING

#### Intent

OMOP semantic rule: When a query filters measurement.value_as_number against a numeric threshold, it must also constrain unit_concept_id.

#### How it works

This rule analyzes the SQL query to identify measurement unit validation patterns.

#### Examples

#### Suggested fix

Add a unit_concept_id constraint alongside the numeric threshold: AND m.unit_concept_id = <unit_concept_id>. Look up the correct UCUM unit concept ID in the OMOP vocabulary (e.g. SELECT concept_id FROM concept WHERE concept_code = '%' AND vocabulary_id = 'UCUM').

---

### 6. Measurement Value Representation Consistency { #domain-specific-measurement-value-as-number-and-concept-validation }

**Rule ID:** `domain_specific.measurement_value_as_number_and_concept_validation`
**Severity:** WARNING

#### Intent

value_as_number stores quantitative results (e.g., 6.5 mg/dL)
    value_as_concept_id stores qualitative results (e.g., "Positive", "Negative")

    Both CAN be populated simultaneously for the same measurement, but they typically
    represent different aspects of the result. Filtering both with AND is usually
    a logic error that will be overly restrictive.

#### How it works

OMOP semantic rule CLIN_028:
Detects when both value_as_number and value_as_concept_id are filtered with AND

#### Examples

**Violation patterns:**

```sql
SELECT * FROM measurement
    WHERE value_as_number > 6.5
      AND value_as_concept_id = 45884084
```

```sql
with AND (overly restrictive)
```

**Correct patterns:**

```sql
SELECT * FROM measurement
    WHERE value_as_number > 6.5
       OR value_as_concept_id = 45884084
```

```sql
SELECT * FROM measurement
    WHERE value_as_number IS NOT NULL
      AND value_as_concept_id IS NOT NULL
```

#### Common scenarios

- Filtering both columns with AND: WHERE value_as_number > 6.5 AND value_as_concept_id = 45884084
- Not understanding that measurements usually have EITHER a numeric OR concept value
- Overly restrictive filters that return no results

#### Suggested fix

Use OR or separate logic depending on measurement type

---

### NOTE Domain

### 1. Note NLP Snippet Misuse { #domain-specific-note-note-nlp-snippet-misuse }

**Rule ID:** `domain_specific.note.note_nlp_snippet_misuse`
**Severity:** WARNING

#### Intent

note_nlp columns and their purposes:
    - snippet: Short text excerpt around the NLP-extracted term (for context)
    - lexical_variant: The exact text string found in the note
    - note_nlp_concept_id: Standardized OMOP concept_id for the extracted entity

#### How it works

This rule analyzes the SQL query to identify note nlp snippet misuse patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM note_nlp WHERE snippet = 'diabetes'
```

```sql
SELECT * FROM note_nlp WHERE lexical_variant = 'DM'
```

**Correct patterns:**

```sql
SELECT * FROM note_nlp WHERE note_nlp_concept_id = 201826
```

```sql
SELECT snippet, note_nlp_concept_id FROM note_nlp
    WHERE note_nlp_concept_id IN (201826, 443238)
```

#### Common scenarios

- Text matching on snippet/lexical_variant instead of using concept_id
- Joining to concept table on text columns instead of concept_id
- Filtering by exact text matches that miss lexical variations

#### Suggested fix

Use note_nlp_concept_id instead of text matching.

---

### OBSERVATION Domain

### 1. Observation Value As Columns Mutually Contextual { #domain-specific-observation-value-as-columns-mutually-contextual }

**Rule ID:** `domain_specific.observation_value_as_columns_mutually_contextual`
**Severity:** WARNING

#### Intent

The observation table stores observation results in multiple format columns:
    - value_as_number: for numeric results (e.g., 98.6, 120, 7.2)
    - value_as_string: for text results (e.g., "Positive", "Negative", "High")
    - value_as_concept_id: for coded results (e.g., concept_id for "Normal")

    For a given observation row, typically only ONE of these columns is populated;
    the others are NULL. They represent alternative formats for the same result,
    not complementary data points.

#### How it works

This rule analyzes the SQL query to identify observation value as columns mutually contextual patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM observation
    WHERE value_as_number > 100 AND value_as_string = 'High'
```

```sql
SELECT * FROM observation
    WHERE value_as_number BETWEEN 90 AND 120
      AND value_as_concept_id = 45884084
```

**Correct patterns:**

```sql
SELECT * FROM observation
    WHERE observation_concept_id = 4058286
      AND value_as_concept_id = 45884084
```

```sql
SELECT * FROM observation
    WHERE observation_concept_id = 3004249
      AND value_as_number > 6.5
```

#### Common scenarios

- Using AND between multiple value_as_* columns
- Assumes multiple columns can be populated simultaneously
- Results in queries that match almost zero rows

#### Suggested fix

Use a single value_as_* column or OR conditions for alternatives.

---

### 2. Observation Value As Concept Confusion { #domain-specific-observation-value-as-concept-confusion }

**Rule ID:** `domain_specific.observation_value_as_concept_confusion`
**Severity:** ERROR

#### Intent

observation_concept_id = "What are you measuring?" (e.g., "Blood pressure")
    value_as_concept_id = "What is the answer?" (e.g., "High", "Low", "Normal")

    Using the same concept_id for both means you're saying:
    "I'm measuring Blood Pressure, and the answer is Blood Pressure" - which is nonsensical.

#### How it works

OMOP semantic rule CLIN_034 (partial):
Detects when the same concept_id is used for both observation_concept_id and value_as_concept_id

#### Examples

**Violation patterns:**

```sql
SELECT * FROM observation
    WHERE observation_concept_id = 4058286
      AND value_as_concept_id = 4058286
```

```sql
SELECT * FROM observation
    WHERE observation_concept_id IN (4058286, 3004249)
      AND value_as_concept_id IN (4058286, 3016502)
```

**Correct patterns:**

```sql
SELECT * FROM observation
    WHERE observation_concept_id = 4058286
      AND value_as_concept_id = 45877994
```

```sql
SELECT * FROM observation
    WHERE observation_concept_id = 4058286
      AND value_as_number > 120
```

#### Common scenarios

- Copying the same concept_id to both columns
- Not understanding the difference between observation type and observation result
- Using observation concepts as answers instead of answer-set concepts

#### Suggested fix

Use different concepts for observation_concept_id (question) and value_as_concept_id (answer)

---

### 3. Observation Value As String Numeric Comparison { #domain-specific-observation-value-as-string-numeric-comparison }

**Rule ID:** `domain_specific.observation_value_as_string_numeric_comparison`
**Severity:** ERROR

#### Intent

value_as_string is a VARCHAR field. Applying numeric comparison
    operators directly is semantically incorrect:
    - The database may silently coerce strings to numbers, returning
      wrong results or errors when non-numeric strings are encountered.
    - The proper column for numeric comparisons is value_as_number.

#### How it works

This rule analyzes the SQL query to identify observation value as string numeric comparison patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM observation
    WHERE observation_concept_id = 3038553
      AND value_as_string > 100
```

```sql
SELECT * FROM observation
    WHERE value_as_string BETWEEN 50 AND 200
```

**Correct patterns:**

```sql
SELECT * FROM observation
    WHERE observation_concept_id = 3038553
      AND value_as_number > 100
```

```sql
SELECT * FROM observation
    WHERE CAST(value_as_string AS FLOAT) > 100
```

#### Common scenarios

- WHERE value_as_string > 100
- WHERE value_as_string <= 7.5
- WHERE value_as_string BETWEEN 50 AND 200

#### Suggested fix

Replace with value_as_number or explicitly CAST(value_as_string AS NUMERIC).

---

### PERSON Domain

### 1. Episode Event No Person ID { #domain-specific-episode-event-no-person-id }

**Rule ID:** `domain_specific.episode_event_no_person_id`
**Severity:** ERROR

#### Intent

The episode_event table is a linking table that connects episodes to their
    constituent clinical events. The table schema is:
    - episode_id (FK to episode.episode_id)
    - event_id (polymorphic ID to clinical event)
    - episode_event_field_concept_id (indicates which domain event_id refers to)

    The episode_event table does NOT have a person_id column.

    Developers sometimes mistakenly try to:
    1. Join episode_event directly to person on person_id (column doesn't exist)
    2. Filter episode_event by person_id (column doesn't exist)
    3. Select episode_event.person_id (column doesn't exist)
    4. Use person_id in WHERE/ORDER BY/GROUP BY with episode_event

Why this is wrong:
    The episode_event table is intentionally designed without person_id to avoid
    denormalization. Person information is accessed through the episode table:
    - episode_event contains the event linkages
    - episode contains the person_id and episode metadata
    - This ensures data consistency and proper normalization

    Attempting to use person_id on episode_event:
    - Causes SQL errors (column does not exist)
    - Indicates misunderstanding of episode_event table structure
    - Breaks query execution

#### How it works

This rule analyzes the SQL query to identify episode event no person id patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM episode_event ee
    JOIN person p ON ee.person_id = p.person_id
```

```sql
SELECT * FROM episode_event
    WHERE person_id = 12345
```

**Correct patterns:**

```sql
SELECT * FROM episode_event ee
    JOIN episode e ON ee.episode_id = e.episode_id
    JOIN person p ON e.person_id = p.person_id
```

```sql
SELECT ee.*, e.person_id
    FROM episode_event ee
    JOIN episode e ON ee.episode_id = e.episode_id
    WHERE e.person_id = 12345
```

#### Suggested fix

Use: FROM episode_event ee JOIN episode e ON ee.episode_id = e.episode_id JOIN person p ON e.person_id = p.person_id

---

### 2. Person Birth Field Validation { #domain-specific-person-birth-field-validation }

**Rule ID:** `domain_specific.person_birth_field_validation`
**Severity:** ERROR

#### Intent

Birth fields in the person table should contain plausible values.
    Values outside valid ranges indicate data quality issues or query logic errors.

#### How it works

This rule analyzes the SQL query to identify person birth field validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM person WHERE year_of_birth = 1850
```

```sql
SELECT * FROM person WHERE month_of_birth = 13
```

**Correct patterns:**

```sql
SELECT * FROM person WHERE year_of_birth BETWEEN 1950 AND 2000
    SELECT * FROM person WHERE month_of_birth = 6
    SELECT * FROM person WHERE day_of_birth = 15
```

#### Common scenarios

- year_of_birth = 1850 (too far in the past)
- year_of_birth = 2050 (in the future)
- month_of_birth = 13 (invalid month)

#### Suggested fix

Use valid birth field values within plausible ranges

---

### PROCEDURE Domain

### 2. Procedure Occurrence Quantity Semantics { #domain-specific-procedure-occurrence-quantity-semantics }

**Rule ID:** `domain_specific.procedure_occurrence_quantity_semantics`
**Severity:** WARNING

#### Intent

quantity is the number of units performed in ONE procedure event.
    It is NOT equivalent to COUNT(*) for counting procedure records.

#### How it works

This rule analyzes the SQL query to identify procedure occurrence quantity semantics patterns.

#### Examples

**Violation patterns:**

```sql
SELECT person_id, SUM(quantity) AS procedure_count
    FROM procedure_occurrence
    GROUP BY person_id
```

```sql
SELECT SUM(quantity) AS num_procedures
    FROM procedure_occurrence
```

**Correct patterns:**

```sql
SELECT person_id, COUNT(*) AS procedure_count
    FROM procedure_occurrence
    GROUP BY person_id
```

```sql
with clear alias):
    SELECT person_id, SUM(quantity) AS total_procedure_units
    FROM procedure_occurrence
    GROUP BY person_id
```

#### Common scenarios

- SUM(quantity) aliased as "procedure_count" (suggests counting records)
- SUM(quantity) AS "number_of_procedures" (implies record count)
- Using SUM(quantity) when COUNT(*) is intended

#### Suggested fix

Use COUNT(*) to count records, or use clearer aliases like 'total_units'

---

### SPECIMEN Domain

### 1. Specimen Source ID Not Specimen ID { #domain-specific-specimen-source-id-not-specimen-id }

**Rule ID:** `domain_specific.specimen_source_id_not_specimen_id`
**Severity:** ERROR

#### Intent

The specimen table has a confusing naming pattern:
    - specimen_id (INTEGER): OMOP primary key
    - specimen_source_id (VARCHAR): Source system identifier (free-text)

    The naming confusion:
    - Most OMOP *_id columns are INTEGER foreign keys or primary keys
    - Most source identifiers use *_source_value (VARCHAR)
    - But specimen_source_id breaks this pattern - it LOOKS like a FK but is VARCHAR

    This is the ONLY column in OMOP CDM that ends with _source_id as a free-text field.
    All other *_source_id columns would be *_source_concept_id (INTEGER FKs to concept).

Common mistake:
    Developers see "specimen_source_id" and assume it's a numeric foreign key
    that can be joined to other tables. This is incorrect.

#### How it works

This rule analyzes the SQL query to identify specimen source id not specimen id patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM specimen s
    JOIN measurement m ON s.specimen_source_id = m.measurement_id
```

```sql
SELECT * FROM specimen s
    JOIN person p ON s.specimen_source_id = p.person_id
```

**Correct patterns:**

```sql
SELECT * FROM specimen s
    JOIN measurement m ON s.specimen_id = m.specimen_id
```

```sql
SELECT * FROM specimen WHERE specimen_source_id = 'LAB-2024-001'
```

#### Suggested fix

Replace specimen_source_id with specimen.specimen_id in JOIN conditions. Use specimen_source_id only for filtering.

---

### VISIT Domain

### 1. Visit Detail Admitted/Discharged Domain Validation { #domain-specific-visit-detail-admitted-discharged-domain }

**Rule ID:** `domain_specific.visit_detail_admitted_discharged_domain`
**Severity:** WARNING

#### Intent

The admission source and discharge destination columns represent WHERE the
    patient came from and WHERE they went, not WHAT condition they had or WHAT
    was done to them.

    Correct domains:
    - Visit: Emergency Room Visit, Inpatient Visit, Outpatient Visit
    - Place of Service: Home, Skilled Nursing Facility, Hospice, Rehabilitation

    Incorrect domains:
    - Condition: Diabetes, Myocardial Infarction (these are clinical diagnoses)
    - Drug: Aspirin, Metformin (these are medications)
    - Procedure: Appendectomy, Chemotherapy (these are treatments)

    When queries use hardcoded concept IDs in these columns without verifying
    the domain, they risk using clinical concepts as location concepts.

#### How it works

This rule analyzes the SQL query to identify visit detail admitted/discharged domain validation patterns.

#### Examples

**Violation patterns:**

```sql
without domain verification
    SELECT * FROM visit_detail
    WHERE admitted_from_concept_id = 201826
```

```sql
SELECT * FROM visit_detail
    WHERE discharged_to_concept_id IN (12345, 67890)
```

**Correct patterns:**

```sql
with domain validation
    SELECT vd.*
    FROM visit_detail vd
    JOIN concept c ON vd.admitted_from_concept_id = c.concept_id
    WHERE c.domain_id IN ('Visit', 'Place of Service')
```

```sql
with comment
    SELECT * FROM visit_detail
    WHERE admitted_from_concept_id = 8870  -- Emergency Room (Visit domain)
```

#### Common scenarios

- Using condition concept IDs for admission source
- Using procedure concept IDs for discharge destination
- Hardcoding concept IDs without domain validation

#### Suggested fix

Join to the concept table and add: c.domain_id IN ('Visit', 'Place of Service').

---

### 2. Visit Detail Dates Within Parent Visit { #domain-specific-visit-detail-dates-within-parent-visit }

**Rule ID:** `domain_specific.visit_detail_dates_within_parent_visit`
**Severity:** WARNING

#### Intent

Queries that filter for visit_detail dates OUTSIDE the parent visit range
    indicate a logic error or misunderstanding of the visit hierarchy:
    - visit_detail_start_date should be >= visit_start_date
    - visit_detail_end_date should be <= visit_end_date

#### How it works

This rule analyzes the SQL query to identify visit detail dates within parent visit patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vd.visit_detail_start_date < vo.visit_start_date
```

```sql
SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vd.visit_detail_end_date > vo.visit_end_date
```

**Correct patterns:**

```sql
within parent range
    SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vd.visit_detail_start_date >= vo.visit_start_date
      AND vd.visit_detail_end_date <= vo.visit_end_date
```

```sql
SELECT * FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_start_date <= vd.visit_detail_start_date
      AND vd.visit_detail_end_date <= vo.visit_end_date
```

#### Suggested fix

Ensure visit_detail dates are within visit_start_date and visit_end_date

---

### 3. Visit Detail Has No Preceding Visit Occurrence ID { #domain-specific-visit-detail-has-no-preceding-visit-occurrence-id }

**Rule ID:** `domain_specific.visit_detail_has_no_preceding_visit_occurrence_id`
**Severity:** ERROR

#### Intent

The OMOP CDM has two separate temporal chains for visits:

    1. visit_occurrence temporal chain:
       - Uses preceding_visit_occurrence_id to link to previous visits
       - visit_occurrence.preceding_visit_occurrence_id → visit_occurrence.visit_occurrence_id

    2. visit_detail temporal chain:
       - Uses preceding_visit_detail_id to link to previous visit details
       - visit_detail.preceding_visit_detail_id → visit_detail.visit_detail_id

    The visit_detail table does NOT have a preceding_visit_occurrence_id column.

#### How it works

This rule analyzes the SQL query to identify visit detail has no preceding visit occurrence id patterns.

#### Examples

**Violation patterns:**

```sql
SELECT preceding_visit_occurrence_id FROM visit_detail
```

```sql
SELECT vd.preceding_visit_occurrence_id FROM visit_detail vd
```

**Correct patterns:**

```sql
SELECT preceding_visit_detail_id FROM visit_detail
```

```sql
SELECT vd.preceding_visit_detail_id
    FROM visit_detail vd
    WHERE preceding_visit_detail_id IS NOT NULL
```

#### Common scenarios

- Referencing visit_detail.preceding_visit_occurrence_id (column doesn't exist)
- Confusing the two temporal chains
- Trying to join visit_detail to visit_occurrence via preceding_visit_occurrence_id

#### Suggested fix

Use preceding_visit_detail_id for visit_detail temporal chain. Use preceding_visit_occurrence_id only with visit_occurrence table.

---

### 4. Visit Detail Visit Occurrence Reference { #domain-specific-visit-detail-visit-occurrence-reference }

**Rule ID:** `domain_specific.visit_detail_visit_occurrence_reference`
**Severity:** WARNING

#### Intent

visit_detail provides granular sub-visit information (ICU stay, ward transfer,
    operating room), but critical context is stored in visit_occurrence:
    - Overall visit type (inpatient, outpatient, ER)
    - Visit-level dates (visit_start_date, visit_end_date)
    - Visit-level provider and care site
    - Admission source and discharge destination

    Analyzing visit_detail without referencing visit_occurrence loses this context.

#### How it works

This rule analyzes the SQL query to identify visit detail visit occurrence reference patterns.

#### Examples

**Violation patterns:**

```sql
SELECT person_id, visit_detail_start_date
    FROM visit_detail
    WHERE visit_detail_concept_id = 32037  -- ICU
```

**Correct patterns:**

```sql
SELECT vd.*, vo.visit_concept_id, vo.visit_start_date
    FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vd.visit_detail_concept_id = 32037
```

```sql
with visit_occurrence
    SELECT * FROM visit_detail
    WHERE visit_occurrence_id IN (
        SELECT visit_occurrence_id FROM visit_occurrence
        WHERE visit_concept_id = 9201  -- Inpatient visits
    )
```

#### Suggested fix

Ensure visit_detail is correctly linked to visit_occurrence via visit_occurrence_id when visit-level context is needed

---

### 5. Visit Event Temporal Validation { #domain-specific-visit-event-temporal-validation }

**Rule ID:** `domain_specific.visit_event_temporal_validation`
**Severity:** WARNING

#### Intent

Clinical events are linked to visits via visit_occurrence_id. These events
    should occur during the visit:
    - event_date >= visit_start_date
    - event_date <= visit_end_date (if not NULL)

    If a query filters for event_date < visit_start_date, this suggests:
    1. Wrong visit_occurrence_id (join error)
    2. Data quality issue in the source data
    3. Logic error in the query

#### How it works

This rule analyzes the SQL query to identify visit event temporal validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE co.condition_start_date < vo.visit_start_date
```

**Correct patterns:**

```sql
SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE co.condition_start_date >= vo.visit_start_date
      AND co.condition_start_date <= COALESCE(vo.visit_end_date, CURRENT_DATE)
```

```sql
SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
```

#### Suggested fix

Use event_date >= visit_start_date or review join logic

---

### 6. Visit Outpatient Same-Day Validation { #domain-specific-visit-outpatient-same-day-validation }

**Rule ID:** `domain_specific.visit_outpatient_same_day_validation`
**Severity:** WARNING

#### Intent

- Outpatient visits: same-day (9202)
    - Inpatient visits: multi-day stays (9201)
    - Emergency Room: same-day (9203)
    - ER+Inpatient: multi-day (262)

#### How it works

This rule analyzes the SQL query to identify visit outpatient same-day validation patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM visit_occurrence
    WHERE visit_concept_id = 9202
      AND DATEDIFF(day, visit_start_date, visit_end_date) > 30
```

**Correct patterns:**

```sql
SELECT * FROM visit_occurrence
    WHERE visit_concept_id = 9201
      AND DATEDIFF(day, visit_start_date, visit_end_date) > 30
```

```sql
SELECT * FROM visit_occurrence
    WHERE visit_concept_id = 9202
      AND DATEDIFF(day, visit_start_date, visit_end_date) <= 1
```

#### Suggested fix

Use visit_concept_id = 9201 (inpatient) for multi-day stays, or adjust date range for outpatient visits

---

### VOCABULARY Domain

### 1. Relationship Boolean Comparison { #domain-specific-vocabulary-relationship-boolean-comparison }

**Rule ID:** `domain_specific.vocabulary.relationship_boolean_comparison`
**Severity:** ERROR

#### Intent

The relationship vocabulary table uses boolean flags to indicate:
    - is_hierarchical: Whether the relationship represents a hierarchy
    - defines_ancestry: Whether the relationship defines ancestry paths

    When filtering these columns, developers sometimes use incorrect value types:
    - String literals: 'true', 'false', '1', '0'
    - Invalid integers: 2, -1, or any value other than 0 or 1

    This causes issues:
    1. Type mismatch errors in strongly-typed databases
    2. Incorrect comparisons (boolean vs string comparison semantics differ)
    3. Performance problems (prevents index usage)
    4. Silent failures or unexpected results

Why this is wrong:
    Boolean columns should be compared with boolean-compatible values:
    - In most SQL dialects, booleans are represented as 1 (TRUE) or 0 (FALSE)
    - Comparing with strings requires implicit conversion that may fail
    - Using invalid integers (2, -1, etc.) is semantically meaningless
    - String comparisons have different semantics than boolean comparisons

#### How it works

This rule analyzes the SQL query to identify relationship boolean comparison patterns.

#### Examples

**Violation patterns:**

```sql
SELECT * FROM relationship WHERE is_hierarchical = 'true'
```

```sql
SELECT * FROM relationship WHERE defines_ancestry = '1'
```

**Correct patterns:**

```sql
SELECT * FROM relationship WHERE is_hierarchical = 1
```

```sql
SELECT * FROM relationship WHERE is_hierarchical = TRUE
```

#### Suggested fix

Use 0/1 or TRUE/FALSE instead of strings.

---

# OMOP Rules Implementation Status

This checklist tracks which rules from `omop_rules.json` have been implemented in the codebase.

**Legend:**
- `[x]` = Implemented
- `[ ]` = Not yet implemented
- `[~]` = Partially implemented or covered by another rule

**Statistics:**
- Total rules in JSON: 350+
- Implemented: ~15 core rules
- Coverage: ~7-10%

---

## Core OMOP Rules (OMOP_001-OMOP_200)

### Concept & Vocabulary Basics

- [x] **OMOP_001**: standard_concept_filter_on_primary_concept_id
  - *Implemented as: `concept_standardization/standard_concept_enforcement.py`*
- [x] **OMOP_002**: concept_id_join_uses_concept_id_column
  - *Implemented as: `joins/join_path_validation.py`*
- [x] **OMOP_003**: source_value_not_used_for_filtering
  - *Implemented as: `anti_patterns/no_string_identification.py`*
- [ ] **OMOP_004**: person_join_uses_person_id
- [ ] **OMOP_005**: visit_occurrence_join_uses_visit_occurrence_id
- [x] **OMOP_006**: concept_ancestor_for_hierarchical_queries
  - *Implemented as: `concept_standardization/hierarchy_expansion.py`*
- [x] **OMOP_007**: concept_ancestor_join_columns
  - *Implemented as: `concept_standardization/hierarchy_expansion.py`*
- [x] **OMOP_008**: invalid_table_name
  - *Implemented as: `data_quality/schema_validation.py`*
- [x] **OMOP_009**: invalid_column_name
  - *Implemented as: `data_quality/schema_validation.py`*
- [x] **OMOP_010**: concept_zero_exclusion
  - *Implemented as: `data_quality/unmapped_concept_handling.py`*
- [ ] **OMOP_011**: era_tables_use_standard_concepts_only
- [x] **OMOP_012**: observation_period_required_for_cohort
  - *Implemented as: `temporal/observation_period_anchoring.py`*
- [ ] **OMOP_013**: visit_concept_id_for_visit_type_filtering
- [ ] **OMOP_014**: type_concept_id_not_for_clinical_filtering
- [ ] **OMOP_015**: drug_exposure_date_range_uses_correct_columns
- [ ] **OMOP_016**: concept_relationship_join_requires_relationship_id
- [x] **OMOP_017**: concept_relationship_invalid_reason_filter
  - *Implemented as: `concept_standardization/invalid_reason_enforcement.py`*
- [x] **OMOP_018**: concept_invalid_reason_filter
  - *Implemented as: `concept_standardization/invalid_reason_enforcement.py`*
- [ ] **OMOP_019**: gender_concept_id_validates_standard_gender
- [x] **OMOP_020**: cross_table_join_requires_shared_key
  - *Implemented as: `joins/join_path_validation.py`*
- [x] **OMOP_021**: measurement_value_as_number_with_unit
  - *Implemented as: `domain_specific/measurement/measurement_unit_validation.py`*
- [ ] **OMOP_022**: source_concept_id_not_for_primary_filtering
- [ ] **OMOP_023**: death_table_primary_key_is_person_id
- [ ] **OMOP_024**: cohort_subject_id_joins_to_person_id
- [ ] **OMOP_025**: vocabulary_id_as_string_not_integer
- [ ] **OMOP_026**: domain_id_as_string_not_integer
- [x] **OMOP_027**: maps_to_relationship_for_source_to_standard
  - *Implemented as: `joins/maps_to_direction.py`*
- [ ] **OMOP_028**: condition_era_not_same_as_condition_occurrence
- [ ] **OMOP_029**: drug_era_not_same_as_drug_exposure
- [ ] **OMOP_030**: clinical_table_to_concept_multiple_aliases
- [ ] **OMOP_031**: date_column_type_mismatch
- [x] **OMOP_032**: concept_code_requires_vocabulary_id
  - *Implemented as: `anti_patterns/concept_code_requires_vocabulary_id.py`*
- [ ] **OMOP_033**: observation_period_date_range_logic
- [ ] **OMOP_034**: visit_detail_joins_to_visit_occurrence
- [ ] **OMOP_035**: concept_class_id_as_string
- [ ] **OMOP_036**: drug_strength_join_for_dosage_info
- [ ] **OMOP_037**: standard_concept_values_are_s_or_c
- [ ] **OMOP_038**: cost_table_requires_domain_join
- [ ] **OMOP_039**: care_site_join_via_care_site_id
- [ ] **OMOP_040**: concept_ancestor_self_referencing_included

### Query Performance & Best Practices

- [ ] **OMOP_041**: select_star_on_large_clinical_tables
- [ ] **OMOP_042**: person_age_from_year_of_birth_not_birth_datetime
- [ ] **OMOP_043**: inner_join_to_visit_loses_records
- [ ] **OMOP_044**: drug_era_contains_ingredient_level_concepts
- [ ] **OMOP_045**: measurement_value_as_concept_id_check
- [ ] **OMOP_046**: no_cartesian_join_clinical_tables_on_person_id_only
- [ ] **OMOP_047**: provider_specialty_via_concept_join
- [ ] **OMOP_048**: condition_status_concept_id_for_diagnosis_status
- [ ] **OMOP_049**: observation_table_not_for_lab_results
- [ ] **OMOP_050**: concept_id_negative_values_invalid

### Advanced Validation Rules (OMOP_051-100)

- [ ] **OMOP_051**: payer_plan_period_date_overlap_with_events
- [ ] **OMOP_052**: visit_end_date_not_before_start_date_filter
- [ ] **OMOP_053**: note_nlp_joins_to_note_not_person
- [ ] **OMOP_054**: concept_synonym_for_name_search
- [ ] **OMOP_055**: drug_exposure_quantity_not_for_duration
- [ ] **OMOP_056**: race_ethnicity_concept_ids_are_standard
- [ ] **OMOP_057**: group_by_must_include_non_aggregated_columns
- [ ] **OMOP_058**: source_to_concept_map_requires_source_vocabulary_id
- [ ] **OMOP_059**: preceding_visit_occurrence_id_self_join
- [ ] **OMOP_060**: measurement_operator_concept_id_usage
- [ ] **OMOP_061**: concept_relationship_bidirectional_awareness
- [ ] **OMOP_062**: condition_end_date_may_be_null
- [ ] **OMOP_063**: distinct_person_count_not_row_count
- [ ] **OMOP_064**: drug_strength_valid_start_end_date_filter
- [ ] **OMOP_065**: observation_table_heterogeneous_domain
- [x] **OMOP_066**: concept_domain_id_matches_target_table
  - *Implemented as: `concept_standardization/domain_segregation.py`*
- [ ] **OMOP_067**: no_union_different_concept_id_types
- [ ] **OMOP_068**: specimen_table_not_for_lab_results
- [ ] **OMOP_069**: cohort_definition_id_required_for_cohort_query
- [ ] **OMOP_070**: visit_occurrence_start_not_after_end
- [ ] **OMOP_071**: location_history_for_temporal_location
- [ ] **OMOP_072**: drug_exposure_sig_not_for_dose_extraction
- [ ] **OMOP_073**: episode_table_requires_episode_concept_id
- [ ] **OMOP_074**: measurement_range_low_high_for_abnormal_detection
- [ ] **OMOP_075**: device_exposure_unique_device_id_not_concept_id
- [ ] **OMOP_076**: multiple_observation_periods_per_person
- [ ] **OMOP_077**: refills_column_semantics
- [ ] **OMOP_078**: concept_id_equality_not_concept_name_equality
- [ ] **OMOP_079**: condition_era_gap_days_not_available
- [ ] **OMOP_080**: cohort_date_columns_required
- [ ] **OMOP_081**: delete_update_on_vocabulary_tables
- [ ] **OMOP_082**: concept_ancestor_only_standard_concepts
- [ ] **OMOP_083**: note_text_not_in_select_without_filter
- [ ] **OMOP_084**: procedure_modifier_concept_id_usage
- [ ] **OMOP_085**: dose_era_unit_concept_id_required
- [ ] **OMOP_086**: relationship_id_as_string_not_integer
- [ ] **OMOP_087**: episode_event_links_episode_to_clinical_tables
- [ ] **OMOP_088**: note_event_id_polymorphic_join
- [ ] **OMOP_089**: measurement_event_id_polymorphic_join
- [ ] **OMOP_090**: observation_event_id_polymorphic_join
- [ ] **OMOP_091**: cost_total_charge_vs_total_cost_vs_total_paid
- [ ] **OMOP_092**: cost_paid_components_sum_to_total_paid
- [ ] **OMOP_093**: care_site_place_of_service_concept_for_facility_type
- [ ] **OMOP_094**: death_cause_concept_id_domain_check
- [ ] **OMOP_095**: source_to_concept_map_invalid_reason_filter
- [ ] **OMOP_096**: drug_strength_multiple_ingredients
- [ ] **OMOP_097**: person_provider_id_is_primary_care_provider
- [ ] **OMOP_098**: verbatim_end_date_not_for_duration
- [ ] **OMOP_099**: note_nlp_term_exists_filter
- [ ] **OMOP_100**: note_nlp_term_temporal_filter

### Specialized Rules (OMOP_101-160)

- [ ] **OMOP_101**: observation_qualifier_concept_id_for_context
- [ ] **OMOP_102**: drug_exposure_route_concept_id_domain
- [ ] **OMOP_103**: visit_occurrence_admitted_from_discharged_to_concepts
- [ ] **OMOP_104**: specimen_anatomic_site_concept_id_domain
- [ ] **OMOP_105**: provider_npi_is_string_not_integer
- [ ] **OMOP_106**: location_state_zip_not_joined_to_concept
- [ ] **OMOP_107**: condition_occurrence_stop_reason_is_free_text
- [ ] **OMOP_108**: drug_exposure_lot_number_is_free_text
- [ ] **OMOP_109**: concept_relationship_concept_id_1_2_not_swapped
- [ ] **OMOP_110**: no_distinct_on_primary_key_column
- [ ] **OMOP_111**: observation_value_as_string_not_for_numeric_comparison
- [ ] **OMOP_112**: cost_currency_concept_id_for_multi_currency
- [ ] **OMOP_113**: cdm_source_no_primary_key_single_row
- [ ] **OMOP_114**: device_exposure_production_id_is_free_text
- [ ] **OMOP_115**: visit_detail_parent_self_join
- [ ] **OMOP_116**: concept_valid_dates_for_temporal_accuracy
- [ ] **OMOP_117**: drug_strength_box_size_awareness
- [ ] **OMOP_118**: era_tables_no_provider_or_visit_columns
- [ ] **OMOP_119**: specimen_source_id_not_specimen_id
- [ ] **OMOP_120**: cohort_definition_syntax_not_executable_sql
- [ ] **OMOP_121**: metadata_table_not_for_clinical_queries
- [ ] **OMOP_122**: observation_period_no_concept_id_filter
- [ ] **OMOP_123**: or_condition_on_different_concept_id_columns
- [ ] **OMOP_124**: device_exposure_no_days_supply
- [ ] **OMOP_125**: device_exposure_no_refills
- [ ] **OMOP_126**: visit_detail_preceding_id_self_join
- [ ] **OMOP_127**: concept_synonym_language_concept_id
- [ ] **OMOP_128**: vocabulary_table_join_uses_vocabulary_id_string
- [ ] **OMOP_129**: domain_table_join_uses_domain_id_string
- [ ] **OMOP_130**: concept_class_table_join_uses_concept_class_id_string
- [ ] **OMOP_131**: having_without_group_by
- [ ] **OMOP_132**: age_at_event_not_age_at_query_time
- [ ] **OMOP_133**: classification_concept_not_in_clinical_table
- [ ] **OMOP_134**: death_join_to_person_not_to_clinical_event
- [ ] **OMOP_135**: cost_payer_plan_period_id_join
- [ ] **OMOP_136**: cost_drg_concept_id_domain
- [ ] **OMOP_137**: cost_revenue_code_concept_id_domain
- [ ] **OMOP_138**: episode_parent_id_self_join
- [ ] **OMOP_139**: episode_event_no_person_id
- [ ] **OMOP_140**: subquery_in_where_without_correlation
- [ ] **OMOP_141**: observation_value_as_columns_mutually_contextual
- [ ] **OMOP_142**: visit_detail_has_no_preceding_visit_occurrence_id
- [ ] **OMOP_143**: measurement_time_is_varchar
- [ ] **OMOP_144**: payer_plan_period_sponsor_concept_id_domain
- [ ] **OMOP_145**: location_history_entity_id_requires_domain_id
- [ ] **OMOP_146**: procedure_date_not_procedure_start_date
- [ ] **OMOP_147**: concept_ancestor_not_for_source_to_standard_mapping
- [ ] **OMOP_148**: observation_period_not_for_clinical_event_lookup
- [ ] **OMOP_149**: left_join_then_where_on_right_table
- [ ] **OMOP_150**: relationship_table_is_hierarchical_defines_ancestry_flags
- [ ] **OMOP_151**: death_date_before_event_date_illogical
- [ ] **OMOP_152**: note_nlp_snippet_not_structured_data
- [ ] **OMOP_153**: specimen_disease_status_concept_id_domain
- [ ] **OMOP_154**: source_to_concept_map_target_concept_should_be_standard
- [ ] **OMOP_155**: condition_occurrence_no_value_as_number
- [ ] **OMOP_156**: procedure_occurrence_no_value_as_number
- [ ] **OMOP_157**: concept_id_used_as_string_comparison
- [ ] **OMOP_158**: note_class_concept_id_for_note_category
- [ ] **OMOP_159**: drug_exposure_end_date_null_handling
- [ ] **OMOP_160**: visit_occurrence_no_condition_concept_id

---

## Concept Set Rules (OMOP_200-209)

- [x] **OMOP_200**: concept_set_requires_standard_concepts
  - *Partially covered by: `concept_standardization/standard_concept_enforcement.py`*
- [ ] **OMOP_201**: concept_set_descendants_via_concept_ancestor
- [ ] **OMOP_202**: concept_set_invalid_reason_filtered
- [ ] **OMOP_203**: concept_set_domain_consistency
- [ ] **OMOP_204**: concept_set_no_mixed_domains
- [ ] **OMOP_205**: concept_set_classification_handling
- [ ] **OMOP_206**: concept_set_vocabulary_filtering
- [ ] **OMOP_207**: concept_set_synonym_search
- [ ] **OMOP_208**: concept_set_descendant_self_handling
- [ ] **OMOP_209**: concept_set_mapping_from_source

---

## Era Table Rules (OMOP_210-229)

- [ ] **OMOP_210**: condition_era_persistence_window
- [ ] **OMOP_211**: condition_era_ordering_by_date
- [ ] **OMOP_212**: condition_era_requires_person_partition
- [ ] **OMOP_213**: drug_era_gap_days_logic
- [ ] **OMOP_214**: drug_era_end_date_derivation
- [ ] **OMOP_215**: drug_era_days_supply_usage
- [ ] **OMOP_216**: drug_era_overlap_merge
- [ ] **OMOP_217**: era_concept_grouping
- [ ] **OMOP_218**: era_minimum_length_validation
- [ ] **OMOP_219**: era_temporal_sorting_required
- [x] **OMOP_220**: event_within_observation_period
  - *Implemented as: `temporal/observation_period_anchoring.py`*
- [ ] **OMOP_221**: multiple_observation_periods_allowed
- [ ] **OMOP_222**: observation_period_gap_detection
- [x] **OMOP_223**: event_before_observation_start
  - *Implemented as: `temporal/observation_period_anchoring.py`*
- [x] **OMOP_224**: event_after_observation_end
  - *Implemented as: `temporal/observation_period_anchoring.py`*
- [ ] **OMOP_225**: cohort_entry_requires_observation
- [ ] **OMOP_226**: observation_period_overlap_validation
- [ ] **OMOP_227**: observation_period_duration_positive
- [ ] **OMOP_228**: observation_period_join_person
- [ ] **OMOP_229**: observation_period_event_alignment

---

## Measurement Rules (OMOP_230-239)

- [x] **OMOP_230**: measurement_requires_unit
  - *Implemented as: `domain_specific/measurement/measurement_unit_validation.py`*
- [ ] **OMOP_231**: measurement_unit_standardization
- [ ] **OMOP_232**: measurement_cross_unit_comparison
- [ ] **OMOP_233**: measurement_range_usage
- [ ] **OMOP_234**: measurement_operator_handling
- [ ] **OMOP_235**: measurement_value_type_validation
- [ ] **OMOP_236**: measurement_null_value_handling
- [ ] **OMOP_237**: measurement_time_alignment
- [ ] **OMOP_238**: measurement_duplicate_detection
- [ ] **OMOP_239**: measurement_unit_grouping

---

## Episode & Fact Relationship Rules (OMOP_240-259)

- [ ] **OMOP_240**: episode_requires_concept_filter
- [ ] **OMOP_241**: episode_event_join_event_table
- [ ] **OMOP_242**: episode_event_requires_valid_event_id
- [ ] **OMOP_243**: episode_event_person_consistency
- [ ] **OMOP_244**: episode_start_before_end
- [ ] **OMOP_245**: episode_event_temporal_alignment
- [ ] **OMOP_246**: episode_concept_domain_validation
- [ ] **OMOP_247**: episode_multiple_events_allowed
- [ ] **OMOP_248**: episode_event_domain_consistency
- [ ] **OMOP_249**: episode_event_relationship_validation
- [ ] **OMOP_250**: fact_relationship_requires_relationship
- [ ] **OMOP_251**: fact_relationship_domain_consistency
- [ ] **OMOP_252**: fact_relationship_valid_concepts
- [ ] **OMOP_253**: fact_relationship_bidirectional_logic
- [ ] **OMOP_254**: fact_relationship_event_exists
- [ ] **OMOP_255**: fact_relationship_no_self_reference
- [ ] **OMOP_256**: fact_relationship_domain_pair_check
- [ ] **OMOP_257**: fact_relationship_temporal_alignment
- [ ] **OMOP_258**: fact_relationship_duplicate_detection
- [ ] **OMOP_259**: fact_relationship_concept_validation

---

## Additional Specialized Rules (OMOP_401-608)

- [ ] **OMOP_401**: payer_plan_period_duration_positive
- [ ] **OMOP_402**: payer_plan_period_no_overlaps
- [ ] **OMOP_403**: payer_plan_period_within_observation_period
- [ ] **OMOP_404**: visit_occurrence_preceding_same_person
- [x] **OMOP_405**: measurement_unit_required_with_numeric_values
  - *Implemented as: `domain_specific/measurement/measurement_unit_validation.py`*
- [x] **OMOP_500**: concept_set_requires_descendant_expansion
  - *Implemented as: `concept_standardization/hierarchy_expansion.py`*
- [ ] **OMOP_501**: concept_set_excludes_invalid_concepts
- [ ] **OMOP_502**: drug_era_start_date_matches_first_exposure
- [ ] **OMOP_503**: drug_era_gap_days_logic
- [ ] **OMOP_504**: condition_era_gap_logic
- [ ] **OMOP_505**: episode_event_requires_episode_id_join
- [ ] **OMOP_506**: episode_event_object_domain_match
- [ ] **OMOP_507**: fact_relationship_requires_relationship_concept_id
- [ ] **OMOP_508**: device_exposure_concept_domain_validation
- [ ] **OMOP_509**: measurement_unit_domain_validation
- [ ] **OMOP_510**: visit_detail_within_visit_occurrence_dates
- [ ] **OMOP_511**: measurement_specimen_linkage
- [ ] **OMOP_512**: clinical_event_within_observation_period
- [x] **OMOP_513**: incident_cohort_requires_prior_washout
  - *Partially covered by: `temporal/future_information_leakage.py`*
- [ ] **OMOP_514**: cohort_index_date_defined
- [ ] **OMOP_515**: drug_exposure_end_after_start
- [ ] **OMOP_516**: provider_join_via_provider_id
- [ ] **OMOP_517**: visit_occurrence_person_consistency
- [ ] **OMOP_518**: measurement_datetime_consistency
- [ ] **OMOP_519**: visit_detail_within_visit_occurrence
- [ ] **OMOP_520**: fact_relationship_domain_consistency
- [ ] **OMOP_521**: drug_exposure_route_concept_domain
- [ ] **OMOP_522**: procedure_occurrence_date_valid
- [ ] **OMOP_523**: measurement_value_number_requires_numeric_type
- [ ] **OMOP_524**: visit_occurrence_type_domain
- [ ] **OMOP_525**: condition_occurrence_start_before_end
- [ ] **OMOP_526**: device_exposure_start_before_end
- [ ] **OMOP_527**: specimen_collection_before_measurement
- [ ] **OMOP_528**: provider_specialty_domain_validation
- [ ] **OMOP_529**: cohort_end_date_not_before_start
- [ ] **OMOP_530**: concept_set_inclusion_exclusion_logic
- [ ] **OMOP_531**: concept_set_mapped_source_concepts
- [ ] **OMOP_532**: cohort_entry_event_unique_per_person
- [ ] **OMOP_533**: time_at_risk_after_index_date
- [ ] **OMOP_534**: cohort_exit_event_after_entry
- [ ] **OMOP_535**: drug_exposure_persistence_window
- [ ] **OMOP_536**: nested_cohort_event_dependency
- [ ] **OMOP_537**: negative_control_outcome_not_exposure_related
- [ ] **OMOP_538**: measurement_threshold_cohort_logic
- [ ] **OMOP_539**: visit_occurrence_precedes_clinical_events
- [x] **OMOP_540**: temporal_event_sequence_logic
  - *Implemented as: `temporal/future_information_leakage.py`*
- [ ] **OMOP_541**: observation_period_before_index
- [ ] **OMOP_542**: minimum_observation_period_duration
- [ ] **OMOP_543**: drug_exposure_overlap_resolution
- [ ] **OMOP_544**: measurement_baseline_before_exposure
- [ ] **OMOP_545**: drug_strength_ingredient_consistency
- [ ] **OMOP_546**: provider_care_site_relationship
- [ ] **OMOP_547**: care_site_location_relationship
- [ ] **OMOP_548**: clinical_event_person_consistency
- [ ] **OMOP_549**: temporal_censoring_after_observation_period
- [ ] **OMOP_550**: event_date_column_correctness
- [ ] **OMOP_551**: event_end_date_not_before_start_date
- [ ] **OMOP_552**: drug_exposure_after_condition_temporal_logic
- [ ] **OMOP_553**: event_within_visit_dates
- [ ] **OMOP_554**: index_date_defined_for_cohort
- [ ] **OMOP_555**: temporal_join_requires_date_constraint
- [ ] **OMOP_556**: cohort_washout_period_required
- [ ] **OMOP_557**: drug_exposure_overlap_detection
- [ ] **OMOP_558**: measurement_temporal_sequence
- [ ] **OMOP_559**: death_after_events_check
- [ ] **OMOP_560**: measurement_requires_measurement_concept_id
- [ ] **OMOP_561**: measurement_numeric_vs_concept_value
- [x] **OMOP_562**: measurement_unit_required_for_numeric_values
  - *Implemented as: `domain_specific/measurement/measurement_unit_validation.py`*
- [ ] **OMOP_563**: measurement_reference_range_available
- [ ] **OMOP_564**: measurement_date_not_null
- [ ] **OMOP_565**: measurement_operator_consistency
- [ ] **OMOP_566**: measurement_concept_domain_validation
- [ ] **OMOP_567**: measurement_unit_domain_validation
- [ ] **OMOP_568**: measurement_duplicate_same_day
- [ ] **OMOP_569**: measurement_value_plausibility
- [ ] **OMOP_600**: maps_to_chain_follow_to_terminal
- [ ] **OMOP_601**: cdm_v53_to_v54_column_renames
- [ ] **OMOP_602**: correlated_subquery_on_large_clinical_table
- [ ] **OMOP_603**: unbounded_date_range_on_clinical_table
- [ ] **OMOP_604**: site_specific_concept_ids_in_multi_site_query
- [ ] **OMOP_605**: datetime_timezone_inconsistency
- [ ] **OMOP_606**: date_preferred_over_datetime_when_datetime_nullable
- [ ] **OMOP_607**: concept_relationship_maps_to_chain_resolution
- [ ] **OMOP_608**: concept_domain_routing_awareness

---

## Vocabulary-Specific Rules (VOCAB_001-042)

- [x] **VOCAB_001**: concept_name_like_without_domain_constraint
  - *Implemented as: `anti_patterns/concept_lookup_context.py` and `concept_name_lookup.py`*
- [ ] **VOCAB_002**: concept_ancestor_descendant_swapped_for_rollup
- [ ] **VOCAB_003**: concept_relationship_maps_to_target_must_be_standard
- [ ] **VOCAB_004**: vocabulary_id_case_sensitive_mismatch
- [ ] **VOCAB_005**: vocabulary_id_with_hyphens_or_spaces
- [ ] **VOCAB_006**: domain_id_case_sensitive_mismatch
- [ ] **VOCAB_007**: concept_class_id_case_sensitive_mismatch
- [ ] **VOCAB_008**: concept_ancestor_max_levels_misused_as_distance
- [ ] **VOCAB_009**: standard_concept_is_null_for_non_standard_lookup
- [ ] **VOCAB_010**: concept_relationship_subsumes_vs_is_a_direction
- [ ] **VOCAB_011**: concept_relationship_mapped_from_vs_maps_to
- [x] **VOCAB_012**: concept_code_like_wildcard_without_vocabulary_id
  - *Implemented as: `anti_patterns/concept_code_requires_vocabulary_id.py`*
- [ ] **VOCAB_013**: multiple_maps_to_targets_not_handled
- [ ] **VOCAB_014**: concept_ancestor_used_across_domains
- [ ] **VOCAB_015**: concept_relationship_no_join_to_concept_on_both_sides
- [ ] **VOCAB_016**: standard_concept_filter_with_or_instead_of_in
- [ ] **VOCAB_017**: concept_ancestor_with_min_zero_unintentional_self_include
- [ ] **VOCAB_018**: concept_relationship_multiple_relationship_types_without_filter
- [x] **VOCAB_019**: standard_concept_filter_missing_on_concept_name_join
  - *Partially covered by: `anti_patterns/concept_name_lookup.py`*
- [ ] **VOCAB_020**: source_concept_id_should_join_concept_without_standard_filter
- [ ] **VOCAB_021**: concept_id_zero_not_joined_to_concept
- [ ] **VOCAB_022**: snomed_vocabulary_id_for_conditions
- [ ] **VOCAB_023**: rxnorm_vocabulary_id_for_drugs
- [ ] **VOCAB_024**: snomed_vocabulary_id_for_procedures_or_cpt4
- [ ] **VOCAB_025**: loinc_vocabulary_id_for_measurements
- [ ] **VOCAB_026**: concept_ancestor_with_concept_class_filter_on_descendant
- [ ] **VOCAB_027**: concept_relationship_concept_id_1_equals_concept_id_2
- [ ] **VOCAB_028**: unit_concept_id_vocabulary_is_ucum
- [ ] **VOCAB_029**: concept_ancestor_depth_filter_off_by_one
- [ ] **VOCAB_030**: concept_code_equality_across_vocabularies
- [ ] **VOCAB_031**: concept_valid_end_date_check_for_current_concepts
- [ ] **VOCAB_032**: concept_relationship_replaces_for_deprecated_concepts
- [ ] **VOCAB_033**: concept_id_not_found_in_concept_table
- [ ] **VOCAB_034**: concept_relationship_transitive_misuse
- [ ] **VOCAB_035**: standard_concept_filter_on_type_concept_id_lookup
- [ ] **VOCAB_036**: concept_ancestor_includes_only_defines_ancestry_relationships
- [ ] **VOCAB_037**: concept_name_trailing_whitespace
- [ ] **VOCAB_038**: concept_ancestor_mixed_with_concept_relationship_redundantly
- [ ] **VOCAB_039**: concept_class_id_ingredient_for_drug_grouping
- [ ] **VOCAB_040**: concept_relationship_valid_date_range_check
- [ ] **VOCAB_041**: concept_relationship_is_a_direction
- [ ] **VOCAB_042**: concept_ancestor_includes_self

---

## Clinical Data Quality Rules (CLIN_001-057)

- [ ] **CLIN_001**: person_no_clinical_event_dates
- [ ] **CLIN_002**: person_no_clinical_concept_id
- [ ] **CLIN_003**: person_gender_domain_constraint
- [ ] **CLIN_004**: person_race_domain_constraint
- [ ] **CLIN_005**: person_ethnicity_domain_constraint
- [ ] **CLIN_006**: person_year_of_birth_plausible_range
- [ ] **CLIN_007**: person_month_of_birth_valid_range
- [ ] **CLIN_008**: person_day_of_birth_valid_range
- [ ] **CLIN_009**: condition_occurrence_domain_constraint
- [ ] **CLIN_010**: condition_occurrence_start_date_required_in_temporal_query
- [ ] **CLIN_011**: condition_occurrence_end_before_start
- [ ] **CLIN_012**: condition_occurrence_status_domain_constraint
- [ ] **CLIN_013**: condition_occurrence_visit_detail_requires_visit_occurrence
- [ ] **CLIN_014**: drug_exposure_domain_constraint
- [ ] **CLIN_015**: drug_exposure_start_date_required_in_temporal_query
- [ ] **CLIN_016**: drug_exposure_days_supply_plausible_range
- [ ] **CLIN_017**: drug_exposure_route_concept_domain_constraint
- [ ] **CLIN_018**: drug_exposure_days_supply_inconsistent_with_dates
- [ ] **CLIN_019**: drug_exposure_quantity_negative_value
- [ ] **CLIN_020**: procedure_occurrence_domain_constraint
- [ ] **CLIN_021**: procedure_occurrence_modifier_domain_constraint
- [ ] **CLIN_022**: procedure_occurrence_end_date_nullable
- [ ] **CLIN_023**: procedure_occurrence_quantity_semantics
- [ ] **CLIN_024**: measurement_domain_constraint
- [ ] **CLIN_025**: measurement_unit_domain_constraint
- [ ] **CLIN_026**: measurement_operator_domain_constraint
- [ ] **CLIN_027**: measurement_range_low_greater_than_range_high
- [ ] **CLIN_028**: measurement_value_as_number_and_concept_exclusive
- [ ] **CLIN_029**: measurement_no_value_as_string
- [ ] **CLIN_030**: measurement_date_required_in_temporal_query
- [ ] **CLIN_031**: observation_domain_constraint
- [ ] **CLIN_032**: observation_unit_domain_constraint
- [ ] **CLIN_033**: observation_qualifier_domain_constraint
- [ ] **CLIN_034**: observation_value_as_concept_domain_constraint
- [ ] **CLIN_035**: observation_date_required_in_temporal_query
- [ ] **CLIN_036**: observation_no_range_low_range_high
- [ ] **CLIN_037**: observation_no_operator_concept_id
- [ ] **CLIN_038**: visit_occurrence_domain_constraint
- [ ] **CLIN_039**: visit_occurrence_inpatient_end_date_required
- [ ] **CLIN_040**: visit_occurrence_outpatient_same_day
- [ ] **CLIN_041**: visit_occurrence_event_before_visit_start
- [ ] **CLIN_042**: visit_occurrence_no_clinical_values
- [ ] **CLIN_043**: visit_detail_domain_constraint
- [ ] **CLIN_044**: visit_detail_must_reference_visit_occurrence
- [ ] **CLIN_045**: visit_detail_end_before_start
- [ ] **CLIN_046**: visit_detail_no_preceding_visit_occurrence_id
- [ ] **CLIN_047**: visit_detail_dates_within_parent_visit
- [ ] **CLIN_048**: death_no_visit_occurrence_id
- [ ] **CLIN_049**: death_cause_concept_condition_domain
- [ ] **CLIN_050**: death_date_not_before_birth
- [ ] **CLIN_051**: death_date_in_future
- [ ] **CLIN_052**: death_cause_source_concept_not_standard
- [ ] **CLIN_053**: clinical_event_date_in_future
- [ ] **CLIN_054**: clinical_event_date_before_1900
- [ ] **CLIN_055**: all_clinical_tables_require_person_id_for_patient_query
- [ ] **CLIN_056**: condition_occurrence_multiple_records_per_person
- [ ] **CLIN_057**: drug_exposure_multiple_records_per_person

---

## JOIN Validation Rules (JOIN_001-031)

- [ ] **JOIN_001**: clinical_to_provider_join_key
- [ ] **JOIN_002**: clinical_to_care_site_join_key
- [ ] **JOIN_003**: care_site_to_location_join_key
- [ ] **JOIN_004**: person_to_location_join_key
- [ ] **JOIN_005**: provider_to_care_site_join_key
- [ ] **JOIN_006**: visit_detail_to_visit_occurrence_join_key
- [ ] **JOIN_007**: clinical_to_visit_detail_join_key
- [ ] **JOIN_008**: concept_primary_concept_id_join_column
- [ ] **JOIN_009**: source_concept_id_to_concept_join_separate_alias
- [ ] **JOIN_010**: type_concept_id_join_requires_own_alias
- [ ] **JOIN_011**: concept_to_vocabulary_join_key
- [ ] **JOIN_012**: concept_to_domain_join_key
- [ ] **JOIN_013**: concept_to_concept_class_join_key
- [ ] **JOIN_014**: concept_relationship_to_relationship_join_key
- [ ] **JOIN_015**: concept_ancestor_to_concept_descendant_side
- [ ] **JOIN_016**: concept_ancestor_to_concept_for_name_resolution
- [ ] **JOIN_017**: concept_relationship_concept_id_1_to_concept
- [ ] **JOIN_018**: drug_exposure_to_drug_strength_join_key
- [ ] **JOIN_019**: note_nlp_to_note_join_key
- [ ] **JOIN_020**: clinical_tables_forbidden_direct_join_to_location
- [ ] **JOIN_021**: death_forbidden_join_to_visit_on_non_person_id
- [ ] **JOIN_022**: cohort_to_clinical_table_via_subject_id_to_person_id
- [ ] **JOIN_023**: observation_period_to_clinical_requires_person_id_and_dates
- [ ] **JOIN_024**: era_table_forbidden_join_to_visit_occurrence
- [ ] **JOIN_025**: cost_to_clinical_table_requires_event_id_and_domain
- [ ] **JOIN_026**: person_id_cross_matched_to_non_person_id_pk
- [ ] **JOIN_027**: visit_occurrence_id_cross_matched_to_non_visit_id
- [ ] **JOIN_028**: forbidden_clinical_to_clinical_pk_cross_join
- [ ] **JOIN_029**: concept_synonym_to_concept_join_key
- [ ] **JOIN_030**: payer_plan_period_to_clinical_requires_person_id_and_dates
- [ ] **JOIN_031**: fact_relationship_join_requires_domain_aware_polymorphic_key

---

## GAP (Miscellaneous) Rules (GAP_001-041)

- [ ] **GAP_001**: fact_relationship_requires_domain_concept_ids
- [ ] **GAP_002**: fact_relationship_fact_id_polymorphic_join
- [ ] **GAP_003**: fact_relationship_relationship_concept_id_required
- [ ] **GAP_004**: delete_truncate_on_clinical_tables
- [ ] **GAP_005**: between_inclusive_both_ends_with_datetime
- [ ] **GAP_006**: not_in_subquery_with_nullable_column
- [ ] **GAP_007**: note_encoding_concept_id_domain
- [ ] **GAP_008**: note_language_concept_id_domain
- [ ] **GAP_009**: note_title_not_for_clinical_filtering
- [ ] **GAP_010**: note_source_value_not_for_filtering
- [ ] **GAP_011**: note_nlp_section_concept_id_domain
- [ ] **GAP_012**: note_nlp_offset_is_character_position
- [ ] **GAP_013**: note_nlp_nlp_system_for_provenance
- [ ] **GAP_014**: note_nlp_term_modifiers_is_free_text
- [ ] **GAP_015**: note_nlp_nlp_date_for_temporal_filtering
- [ ] **GAP_016**: drug_strength_numerator_denominator_for_concentration
- [ ] **GAP_017**: drug_strength_unit_concept_ids_must_be_unit_domain
- [ ] **GAP_018**: specimen_source_values_not_for_analytical_filtering
- [ ] **GAP_019**: cost_amount_allowed_semantics
- [ ] **GAP_020**: cost_paid_ingredient_cost_drug_specific
- [ ] **GAP_021**: visit_detail_source_concept_id_not_for_filtering
- [ ] **GAP_022**: visit_detail_admitted_discharged_source_values_not_for_filtering
- [ ] **GAP_023**: visit_occurrence_admitted_discharged_source_values_not_for_filtering
- [ ] **GAP_024**: visit_occurrence_source_concept_id_not_for_visit_type
- [ ] **GAP_025**: person_source_concept_ids_not_for_demographic_filtering
- [ ] **GAP_026**: drug_exposure_dose_unit_source_value_is_free_text
- [ ] **GAP_027**: drug_exposure_route_source_value_not_for_filtering
- [ ] **GAP_028**: measurement_unit_source_concept_id_not_for_filtering
- [ ] **GAP_029**: measurement_value_source_value_is_free_text
- [ ] **GAP_030**: observation_value_source_value_is_free_text
- [ ] **GAP_031**: observation_qualifier_source_value_not_for_filtering
- [ ] **GAP_032**: condition_status_source_value_not_for_filtering
- [ ] **GAP_033**: death_type_concept_id_domain_constraint
- [ ] **GAP_034**: cost_type_concept_id_domain_constraint
- [ ] **GAP_035**: cross_join_from_comma_separated_tables
- [ ] **GAP_036**: union_vs_union_all_for_clinical_events
- [ ] **GAP_037**: ambiguous_column_reference_in_multi_table_query
- [ ] **GAP_038**: visit_detail_type_concept_id_is_provenance
- [ ] **GAP_039**: visit_detail_admitted_from_discharged_to_domain
- [ ] **GAP_040**: attribute_definition_is_legacy_table
- [ ] **GAP_041**: fact_relationship_table_columns_in_cdm

---

## Implementation Priority Recommendations

### High Priority (Core Correctness)
1. JOIN validation rules (JOIN_001-031) - Critical for data integrity
2. Person/Visit join rules (OMOP_004, OMOP_005)
3. Era table rules (OMOP_028, OMOP_029, OMOP_210-219)
4. Type concept rules (OMOP_014)
5. Clinical data constraints (CLIN_009-057)

### Medium Priority (Data Quality)
1. Source concept rules (OMOP_022)
2. Cohort rules (OMOP_024, OMOP_069, OMOP_514, etc.)
3. Measurement advanced rules (OMOP_231-239)
4. Vocabulary best practices (VOCAB_002-042)

### Lower Priority (Performance & Nice-to-Have)
1. Performance optimizations (OMOP_041, OMOP_602)
2. Advanced temporal rules (OMOP_538-559)
3. Episode & fact_relationship (OMOP_240-259)
4. GAP rules (GAP_001-041)

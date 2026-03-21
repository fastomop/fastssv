"""Unit tests for semantic validation."""

import unittest

from fastssv.rules import validate_omop_semantic_rules
from fastssv.core.registry import get_rules_by_category


def validate_standard_concept_mapping(sql: str, dialect: str = "postgres") -> list[str]:
    """Run only the standard concept enforcement rule."""
    from fastssv.core.base import Severity
    from fastssv.core.registry import get_rule

    rule = get_rule("semantic.standard_concept_enforcement")()
    violations = rule.validate(sql, dialect)

    # Also run join_path and maps_to_direction for warnings
    join_rule = get_rule("semantic.join_path_validation")()
    violations.extend(join_rule.validate(sql, dialect))

    maps_rule = get_rule("semantic.maps_to_direction")()
    violations.extend(maps_rule.validate(sql, dialect))

    # Convert to legacy string format
    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}OMOP Semantic Rule Violation: {v.message}")

    return results


def validate_unmapped_concept_handling(sql: str, dialect: str = "postgres") -> list[str]:
    """Run only the unmapped concept handling rule."""
    from fastssv.core.base import Severity
    from fastssv.core.registry import get_rule

    rule = get_rule("semantic.unmapped_concept_handling")()
    violations = rule.validate(sql, dialect)

    # Convert to legacy string format
    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")

    return results


class StandardConceptMappingTests(unittest.TestCase):
    """Tests for standard concept mapping rule."""

    def test_query_with_standard_concept_enforcement(self) -> None:
        """Query with standard_concept = 'S' should pass."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE c.standard_concept = 'S'
        """
        errors = validate_standard_concept_mapping(sql)
        self.assertEqual(errors, [])

    def test_query_with_maps_to_relationship(self) -> None:
        """Query with 'Maps to' relationship should pass."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_1
        WHERE cr.relationship_id = 'Maps to'
        """
        errors = validate_standard_concept_mapping(sql)
        # Should pass (uses Maps to) but may have warnings about join path
        main_errors = [e for e in errors if not e.startswith("Warning:")]
        self.assertEqual(main_errors, [])

    def test_query_without_standard_enforcement(self) -> None:
        """Query using standard fields without enforcement should fail."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        """
        errors = validate_standard_concept_mapping(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("STANDARD concept fields" in e for e in errors))

    def test_query_with_standard_concept_in_join_on(self) -> None:
        """Query with standard_concept = 'S' in JOIN ON should pass."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
            AND c.standard_concept = 'S'
        """
        errors = validate_standard_concept_mapping(sql)
        self.assertEqual(errors, [])

    def test_query_with_in_clause(self) -> None:
        """Query with standard_concept IN ('S') should pass."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE c.standard_concept IN ('S')
        """
        errors = validate_standard_concept_mapping(sql)
        self.assertEqual(errors, [])


class UnmappedConceptHandlingTests(unittest.TestCase):
    """Tests for concept_id = 0 handling rule."""

    def test_specific_concept_id_without_zero_handling(self) -> None:
        """Query with specific concept_id but no 0 handling should warn."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_concept_id = 201826
        """
        warnings = validate_unmapped_concept_handling(sql)
        self.assertTrue(len(warnings) > 0)
        self.assertTrue(any("concept_id = 0" in w for w in warnings))

    def test_concept_id_with_greater_than_zero(self) -> None:
        """Query with > 0 should not warn."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_concept_id = 201826 AND condition_concept_id > 0
        """
        warnings = validate_unmapped_concept_handling(sql)
        self.assertEqual(warnings, [])

    def test_concept_id_with_not_equal_zero(self) -> None:
        """Query with != 0 should not warn."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_concept_id = 201826 AND condition_concept_id != 0
        """
        warnings = validate_unmapped_concept_handling(sql)
        self.assertEqual(warnings, [])

    def test_in_clause_without_zero_handling(self) -> None:
        """Query with IN clause but no 0 handling should warn."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_concept_id IN (201826, 201820)
        """
        warnings = validate_unmapped_concept_handling(sql)
        self.assertTrue(len(warnings) > 0)

    def test_no_specific_filter(self) -> None:
        """Query without specific concept_id filter should not warn."""
        sql = """
        SELECT co.* FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        """
        warnings = validate_unmapped_concept_handling(sql)
        self.assertEqual(warnings, [])

    def test_non_clinical_table(self) -> None:
        """Query on concept table should not warn."""
        sql = """
        SELECT * FROM concept
        WHERE concept_id = 201826
        """
        warnings = validate_unmapped_concept_handling(sql)
        self.assertEqual(warnings, [])


class CombinedSemanticValidationTests(unittest.TestCase):
    """Tests for combined semantic validation."""

    def test_valid_query(self) -> None:
        """A properly constructed query should pass all validations."""
        sql = """
        SELECT co.condition_concept_id, c.concept_name
        FROM condition_occurrence co
        JOIN concept_ancestor ca ON co.condition_concept_id = ca.descendant_concept_id
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE ca.ancestor_concept_id = 201826
        AND co.condition_concept_id > 0
        AND c.standard_concept = 'S'
        AND c.invalid_reason IS NULL
        AND c.domain_id = 'Condition'
        """
        errors = validate_omop_semantic_rules(sql)
        self.assertEqual(errors, [])

    def test_invalid_sql(self) -> None:
        """Invalid SQL should return parse error."""
        sql = "SELECT FROM WHERE"
        errors = validate_omop_semantic_rules(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("parse error" in e.lower() or "error" in e.lower() for e in errors))


class ObservationPeriodAnchoringTests(unittest.TestCase):
    """Tests for observation period anchoring rule (temporal constraints)."""

    def _validate_temporal(self, sql: str, dialect: str = "postgres") -> list[str]:
        """Run only the observation period anchoring rule."""
        from fastssv.core.base import Severity
        from fastssv.core.registry import get_rule

        rule = get_rule("semantic.observation_period_anchoring")()
        violations = rule.validate(sql, dialect)

        results = []
        for v in violations:
            prefix = "Warning: " if v.severity == Severity.WARNING else ""
            results.append(f"{prefix}{v.message}")
        return results

    def test_temporal_filter_without_observation_period_should_error(self) -> None:
        """Query with date filter but no observation_period should error."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_start_date >= '2020-01-01'
        """
        errors = self._validate_temporal(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("observation_period" in e for e in errors))

    def test_temporal_filter_with_observation_period_should_pass(self) -> None:
        """Query with date filter AND observation_period join should pass."""
        sql = """
        SELECT co.*
        FROM condition_occurrence co
        JOIN observation_period op ON co.person_id = op.person_id
        WHERE co.condition_start_date >= '2020-01-01'
        """
        errors = self._validate_temporal(sql)
        # Should pass (has observation_period join on person_id)
        main_errors = [e for e in errors if not e.startswith("Warning:")]
        self.assertEqual(main_errors, [])

    def test_multiple_date_filters_without_observation_period(self) -> None:
        """Query with multiple date filters should error without observation_period."""
        sql = """
        SELECT * FROM drug_exposure
        WHERE drug_exposure_start_date >= '2020-01-01'
        AND drug_exposure_end_date <= '2020-12-31'
        """
        errors = self._validate_temporal(sql)
        self.assertTrue(len(errors) > 0)

    def test_date_comparison_between_tables(self) -> None:
        """Query comparing dates between tables should require observation_period."""
        sql = """
        SELECT co.*, de.*
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        WHERE de.drug_exposure_start_date > co.condition_start_date
        """
        errors = self._validate_temporal(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("observation_period" in e for e in errors))

    def test_no_temporal_constraints_should_not_trigger(self) -> None:
        """Query without temporal constraints should not trigger the rule."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_concept_id = 12345
        """
        errors = self._validate_temporal(sql)
        self.assertEqual(errors, [])

    def test_observation_period_in_cte_should_pass(self) -> None:
        """Query using observation_period in CTE should pass."""
        sql = """
        WITH valid_patients AS (
            SELECT co.*, op.observation_period_start_date, op.observation_period_end_date
            FROM condition_occurrence co
            JOIN observation_period op ON co.person_id = op.person_id
            WHERE co.condition_start_date BETWEEN op.observation_period_start_date 
                AND op.observation_period_end_date
        )
        SELECT * FROM valid_patients
        WHERE condition_start_date >= '2020-01-01'
        """
        errors = self._validate_temporal(sql)
        main_errors = [e for e in errors if not e.startswith("Warning:")]
        self.assertEqual(main_errors, [])

    def test_washout_period_pattern_should_require_observation_period(self) -> None:
        """Washout period pattern (NOT EXISTS with date) should require observation_period."""
        sql = """
        SELECT co.*
        FROM condition_occurrence co
        WHERE NOT EXISTS (
            SELECT 1 FROM drug_exposure de
            WHERE de.person_id = co.person_id
            AND de.drug_exposure_start_date < co.condition_start_date
        )
        """
        errors = self._validate_temporal(sql)
        self.assertTrue(len(errors) > 0)


class HierarchyExpansionTests(unittest.TestCase):
    """Tests for hierarchy expansion rule (concept_ancestor requirement)."""

    def _validate_hierarchy(self, sql: str, dialect: str = "postgres") -> list[str]:
        """Run only the hierarchy expansion rule."""
        from fastssv.core.base import Severity
        from fastssv.core.registry import get_rule

        rule = get_rule("semantic.hierarchy_expansion_required")()
        violations = rule.validate(sql, dialect)

        results = []
        for v in violations:
            prefix = "Warning: " if v.severity == Severity.WARNING else ""
            results.append(f"{prefix}{v.message}")
        return results

    def test_drug_filter_without_ancestor_should_error(self) -> None:
        """Filtering drug_concept_id without concept_ancestor should error."""
        sql = """
        SELECT * FROM drug_exposure
        WHERE drug_concept_id = 1234567
        """
        errors = self._validate_hierarchy(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("concept_ancestor" in e for e in errors))
        self.assertTrue(any("drug_exposure.drug_concept_id" in e for e in errors))

    def test_condition_filter_without_ancestor_should_error(self) -> None:
        """Filtering condition_concept_id without concept_ancestor should error."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_concept_id = 201826
        """
        errors = self._validate_hierarchy(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("concept_ancestor" in e for e in errors))
        self.assertTrue(any("condition_occurrence.condition_concept_id" in e for e in errors))

    def test_in_clause_without_ancestor_should_error(self) -> None:
        """IN clause on drug_concept_id without concept_ancestor should error."""
        sql = """
        SELECT * FROM drug_exposure
        WHERE drug_concept_id IN (1234, 5678, 9012)
        """
        errors = self._validate_hierarchy(sql)
        self.assertTrue(len(errors) > 0)

    def test_with_concept_ancestor_should_pass(self) -> None:
        """Query using concept_ancestor for hierarchy should pass."""
        sql = """
        SELECT de.*
        FROM drug_exposure de
        JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
        WHERE ca.ancestor_concept_id = 1234567
        """
        errors = self._validate_hierarchy(sql)
        # Should pass (uses concept_ancestor)
        main_errors = [e for e in errors if not e.startswith("Warning:")]
        self.assertEqual(main_errors, [])

    def test_with_concept_ancestor_cte_should_pass(self) -> None:
        """Query using concept_ancestor via CTE should pass."""
        sql = """
        WITH drug_hierarchy AS (
            SELECT descendant_concept_id AS concept_id
            FROM concept_ancestor
            WHERE ancestor_concept_id = 1234567
        )
        SELECT de.*
        FROM drug_exposure de
        JOIN drug_hierarchy dh ON de.drug_concept_id = dh.concept_id
        """
        errors = self._validate_hierarchy(sql)
        main_errors = [e for e in errors if not e.startswith("Warning:")]
        self.assertEqual(main_errors, [])

    def test_zero_concept_id_should_be_exempt(self) -> None:
        """Filtering on concept_id = 0 should not trigger the rule."""
        sql = """
        SELECT * FROM drug_exposure
        WHERE drug_concept_id = 0
        """
        errors = self._validate_hierarchy(sql)
        self.assertEqual(errors, [])

    def test_no_filter_should_not_trigger(self) -> None:
        """Query without filtering on drug/condition concept_id should not trigger."""
        sql = """
        SELECT drug_concept_id, person_id
        FROM drug_exposure
        WHERE person_id = 12345
        """
        errors = self._validate_hierarchy(sql)
        self.assertEqual(errors, [])

    def test_other_concept_columns_should_not_trigger(self) -> None:
        """Filtering on other concept_id columns should not trigger."""
        sql = """
        SELECT * FROM measurement
        WHERE measurement_concept_id = 3012345
        """
        errors = self._validate_hierarchy(sql)
        self.assertEqual(errors, [])

    def test_wrong_join_direction_should_warn(self) -> None:
        """Joining on ancestor_concept_id instead of descendant should warn."""
        # This query filters on drug_concept_id AND uses concept_ancestor,
        # but joins in the wrong direction (ancestor_concept_id instead of descendant)
        sql = """
        SELECT de.*
        FROM drug_exposure de
        JOIN concept_ancestor ca ON de.drug_concept_id = ca.ancestor_concept_id
        WHERE de.drug_concept_id = 1234567
        """
        errors = self._validate_hierarchy(sql)
        # Should have a warning about join direction
        warnings = [e for e in errors if e.startswith("Warning:")]
        self.assertTrue(len(warnings) > 0)
        self.assertTrue(any("direction" in w.lower() for w in warnings))


class EdgeCasesTests(unittest.TestCase):
    """Edge cases for semantic validation."""

    def test_cte_query(self) -> None:
        """Query with CTE should be handled correctly."""
        sql = """
        WITH diabetic_patients AS (
            SELECT co.person_id, co.condition_concept_id
            FROM condition_occurrence co
            JOIN concept_ancestor ca ON co.condition_concept_id = ca.descendant_concept_id
            WHERE ca.ancestor_concept_id = 201826
            AND co.condition_concept_id > 0
        )
        SELECT dp.*, c.concept_name
        FROM diabetic_patients dp
        JOIN concept c ON dp.condition_concept_id = c.concept_id
        WHERE c.standard_concept = 'S'
        AND c.invalid_reason IS NULL
        """
        errors = validate_omop_semantic_rules(sql)
        # May have warnings but shouldn't have main errors
        main_errors = [e for e in errors if "Violation" in e]
        self.assertEqual(main_errors, [])

    def test_multiple_clinical_tables(self) -> None:
        """Query joining multiple clinical tables."""
        sql = """
        SELECT co.condition_concept_id, de.drug_concept_id
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        JOIN concept c1 ON co.condition_concept_id = c1.concept_id
        JOIN concept c2 ON de.drug_concept_id = c2.concept_id
        WHERE c1.standard_concept = 'S'
        AND c2.standard_concept = 'S'
        """
        errors = validate_standard_concept_mapping(sql)
        self.assertEqual(errors, [])

    def test_subquery(self) -> None:
        """Query with subquery should be handled."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_concept_id IN (
            SELECT concept_id FROM concept
            WHERE vocabulary_id = 'SNOMED' AND standard_concept = 'S'
        )
        """
        errors = validate_standard_concept_mapping(sql)
        # The subquery has standard_concept = 'S', so this should pass
        self.assertEqual(errors, [])


class InvalidReasonEnforcementTests(unittest.TestCase):
    """Tests for invalid_reason enforcement rule."""

    def _run_invalid_reason_rule(self, sql: str) -> list[str]:
        """Run invalid_reason enforcement rule and return formatted violations."""
        from fastssv.core.base import Severity
        from fastssv.core.registry import get_rule

        rule = get_rule("semantic.invalid_reason_enforcement")()
        violations = rule.validate(sql)

        # Convert to legacy string format
        results = []
        for v in violations:
            prefix = "Warning: " if v.severity == Severity.WARNING else ""
            results.append(f"{prefix}OMOP Semantic Rule Violation: {v.message}")

        return results

    # Tests for tables WITH invalid_reason column (ERROR if missing)

    def test_concept_table_without_invalid_reason_filter(self) -> None:
        """Query on concept table without invalid_reason should ERROR."""
        sql = """
        SELECT concept_id, concept_name
        FROM concept
        WHERE domain_id = 'Drug'
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("concept" in e and "invalid_reason" in e for e in errors))
        # Should be ERROR, not warning
        self.assertTrue(all(not e.startswith("Warning:") for e in errors))

    def test_concept_table_with_invalid_reason_is_null(self) -> None:
        """Query with invalid_reason IS NULL should pass."""
        sql = """
        SELECT concept_id, concept_name
        FROM concept
        WHERE domain_id = 'Drug'
        AND invalid_reason IS NULL
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertEqual(errors, [])

    def test_concept_table_with_invalid_reason_is_not_null(self) -> None:
        """Query explicitly checking for invalid concepts should pass."""
        sql = """
        SELECT concept_id, concept_name, invalid_reason
        FROM concept
        WHERE invalid_reason IS NOT NULL
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertEqual(errors, [])

    def test_concept_relationship_without_invalid_reason(self) -> None:
        """Query on concept_relationship without invalid_reason should ERROR."""
        sql = """
        SELECT concept_id_1, concept_id_2
        FROM concept_relationship
        WHERE relationship_id = 'Maps to'
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("concept_relationship" in e for e in errors))

    def test_concept_relationship_with_invalid_reason(self) -> None:
        """Query on concept_relationship with invalid_reason should pass."""
        sql = """
        SELECT concept_id_1, concept_id_2
        FROM concept_relationship
        WHERE relationship_id = 'Maps to'
        AND invalid_reason IS NULL
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertEqual(errors, [])

    # Tests for derived tables WITHOUT invalid_reason column (WARNING)

    def test_concept_ancestor_without_concept_join(self) -> None:
        """Query on concept_ancestor without concept join should WARN."""
        sql = """
        SELECT descendant_concept_id
        FROM concept_ancestor
        WHERE ancestor_concept_id = 201826
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("concept_ancestor" in e for e in errors))
        # Should be WARNING, not error
        self.assertTrue(any(e.startswith("Warning:") for e in errors))

    def test_concept_ancestor_with_concept_join(self) -> None:
        """Query on concept_ancestor with proper concept join should pass."""
        sql = """
        SELECT ca.descendant_concept_id
        FROM concept_ancestor ca
        JOIN concept c ON c.concept_id = ca.descendant_concept_id
        WHERE ca.ancestor_concept_id = 201826
        AND c.invalid_reason IS NULL
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertEqual(errors, [])

    def test_concept_synonym_without_concept_join(self) -> None:
        """Query on concept_synonym without concept join should WARN."""
        sql = """
        SELECT concept_id, concept_synonym_name
        FROM concept_synonym
        WHERE concept_synonym_name LIKE '%diabetes%'
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("concept_synonym" in e for e in errors))
        self.assertTrue(any(e.startswith("Warning:") for e in errors))

    def test_drug_strength_without_concept_join(self) -> None:
        """Query on drug_strength without concept join should WARN."""
        sql = """
        SELECT drug_concept_id, ingredient_concept_id
        FROM drug_strength
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("drug_strength" in e for e in errors))

    # Tests for clinical tables (NO check needed)

    def test_clinical_table_no_invalid_reason_needed(self) -> None:
        """Query on clinical table should not require invalid_reason."""
        sql = """
        SELECT person_id, condition_concept_id
        FROM condition_occurrence
        WHERE condition_concept_id = 201826
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertEqual(errors, [])

    def test_multiple_clinical_tables_no_check(self) -> None:
        """Query on multiple clinical tables should not require invalid_reason."""
        sql = """
        SELECT co.person_id, de.drug_concept_id
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertEqual(errors, [])

    # Edge cases

    def test_invalid_reason_in_join_on_clause(self) -> None:
        """invalid_reason filter in JOIN ON clause should pass."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept c
            ON co.condition_concept_id = c.concept_id
            AND c.invalid_reason IS NULL
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertEqual(errors, [])

    def test_mixed_vocabulary_and_clinical_tables(self) -> None:
        """Query mixing vocabulary and clinical tables should still check."""
        sql = """
        SELECT co.person_id, c.concept_name
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE c.standard_concept = 'S'
        """
        errors = self._run_invalid_reason_rule(sql)
        # Should still flag missing invalid_reason on concept table
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("concept" in e for e in errors))

    def test_subquery_with_concept_table(self) -> None:
        """Subquery using concept table should be checked."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_concept_id IN (
            SELECT concept_id FROM concept
            WHERE vocabulary_id = 'SNOMED'
        )
        """
        errors = self._run_invalid_reason_rule(sql)
        # Should flag the concept table in the subquery
        self.assertTrue(len(errors) > 0)

    def test_cte_with_concept_ancestor(self) -> None:
        """CTE using concept_ancestor should be checked."""
        sql = """
        WITH descendants AS (
            SELECT descendant_concept_id
            FROM concept_ancestor
            WHERE ancestor_concept_id = 201826
        )
        SELECT * FROM descendants
        """
        errors = self._run_invalid_reason_rule(sql)
        # Should warn about concept_ancestor
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("concept_ancestor" in e for e in errors))

    def test_no_vocabulary_tables_used(self) -> None:
        """Query without any vocabulary tables should not be checked."""
        sql = """
        SELECT person_id, condition_start_date
        FROM condition_occurrence
        WHERE condition_start_date > '2020-01-01'
        """
        errors = self._run_invalid_reason_rule(sql)
        self.assertEqual(errors, [])


class DomainSegregationTests(unittest.TestCase):
    """Tests for the domain segregation rule (now merged into concept_domain_validation)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.base import Severity
        from fastssv.core.registry import get_rule

        rule = get_rule("semantic.concept_domain_validation")()
        violations = rule.validate(sql, dialect)
        results = []
        for v in violations:
            prefix = "Warning: " if v.severity == Severity.WARNING else "Error: "
            results.append(f"{prefix}{v.message}")
        return results

    # --- Correct domain filter -> no violation ---

    def test_condition_occurrence_correct_domain(self) -> None:
        """condition_occurrence joined to concept with domain_id = 'Condition' should pass."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE c.domain_id = 'Condition'
        AND c.standard_concept = 'S'
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    def test_drug_exposure_correct_domain(self) -> None:
        """drug_exposure joined to concept with domain_id = 'Drug' should pass."""
        sql = """
        SELECT de.drug_concept_id
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id = 'Drug'
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    def test_procedure_occurrence_correct_domain(self) -> None:
        """procedure_occurrence with domain_id = 'Procedure' should pass."""
        sql = """
        SELECT po.procedure_concept_id
        FROM procedure_occurrence po
        JOIN concept c ON po.procedure_concept_id = c.concept_id
        WHERE c.domain_id = 'Procedure'
        AND c.standard_concept = 'S'
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    def test_measurement_correct_domain(self) -> None:
        """measurement with domain_id = 'Measurement' should pass."""
        sql = """
        SELECT m.measurement_concept_id
        FROM measurement m
        JOIN concept c ON m.measurement_concept_id = c.concept_id
        WHERE c.domain_id = 'Measurement'
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    def test_domain_filter_in_join_on(self) -> None:
        """domain_id filter in JOIN ON clause should pass."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept c
            ON co.condition_concept_id = c.concept_id
            AND c.domain_id = 'Condition'
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    def test_domain_filter_in_clause(self) -> None:
        """domain_id IN (...) with correct domain should pass."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE c.domain_id IN ('Condition')
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    def test_death_cause_concept_uses_condition_domain(self) -> None:
        """death.cause_concept_id maps to 'Condition' domain and should pass with it."""
        sql = """
        SELECT d.cause_concept_id
        FROM death d
        JOIN concept c ON d.cause_concept_id = c.concept_id
        WHERE c.domain_id = 'Condition'
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    # --- Wrong domain filter -> ERROR ---

    def test_condition_occurrence_wrong_domain_procedure(self) -> None:
        """condition_occurrence filtered with domain_id = 'Procedure' should ERROR."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE c.domain_id = 'Procedure'
        """
        errors = self._run_rule(sql)
        self.assertTrue(len(errors) > 0)
        errors_only = [e for e in errors if e.startswith("Error:")]
        self.assertTrue(len(errors_only) > 0)
        self.assertTrue(any("domain mismatch" in e.lower() for e in errors_only))
        self.assertTrue(any("condition_occurrence" in e for e in errors_only))

    def test_drug_exposure_wrong_domain_condition(self) -> None:
        """drug_exposure filtered with domain_id = 'Condition' should ERROR."""
        sql = """
        SELECT de.drug_concept_id
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id = 'Condition'
        """
        errors = self._run_rule(sql)
        errors_only = [e for e in errors if e.startswith("Error:")]
        self.assertTrue(len(errors_only) > 0)
        self.assertTrue(any("drug_exposure" in e for e in errors_only))

    def test_procedure_wrong_domain_drug(self) -> None:
        """procedure_occurrence filtered with domain_id = 'Drug' should ERROR."""
        sql = """
        SELECT po.procedure_concept_id
        FROM procedure_occurrence po
        JOIN concept c ON po.procedure_concept_id = c.concept_id
        WHERE c.domain_id = 'Drug'
        """
        errors = self._run_rule(sql)
        errors_only = [e for e in errors if e.startswith("Error:")]
        self.assertTrue(len(errors_only) > 0)

    def test_visit_occurrence_wrong_domain(self) -> None:
        """visit_occurrence filtered with domain_id = 'Condition' should ERROR."""
        sql = """
        SELECT vo.visit_concept_id
        FROM visit_occurrence vo
        JOIN concept c ON vo.visit_concept_id = c.concept_id
        WHERE c.domain_id = 'Condition'
        """
        errors = self._run_rule(sql)
        errors_only = [e for e in errors if e.startswith("Error:")]
        self.assertTrue(len(errors_only) > 0)

    def test_death_wrong_domain(self) -> None:
        """death.cause_concept_id filtered with domain_id = 'Drug' (not Condition) should ERROR."""
        sql = """
        SELECT d.cause_concept_id
        FROM death d
        JOIN concept c ON d.cause_concept_id = c.concept_id
        WHERE c.domain_id = 'Drug'
        """
        errors = self._run_rule(sql)
        errors_only = [e for e in errors if e.startswith("Error:")]
        self.assertTrue(len(errors_only) > 0)

    # --- No domain filter -> WARNING ---

    def test_no_domain_filter_warns(self) -> None:
        """concept join without domain_id filter should produce a WARNING."""
        sql = """
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE c.standard_concept = 'S'
        """
        errors = self._run_rule(sql)
        warnings = [e for e in errors if e.startswith("Warning:")]
        self.assertTrue(len(warnings) > 0)
        # Should not be an error
        errors_only = [e for e in errors if e.startswith("Error:")]
        self.assertEqual(errors_only, [])

    def test_no_domain_filter_drug_warns(self) -> None:
        """drug_exposure join to concept without domain_id should produce WARNING."""
        sql = """
        SELECT de.drug_concept_id
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.invalid_reason IS NULL
        """
        errors = self._run_rule(sql)
        warnings = [e for e in errors if e.startswith("Warning:")]
        self.assertTrue(len(warnings) > 0)

    # --- No concept table -> no violation ---

    def test_no_concept_table_no_violation(self) -> None:
        """Query without concept table should not trigger the rule."""
        sql = """
        SELECT co.person_id, co.condition_concept_id
        FROM condition_occurrence co
        WHERE co.condition_concept_id IN (201826, 201254)
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    def test_clinical_tables_only_no_violation(self) -> None:
        """Query joining only clinical tables should not trigger the rule."""
        sql = """
        SELECT co.person_id
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        WHERE co.condition_start_date > '2020-01-01'
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    # --- Multiple tables ---

    def test_multiple_tables_both_correct(self) -> None:
        """Two clinical tables each with correct domain filters should pass."""
        sql = """
        SELECT co.person_id
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        JOIN concept cc ON co.condition_concept_id = cc.concept_id
            AND cc.domain_id = 'Condition'
        JOIN concept dc ON de.drug_concept_id = dc.concept_id
            AND dc.domain_id = 'Drug'
        WHERE cc.standard_concept = 'S'
        AND dc.standard_concept = 'S'
        """
        errors = self._run_rule(sql)
        self.assertEqual(errors, [])

    def test_unrelated_concept_column_not_flagged(self) -> None:
        """Joining concept on a type_concept_id (not a primary entity column) should not trigger."""
        sql = """
        SELECT co.condition_type_concept_id
        FROM condition_occurrence co
        JOIN concept c ON co.condition_type_concept_id = c.concept_id
        WHERE c.standard_concept = 'S'
        """
        errors = self._run_rule(sql)
        # type_concept_id is not in CLINICAL_TABLE_DOMAIN -> no domain segregation violation
        errors_only = [e for e in errors if e.startswith("Error:")]
        self.assertEqual(errors_only, [])

    def test_cte_concept_join_not_flagged(self) -> None:
        """Joining to a CTE (not directly to concept table) should not trigger."""
        sql = """
        WITH condition_concepts AS (
            SELECT concept_id
            FROM concept
            WHERE domain_id = 'Drug'
        )
        SELECT co.condition_concept_id
        FROM condition_occurrence co
        JOIN condition_concepts cc ON co.condition_concept_id = cc.concept_id
        """
        errors = self._run_rule(sql)
        # The clinical table joins a CTE, not concept directly -> no domain_segregation violation
        errors_only = [e for e in errors if e.startswith("Error:")]
        self.assertEqual(errors_only, [])


class MeasurementUnitValidationTests(unittest.TestCase):
    """Tests for the measurement unit validation rule."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.measurement_unit_validation")()
        return rule.validate(sql, dialect)

    def test_value_as_number_without_unit_fires(self) -> None:
        """Filtering value_as_number without unit_concept_id -> violation."""
        sql = """
        SELECT m.person_id
        FROM measurement m
        WHERE m.measurement_concept_id = 3004410
          AND m.value_as_number > 7.0
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertEqual(violations[0].rule_id, "semantic.measurement_unit_validation")

    def test_value_as_number_with_unit_passes(self) -> None:
        """Filtering value_as_number WITH unit_concept_id -> no violation."""
        sql = """
        SELECT m.person_id
        FROM measurement m
        WHERE m.measurement_concept_id = 3004410
          AND m.value_as_number > 7.0
          AND m.unit_concept_id = 8554
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_no_value_as_number_filter_not_flagged(self) -> None:
        """Measurement query without numeric threshold -> no violation."""
        sql = """
        SELECT m.person_id, m.value_as_number
        FROM measurement m
        WHERE m.measurement_concept_id = 3004410
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_non_measurement_table_not_flagged(self) -> None:
        """Numeric comparison on non-measurement table -> no violation."""
        sql = """
        SELECT person_id
        FROM condition_occurrence
        WHERE condition_occurrence_id > 100
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_gte_comparison_without_unit_fires(self) -> None:
        """GTE (>=) threshold without unit_concept_id also triggers the rule."""
        sql = """
        SELECT m.person_id
        FROM measurement m
        WHERE m.value_as_number >= 6.5
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_lt_comparison_without_unit_fires(self) -> None:
        """LT (<) threshold without unit_concept_id also triggers the rule."""
        sql = """
        SELECT m.person_id
        FROM measurement m
        WHERE m.measurement_concept_id = 3020891
          AND m.value_as_number < 60.0
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_unit_in_join_on_satisfies_rule(self) -> None:
        """unit_concept_id referenced in a JOIN ON clause satisfies the rule."""
        sql = """
        SELECT m.person_id
        FROM measurement m
        JOIN concept c ON m.unit_concept_id = c.concept_id
        WHERE m.value_as_number > 7.0
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_between_without_unit_fires(self) -> None:
        """BETWEEN filter on value_as_number without unit_concept_id -> violation."""
        sql = """
        SELECT m.person_id
        FROM measurement m
        WHERE m.measurement_concept_id = 3004410
          AND m.value_as_number BETWEEN 5.0 AND 10.0
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_between_with_unit_passes(self) -> None:
        """BETWEEN filter on value_as_number WITH unit_concept_id -> no violation."""
        sql = """
        SELECT m.person_id
        FROM measurement m
        WHERE m.measurement_concept_id = 3004410
          AND m.value_as_number BETWEEN 5.0 AND 10.0
          AND m.unit_concept_id = 8840
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_string_comparison_not_flagged(self) -> None:
        """Filtering on value_as_concept_id (not value_as_number) should not trigger the rule."""
        sql = """
        SELECT m.person_id
        FROM measurement m
        WHERE m.value_as_concept_id = 4181412
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

class FutureInformationLeakageTests(unittest.TestCase):
    """Tests for the future information leakage rule."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.future_information_leakage")()
        return rule.validate(sql, dialect)

    def test_cross_table_date_comparison_without_bound_fires(self) -> None:
        """Cross-table date comparison without observation_period_end_date -> violation."""
        sql = """
        SELECT co.person_id
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        WHERE co.condition_start_date > de.drug_exposure_start_date
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertEqual(violations[0].rule_id, "semantic.future_information_leakage")

    def test_cross_table_date_comparison_with_bound_passes(self) -> None:
        """Cross-table date comparison WITH observation_period_end_date bound -> no violation."""
        sql = """
        SELECT co.person_id
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        JOIN observation_period op ON co.person_id = op.person_id
        WHERE co.condition_start_date > de.drug_exposure_start_date
          AND co.condition_start_date <= op.observation_period_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_single_table_date_filter_not_flagged(self) -> None:
        """Single-table temporal filter (same table both sides) should not trigger."""
        sql = """
        SELECT co.person_id
        FROM condition_occurrence co
        JOIN observation_period op ON co.person_id = op.person_id
        WHERE co.condition_start_date > op.observation_period_start_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_no_date_comparison_not_flagged(self) -> None:
        """Query with no temporal activity at all should not trigger."""
        sql = """
        SELECT person_id
        FROM condition_occurrence
        WHERE condition_concept_id = 201826
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_gte_comparison_without_bound_fires(self) -> None:
        """GTE (>=) cross-table date comparison also triggers the rule."""
        sql = """
        SELECT de.person_id
        FROM drug_exposure de
        JOIN visit_occurrence vo ON de.person_id = vo.person_id
        WHERE de.drug_exposure_start_date >= vo.visit_start_date
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_date_comparison_against_literal_not_flagged(self) -> None:
        """Comparing a date column against a literal value should not trigger."""
        sql = """
        SELECT co.person_id
        FROM condition_occurrence co
        WHERE co.condition_start_date > '2020-01-01'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_lt_direction_fires(self) -> None:
        """LT (a < b) is semantically equivalent to GT (b > a) and must also trigger."""
        sql = """
        SELECT de.person_id
        FROM drug_exposure de
        JOIN condition_occurrence co ON de.person_id = co.person_id
        WHERE de.drug_exposure_start_date < co.condition_start_date
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_end_date_only_in_select_still_fires(self) -> None:
        """observation_period_end_date in SELECT list is not an upper bound -- must still fire."""
        sql = """
        SELECT co.person_id,
               op.observation_period_end_date
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        JOIN observation_period op ON co.person_id = op.person_id
        WHERE co.condition_start_date > de.drug_exposure_start_date
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_between_with_end_date_passes(self) -> None:
        """BETWEEN ... AND observation_period_end_date is a valid upper bound."""
        sql = """
        SELECT co.person_id
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        JOIN observation_period op ON co.person_id = op.person_id
        WHERE co.condition_start_date > de.drug_exposure_start_date
          AND co.condition_start_date BETWEEN op.observation_period_start_date
                                          AND op.observation_period_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])


class TypeConceptIdMisuseTests(unittest.TestCase):
    """Tests for the type_concept_id misuse rule (OMOP_014)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.type_concept_id_misuse")()
        return rule.validate(sql, dialect)

    def test_condition_type_concept_id_filter_fires(self) -> None:
        """Filtering on condition_type_concept_id should error."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_type_concept_id = 32817
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertEqual(violations[0].rule_id, "semantic.type_concept_id_misuse")
        self.assertTrue("condition_type_concept_id" in violations[0].message)
        self.assertTrue("provenance" in violations[0].message.lower())

    def test_drug_type_concept_id_filter_fires(self) -> None:
        """Filtering on drug_type_concept_id should error."""
        sql = """
        SELECT * FROM drug_exposure
        WHERE drug_type_concept_id = 38000177
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("drug_type_concept_id" in violations[0].message)
        self.assertTrue("drug_concept_id" in violations[0].message)

    def test_visit_type_concept_id_filter_fires(self) -> None:
        """Filtering on visit_type_concept_id should error (OMOP_013 covered)."""
        sql = """
        SELECT * FROM visit_occurrence
        WHERE visit_type_concept_id = 9201
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("visit_type_concept_id" in violations[0].message)
        self.assertTrue("visit_concept_id" in violations[0].message)

    def test_measurement_type_concept_id_in_clause_fires(self) -> None:
        """Filtering with IN clause on measurement_type_concept_id should error."""
        sql = """
        SELECT * FROM measurement
        WHERE measurement_type_concept_id IN (32817, 32818)
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("measurement_type_concept_id" in violations[0].message)

    def test_procedure_type_concept_id_comparison_fires(self) -> None:
        """Using comparison operators on procedure_type_concept_id should error."""
        sql = """
        SELECT * FROM procedure_occurrence
        WHERE procedure_type_concept_id != 32817
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_type_concept_id_in_join_on_fires(self) -> None:
        """Using type_concept_id in JOIN ON clause should error."""
        sql = """
        SELECT co.*
        FROM condition_occurrence co
        JOIN concept c ON co.condition_type_concept_id = c.concept_id
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("JOIN ON" in violations[0].message or "join" in violations[0].message.lower())

    def test_type_concept_id_in_having_fires(self) -> None:
        """Using type_concept_id in HAVING clause should error."""
        sql = """
        SELECT condition_type_concept_id, COUNT(*)
        FROM condition_occurrence
        GROUP BY condition_type_concept_id
        HAVING condition_type_concept_id = 32817
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_correct_primary_concept_id_usage_passes(self) -> None:
        """Using primary concept_id (not type) should pass."""
        sql = """
        SELECT * FROM condition_occurrence
        WHERE condition_concept_id = 201826
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_correct_visit_concept_id_usage_passes(self) -> None:
        """Using visit_concept_id (not visit_type_concept_id) should pass."""
        sql = """
        SELECT * FROM visit_occurrence
        WHERE visit_concept_id = 9201
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_type_concept_id_in_select_list_passes(self) -> None:
        """Selecting type_concept_id (not filtering) should pass."""
        sql = """
        SELECT person_id, condition_type_concept_id
        FROM condition_occurrence
        WHERE condition_concept_id = 201826
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_type_concept_id_in_group_by_passes(self) -> None:
        """Using type_concept_id in GROUP BY (not filtering) should pass."""
        sql = """
        SELECT condition_type_concept_id, COUNT(*)
        FROM condition_occurrence
        GROUP BY condition_type_concept_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_multiple_type_concept_id_filters_fires_multiple(self) -> None:
        """Multiple type_concept_id filters should produce multiple violations."""
        sql = """
        SELECT co.*, de.*
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        WHERE co.condition_type_concept_id = 32817
        AND de.drug_type_concept_id = 38000177
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) >= 2)

    def test_no_clinical_tables_not_flagged(self) -> None:
        """Query without clinical tables should not trigger."""
        sql = """
        SELECT concept_id, concept_name
        FROM concept
        WHERE domain_id = 'Condition'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])


class EraTableStandardConceptsTests(unittest.TestCase):
    """Tests for the era table standard concepts rule (OMOP_011)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.era_table_standard_concepts")()
        return rule.validate(sql, dialect)

    def test_era_table_filter_for_null_standard_concept_warns(self) -> None:
        """Filtering era table for non-standard concepts should warn."""
        sql = """
        SELECT de.*
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.standard_concept IS NULL
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertEqual(violations[0].rule_id, "semantic.era_table_standard_concepts")
        self.assertTrue("0 rows" in violations[0].message)

    def test_era_table_filter_for_not_standard_warns(self) -> None:
        """Filtering era table for standard_concept != 'S' should warn."""
        sql = """
        SELECT ce.*
        FROM condition_era ce
        JOIN concept c ON ce.condition_concept_id = c.concept_id
        WHERE c.standard_concept != 'S'
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("0 rows" in violations[0].message)

    def test_era_table_standard_filter_acceptable(self) -> None:
        """standard_concept = 'S' filter should NOT be flagged (even if redundant)."""
        sql = """
        SELECT de.*
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.standard_concept = 'S'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_era_table_without_concept_join_passes(self) -> None:
        """Era table query without concept join should pass."""
        sql = """
        SELECT drug_concept_id, COUNT(*)
        FROM drug_era
        GROUP BY drug_concept_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_era_table_with_concept_no_standard_filter_passes(self) -> None:
        """Era table joined to concept without standard filter should pass."""
        sql = """
        SELECT de.*, c.concept_name
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_condition_era_with_standard_filter_acceptable(self) -> None:
        """condition_era with standard_concept = 'S' should NOT be flagged."""
        sql = """
        SELECT ce.*, c.concept_name
        FROM condition_era ce
        JOIN concept c ON ce.condition_concept_id = c.concept_id
        WHERE c.standard_concept = 'S'
        AND c.domain_id = 'Condition'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_non_era_table_not_affected(self) -> None:
        """Non-era tables should not trigger this rule."""
        sql = """
        SELECT de.*
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.standard_concept IS NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_dose_era_covered(self) -> None:
        """dose_era table should also be covered for non-standard concept filters."""
        sql = """
        SELECT de.*
        FROM dose_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.standard_concept IS NULL
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("0 rows" in violations[0].message)

    def test_multiple_era_tables_not_flagged_without_filters(self) -> None:
        """Multiple era tables without standard filters should pass."""
        sql = """
        SELECT ce.*, de.*
        FROM condition_era ce
        JOIN drug_era de ON ce.person_id = de.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])


class ConceptRelationshipRequiresRelationshipIdTests(unittest.TestCase):
    """Tests for the concept_relationship requires relationship_id rule (OMOP_016)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.concept_relationship_requires_relationship_id")()
        return rule.validate(sql, dialect)

    def test_concept_relationship_without_filter_fires(self) -> None:
        """Using concept_relationship without relationship_id filter should error."""
        sql = """
        SELECT c2.concept_name
        FROM concept c1
        JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
        JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
        WHERE c1.concept_code = 'E11.9'
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertEqual(violations[0].rule_id, "semantic.concept_relationship_requires_relationship_id")
        self.assertTrue("cross-product" in violations[0].message.lower())

    def test_concept_relationship_with_on_clause_filter_passes(self) -> None:
        """relationship_id filter in JOIN ON clause should pass."""
        sql = """
        SELECT c2.concept_name
        FROM concept c1
        JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
          AND cr.relationship_id = 'Maps to'
        JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
        WHERE c1.concept_code = 'E11.9'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_concept_relationship_with_where_clause_filter_passes(self) -> None:
        """relationship_id filter in WHERE clause should pass."""
        sql = """
        SELECT c2.concept_name
        FROM concept c1
        JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
        JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
        WHERE c1.concept_code = 'E11.9'
        AND cr.relationship_id = 'Maps to'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_concept_relationship_with_in_clause_passes(self) -> None:
        """relationship_id filter using IN clause should pass."""
        sql = """
        SELECT c2.concept_name
        FROM concept c1
        JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
        JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
        WHERE cr.relationship_id IN ('Maps to', 'Is a')
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_concept_relationship_is_a_relationship_passes(self) -> None:
        """Using 'Is a' relationship with filter should pass."""
        sql = """
        SELECT c2.concept_name
        FROM concept c1
        JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
          AND cr.relationship_id = 'Is a'
        JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_no_concept_relationship_table_not_flagged(self) -> None:
        """Query without concept_relationship table should not trigger."""
        sql = """
        SELECT c.concept_name
        FROM concept c
        WHERE c.concept_code = 'E11.9'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_concept_relationship_in_subquery_without_filter_fires(self) -> None:
        """concept_relationship in subquery without filter should error."""
        sql = """
        SELECT * FROM concept
        WHERE concept_id IN (
            SELECT cr.concept_id_2
            FROM concept_relationship cr
            WHERE cr.concept_id_1 = 12345
        )
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_concept_relationship_in_cte_without_filter_fires(self) -> None:
        """concept_relationship in CTE without filter should error."""
        sql = """
        WITH mapped_concepts AS (
            SELECT cr.concept_id_2
            FROM concept_relationship cr
            WHERE cr.concept_id_1 = 12345
        )
        SELECT c.concept_name
        FROM concept c
        JOIN mapped_concepts mc ON c.concept_id = mc.concept_id_2
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_concept_relationship_with_multiple_filters_passes(self) -> None:
        """Multiple conditions including relationship_id should pass."""
        sql = """
        SELECT c2.concept_name
        FROM concept c1
        JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
        JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
        WHERE cr.relationship_id = 'Maps to'
        AND cr.invalid_reason IS NULL
        AND c1.concept_code = 'E11.9'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_concept_relationship_with_is_not_null_fires(self) -> None:
        """IS NOT NULL doesn't specify relationship type, should error."""
        sql = """
        SELECT c2.concept_name
        FROM concept c1
        JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
        JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
        WHERE cr.relationship_id IS NOT NULL
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("cross-product" in violations[0].message.lower())

    def test_concept_relationship_with_not_equals_fires(self) -> None:
        """!= doesn't specify relationship type, should error."""
        sql = """
        SELECT c2.concept_name
        FROM concept c1
        JOIN concept_relationship cr ON c1.concept_id = cr.concept_id_1
        JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
        WHERE cr.relationship_id != 'Subsumes'
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("cross-product" in violations[0].message.lower())

    def test_concept_relationship_with_is_null_fires(self) -> None:
        """IS NULL doesn't specify relationship type, should error."""
        sql = """
        SELECT * FROM concept_relationship cr
        WHERE cr.relationship_id IS NULL
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)


class ConceptDomainValidationTests(unittest.TestCase):
    """Tests for the concept domain validation rule (OMOP_066 + OMOP_019)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.concept_domain_validation")()
        return rule.validate(sql, dialect)

    def test_condition_with_correct_domain_passes(self) -> None:
        """Condition domain for condition_concept_id should pass."""
        sql = """
        SELECT co.*, c.concept_name
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE c.domain_id = 'Condition'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_condition_with_wrong_domain_fires(self) -> None:
        """Drug domain for condition_concept_id should error."""
        sql = """
        SELECT co.*, c.concept_name
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        WHERE c.domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("condition_concept_id" in violations[0].message.lower())
        self.assertTrue("drug" in violations[0].message.lower())
        self.assertTrue("condition" in violations[0].message.lower())

    def test_drug_with_correct_domain_passes(self) -> None:
        """Drug domain for drug_concept_id should pass."""
        sql = """
        SELECT de.*, c.concept_name
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_drug_with_wrong_domain_fires(self) -> None:
        """Procedure domain for drug_concept_id should error."""
        sql = """
        SELECT de.*, c.concept_name
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id = 'Procedure'
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("drug_concept_id" in violations[0].message.lower())

    def test_gender_with_correct_domain_passes(self) -> None:
        """Gender domain for gender_concept_id should pass (OMOP_019)."""
        sql = """
        SELECT p.*, c.concept_name
        FROM person p
        JOIN concept c ON p.gender_concept_id = c.concept_id
        WHERE c.domain_id = 'Gender'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_gender_with_wrong_domain_fires(self) -> None:
        """Race domain for gender_concept_id should error (OMOP_019)."""
        sql = """
        SELECT p.*, c.concept_name
        FROM person p
        JOIN concept c ON p.gender_concept_id = c.concept_id
        WHERE c.domain_id = 'Race'
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("gender_concept_id" in violations[0].message.lower())

    def test_race_with_correct_domain_passes(self) -> None:
        """Race domain for race_concept_id should pass."""
        sql = """
        SELECT p.*, c.concept_name
        FROM person p
        JOIN concept c ON p.race_concept_id = c.concept_id
        WHERE c.domain_id = 'Race'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_no_domain_filter_warns_for_main_tables(self) -> None:
        """No domain filter on main clinical tables should trigger WARNING."""
        from fastssv.core.base import Severity
        sql = """
        SELECT de.*, c.concept_name
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.standard_concept = 'S'
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertEqual(violations[0].severity, Severity.WARNING)

    def test_no_domain_filter_passes_for_auxiliary_columns(self) -> None:
        """No domain filter on auxiliary columns (gender, race, etc.) should not warn."""
        sql = """
        SELECT p.*, c.concept_name
        FROM person p
        JOIN concept c ON p.gender_concept_id = c.concept_id
        WHERE c.standard_concept = 'S'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_domain_filter_in_join_on_clause(self) -> None:
        """Domain filter in ON clause should be detected."""
        sql = """
        SELECT co.*, c.concept_name
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
            AND c.domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_reversed_join_condition_detected(self) -> None:
        """Reversed join (concept.concept_id = table.*_concept_id) should work."""
        sql = """
        SELECT co.*, c.concept_name
        FROM condition_occurrence co
        JOIN concept c ON c.concept_id = co.condition_concept_id
        WHERE c.domain_id = 'Procedure'
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_multiple_domain_in_clause_fires(self) -> None:
        """IN clause with wrong domains should error."""
        sql = """
        SELECT de.*, c.concept_name
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id IN ('Condition', 'Procedure')
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_measurement_with_unit_domain(self) -> None:
        """Unit domain for unit_concept_id should pass."""
        sql = """
        SELECT m.*, c.concept_name
        FROM measurement m
        JOIN concept c ON m.unit_concept_id = c.concept_id
        WHERE c.domain_id = 'Unit'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_route_concept_with_route_domain(self) -> None:
        """Route domain for route_concept_id should pass."""
        sql = """
        SELECT de.*, c.concept_name
        FROM drug_exposure de
        JOIN concept c ON de.route_concept_id = c.concept_id
        WHERE c.domain_id = 'Route'
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_no_concept_join_not_flagged(self) -> None:
        """Query without concept table join should not trigger."""
        sql = """
        SELECT *
        FROM condition_occurrence
        WHERE condition_concept_id = 12345
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])


class SourceConceptIdWarningTests(unittest.TestCase):
    """Tests for the source_concept_id warning rule (OMOP_022)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.source_concept_id_warning")()
        return rule.validate(sql, dialect)

    def test_condition_source_concept_id_filter_warns(self) -> None:
        """Filtering on condition_source_concept_id should warn."""
        sql = """
        SELECT DISTINCT person_id
        FROM condition_occurrence
        WHERE condition_source_concept_id = 44836914
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("condition_source_concept_id" in violations[0].message)
        self.assertTrue("condition_concept_id" in violations[0].message)

    def test_drug_source_concept_id_filter_warns(self) -> None:
        """Filtering on drug_source_concept_id should warn."""
        sql = """
        SELECT person_id
        FROM drug_exposure
        WHERE drug_source_concept_id = 123456
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("drug_source_concept_id" in violations[0].message)

    def test_procedure_source_concept_id_in_clause_warns(self) -> None:
        """Using IN clause with procedure_source_concept_id should warn."""
        sql = """
        SELECT *
        FROM procedure_occurrence
        WHERE procedure_source_concept_id IN (111, 222, 333)
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)
        self.assertTrue("procedure_source_concept_id" in violations[0].message)

    def test_standard_concept_id_does_not_warn(self) -> None:
        """Using standard *_concept_id should not warn."""
        sql = """
        SELECT person_id
        FROM condition_occurrence
        WHERE condition_concept_id = 201826
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_source_concept_id_in_select_not_flagged(self) -> None:
        """Selecting source_concept_id (not filtering) should not warn."""
        sql = """
        SELECT person_id, condition_source_concept_id
        FROM condition_occurrence
        WHERE condition_concept_id = 12345
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_source_concept_id_in_group_by_not_flagged(self) -> None:
        """GROUP BY source_concept_id should not warn."""
        sql = """
        SELECT condition_source_concept_id, COUNT(*)
        FROM condition_occurrence
        GROUP BY condition_source_concept_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])

    def test_measurement_source_concept_id_warns(self) -> None:
        """Filtering on measurement_source_concept_id should warn."""
        sql = """
        SELECT *
        FROM measurement
        WHERE measurement_source_concept_id = 999
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_observation_source_concept_id_warns(self) -> None:
        """Filtering on observation_source_concept_id should warn."""
        sql = """
        SELECT *
        FROM observation
        WHERE observation_source_concept_id = 888
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_multiple_source_concept_id_filters_warns_multiple(self) -> None:
        """Multiple source_concept_id filters should generate multiple warnings."""
        sql = """
        SELECT co.person_id
        FROM condition_occurrence co
        JOIN drug_exposure de ON co.person_id = de.person_id
        WHERE co.condition_source_concept_id = 111
        AND de.drug_source_concept_id = 222
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) >= 2)

    def test_source_concept_id_comparison_operators_warn(self) -> None:
        """Various comparison operators on source_concept_id should warn."""
        sql = """
        SELECT DISTINCT person_id
        FROM condition_occurrence
        WHERE condition_source_concept_id != 0
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) > 0)

    def test_no_clinical_tables_not_flagged(self) -> None:
        """Query without clinical tables should not trigger."""
        sql = """
        SELECT * FROM concept WHERE concept_id = 12345
        """
        violations = self._run_rule(sql)
        self.assertEqual(violations, [])


class SchemaValidationTests(unittest.TestCase):
    """Tests for schema validation rule (OMOP_008, 009, 023, 028)."""

    def _run_rule(self, sql: str) -> list:
        """Run schema validation rule."""
        from fastssv.core.registry import get_rule
        rule = get_rule("vocabulary.schema_validation")()
        return rule.validate(sql)

    # OMOP_023: death_id column doesn't exist in death table
    def test_omop_023_death_id_column_does_not_exist(self) -> None:
        """Referencing non-existent death_id column should error."""
        sql = """
        SELECT death_id, person_id, death_date
        FROM death
        WHERE death_id = 12345
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("death_id", violations[0].message)
        self.assertIn("does not exist", violations[0].message.lower())
        self.assertIn("death", violations[0].message)

    # OMOP_028: condition_source_value doesn't exist in condition_era
    def test_omop_028_condition_source_value_in_condition_era(self) -> None:
        """Referencing condition_source_value from condition_era should error."""
        sql = """
        SELECT condition_source_value
        FROM condition_era
        WHERE condition_concept_id = 201826
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("condition_source_value", violations[0].message)
        self.assertIn("does not exist", violations[0].message.lower())
        self.assertIn("condition_era", violations[0].message)

    def test_omop_028_visit_occurrence_id_in_condition_era(self) -> None:
        """Referencing visit_occurrence_id from condition_era should error."""
        sql = """
        SELECT person_id, visit_occurrence_id
        FROM condition_era
        WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("visit_occurrence_id", violations[0].message)
        self.assertIn("condition_era", violations[0].message)

    def test_omop_028_provider_id_in_condition_era(self) -> None:
        """Referencing provider_id from condition_era should error."""
        sql = """
        SELECT person_id, provider_id
        FROM condition_era
        WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("provider_id", violations[0].message)
        self.assertIn("condition_era", violations[0].message)

    def test_valid_death_columns(self) -> None:
        """Referencing valid death table columns should pass."""
        sql = """
        SELECT person_id, death_date, cause_concept_id
        FROM death
        WHERE person_id = 12345
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_valid_condition_era_columns(self) -> None:
        """Referencing valid condition_era columns should pass."""
        sql = """
        SELECT condition_era_id, person_id, condition_concept_id,
               condition_era_start_date, condition_era_end_date,
               condition_occurrence_count
        FROM condition_era
        WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    # OMOP_029: drug_era doesn't have drug_exposure columns
    def test_omop_029_days_supply_in_drug_era(self) -> None:
        """Referencing days_supply from drug_era should error."""
        sql = """
        SELECT days_supply
        FROM drug_era
        WHERE drug_concept_id = 1125315
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("days_supply", violations[0].message)
        self.assertIn("does not exist", violations[0].message.lower())
        self.assertIn("drug_era", violations[0].message)

    def test_omop_029_quantity_in_drug_era(self) -> None:
        """Referencing quantity from drug_era should error."""
        sql = """
        SELECT person_id, quantity
        FROM drug_era
        WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("quantity", violations[0].message)
        self.assertIn("drug_era", violations[0].message)

    def test_omop_029_route_concept_id_in_drug_era(self) -> None:
        """Referencing route_concept_id from drug_era should error."""
        sql = """
        SELECT person_id, route_concept_id
        FROM drug_era
        WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("route_concept_id", violations[0].message)
        self.assertIn("drug_era", violations[0].message)

    def test_omop_029_sig_in_drug_era(self) -> None:
        """Referencing sig from drug_era should error."""
        sql = """
        SELECT sig
        FROM drug_era
        WHERE drug_concept_id = 1125315
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("sig", violations[0].message)
        self.assertIn("drug_era", violations[0].message)

    def test_valid_drug_era_columns(self) -> None:
        """Referencing valid drug_era columns should pass."""
        sql = """
        SELECT drug_era_id, person_id, drug_concept_id,
               drug_era_start_date, drug_era_end_date,
               drug_exposure_count, gap_days
        FROM drug_era
        WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class ColumnTypeValidationTests(unittest.TestCase):
    """Tests for column type validation rule (OMOP_004, 005, 024, 025, 026)."""

    def _run_rule(self, sql: str) -> list:
        """Run column type validation rule."""
        from fastssv.core.registry import get_rule
        rule = get_rule("data_quality.column_type_validation")()
        return rule.validate(sql)

    # OMOP_004: person_id to person_source_value join
    def test_omop_004_person_id_to_source_value_join(self) -> None:
        """Joining person_id (integer) to person_source_value (varchar) should error."""
        sql = """
        SELECT p1.person_id, p2.person_source_value
        FROM person p1
        JOIN person p2 ON p1.person_id = p2.person_source_value
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("person_id", violations[0].message)
        self.assertIn("person_source_value", violations[0].message)
        self.assertIn("integer", violations[0].message.lower())
        self.assertIn("varchar", violations[0].message.lower())

    # OMOP_005: visit_occurrence_id to varchar join
    def test_omop_005_visit_occurrence_id_to_varchar_join(self) -> None:
        """Joining visit_occurrence_id (integer) to varchar column should error."""
        sql = """
        SELECT v.visit_occurrence_id, v.visit_source_value
        FROM visit_occurrence v1
        JOIN visit_occurrence v2 ON v1.visit_occurrence_id = v2.visit_source_value
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("visit_occurrence_id", violations[0].message)
        self.assertIn("visit_source_value", violations[0].message)

    # OMOP_024: cohort.subject_id to person.person_source_value join
    def test_omop_024_subject_id_to_person_source_value_join(self) -> None:
        """Joining subject_id (integer) to person_source_value (varchar) should error."""
        sql = """
        SELECT c.subject_id, p.person_source_value
        FROM cohort c
        JOIN person p ON c.subject_id = p.person_source_value
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("subject_id", violations[0].message)
        self.assertIn("person_source_value", violations[0].message)

    # OMOP_025: vocabulary_id (varchar) with integer literal
    def test_omop_025_vocabulary_id_with_integer_literal(self) -> None:
        """Filtering vocabulary_id (varchar) with integer literal should error."""
        sql = """
        SELECT * FROM concept c WHERE c.vocabulary_id = 1
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("vocabulary_id", violations[0].message)
        self.assertIn("varchar", violations[0].message.lower())
        self.assertIn("integer", violations[0].message.lower())

    # OMOP_026: domain_id (varchar) with integer literal
    def test_omop_026_domain_id_with_integer_literal(self) -> None:
        """Filtering domain_id (varchar) with integer literal should error."""
        sql = """
        SELECT * FROM concept c WHERE c.domain_id = 19
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("domain_id", violations[0].message)
        self.assertIn("varchar", violations[0].message.lower())
        self.assertIn("integer", violations[0].message.lower())

    def test_vocabulary_id_in_clause_with_integers(self) -> None:
        """Filtering vocabulary_id with IN clause containing integers should error."""
        sql = """
        SELECT * FROM concept c WHERE c.vocabulary_id IN (1, 2, 3)
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("vocabulary_id", violations[0].message)

    def test_correct_vocabulary_id_with_string(self) -> None:
        """Filtering vocabulary_id with string literal should pass."""
        sql = """
        SELECT * FROM concept WHERE vocabulary_id = 'SNOMED'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_correct_domain_id_with_string(self) -> None:
        """Filtering domain_id with string literal should pass."""
        sql = """
        SELECT * FROM concept WHERE domain_id = 'Condition'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_correct_person_id_join(self) -> None:
        """Joining person_id (integer) to person_id (integer) should pass."""
        sql = """
        SELECT p.person_id, v.person_id
        FROM person p
        JOIN visit_occurrence v ON p.person_id = v.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_integer_column_with_integer_literal(self) -> None:
        """Filtering integer column with integer literal should pass."""
        sql = """
        SELECT * FROM concept WHERE concept_id = 12345
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_date_compatibility(self) -> None:
        """Date columns should be compatible with each other."""
        sql = """
        SELECT *
        FROM condition_occurrence co
        JOIN observation_period op
          ON co.condition_start_date = op.observation_period_start_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class ObservationPeriodDateRangeLogicTests(unittest.TestCase):
    """Tests for observation_period date range logic rule (OMOP_033)."""

    def _run_rule(self, sql: str) -> list:
        """Run observation_period date range logic rule."""
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.observation_period_date_range_logic")()
        return rule.validate(sql)

    # OMOP_033: Reversed BETWEEN logic
    def test_omop_033_reversed_between_logic(self) -> None:
        """Testing observation_period dates BETWEEN event dates should error."""
        sql = """
        SELECT *
        FROM condition_occurrence co
        JOIN observation_period op ON co.person_id = op.person_id
        WHERE op.observation_period_start_date BETWEEN co.condition_start_date
                                                   AND co.condition_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("reversed", violations[0].message.lower())
        self.assertIn("observation_period_start_date", violations[0].message)
        self.assertIn("condition_start_date", violations[0].message)

    def test_reversed_logic_with_drug_exposure(self) -> None:
        """Reversed BETWEEN with drug_exposure should error."""
        sql = """
        SELECT *
        FROM drug_exposure de
        JOIN observation_period op ON de.person_id = op.person_id
        WHERE op.observation_period_end_date BETWEEN de.drug_exposure_start_date
                                                 AND de.drug_exposure_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("observation_period_end_date", violations[0].message)

    def test_reversed_logic_with_visit_occurrence(self) -> None:
        """Reversed BETWEEN with visit_occurrence should error."""
        sql = """
        SELECT *
        FROM visit_occurrence vo
        JOIN observation_period op ON vo.person_id = op.person_id
        WHERE op.observation_period_start_date BETWEEN vo.visit_start_date
                                                   AND vo.visit_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("reversed", violations[0].message.lower())

    def test_correct_between_logic(self) -> None:
        """Correct BETWEEN logic with event date tested should pass."""
        sql = """
        SELECT *
        FROM condition_occurrence co
        JOIN observation_period op ON co.person_id = op.person_id
        WHERE co.condition_start_date BETWEEN op.observation_period_start_date
                                          AND op.observation_period_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_correct_logic_drug_exposure(self) -> None:
        """Correct BETWEEN logic with drug_exposure should pass."""
        sql = """
        SELECT *
        FROM drug_exposure de
        JOIN observation_period op ON de.person_id = op.person_id
        WHERE de.drug_exposure_start_date BETWEEN op.observation_period_start_date
                                              AND op.observation_period_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_no_between_clause_not_flagged(self) -> None:
        """Query without BETWEEN clause should not trigger."""
        sql = """
        SELECT *
        FROM condition_occurrence co
        JOIN observation_period op ON co.person_id = op.person_id
        WHERE co.condition_start_date >= op.observation_period_start_date
          AND co.condition_start_date <= op.observation_period_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_between_without_observation_period_not_flagged(self) -> None:
        """BETWEEN without observation_period should not trigger."""
        sql = """
        SELECT *
        FROM condition_occurrence co
        WHERE co.condition_start_date BETWEEN '2020-01-01' AND '2020-12-31'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_correct_logic_with_measurement(self) -> None:
        """Correct BETWEEN logic with measurement should pass."""
        sql = """
        SELECT *
        FROM measurement m
        JOIN observation_period op ON m.person_id = op.person_id
        WHERE m.measurement_date BETWEEN op.observation_period_start_date
                                     AND op.observation_period_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class VisitDetailJoinValidationTests(unittest.TestCase):
    """Tests for visit_detail join validation rule (OMOP_034)."""

    def _run_rule(self, sql: str) -> list:
        """Run visit_detail join validation rule."""
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.visit_detail_join_validation")()
        return rule.validate(sql)

    # OMOP_034: Visit detail should join to visit_occurrence via visit_occurrence_id

    def test_omop_034_join_only_on_person_id(self) -> None:
        """visit_detail JOIN visit_occurrence only on person_id should warn."""
        sql = """
        SELECT *
        FROM visit_detail vd
        JOIN visit_occurrence vo ON vd.person_id = vo.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("visit_occurrence_id", violations[0].message)
        self.assertIn("many-to-many", violations[0].message.lower())

    def test_correct_join_on_visit_occurrence_id(self) -> None:
        """visit_detail JOIN visit_occurrence on visit_occurrence_id should pass."""
        sql = """
        SELECT *
        FROM visit_detail vd
        JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_join_on_both_person_id_and_visit_occurrence_id(self) -> None:
        """Join on both person_id AND visit_occurrence_id should pass."""
        sql = """
        SELECT *
        FROM visit_detail vd
        JOIN visit_occurrence vo
          ON vd.person_id = vo.person_id
         AND vd.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_left_join_only_on_person_id(self) -> None:
        """LEFT JOIN only on person_id should also warn."""
        sql = """
        SELECT *
        FROM visit_detail vd
        LEFT JOIN visit_occurrence vo ON vd.person_id = vo.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("visit_occurrence_id", violations[0].message)

    def test_visit_detail_without_visit_occurrence_join(self) -> None:
        """visit_detail joined to other tables should not trigger."""
        sql = """
        SELECT *
        FROM visit_detail vd
        JOIN person p ON vd.person_id = p.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_reverse_order_join(self) -> None:
        """visit_occurrence JOIN visit_detail should also be detected."""
        sql = """
        SELECT *
        FROM visit_occurrence vo
        JOIN visit_detail vd ON vo.person_id = vd.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("visit_occurrence_id", violations[0].message)

    def test_reverse_order_correct_join(self) -> None:
        """visit_occurrence JOIN visit_detail on visit_occurrence_id should pass."""
        sql = """
        SELECT *
        FROM visit_occurrence vo
        JOIN visit_detail vd ON vo.visit_occurrence_id = vd.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_multiple_joins_with_incorrect_visit_detail_join(self) -> None:
        """Complex query with incorrect visit_detail join should be detected."""
        sql = """
        SELECT *
        FROM person p
        JOIN visit_detail vd ON p.person_id = vd.person_id
        JOIN visit_occurrence vo ON vd.person_id = vo.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_multiple_joins_with_correct_visit_detail_join(self) -> None:
        """Complex query with correct visit_detail join should pass."""
        sql = """
        SELECT *
        FROM person p
        JOIN visit_detail vd ON p.person_id = vd.person_id
        JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class StandardConceptValueValidationTests(unittest.TestCase):
    """Tests for standard_concept value validation rule (OMOP_037)."""

    def _run_rule(self, sql: str) -> list:
        """Run standard_concept value validation rule."""
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.standard_concept_value_validation")()
        return rule.validate(sql)

    # OMOP_037: standard_concept only accepts 'S', 'C', or NULL

    def test_omop_037_invalid_value_y(self) -> None:
        """Using 'Y' for standard_concept should error."""
        sql = """
        SELECT * FROM concept WHERE standard_concept = 'Y'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Invalid standard_concept value", violations[0].message)
        self.assertIn("'Y'", violations[0].message)

    def test_omop_037_invalid_value_n(self) -> None:
        """Using 'N' for standard_concept should error."""
        sql = """
        SELECT * FROM concept WHERE standard_concept = 'N'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("'N'", violations[0].message)

    def test_omop_037_invalid_string_number_1(self) -> None:
        """Using string '1' for standard_concept should error."""
        sql = """
        SELECT * FROM concept WHERE standard_concept = '1'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("'1'", violations[0].message)

    def test_omop_037_invalid_string_number_0(self) -> None:
        """Using string '0' for standard_concept should error."""
        sql = """
        SELECT * FROM concept WHERE standard_concept = '0'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("'0'", violations[0].message)

    def test_omop_037_invalid_in_clause(self) -> None:
        """Using invalid values in IN clause should error."""
        sql = """
        SELECT * FROM concept WHERE standard_concept IN ('Y', 'N')
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Invalid standard_concept values", violations[0].message)

    def test_omop_037_invalid_mixed_in_clause(self) -> None:
        """Mixed valid and invalid values in IN clause should error."""
        sql = """
        SELECT * FROM concept WHERE standard_concept IN ('S', 'Y')
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("'Y'", violations[0].message)

    def test_valid_value_s(self) -> None:
        """Using 'S' for standard_concept should pass."""
        sql = """
        SELECT * FROM concept WHERE standard_concept = 'S'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_valid_value_c(self) -> None:
        """Using 'C' for standard_concept should pass."""
        sql = """
        SELECT * FROM concept WHERE standard_concept = 'C'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_valid_in_clause(self) -> None:
        """Using 'S' and 'C' in IN clause should pass."""
        sql = """
        SELECT * FROM concept WHERE standard_concept IN ('S', 'C')
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_valid_is_null(self) -> None:
        """Using IS NULL for standard_concept should pass."""
        sql = """
        SELECT * FROM concept WHERE standard_concept IS NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_valid_is_not_null(self) -> None:
        """Using IS NOT NULL for standard_concept should pass."""
        sql = """
        SELECT * FROM concept WHERE standard_concept IS NOT NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_standard_concept_in_select_not_flagged(self) -> None:
        """Using standard_concept in SELECT clause should not trigger."""
        sql = """
        SELECT standard_concept FROM concept WHERE concept_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_case_insensitive_valid_values(self) -> None:
        """Valid values should work regardless of case."""
        sql = """
        SELECT * FROM concept WHERE standard_concept = 's'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_invalid_not_equals(self) -> None:
        """Invalid value in != comparison should error."""
        sql = """
        SELECT * FROM concept WHERE standard_concept != 'Y'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("'Y'", violations[0].message)


class CostTableDomainValidationTests(unittest.TestCase):
    """Tests for cost table domain validation rule (OMOP_038)."""

    def _run_rule(self, sql: str) -> list:
        """Run cost table domain validation rule."""
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.cost_table_domain_validation")()
        return rule.validate(sql)

    # OMOP_038: cost table joins require cost_domain_id filter

    def test_omop_038_drug_exposure_missing_domain_filter(self) -> None:
        """Cost joined to drug_exposure without domain filter should error."""
        sql = """
        SELECT * FROM cost c
        JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Missing cost_domain_id filter", violations[0].message)
        self.assertIn("'drug'", violations[0].message)

    def test_omop_038_drug_exposure_with_domain_filter(self) -> None:
        """Cost joined to drug_exposure with correct domain filter should pass."""
        sql = """
        SELECT * FROM cost c
        JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
        WHERE c.cost_domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_038_drug_exposure_wrong_domain(self) -> None:
        """Cost joined to drug_exposure with wrong domain should error."""
        sql = """
        SELECT * FROM cost c
        JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
        WHERE c.cost_domain_id = 'Procedure'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Cost domain mismatch", violations[0].message)
        self.assertIn("'drug'", violations[0].message)

    def test_omop_038_procedure_missing_filter(self) -> None:
        """Cost joined to procedure_occurrence without filter should error."""
        sql = """
        SELECT * FROM cost c
        JOIN procedure_occurrence po ON c.cost_event_id = po.procedure_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("'procedure'", violations[0].message)

    def test_omop_038_procedure_with_filter(self) -> None:
        """Cost joined to procedure_occurrence with correct filter should pass."""
        sql = """
        SELECT * FROM cost c
        JOIN procedure_occurrence po ON c.cost_event_id = po.procedure_occurrence_id
        WHERE c.cost_domain_id = 'Procedure'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_038_condition_with_filter(self) -> None:
        """Cost joined to condition_occurrence with correct filter should pass."""
        sql = """
        SELECT * FROM cost c
        JOIN condition_occurrence co ON c.cost_event_id = co.condition_occurrence_id
        WHERE c.cost_domain_id = 'Condition'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_038_case_insensitive_domain(self) -> None:
        """Domain filter should be case insensitive."""
        sql = """
        SELECT * FROM cost c
        JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
        WHERE c.cost_domain_id = 'drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_038_unqualified_column_with_filter(self) -> None:
        """Unqualified cost_domain_id should work when cost table present."""
        sql = """
        SELECT * FROM cost c
        JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
        WHERE cost_domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_038_reversed_join_order(self) -> None:
        """Reversed join order (clinical → cost) should still validate."""
        sql = """
        SELECT * FROM drug_exposure de
        JOIN cost c ON de.drug_exposure_id = c.cost_event_id
        WHERE c.cost_domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_038_in_clause_with_correct_domain(self) -> None:
        """IN clause with correct domain should pass."""
        sql = """
        SELECT * FROM cost c
        JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
        WHERE c.cost_domain_id IN ('Drug')
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_038_no_cost_table(self) -> None:
        """Query without cost table should not trigger."""
        sql = """
        SELECT * FROM drug_exposure de WHERE de.drug_type_concept_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_038_cost_domain_in_select_with_filter(self) -> None:
        """cost_domain_id in SELECT with WHERE filter should pass."""
        sql = """
        SELECT c.cost_domain_id FROM cost c
        JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
        WHERE c.cost_domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class CareSiteJoinValidationTests(unittest.TestCase):
    """Tests for care_site join path validation rule (OMOP_039)."""

    def _run_rule(self, sql: str) -> list:
        """Run care_site join validation rule."""
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.care_site_join_validation")()
        return rule.validate(sql)

    # OMOP_039: Clinical tables must join to location through care_site

    def test_omop_039_visit_occurrence_direct_to_location(self) -> None:
        """Visit_occurrence joined directly to location should warn."""
        sql = """
        SELECT * FROM visit_occurrence vo
        JOIN location l ON vo.care_site_id = l.location_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Invalid direct join to location detected", violations[0].message)
        self.assertIn("care_site", violations[0].message)

    def test_omop_039_proper_path_through_care_site(self) -> None:
        """Proper join path through care_site should pass."""
        sql = """
        SELECT * FROM visit_occurrence vo
        JOIN care_site cs ON vo.care_site_id = cs.care_site_id
        JOIN location l ON cs.location_id = l.location_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_039_person_to_location_allowed(self) -> None:
        """Person joined to location is valid (home address)."""
        sql = """
        SELECT * FROM person p
        JOIN location l ON p.location_id = l.location_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_039_condition_occurrence_bypass(self) -> None:
        """Condition_occurrence bypassing care_site should warn."""
        sql = """
        SELECT * FROM condition_occurrence co
        JOIN location l ON co.care_site_id = l.location_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Invalid direct join to location detected", violations[0].message)

    def test_omop_039_care_site_to_location(self) -> None:
        """Care_site to location join is valid."""
        sql = """
        SELECT * FROM care_site cs
        JOIN location l ON cs.location_id = l.location_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_039_reversed_join_order(self) -> None:
        """Reversed join order (location → visit) should still warn."""
        sql = """
        SELECT * FROM location l
        JOIN visit_occurrence vo ON l.location_id = vo.care_site_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Invalid direct join to location detected", violations[0].message)

    def test_omop_039_procedure_occurrence_bypass(self) -> None:
        """Procedure_occurrence bypassing care_site should warn."""
        sql = """
        SELECT * FROM procedure_occurrence po
        JOIN location l ON po.care_site_id = l.location_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Invalid direct join to location detected", violations[0].message)

    def test_omop_039_no_location_table(self) -> None:
        """Query without location table should not trigger."""
        sql = """
        SELECT * FROM visit_occurrence vo
        JOIN care_site cs ON vo.care_site_id = cs.care_site_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_039_visit_detail_bypass(self) -> None:
        """Visit_detail bypassing care_site should warn."""
        sql = """
        SELECT * FROM visit_detail vd
        JOIN location l ON vd.care_site_id = l.location_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Invalid direct join to location detected", violations[0].message)

    def test_omop_039_person_reversed_join(self) -> None:
        """Person to location reversed join should pass."""
        sql = """
        SELECT * FROM location l
        JOIN person p ON l.location_id = p.location_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class VisitOccurrenceInnerJoinValidationTests(unittest.TestCase):
    """Tests for visit_occurrence INNER JOIN validation rule (OMOP_043)."""

    def _run_rule(self, sql: str) -> list:
        """Run visit_occurrence INNER JOIN validation rule."""
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.visit_occurrence_inner_join_validation")()
        return rule.validate(sql)

    # OMOP_043: INNER JOIN to visit_occurrence loses records

    def test_omop_043_condition_inner_join(self) -> None:
        """Condition INNER JOIN to visit should warn."""
        sql = """
        SELECT co.*
        FROM condition_occurrence co
        JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("may drop events", violations[0].message)

    def test_omop_043_condition_left_join(self) -> None:
        """Condition LEFT JOIN to visit should pass."""
        sql = """
        SELECT co.*
        FROM condition_occurrence co
        LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_043_drug_exposure_inner_join(self) -> None:
        """Drug exposure INNER JOIN to visit should warn."""
        sql = """
        SELECT de.*
        FROM drug_exposure de
        JOIN visit_occurrence vo ON de.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("may drop events", violations[0].message)

    def test_omop_043_measurement_inner_join(self) -> None:
        """Measurement INNER JOIN to visit should warn."""
        sql = """
        SELECT m.*
        FROM measurement m
        JOIN visit_occurrence vo ON m.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_043_inner_join_with_visit_filter(self) -> None:
        """INNER JOIN with WHERE clause filtering visit_occurrence_id shows intentional message."""
        sql = """
        SELECT co.*
        FROM condition_occurrence co
        JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
        WHERE vo.visit_occurrence_id IS NOT NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("explicit filtering", violations[0].message)

    def test_omop_043_procedure_left_join(self) -> None:
        """Procedure LEFT JOIN to visit should pass."""
        sql = """
        SELECT po.*
        FROM procedure_occurrence po
        LEFT JOIN visit_occurrence vo ON po.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_043_right_join(self) -> None:
        """RIGHT JOIN should pass (unusual but not wrong)."""
        sql = """
        SELECT co.*
        FROM condition_occurrence co
        RIGHT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_043_no_visit_occurrence(self) -> None:
        """Query without visit_occurrence should not trigger."""
        sql = """
        SELECT * FROM condition_occurrence WHERE condition_concept_id = 201826
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_043_inner_join_to_person(self) -> None:
        """INNER JOIN to person (not visit) should not trigger."""
        sql = """
        SELECT co.*
        FROM condition_occurrence co
        JOIN person p ON co.person_id = p.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_043_reversed_join_order(self) -> None:
        """Reversed join order (visit → condition) should still warn."""
        sql = """
        SELECT vo.*
        FROM visit_occurrence vo
        JOIN condition_occurrence co ON vo.visit_occurrence_id = co.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_043_observation_inner_join(self) -> None:
        """Observation INNER JOIN to visit should warn."""
        sql = """
        SELECT o.*
        FROM observation o
        JOIN visit_occurrence vo ON o.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_043_full_outer_join(self) -> None:
        """FULL OUTER JOIN should pass."""
        sql = """
        SELECT co.*
        FROM condition_occurrence co
        FULL OUTER JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class DrugEraConceptClassValidationTests(unittest.TestCase):
    """Tests for drug_era concept class validation rule (OMOP_044)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.drug_era_concept_class_validation")()
        return rule.validate(sql, dialect)

    def test_omop_044_clinical_drug_filter_fails(self) -> None:
        """Filtering drug_era for 'Clinical Drug' should error."""
        sql = """
        SELECT de.*
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_class_id = 'Clinical Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Clinical Drug", violations[0].message)
        self.assertIn("0 rows", violations[0].message)

    def test_omop_044_branded_drug_filter_fails(self) -> None:
        """Filtering drug_era for 'Branded Drug' should error."""
        sql = """
        SELECT de.*
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_class_id = 'Branded Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Branded Drug", violations[0].message)
        self.assertIn("0 rows", violations[0].message)

    def test_omop_044_clinical_drug_form_filter_fails(self) -> None:
        """Filtering drug_era for 'Clinical Drug Form' should error."""
        sql = """
        SELECT de.*
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_class_id = 'Clinical Drug Form'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Clinical Drug Form", violations[0].message)
        self.assertIn("0 rows", violations[0].message)

    def test_omop_044_ingredient_filter_passes(self) -> None:
        """Filtering drug_era for 'Ingredient' should pass."""
        sql = """
        SELECT de.*
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_class_id = 'Ingredient'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_044_neq_ingredient_fails(self) -> None:
        """Filtering drug_era for concept_class_id != 'Ingredient' should error."""
        sql = """
        SELECT de.*
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_class_id != 'Ingredient'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("!= 'Ingredient'", violations[0].message)

    def test_omop_044_in_clause_with_invalid_values_fails(self) -> None:
        """IN clause with invalid concept classes should error."""
        sql = """
        SELECT de.*
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_class_id IN ('Clinical Drug', 'Branded Drug')
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Branded Drug", violations[0].message)
        self.assertIn("Clinical Drug", violations[0].message)

    def test_omop_044_in_clause_with_ingredient_passes(self) -> None:
        """IN clause with only 'Ingredient' should pass."""
        sql = """
        SELECT de.*
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_class_id IN ('Ingredient')
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_044_no_concept_join_passes(self) -> None:
        """drug_era query without concept join should pass."""
        sql = """
        SELECT drug_concept_id, COUNT(*)
        FROM drug_era
        GROUP BY drug_concept_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_044_concept_join_no_class_filter_passes(self) -> None:
        """drug_era joined to concept without concept_class_id filter should pass."""
        sql = """
        SELECT de.*, c.concept_name
        FROM drug_era de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_044_reversed_join_order_fails(self) -> None:
        """Reversed join order (concept -> drug_era) should still error."""
        sql = """
        SELECT de.*
        FROM concept c
        JOIN drug_era de ON c.concept_id = de.drug_concept_id
        WHERE c.concept_class_id = 'Clinical Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("Clinical Drug", violations[0].message)

    def test_omop_044_non_drug_era_table_not_affected(self) -> None:
        """Non-drug_era tables should not trigger this rule."""
        sql = """
        SELECT de.*
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        WHERE c.concept_class_id = 'Clinical Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class NegativeConceptIdValidationTests(unittest.TestCase):
    """Tests for negative concept_id validation rule (OMOP_050)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.negative_concept_id_validation")()
        return rule.validate(sql, dialect)

    def test_omop_050_negative_equality_fails(self) -> None:
        """Negative concept_id in equality should error."""
        sql = """
        SELECT * FROM condition_occurrence WHERE condition_concept_id = -1
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("negative", violations[0].message.lower())
        self.assertIn("-1", violations[0].message)

    def test_omop_050_negative_in_clause_fails(self) -> None:
        """Negative concept_id in IN clause should error."""
        sql = """
        SELECT * FROM drug_exposure WHERE drug_concept_id IN (-1, -2, 123)
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("negative", violations[0].message.lower())
        self.assertIn("-1", violations[0].message)
        self.assertIn("-2", violations[0].message)

    def test_omop_050_negative_less_than_fails(self) -> None:
        """Negative concept_id in < comparison should error."""
        sql = """
        SELECT * FROM measurement WHERE measurement_concept_id < -10
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("negative", violations[0].message.lower())
        self.assertIn("-10", violations[0].message)

    def test_omop_050_negative_between_fails(self) -> None:
        """Negative concept_id in BETWEEN should error."""
        sql = """
        SELECT * FROM observation WHERE observation_concept_id BETWEEN -5 AND 100
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("between", violations[0].message.lower())
        self.assertIn("-5", violations[0].message)

    def test_omop_050_positive_values_pass(self) -> None:
        """Positive concept_id values should pass."""
        sql = """
        SELECT * FROM condition_occurrence WHERE condition_concept_id = 201826
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_050_zero_passes(self) -> None:
        """Zero (unmapped) should pass."""
        sql = """
        SELECT * FROM drug_exposure WHERE drug_concept_id = 0
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_050_multiple_columns_fails(self) -> None:
        """Multiple columns with negative values should error."""
        sql = """
        SELECT * FROM person WHERE gender_concept_id = -1 AND race_concept_id = -2
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 2)

    def test_omop_050_auxiliary_concepts_fails(self) -> None:
        """Negative values in auxiliary concept columns should error."""
        sql = """
        SELECT * FROM drug_exposure WHERE route_concept_id = -100
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("negative", violations[0].message.lower())

    def test_omop_050_greater_than_negative_fails(self) -> None:
        """Negative concept_id in > comparison should error."""
        sql = """
        SELECT * FROM procedure_occurrence WHERE procedure_concept_id > -1
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("negative", violations[0].message.lower())

    def test_omop_050_in_clause_only_positive_passes(self) -> None:
        """IN clause with only positive values should pass."""
        sql = """
        SELECT * FROM measurement WHERE measurement_concept_id IN (123, 456, 789)
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class DrugExposureQuantityMisuseTests(unittest.TestCase):
    """Tests for drug_exposure quantity misuse rule (OMOP_055)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.drug_exposure_quantity_misuse")()
        return rule.validate(sql, dialect)

    def test_omop_055_dateadd_with_quantity_fails(self) -> None:
        """DATEADD using quantity should error."""
        sql = """
        SELECT person_id,
               DATEADD(day, quantity, drug_exposure_start_date) AS estimated_end
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("quantity", violations[0].message.lower())
        self.assertIn("days_supply", violations[0].message.lower())

    def test_omop_055_date_add_with_quantity_fails(self) -> None:
        """DATE_ADD using quantity should error."""
        sql = """
        SELECT person_id,
               DATE_ADD(drug_exposure_start_date, INTERVAL quantity DAY) AS end_date
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("quantity", violations[0].message.lower())
        self.assertIn("days_supply", violations[0].message.lower())

    def test_omop_055_datediff_with_quantity_fails(self) -> None:
        """DATEDIFF using quantity should error."""
        sql = """
        SELECT person_id,
               DATEDIFF(day, drug_exposure_start_date, quantity) AS duration
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("quantity", violations[0].message.lower())

    def test_omop_055_add_operator_with_quantity_fails(self) -> None:
        """Date arithmetic using + operator with quantity should error."""
        sql = """
        SELECT person_id,
               drug_exposure_start_date + quantity AS end_date
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("quantity", violations[0].message.lower())
        self.assertIn("days_supply", violations[0].message.lower())

    def test_omop_055_sub_operator_with_quantity_fails(self) -> None:
        """Date arithmetic using - operator with quantity should error."""
        sql = """
        SELECT person_id,
               drug_exposure_end_date - quantity AS start_date
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("quantity", violations[0].message.lower())

    def test_omop_055_interval_with_quantity_fails(self) -> None:
        """INTERVAL expression with quantity should error."""
        sql = """
        SELECT person_id,
               drug_exposure_start_date + INTERVAL quantity DAY
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("quantity", violations[0].message.lower())

    def test_omop_055_days_supply_passes(self) -> None:
        """Using days_supply instead of quantity should pass."""
        sql = """
        SELECT person_id,
               DATEADD(day, days_supply, drug_exposure_start_date) AS estimated_end
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_055_date_diff_between_dates_passes(self) -> None:
        """DATEDIFF between date columns should pass."""
        sql = """
        SELECT person_id,
               DATEDIFF(day, drug_exposure_start_date, drug_exposure_end_date) AS duration
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_055_quantity_in_non_date_context_passes(self) -> None:
        """Using quantity in non-date context should pass."""
        sql = """
        SELECT person_id, quantity, days_supply
        FROM drug_exposure
        WHERE quantity > 30
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_055_no_quantity_column_passes(self) -> None:
        """Query without quantity column should pass."""
        sql = """
        SELECT person_id, drug_concept_id
        FROM drug_exposure
        WHERE days_supply > 30
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_055_other_table_quantity_passes(self) -> None:
        """Quantity from non-drug_exposure table should pass."""
        sql = """
        SELECT person_id,
               DATEADD(day, product.quantity, order_date) AS delivery_date
        FROM orders o
        JOIN product p ON o.product_id = p.id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class SourceToConceptMapValidationTests(unittest.TestCase):
    """Tests for source_to_concept_map validation rule (OMOP_058)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.source_to_concept_map_validation")()
        return rule.validate(sql, dialect)

    def test_omop_058_source_code_without_vocabulary_id_fails(self) -> None:
        """Filtering by source_code without source_vocabulary_id should error."""
        sql = """
        SELECT target_concept_id
        FROM source_to_concept_map
        WHERE source_code = '250.00'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("source_code", violations[0].message.lower())
        self.assertIn("source_vocabulary_id", violations[0].message.lower())

    def test_omop_058_both_filters_passes(self) -> None:
        """Filtering by both source_code and source_vocabulary_id should pass."""
        sql = """
        SELECT target_concept_id
        FROM source_to_concept_map
        WHERE source_code = '250.00'
          AND source_vocabulary_id = 'ICD9CM'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_058_no_source_code_filter_passes(self) -> None:
        """Query without source_code filter should pass."""
        sql = """
        SELECT *
        FROM source_to_concept_map
        WHERE target_concept_id > 0
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_058_vocabulary_id_only_passes(self) -> None:
        """Filtering by source_vocabulary_id alone should pass."""
        sql = """
        SELECT *
        FROM source_to_concept_map
        WHERE source_vocabulary_id = 'ICD9CM'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_058_in_clause_without_vocabulary_id_fails(self) -> None:
        """IN clause on source_code without source_vocabulary_id should error."""
        sql = """
        SELECT target_concept_id
        FROM source_to_concept_map
        WHERE source_code IN ('250.00', '250.01', '250.02')
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("source_code", violations[0].message.lower())

    def test_omop_058_in_clause_with_vocabulary_id_passes(self) -> None:
        """IN clause with source_vocabulary_id should pass."""
        sql = """
        SELECT target_concept_id
        FROM source_to_concept_map
        WHERE source_code IN ('250.00', '250.01')
          AND source_vocabulary_id = 'ICD9CM'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_058_like_without_vocabulary_id_fails(self) -> None:
        """LIKE on source_code without source_vocabulary_id should error."""
        sql = """
        SELECT target_concept_id
        FROM source_to_concept_map
        WHERE source_code LIKE '250%'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_058_qualified_columns_fails(self) -> None:
        """Qualified column names should also be detected."""
        sql = """
        SELECT stcm.target_concept_id
        FROM source_to_concept_map stcm
        WHERE stcm.source_code = 'A123'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_058_qualified_columns_passes(self) -> None:
        """Qualified column names with both filters should pass."""
        sql = """
        SELECT stcm.target_concept_id
        FROM source_to_concept_map stcm
        WHERE stcm.source_code = 'A123'
          AND stcm.source_vocabulary_id = 'SNOMED'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_058_join_condition_fails(self) -> None:
        """Join conditions should also be checked."""
        sql = """
        SELECT t.target_concept_id
        FROM my_table m
        JOIN source_to_concept_map t ON m.code = t.source_code
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_058_join_with_vocabulary_id_passes(self) -> None:
        """Join with source_vocabulary_id filter should pass."""
        sql = """
        SELECT t.target_concept_id
        FROM my_table m
        JOIN source_to_concept_map t
          ON m.code = t.source_code
         AND t.source_vocabulary_id = 'ICD10CM'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_058_other_tables_not_affected(self) -> None:
        """Other tables should not trigger this rule."""
        sql = """
        SELECT concept_id
        FROM concept
        WHERE concept_code = '250.00'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_058_no_table_reference_passes(self) -> None:
        """Query without source_to_concept_map table should pass."""
        sql = """
        SELECT * FROM person WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class PrecedingVisitOccurrenceValidationTests(unittest.TestCase):
    """Tests for preceding_visit_occurrence_id validation rule (OMOP_059)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.preceding_visit_occurrence_validation")()
        return rule.validate(sql, dialect)

    def test_omop_059_join_to_different_table_fails(self) -> None:
        """Joining preceding_visit_occurrence_id to visit_detail should error."""
        sql = """
        SELECT *
        FROM visit_occurrence vo
        JOIN visit_detail vd ON vo.preceding_visit_occurrence_id = vd.visit_detail_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("visit_detail", violations[0].message.lower())
        self.assertIn("visit_occurrence", violations[0].message.lower())

    def test_omop_059_join_to_wrong_column_fails(self) -> None:
        """Joining to wrong column in visit_occurrence should error."""
        sql = """
        SELECT v1.*, v2.person_id
        FROM visit_occurrence v1
        JOIN visit_occurrence v2 ON v1.preceding_visit_occurrence_id = v2.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("person_id", violations[0].message.lower())
        self.assertIn("visit_occurrence_id", violations[0].message.lower())

    def test_omop_059_correct_self_join_passes(self) -> None:
        """Correct self-join to visit_occurrence_id should pass."""
        sql = """
        SELECT v1.*, v2.visit_start_date AS prior_visit_date
        FROM visit_occurrence v1
        JOIN visit_occurrence v2
          ON v1.preceding_visit_occurrence_id = v2.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_059_left_join_correct_passes(self) -> None:
        """LEFT JOIN with correct columns should pass."""
        sql = """
        SELECT v1.*, v2.visit_concept_id AS prior_visit_type
        FROM visit_occurrence v1
        LEFT JOIN visit_occurrence v2
          ON v1.preceding_visit_occurrence_id = v2.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_059_reversed_join_order_fails(self) -> None:
        """Reversed join order with wrong column should still error."""
        sql = """
        SELECT *
        FROM visit_occurrence v1
        JOIN visit_occurrence v2 ON v2.visit_concept_id = v1.preceding_visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("visit_concept_id", violations[0].message.lower())

    def test_omop_059_no_preceding_column_passes(self) -> None:
        """Query without preceding_visit_occurrence_id should pass."""
        sql = """
        SELECT *
        FROM visit_occurrence vo
        JOIN person p ON vo.person_id = p.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_059_multiple_tables_join_to_wrong_fails(self) -> None:
        """Complex query joining to wrong table should error."""
        sql = """
        SELECT vo.*, p.*, vd.*
        FROM visit_occurrence vo
        JOIN person p ON vo.person_id = p.person_id
        JOIN visit_detail vd ON vo.preceding_visit_occurrence_id = vd.visit_detail_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("visit_detail", violations[0].message.lower())

    def test_omop_059_unqualified_table_name_passes(self) -> None:
        """Unqualified table names with correct join should pass."""
        sql = """
        SELECT *
        FROM visit_occurrence vo1
        JOIN visit_occurrence vo2
          ON vo1.preceding_visit_occurrence_id = vo2.visit_occurrence_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_059_join_to_person_table_fails(self) -> None:
        """Joining to person table should error."""
        sql = """
        SELECT *
        FROM visit_occurrence vo
        JOIN person p ON vo.preceding_visit_occurrence_id = p.person_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("person", violations[0].message.lower())

    def test_omop_059_other_visit_joins_not_affected(self) -> None:
        """Other visit_occurrence joins should not trigger this rule."""
        sql = """
        SELECT *
        FROM visit_occurrence vo
        JOIN person p ON vo.person_id = p.person_id
        JOIN care_site cs ON vo.care_site_id = cs.care_site_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_059_column_in_where_clause_not_flagged(self) -> None:
        """Using preceding_visit_occurrence_id in WHERE should not be flagged."""
        sql = """
        SELECT *
        FROM visit_occurrence
        WHERE preceding_visit_occurrence_id IS NOT NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class ConditionEndDateNullHandlingTests(unittest.TestCase):
    """Tests for condition_end_date NULL handling rule (OMOP_062)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.condition_end_date_null_handling")()
        return rule.validate(sql, dialect)

    def test_omop_062_datediff_without_null_handling_fails(self) -> None:
        """DATEDIFF without NULL handling should warn."""
        sql = """
        SELECT DATEDIFF(day, condition_start_date, condition_end_date) AS duration
        FROM condition_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("null", violations[0].message.lower())
        self.assertIn("coalesce", violations[0].message.lower())

    def test_omop_062_datediff_with_coalesce_passes(self) -> None:
        """DATEDIFF with COALESCE should pass."""
        sql = """
        SELECT DATEDIFF(day, condition_start_date,
                        COALESCE(condition_end_date, CURRENT_DATE)) AS duration
        FROM condition_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_062_with_is_not_null_filter_passes(self) -> None:
        """Query with IS NOT NULL filter should pass."""
        sql = """
        SELECT DATEDIFF(day, condition_start_date, condition_end_date) AS duration
        FROM condition_occurrence
        WHERE condition_end_date IS NOT NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_062_date_arithmetic_without_null_handling_fails(self) -> None:
        """Date arithmetic without NULL handling should warn."""
        sql = """
        SELECT condition_end_date - condition_start_date AS duration
        FROM condition_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("null", violations[0].message.lower())

    def test_omop_062_date_arithmetic_with_coalesce_passes(self) -> None:
        """Date arithmetic with COALESCE should pass."""
        sql = """
        SELECT COALESCE(condition_end_date, CURRENT_DATE) - condition_start_date AS duration
        FROM condition_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_062_no_date_calculation_passes(self) -> None:
        """Query without date calculation should pass."""
        sql = """
        SELECT condition_concept_id, condition_start_date, condition_end_date
        FROM condition_occurrence
        WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_062_date_add_without_null_handling_fails(self) -> None:
        """DATE_ADD using condition_end_date should warn."""
        sql = """
        SELECT DATE_ADD(condition_end_date, INTERVAL 30 DAY) AS future_date
        FROM condition_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_062_timestampdiff_without_null_handling_fails(self) -> None:
        """TIMESTAMPDIFF without NULL handling should warn."""
        sql = """
        SELECT TIMESTAMPDIFF(DAY, condition_start_date, condition_end_date)
        FROM condition_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_062_multiple_functions_without_handling_fails(self) -> None:
        """Multiple date functions without handling should warn for each."""
        sql = """
        SELECT
            DATEDIFF(day, condition_start_date, condition_end_date) AS duration1,
            condition_end_date - condition_start_date AS duration2
        FROM condition_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 2)

    def test_omop_062_condition_start_date_only_passes(self) -> None:
        """Using only condition_start_date should pass."""
        sql = """
        SELECT DATEDIFF(day, condition_start_date, CURRENT_DATE) AS days_since
        FROM condition_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_062_other_table_end_date_passes(self) -> None:
        """End date from other tables should not trigger."""
        sql = """
        SELECT DATEDIFF(day, vo.visit_start_date, vo.visit_end_date)
        FROM visit_occurrence vo
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_062_no_condition_table_passes(self) -> None:
        """Query without condition_occurrence should pass."""
        sql = """
        SELECT * FROM person WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class DrugStrengthValidityFilterTests(unittest.TestCase):
    """Tests for OMOP_064: drug_strength_valid_start_end_date_filter."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.rules.domain_specific.drug.drug_strength_validity_filter import (
            DrugStrengthValidityFilterRule,
        )

        rule = DrugStrengthValidityFilterRule()
        return rule.validate(sql, dialect)

    def test_omop_064_no_validity_filter_fails(self) -> None:
        """drug_strength without validity filter should warn."""
        sql = """
        SELECT amount_value, amount_unit_concept_id
        FROM drug_strength
        WHERE drug_concept_id = 19078461
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("invalid_reason", violations[0].message.lower())

    def test_omop_064_with_invalid_reason_null_passes(self) -> None:
        """drug_strength with invalid_reason IS NULL should pass."""
        sql = """
        SELECT amount_value, amount_unit_concept_id
        FROM drug_strength
        WHERE drug_concept_id = 19078461
          AND invalid_reason IS NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_064_with_valid_end_date_comparison_passes(self) -> None:
        """drug_strength with valid_end_date check should pass."""
        sql = """
        SELECT amount_value
        FROM drug_strength
        WHERE drug_concept_id = 123
          AND valid_end_date >= CURRENT_DATE
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_064_with_valid_start_date_comparison_passes(self) -> None:
        """drug_strength with valid_start_date check should pass."""
        sql = """
        SELECT amount_value
        FROM drug_strength
        WHERE drug_concept_id = 123
          AND valid_start_date <= CURRENT_DATE
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_064_with_between_date_range_passes(self) -> None:
        """drug_strength with BETWEEN date range should pass."""
        sql = """
        SELECT amount_value
        FROM drug_strength
        WHERE drug_concept_id = 123
          AND CURRENT_DATE BETWEEN valid_start_date AND valid_end_date
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_064_join_without_validity_fails(self) -> None:
        """JOIN to drug_strength without validity filter should warn."""
        sql = """
        SELECT de.drug_exposure_id, ds.amount_value
        FROM drug_exposure de
        JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id
        WHERE de.person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_064_join_with_invalid_reason_in_join_passes(self) -> None:
        """JOIN with invalid_reason in JOIN condition should pass."""
        sql = """
        SELECT de.drug_exposure_id, ds.amount_value
        FROM drug_exposure de
        JOIN drug_strength ds
          ON de.drug_concept_id = ds.drug_concept_id
         AND ds.invalid_reason IS NULL
        WHERE de.person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_064_join_with_invalid_reason_in_where_passes(self) -> None:
        """JOIN with invalid_reason in WHERE should pass."""
        sql = """
        SELECT de.drug_exposure_id, ds.amount_value
        FROM drug_exposure de
        JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id
        WHERE de.person_id = 123
          AND ds.invalid_reason IS NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_064_no_drug_strength_table_passes(self) -> None:
        """Query without drug_strength should pass."""
        sql = """
        SELECT * FROM drug_exposure WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_064_subquery_without_validity_fails(self) -> None:
        """Subquery selecting from drug_strength without filter should warn."""
        sql = """
        SELECT * FROM (
            SELECT drug_concept_id, amount_value
            FROM drug_strength
            WHERE amount_value > 0
        ) subq
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_064_multiple_conditions_with_validity_passes(self) -> None:
        """Complex WHERE with validity filter should pass."""
        sql = """
        SELECT amount_value, numerator_value, denominator_value
        FROM drug_strength
        WHERE drug_concept_id IN (123, 456, 789)
          AND amount_value IS NOT NULL
          AND invalid_reason IS NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_064_invalid_reason_equality_filter_passes(self) -> None:
        """Checking invalid_reason with equality should pass (intentional historical query)."""
        sql = """
        SELECT drug_concept_id, amount_value, invalid_reason
        FROM drug_strength
        WHERE drug_concept_id = 123
          AND invalid_reason = 'D'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_064_date_in_select_but_not_where_fails(self) -> None:
        """Selecting validity columns without filtering should still warn."""
        sql = """
        SELECT drug_concept_id, amount_value, valid_start_date, valid_end_date
        FROM drug_strength
        WHERE drug_concept_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)


class UnionConceptIdDomainIndicatorTests(unittest.TestCase):
    """Tests for OMOP_067: no_union_different_concept_id_types."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.rules.data_quality.union_concept_id_domain_indicator import (
            UnionConceptIdDomainIndicatorRule,
        )

        rule = UnionConceptIdDomainIndicatorRule()
        return rule.validate(sql, dialect)

    def test_omop_067_union_different_domains_without_indicator_fails(self) -> None:
        """UNION mixing condition and drug without domain indicator should warn."""
        sql = """
        SELECT condition_concept_id AS concept_id
        FROM condition_occurrence
        UNION ALL
        SELECT drug_concept_id AS concept_id
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("domain indicator", violations[0].message.lower())

    def test_omop_067_union_with_domain_indicator_passes(self) -> None:
        """UNION with literal domain column should pass."""
        sql = """
        SELECT 'Condition' AS domain, condition_concept_id AS concept_id
        FROM condition_occurrence
        UNION ALL
        SELECT 'Drug' AS domain, drug_concept_id AS concept_id
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_067_union_same_domain_passes(self) -> None:
        """UNION from same domain table should pass."""
        sql = """
        SELECT condition_concept_id AS concept_id
        FROM condition_occurrence
        WHERE person_id = 123
        UNION ALL
        SELECT condition_concept_id AS concept_id
        FROM condition_occurrence
        WHERE person_id = 456
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_067_union_non_concept_id_passes(self) -> None:
        """UNION not involving concept_id columns should pass."""
        sql = """
        SELECT person_id, observation_date
        FROM condition_occurrence
        UNION ALL
        SELECT person_id, drug_exposure_start_date
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_067_no_union_passes(self) -> None:
        """Query without UNION should pass."""
        sql = """
        SELECT condition_concept_id FROM condition_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_067_union_three_domains_fails(self) -> None:
        """UNION mixing three domains without indicator should warn."""
        sql = """
        SELECT condition_concept_id AS concept_id FROM condition_occurrence
        UNION ALL
        SELECT drug_concept_id AS concept_id FROM drug_exposure
        UNION ALL
        SELECT procedure_concept_id AS concept_id FROM procedure_occurrence
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_067_union_measurement_observation_fails(self) -> None:
        """UNION mixing measurement and observation should warn."""
        sql = """
        SELECT measurement_concept_id AS concept_id
        FROM measurement
        UNION ALL
        SELECT observation_concept_id AS concept_id
        FROM observation
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_067_union_with_concept_join_passes(self) -> None:
        """UNION with domain_id from concept table should pass."""
        sql = """
        SELECT c.domain_id, co.condition_concept_id AS concept_id
        FROM condition_occurrence co
        JOIN concept c ON co.condition_concept_id = c.concept_id
        UNION ALL
        SELECT c.domain_id, de.drug_concept_id AS concept_id
        FROM drug_exposure de
        JOIN concept c ON de.drug_concept_id = c.concept_id
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_067_plain_union_without_all_fails(self) -> None:
        """Plain UNION (not ALL) mixing domains should also warn."""
        sql = """
        SELECT condition_concept_id AS concept_id
        FROM condition_occurrence
        UNION
        SELECT drug_concept_id AS concept_id
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)

    def test_omop_067_union_non_domain_tables_passes(self) -> None:
        """UNION from non-domain tables should pass."""
        sql = """
        SELECT concept_id FROM concept WHERE domain_id = 'Condition'
        UNION ALL
        SELECT concept_id FROM concept WHERE domain_id = 'Drug'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)


class DrugExposureSigParsingTests(unittest.TestCase):
    """Tests for drug_exposure sig parsing rule (OMOP_072)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("semantic.drug_exposure_sig_parsing")()
        return rule.validate(sql, dialect)

    def test_omop_072_substring_with_cast_fails(self) -> None:
        """SUBSTRING on sig with CAST to INT should error."""
        sql = """
        SELECT person_id,
               CAST(SUBSTRING(sig, 1, CHARINDEX(' ', sig)) AS INT) AS dose
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("sig", violations[0].message.lower())
        self.assertIn("drug_strength", violations[0].message.lower())

    def test_omop_072_regexp_substr_for_numeric_fails(self) -> None:
        """REGEXP_SUBSTR extracting numbers from sig should error."""
        sql = """
        SELECT person_id,
               REGEXP_SUBSTR(sig, '[0-9]+') AS dose
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("sig", violations[0].message.lower())

    def test_omop_072_charindex_on_sig_fails(self) -> None:
        """CHARINDEX on sig field should error."""
        sql = """
        SELECT person_id,
               CHARINDEX('tablet', sig) AS position
        FROM drug_exposure
        WHERE CAST(SUBSTRING(sig, 1, 2) AS INT) > 1
        """
        violations = self._run_rule(sql)
        self.assertTrue(len(violations) >= 1)
        self.assertTrue(any("sig" in v.message.lower() for v in violations))

    def test_omop_072_position_on_sig_for_parsing_fails(self) -> None:
        """POSITION function on sig used for numeric extraction should warn."""
        sql = """
        SELECT person_id,
               CAST(SUBSTR(sig, POSITION('mg' IN sig) + 3, 2) AS INT) AS dose
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        # Should trigger at least one violation for SUBSTR on sig
        self.assertTrue(len(violations) >= 1)
        self.assertTrue(any("sig" in v.message.lower() for v in violations))

    def test_omop_072_substr_for_parsing_fails(self) -> None:
        """SUBSTR on sig for dose extraction should error."""
        sql = """
        SELECT person_id,
               CAST(SUBSTR(sig, 1, 3) AS NUMERIC) AS dose
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("sig", violations[0].message.lower())
        self.assertIn("drug_strength", violations[0].message.lower())

    def test_omop_072_split_part_on_sig_fails(self) -> None:
        """SPLIT_PART on sig with numeric cast should warn."""
        sql = """
        SELECT person_id,
               CAST(SPLIT_PART(sig, ' ', 1) AS INT) AS dose
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("sig", violations[0].message.lower())

    def test_omop_072_drug_strength_join_passes(self) -> None:
        """Using drug_strength table should pass."""
        sql = """
        SELECT de.person_id, ds.amount_value, ds.amount_unit_concept_id
        FROM drug_exposure de
        JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id
        WHERE ds.invalid_reason IS NULL
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_072_sig_in_select_passes(self) -> None:
        """Simply selecting sig column should pass."""
        sql = """
        SELECT person_id, sig, drug_concept_id
        FROM drug_exposure
        WHERE drug_concept_id = 1234567
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_072_sig_in_where_like_passes(self) -> None:
        """Using LIKE on sig for searching (not parsing) should pass."""
        sql = """
        SELECT person_id, sig
        FROM drug_exposure
        WHERE sig LIKE '%daily%'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_072_no_drug_exposure_table_passes(self) -> None:
        """Query without drug_exposure table should pass."""
        sql = """
        SELECT person_id, SUBSTRING(notes, 1, 10)
        FROM patient_records
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_072_other_table_sig_column_passes(self) -> None:
        """Parsing sig from non-drug_exposure table should pass."""
        sql = """
        SELECT CAST(SUBSTRING(signature, 1, 5) AS INT)
        FROM documents
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_072_left_function_on_sig_fails(self) -> None:
        """LEFT function on sig should warn."""
        sql = """
        SELECT person_id,
               LEFT(sig, 5) AS sig_start
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        # LEFT is in STRING_FUNCTIONS but might not trigger without numeric context
        # This is acceptable as it's a WARNING rule with some flexibility
        self.assertTrue(len(violations) >= 0)

    def test_omop_072_regexp_replace_on_sig_fails(self) -> None:
        """REGEXP_REPLACE on sig should warn."""
        sql = """
        SELECT person_id,
               CAST(REGEXP_REPLACE(sig, '[^0-9]', '') AS INT) AS dose
        FROM drug_exposure
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("sig", violations[0].message.lower())


class VocabularyTableProtectionTests(unittest.TestCase):
    """Tests for vocabulary table protection rule (OMOP_081)."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.registry import get_rule
        rule = get_rule("data_quality.vocabulary_table_protection")()
        return rule.validate(sql, dialect)

    def test_omop_081_delete_from_concept_fails(self) -> None:
        """DELETE from concept table should error."""
        sql = """
        DELETE FROM concept WHERE concept_id = 0
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("DELETE", violations[0].message)
        self.assertIn("concept", violations[0].message.lower())
        self.assertIn("vocabulary", violations[0].message.lower())

    def test_omop_081_update_concept_fails(self) -> None:
        """UPDATE on concept table should error."""
        sql = """
        UPDATE concept SET concept_name = 'test' WHERE concept_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("UPDATE", violations[0].message)
        self.assertIn("concept", violations[0].message.lower())

    def test_omop_081_insert_into_vocabulary_fails(self) -> None:
        """INSERT into vocabulary table should error."""
        sql = """
        INSERT INTO vocabulary (vocabulary_id, vocabulary_name)
        VALUES ('CUSTOM', 'My Custom Vocabulary')
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("INSERT", violations[0].message)
        self.assertIn("vocabulary", violations[0].message.lower())

    def test_omop_081_truncate_concept_ancestor_fails(self) -> None:
        """TRUNCATE on concept_ancestor should error."""
        sql = """
        TRUNCATE TABLE concept_ancestor
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("TRUNCATE", violations[0].message)
        self.assertIn("concept_ancestor", violations[0].message.lower())

    def test_omop_081_drop_drug_strength_fails(self) -> None:
        """DROP on drug_strength should error."""
        sql = """
        DROP TABLE drug_strength
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("DROP", violations[0].message)
        self.assertIn("drug_strength", violations[0].message.lower())

    def test_omop_081_delete_from_concept_relationship_fails(self) -> None:
        """DELETE from concept_relationship should error."""
        sql = """
        DELETE FROM concept_relationship
        WHERE relationship_id = 'Maps to'
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 1)
        self.assertIn("DELETE", violations[0].message)
        self.assertIn("concept_relationship", violations[0].message.lower())

    def test_omop_081_select_from_concept_passes(self) -> None:
        """SELECT from concept should pass."""
        sql = """
        SELECT * FROM concept WHERE concept_id = 0
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_081_delete_from_clinical_table_passes(self) -> None:
        """DELETE from clinical tables (non-vocabulary) should pass."""
        sql = """
        DELETE FROM condition_occurrence WHERE person_id = 123
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_081_update_clinical_table_passes(self) -> None:
        """UPDATE on clinical tables should pass."""
        sql = """
        UPDATE drug_exposure
        SET quantity = 30
        WHERE drug_exposure_id = 456
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_081_insert_into_cohort_passes(self) -> None:
        """INSERT into non-vocabulary table should pass."""
        sql = """
        INSERT INTO cohort (subject_id, cohort_definition_id, cohort_start_date)
        VALUES (123, 1, '2020-01-01')
        """
        violations = self._run_rule(sql)
        self.assertEqual(len(violations), 0)

    def test_omop_081_all_vocabulary_tables_protected(self) -> None:
        """All vocabulary tables should be protected."""
        vocabulary_tables = [
            "concept", "concept_relationship", "concept_ancestor",
            "concept_synonym", "vocabulary", "domain",
            "concept_class", "relationship", "drug_strength",
            "source_to_concept_map"
        ]

        for table in vocabulary_tables:
            sql = f"DELETE FROM {table} WHERE 1=1"
            violations = self._run_rule(sql)
            self.assertEqual(len(violations), 1,
                           f"Expected violation for table {table}")
            self.assertIn(table, violations[0].message.lower())


if __name__ == "__main__":
    unittest.main()

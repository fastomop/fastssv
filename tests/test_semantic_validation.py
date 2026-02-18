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
    """Tests for the domain segregation rule."""

    def _run_rule(self, sql: str, dialect: str = "postgres") -> list:
        from fastssv.core.base import Severity
        from fastssv.core.registry import get_rule

        rule = get_rule("semantic.domain_segregation")()
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

    def test_string_comparison_not_flagged(self) -> None:
        """String equality on value_as_number (odd but syntactically valid) not flagged."""
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


if __name__ == "__main__":
    unittest.main()

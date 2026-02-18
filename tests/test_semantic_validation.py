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


if __name__ == "__main__":
    unittest.main()

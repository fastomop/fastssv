"""Integration tests for the unified validation API."""

import unittest

from fastssv import validate_sql


class UnifiedAPITests(unittest.TestCase):
    """Tests for the unified validate_sql() API."""

    def test_validate_sql_semantic_only(self) -> None:
        """Test running only semantic validation."""
        # Query properly handles concept_id = 0 with > 0 filter
        # person table doesn't require standard_concept enforcement
        sql = """
        SELECT person_id, gender_concept_id
        FROM person
        WHERE gender_concept_id = 8507
        AND gender_concept_id > 0
        """
        results = validate_sql(sql, validators="semantic")

        self.assertEqual(results["semantic_errors"], [])
        self.assertEqual(results["all_errors"], [])

    def test_validate_sql_all_validators(self) -> None:
        """Test running all validators."""
        # Query properly enforces standard concepts, handles concept_id = 0,
        # uses concept_ancestor for hierarchy expansion, and restricts
        # concept string columns (domain_id) inside a concept_id lookup CTE
        sql = """
        WITH valid_drug_concepts AS (
            SELECT concept_id
            FROM concept
            WHERE domain_id = 'Drug'
            AND standard_concept = 'S'
            AND invalid_reason IS NULL
        )
        SELECT p.person_id, de.drug_concept_id
        FROM drug_exposure de
        JOIN person p ON de.person_id = p.person_id
        JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
        JOIN valid_drug_concepts vdc ON de.drug_concept_id = vdc.concept_id
        WHERE ca.ancestor_concept_id = 1234
        AND de.drug_concept_id > 0
        """
        results = validate_sql(sql, validators="all")

        self.assertEqual(results["semantic_errors"], [])
        self.assertEqual(results["all_errors"], [])

    def test_validate_sql_list_of_validators(self) -> None:
        """Test running specific list of validators."""
        sql = """
        SELECT 1 FROM person
        """
        results = validate_sql(sql, validators=["semantic"])

        # Result dict has all expected keys
        self.assertIn("semantic_errors", results)
        self.assertIn("vocabulary_errors", results)
        self.assertIn("all_errors", results)

        # Only semantic validators ran — no vocabulary errors should be present
        self.assertEqual(results["vocabulary_errors"], [])
        self.assertEqual(results["semantic_errors"], [])
        self.assertEqual(results["all_errors"], [])

    def test_validate_sql_different_dialects(self) -> None:
        """Test validation with different SQL dialects."""
        sql = """
        SELECT p.person_id
        FROM person p
        """

        # Should work with different dialects
        for dialect in ["postgres", "mysql", "sqlite", "duckdb"]:
            results = validate_sql(sql, validators="all", dialect=dialect)
            self.assertEqual(results["semantic_errors"], [], f"Unexpected semantic errors for dialect {dialect}")
            self.assertEqual(results["all_errors"], [], f"Unexpected errors for dialect {dialect}")


if __name__ == "__main__":
    unittest.main()

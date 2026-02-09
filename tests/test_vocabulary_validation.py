"""Unit tests for vocabulary validation rules."""

import unittest

from fastssv.core.registry import get_rule


def _run_concept_code_rule(sql: str, dialect: str = "postgres") -> list[str]:
    """Run the concept_code_requires_vocabulary_id rule and return violation messages."""
    rule = get_rule("vocabulary.concept_code_requires_vocabulary_id")()
    return [v.message for v in rule.validate(sql, dialect)]


class ConceptCodeRequiresVocabularyIdTests(unittest.TestCase):
    """Tests for the concept_code + vocabulary_id rule."""

    # --- PASS cases ---

    def test_eq_with_vocabulary_id_passes(self) -> None:
        """concept_code = with vocabulary_id in same WHERE should pass."""
        sql = """
        SELECT concept_id FROM concept c
        WHERE c.concept_code = '1661387'
          AND c.vocabulary_id = 'RxNorm'
        """
        self.assertEqual(_run_concept_code_rule(sql), [])

    def test_eq_unqualified_with_vocabulary_id_passes(self) -> None:
        """Unqualified concept_code with unqualified vocabulary_id should pass."""
        sql = """
        SELECT concept_id FROM concept
        WHERE concept_code = '308136'
          AND vocabulary_id = 'RxNorm'
        """
        self.assertEqual(_run_concept_code_rule(sql), [])

    def test_in_with_vocabulary_id_passes(self) -> None:
        """concept_code IN with vocabulary_id should pass."""
        sql = """
        SELECT concept_id FROM concept c
        WHERE c.concept_code IN ('308136', '314076')
          AND c.vocabulary_id = 'RxNorm'
        """
        self.assertEqual(_run_concept_code_rule(sql), [])

    def test_vocabulary_id_in_clause_passes(self) -> None:
        """vocabulary_id as IN clause should also satisfy the rule."""
        sql = """
        SELECT concept_id FROM concept c
        WHERE c.concept_code = '308136'
          AND c.vocabulary_id IN ('RxNorm', 'RxNorm Extension')
        """
        self.assertEqual(_run_concept_code_rule(sql), [])

    def test_vocabulary_id_in_join_on_passes(self) -> None:
        """vocabulary_id filter in JOIN ON should satisfy the rule."""
        sql = """
        SELECT co.person_id
        FROM condition_occurrence co
        JOIN concept c
          ON co.condition_concept_id = c.concept_id
          AND c.concept_code = '123'
          AND c.vocabulary_id = 'SNOMED'
        """
        self.assertEqual(_run_concept_code_rule(sql), [])

    def test_cte_with_both_filters_passes(self) -> None:
        """CTE containing concept_code + vocabulary_id should pass."""
        sql = """
        WITH mapped AS (
            SELECT c.concept_id
            FROM concept c
            WHERE c.concept_code = '1661387'
              AND c.vocabulary_id = 'RxNorm'
              AND c.invalid_reason IS NULL
        )
        SELECT de.person_id
        FROM drug_exposure de
        WHERE de.drug_concept_id IN (SELECT concept_id FROM mapped)
        """
        self.assertEqual(_run_concept_code_rule(sql), [])

    def test_no_concept_code_usage_passes(self) -> None:
        """Query without concept_code should not trigger the rule."""
        sql = """
        SELECT de.person_id
        FROM drug_exposure de
        WHERE de.drug_concept_id = 46276149
        """
        self.assertEqual(_run_concept_code_rule(sql), [])

    def test_concept_code_on_non_concept_table_ignored(self) -> None:
        """concept_code on a table that resolves to something other than concept is skipped."""
        sql = """
        SELECT * FROM other_table t
        WHERE t.concept_code = '123'
        """
        self.assertEqual(_run_concept_code_rule(sql), [])

    # --- FAIL cases ---

    def test_eq_without_vocabulary_id_fails(self) -> None:
        """concept_code = without vocabulary_id should error."""
        sql = """
        SELECT concept_id FROM concept c
        WHERE c.concept_code = '1661387'
        """
        errors = _run_concept_code_rule(sql)
        self.assertEqual(len(errors), 1)
        self.assertIn("concept_code", errors[0])
        self.assertIn("vocabulary_id", errors[0])

    def test_in_without_vocabulary_id_fails(self) -> None:
        """concept_code IN without vocabulary_id should error."""
        sql = """
        SELECT concept_id FROM concept c
        WHERE c.concept_code IN ('308136', '314076')
        """
        errors = _run_concept_code_rule(sql)
        self.assertEqual(len(errors), 1)
        self.assertIn("IN", errors[0])

    def test_like_without_vocabulary_id_fails(self) -> None:
        """concept_code LIKE without vocabulary_id should error."""
        sql = """
        SELECT concept_id FROM concept c
        WHERE c.concept_code LIKE '308%'
        """
        errors = _run_concept_code_rule(sql)
        self.assertEqual(len(errors), 1)
        self.assertIn("LIKE", errors[0])

    def test_mismatched_aliases_fails(self) -> None:
        """concept_code on alias c with vocabulary_id on alias d should error."""
        sql = """
        SELECT c.concept_id
        FROM concept c
        JOIN concept d ON c.concept_id = d.concept_id
        WHERE c.concept_code = '308136'
          AND d.vocabulary_id = 'RxNorm'
        """
        errors = _run_concept_code_rule(sql)
        self.assertEqual(len(errors), 1)

    def test_vocabulary_id_only_in_subquery_fails(self) -> None:
        """vocabulary_id inside a subquery does not satisfy the outer concept_code."""
        sql = """
        SELECT concept_id FROM concept c
        WHERE c.concept_code = '308136'
          AND EXISTS (
            SELECT 1 FROM vocabulary v
            WHERE v.vocabulary_id = 'RxNorm'
          )
        """
        errors = _run_concept_code_rule(sql)
        self.assertEqual(len(errors), 1)

    def test_cte_missing_vocabulary_id_fails(self) -> None:
        """CTE with concept_code but no vocabulary_id should error."""
        sql = """
        WITH mapped AS (
            SELECT c.concept_id
            FROM concept c
            WHERE c.concept_code = '665077'
        )
        SELECT de.person_id
        FROM drug_exposure de
        WHERE de.drug_concept_id IN (SELECT concept_id FROM mapped)
        """
        errors = _run_concept_code_rule(sql)
        self.assertEqual(len(errors), 1)

    # --- Deduplication ---

    def test_single_violation_per_scope(self) -> None:
        """Multiple concept_code filters in same scope produce only one violation."""
        sql = """
        SELECT concept_id FROM concept c
        WHERE c.concept_code = '123'
          OR c.concept_code = '456'
        """
        errors = _run_concept_code_rule(sql)
        self.assertEqual(len(errors), 1)

    def test_separate_violations_for_different_aliases(self) -> None:
        """Two concept table aliases each missing vocabulary_id get separate violations."""
        sql = """
        SELECT c1.concept_id, c2.concept_id
        FROM concept c1
        JOIN concept c2 ON c1.concept_id = c2.concept_id
        WHERE c1.concept_code = '123'
          AND c2.concept_code = '456'
        """
        errors = _run_concept_code_rule(sql)
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()

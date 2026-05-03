"""Tests for strict mode vs normal mode behavior.

These tests verify that certain rules escalate from WARNING to ERROR
when --strict mode is enabled.
"""

import pytest
from fastssv.core.base import Severity
from fastssv.core.registry import get_rule
from fastssv.core.validation_context import ValidationContext, set_validation_context


class TestStrictMode:
    """Tests for strict mode escalation."""

    @pytest.fixture(autouse=True)
    def reset_context(self):
        """Reset validation context before each test."""
        # Create a normal mode context
        ctx = ValidationContext(strict_mode=False)
        set_validation_context(ctx)
        yield
        # Clean up after test
        set_validation_context(ValidationContext(strict_mode=False))

    def test_invalid_reason_enforcement_normal_mode_silent(self):
        """invalid_reason_enforcement is gated behind strict mode — silent
        in default mode regardless of the SQL pattern.

        (Previously this rule fired as WARNING in default mode, which
        produced a violation on essentially every realistic OMOP query
        and diluted signal from every other rule. As of [Unreleased] it
        is opt-in via strict mode.)
        """
        sql = """
        SELECT concept_id, concept_name
        FROM concept
        WHERE domain_id = 'Drug'
        """

        ctx = ValidationContext(strict_mode=False)
        set_validation_context(ctx)

        rule = get_rule("concept_standardization.invalid_reason_enforcement")()
        violations = rule.validate(sql)

        assert violations == []

    def test_invalid_reason_enforcement_strict_mode_fires_as_warning(self):
        """In strict mode, invalid_reason_enforcement fires as WARNING.

        Strict mode here *enables* the rule rather than escalating it
        from WARNING to ERROR — the rule is opt-in.
        """
        sql = """
        SELECT concept_id, concept_name
        FROM concept
        WHERE domain_id = 'Drug'
        """

        ctx = ValidationContext(strict_mode=True)
        set_validation_context(ctx)

        rule = get_rule("concept_standardization.invalid_reason_enforcement")()
        violations = rule.validate(sql)

        assert len(violations) > 0
        assert all(v.severity == Severity.WARNING for v in violations)
        assert all("invalid_reason" in v.message for v in violations)

    def test_invalid_reason_valid_query_both_modes(self):
        """Valid queries should pass in both normal and strict mode."""
        sql = """
        SELECT concept_id, concept_name
        FROM concept
        WHERE domain_id = 'Drug'
        AND invalid_reason IS NULL
        """

        rule = get_rule("concept_standardization.invalid_reason_enforcement")()

        # Test normal mode
        ctx = ValidationContext(strict_mode=False)
        set_validation_context(ctx)
        violations_normal = rule.validate(sql)

        # Test strict mode
        ctx = ValidationContext(strict_mode=True)
        set_validation_context(ctx)
        violations_strict = rule.validate(sql)

        assert len(violations_normal) == 0
        assert len(violations_strict) == 0

    def test_concept_domain_validation_normal_mode(self):
        """In normal mode, missing domain_id violations should be WARNING."""
        sql = """
        SELECT co.person_id, c.concept_name
        FROM condition_occurrence co
        JOIN concept c ON c.concept_id = co.condition_concept_id
        """

        # Set normal mode
        ctx = ValidationContext(strict_mode=False)
        set_validation_context(ctx)

        rule = get_rule("concept_standardization.concept_domain_validation")()
        violations = rule.validate(sql)

        # Should have violation for missing domain_id filter
        assert len(violations) > 0
        # In normal mode, should be WARNING
        assert any(v.severity == Severity.WARNING and "domain_id" in v.message for v in violations)

    def test_concept_domain_validation_strict_mode(self):
        """In strict mode, missing domain_id violations should be ERROR."""
        sql = """
        SELECT co.person_id, c.concept_name
        FROM condition_occurrence co
        JOIN concept c ON c.concept_id = co.condition_concept_id
        """

        # Set strict mode
        ctx = ValidationContext(strict_mode=True)
        set_validation_context(ctx)

        rule = get_rule("concept_standardization.concept_domain_validation")()
        violations = rule.validate(sql)

        # Should have violation for missing domain_id filter
        assert len(violations) > 0
        # In strict mode, should be ERROR
        assert any(v.severity == Severity.ERROR and "domain_id" in v.message for v in violations)

    def test_concept_domain_validation_wrong_domain_always_error(self):
        """Wrong domain_id should always be ERROR, even in normal mode."""
        sql = """
        SELECT co.person_id, c.concept_name
        FROM condition_occurrence co
        JOIN concept c ON c.concept_id = co.condition_concept_id
        WHERE c.domain_id = 'Drug'
        """

        rule = get_rule("concept_standardization.concept_domain_validation")()

        # Test normal mode
        ctx = ValidationContext(strict_mode=False)
        set_validation_context(ctx)
        violations_normal = rule.validate(sql)

        # Test strict mode
        ctx = ValidationContext(strict_mode=True)
        set_validation_context(ctx)
        violations_strict = rule.validate(sql)

        # Wrong domain should always be ERROR
        assert len(violations_normal) > 0
        assert all(v.severity == Severity.ERROR for v in violations_normal)
        assert len(violations_strict) > 0
        assert all(v.severity == Severity.ERROR for v in violations_strict)

    def test_concept_domain_validation_correct_domain_passes_both_modes(self):
        """Queries with correct domain_id should pass in both modes."""
        sql = """
        SELECT co.person_id, c.concept_name
        FROM condition_occurrence co
        JOIN concept c ON c.concept_id = co.condition_concept_id
        WHERE c.domain_id = 'Condition'
        """

        rule = get_rule("concept_standardization.concept_domain_validation")()

        # Test normal mode
        ctx = ValidationContext(strict_mode=False)
        set_validation_context(ctx)
        violations_normal = rule.validate(sql)

        # Test strict mode
        ctx = ValidationContext(strict_mode=True)
        set_validation_context(ctx)
        violations_strict = rule.validate(sql)

        assert len(violations_normal) == 0
        assert len(violations_strict) == 0

    def test_derived_tables_silent_in_default_fire_in_strict(self):
        """Derived-vocabulary-table usage (e.g. concept_ancestor without a
        JOIN back to concept for invalid_reason) is silent in default
        mode and fires as WARNING in strict mode — same gating as the
        primary case.
        """
        sql = """
        SELECT descendant_concept_id
        FROM concept_ancestor
        WHERE ancestor_concept_id = 201826
        """

        rule = get_rule("concept_standardization.invalid_reason_enforcement")()

        # Default mode: silent
        set_validation_context(ValidationContext(strict_mode=False))
        violations_normal = rule.validate(sql)
        assert violations_normal == []

        # Strict mode: fires as WARNING
        set_validation_context(ValidationContext(strict_mode=True))
        violations_strict = rule.validate(sql)
        assert len(violations_strict) > 0
        assert all(v.severity == Severity.WARNING for v in violations_strict)

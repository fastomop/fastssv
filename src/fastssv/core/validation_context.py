"""Validation Context.

Provides context for rule execution including strict mode and other configuration.
"""

from dataclasses import dataclass


@dataclass
class ValidationContext:
    """Context for rule validation execution."""

    strict_mode: bool = False
    """Strict mode: escalates best-practice warnings to errors."""

    dialect: str = "postgres"
    """SQL dialect being validated."""

    def should_escalate_rule(self, rule_id: str) -> bool:
        """Determine if a rule should be escalated to ERROR in strict mode."""
        if not self.strict_mode:
            return False

        # Rules that escalate in strict mode
        # These are best-practice rules that default to WARNING
        strict_escalation_rules = {
            "concept_standardization.standard_concept_enforcement",
            "concept_standardization.invalid_reason_enforcement",
            "concept_standardization.concept_domain_validation",
            "anti_patterns.concept_code_requires_vocabulary_id",
            "joins.concept_relationship_requires_relationship_id",
        }

        return rule_id in strict_escalation_rules


# Global validation context (thread-safe for single-threaded CLI use)
_current_context = ValidationContext()


def get_validation_context() -> ValidationContext:
    """Get the current validation context."""
    return _current_context


def set_validation_context(context: ValidationContext):
    """Set the current validation context."""
    global _current_context
    _current_context = context


__all__ = ["ValidationContext", "get_validation_context", "set_validation_context"]

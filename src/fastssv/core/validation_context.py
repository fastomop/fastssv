"""Validation Context.

Provides context for rule execution including strict mode and other configuration.
"""

from dataclasses import dataclass


@dataclass
class ValidationContext:
    """Context for rule validation execution."""

    strict_mode: bool = False
    """Strict mode for cohort definitions: escalates certain warnings to errors."""

    dialect: str = "postgres"
    """SQL dialect being validated."""

    def should_escalate_rule(self, rule_id: str) -> bool:
        """Determine if a rule should be escalated from WARNING to ERROR in strict mode."""
        if not self.strict_mode:
            return False

        # Rules that escalate in strict mode (cohort definition context)
        strict_escalation_rules = {
            "concept_standardization.standard_concept_enforcement",
            "concept_standardization.invalid_reason_enforcement",
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

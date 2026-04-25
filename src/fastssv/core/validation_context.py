"""Validation Context.

Provides context for rule execution including strict mode and other
configuration. Backed by a ``ContextVar`` so concurrent requests in the
FastAPI service don't stomp on each other's strict-mode setting —
``asyncio.to_thread`` copies the current ``contextvars.Context`` to the
worker thread, so rules running in the threadpool observe the context
set by the HTTP handler.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


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

        # Rules that escalate in strict mode (best-practice rules that
        # default to WARNING but cohort-definition workflows want as ERROR).
        #
        # Note: ``concept_standardization.invalid_reason_enforcement`` is
        # NOT in this set even though its rule_id appears related. That
        # rule is *gated* behind strict mode (silent in default mode,
        # fires as WARNING when strict mode is on); it isn't escalated
        # to ERROR. Strict mode there means "enable the rule," not
        # "promote a warning."
        strict_escalation_rules = {
            "concept_standardization.standard_concept_enforcement",
            "concept_standardization.concept_domain_validation",
            "anti_patterns.concept_code_requires_vocabulary_id",
            "joins.concept_relationship_requires_relationship_id",
        }
        return rule_id in strict_escalation_rules


_current_context: ContextVar[ValidationContext] = ContextVar(
    "fastssv_validation_context",
    default=ValidationContext(),
)


def get_validation_context() -> ValidationContext:
    """Get the current validation context."""
    return _current_context.get()


def set_validation_context(context: ValidationContext) -> None:
    """Set the current validation context.

    Prefer ``with_strict_mode`` when the scope is a single call — it
    restores the prior context automatically.
    """
    _current_context.set(context)


@contextmanager
def with_strict_mode(enabled: bool = True) -> Iterator[ValidationContext]:
    """Temporarily enable/disable strict mode while keeping the current dialect."""
    current = _current_context.get()
    new_ctx = ValidationContext(strict_mode=enabled, dialect=current.dialect)
    token = _current_context.set(new_ctx)
    try:
        yield new_ctx
    finally:
        _current_context.reset(token)


__all__ = [
    "ValidationContext",
    "get_validation_context",
    "set_validation_context",
    "with_strict_mode",
]

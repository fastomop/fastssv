"""Base classes for FastSSV validation rules."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Severity(Enum):
    """Severity level for rule violations."""

    ERROR = "error"
    WARNING = "warning"


@dataclass
class RuleViolation:
    """Structured representation of a rule violation."""

    rule_id: str
    severity: Severity
    message: str
    suggested_fix: str
    location: Optional[str] = None
    details: dict = field(default_factory=dict)
    suggested_fix_patch: Optional[dict] = None

    def __post_init__(self) -> None:
        # Every violation carries a structured patch envelope so consumers
        # can switch on `action` uniformly. Rules that haven't supplied a
        # specific REPLACE/ADD/REMOVE patch fall back to FREEFORM, wrapping
        # the prose `suggested_fix` text.
        if self.suggested_fix_patch is None and self.suggested_fix:
            self.suggested_fix_patch = {
                "action": "FREEFORM",
                "text": self.suggested_fix,
            }

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization.

        The single ``fix`` field unifies the human prose and the structured
        patch:

        - For FREEFORM patches (the auto-default for ~60% of rules), ``fix``
          is the prose string — the same text the LLM-friendly
          ``suggested_fix`` carries.
        - For REPLACE / ADD / REMOVE patches (mechanical, ~40%), ``fix`` is
          the patch dict; an outer correction loop can apply it directly
          via ``apply_patch()``.

        Consumers switch on ``isinstance(fix, str)`` vs ``dict``.
        """
        result = {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "issue": self.message,
        }
        fix = self._serialised_fix()
        if fix is not None:
            result["fix"] = fix
        if self.location:
            result["location"] = self.location
        # ``details`` stays as an internal field on the dataclass for tests,
        # programmatic introspection, and rule-side diagnostics — but it
        # doesn't appear in the serialised payload. The keys vary too much
        # across rules to be programmatically useful, and most content
        # duplicates ``issue``/``fix``.
        return result

    def _serialised_fix(self):
        """Compute the single-field ``fix`` value: string for FREEFORM /
        no-patch, dict for mechanical REPLACE/ADD/REMOVE."""
        patch = self.suggested_fix_patch
        if patch is None:
            return self.suggested_fix or None
        action = patch.get("action")
        if action == "FREEFORM":
            return patch.get("text") or self.suggested_fix or None
        return patch


class Rule(ABC):
    """Base class for all validation rules.

    Subclasses must define class attributes:
        rule_id: Unique identifier (e.g., "concept_standardization.standard_concept_enforcement")
        name: Human-readable name (e.g., "Standard Concept Enforcement")
        description: Short one-line summary shown in tool output and rule lists
        severity: Default severity level
        suggested_fix: Default fix suggestion

    Subclasses may optionally define:
        long_description: Multi-sentence explanation rendered on the /rules page
        example_bad: Minimal SQL snippet that trips the rule (for the /rules page)
        example_good: Corrected version of example_bad (for the /rules page)

    Subclasses must implement:
        validate(sql, dialect) -> List[RuleViolation]
    """

    rule_id: str
    name: str
    description: str
    severity: Severity
    suggested_fix: str
    long_description: Optional[str] = None
    example_bad: Optional[str] = None
    example_good: Optional[str] = None

    @abstractmethod
    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations.

        Args:
            sql: The SQL query to validate
            dialect: SQL dialect for parsing (default: postgres)

        Returns:
            List of RuleViolation objects. Empty list means validation passed.
        """
        pass

    def create_violation(
        self,
        message: str,
        severity: Optional[Severity] = None,
        suggested_fix: Optional[str] = None,
        location: Optional[str] = None,
        details: Optional[dict] = None,
        suggested_fix_patch: Optional[dict] = None,
    ) -> RuleViolation:
        """Helper to create a violation with rule defaults."""
        return RuleViolation(
            rule_id=self.rule_id,
            severity=severity or self.severity,
            message=message,
            suggested_fix=suggested_fix or self.suggested_fix,
            location=location,
            details=details or {},
            suggested_fix_patch=suggested_fix_patch,
        )


__all__ = ["Rule", "RuleViolation", "Severity"]

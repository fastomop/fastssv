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

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "issue": self.message,
            "suggested_fix": self.suggested_fix,
        }
        if self.location:
            result["location"] = self.location
        if self.details:
            result["details"] = self.details
        return result


class Rule(ABC):
    """Base class for all validation rules.

    Subclasses must define class attributes:
        rule_id: Unique identifier (e.g., "concept_standardization.standard_concept_enforcement")
        name: Human-readable name (e.g., "Standard Concept Enforcement")
        description: What this rule checks
        severity: Default severity level
        suggested_fix: Default fix suggestion

    Subclasses must implement:
        validate(sql, dialect) -> List[RuleViolation]
    """

    # Metadata - must be defined by subclasses
    rule_id: str
    name: str
    description: str
    severity: Severity
    suggested_fix: str

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
    ) -> RuleViolation:
        """Helper to create a violation with rule defaults."""
        return RuleViolation(
            rule_id=self.rule_id,
            severity=severity or self.severity,
            message=message,
            suggested_fix=suggested_fix or self.suggested_fix,
            location=location,
            details=details or {},
        )


__all__ = ["Rule", "RuleViolation", "Severity"]

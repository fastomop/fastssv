"""Rule Layer Classification System.

Defines three distinct validation layers with clear separation of concerns:
1. STRUCTURAL: SQL syntax and parseability (always ERROR)
2. SCHEMA: OMOP data model compliance (ERROR for violations)
3. BEST_PRACTICE: Portability and optimization (WARNING)
"""

from enum import Enum
from typing import Optional
from dataclasses import dataclass


class RuleLayer(Enum):
    """Three-layer validation architecture."""

    STRUCTURAL = "structural"
    """SQL structural validity - syntax, parseability, statement structure.

    Rules in this layer validate fundamental SQL correctness:
    - Parseable SQL syntax
    - Single statement per query (or explicit multi-statement support)
    - Valid operators and expressions
    - Proper quoting and escaping

    Violations are ALWAYS errors - these queries cannot execute.
    Severity: ERROR (non-negotiable)
    """

    SCHEMA = "schema"
    """OMOP CDM schema and data model compliance.

    Rules in this layer validate against OMOP CDM structure:
    - Valid table and column names
    - Correct join keys between tables
    - Type compatibility in comparisons
    - Domain-specific constraints
    - Required filters for correctness

    Violations are typically errors but may be warnings for exploratory queries.
    Severity: ERROR (default), WARNING (with context-awareness)
    """

    BEST_PRACTICE = "best_practice"
    """Portability, optimization, and best practices.

    Rules in this layer suggest improvements:
    - Dialect-specific function usage (portability warnings)
    - Performance anti-patterns
    - Code maintainability
    - Semantic best practices

    Violations are suggestions, not correctness issues.
    Severity: WARNING (always)
    """


@dataclass(frozen=True)
class RuleMetadata:
    """Governance metadata for each rule.

    Provides complete documentation and behavioral specification for a rule.
    """

    category: str
    """Rule category within layer (e.g., 'joins', 'concept_standardization')."""

    layer: RuleLayer
    """Validation layer this rule belongs to."""

    default_severity: str
    """Default severity: 'error' or 'warning'."""

    rationale: str
    """Why this rule exists - the problem it prevents."""

    escalation_conditions: Optional[str] = None
    """Conditions under which severity increases (WARNING → ERROR)."""

    demotion_conditions: Optional[str] = None
    """Conditions under which severity decreases (ERROR → WARNING)."""

    context_aware: bool = False
    """Whether rule adjusts severity based on query context."""

    omop_rule_id: Optional[str] = None
    """OMOP semantic rule ID if applicable (e.g., VOCAB_022, JOIN_005)."""

    examples_valid: Optional[str] = None
    """Example queries that pass this rule."""

    examples_invalid: Optional[str] = None
    """Example queries that violate this rule."""

    def __post_init__(self):
        """Validate metadata consistency."""
        # Structural rules must always be ERROR
        if self.layer == RuleLayer.STRUCTURAL and self.default_severity != "error":
            raise ValueError(f"Structural rules must have ERROR severity, got {self.default_severity}")

        # Best practice rules must always be WARNING
        if self.layer == RuleLayer.BEST_PRACTICE and self.default_severity != "warning":
            raise ValueError(f"Best practice rules must have WARNING severity, got {self.default_severity}")

        # Context-aware rules need escalation/demotion conditions
        if self.context_aware and not (self.escalation_conditions or self.demotion_conditions):
            raise ValueError("Context-aware rules must specify escalation or demotion conditions")


__all__ = ["RuleLayer", "RuleMetadata"]

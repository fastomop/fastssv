"""FastSSV validation rules module.

This module auto-imports all rule submodules to trigger rule registration.

Rules are organized by the type of issue they tackle:
- concept_standardization: Rules for standard, valid, and domain-appropriate concepts
- temporal: Rules for temporal logic and observation period validation
- joins: Rules for proper table relationships and join paths
- data_quality: Rules for schema compliance and unmapped data handling
- domain_specific: Table-specific validation rules (measurement, drug, etc.)
- anti_patterns: Common mistakes and anti-patterns to avoid
"""

# Import rule modules to trigger registration via @register decorator
from . import (
    anti_patterns,
    concept_standardization,
    data_quality,
    domain_specific,
    joins,
    temporal,
)

from fastssv.core.base import RuleViolation, Severity
from fastssv.core.registry import get_all_rules, get_rule, get_rules_by_category


def _validate_category(category: str, sql: str, dialect: str) -> list[str]:
    """Run all rules in *category* and return human-readable messages.

    Parses SQL once up-front so that a parse failure is reported to the
    caller (instead of every rule silently returning ``[]``).
    """
    from fastssv.core.helpers import parse_sql

    _trees, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    violations = []
    for rule_cls in get_rules_by_category(category):
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")
    return results


def validate_anti_patterns(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate OMOP query anti-patterns.

    Detects common anti-patterns including:
    - String-based concept identification
    - Improper type concept usage
    - Context-dependent vocabulary lookups

    Returns list of error/warning messages.
    """
    return _validate_category("anti_patterns", sql, dialect)


def validate_concept_standardization(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate concept standardization rules.

    Enforces:
    - Standard concept usage
    - Hierarchy expansion
    - Invalid reason checks
    - Domain validation
    - Source concept handling

    Returns list of error/warning messages.
    """
    return _validate_category("concept_standardization", sql, dialect)


def validate_data_quality(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate data quality rules.

    Checks:
    - Schema validation
    - Unmapped concept handling
    - Negative concept ID validation
    - Column type validation
    - Data quality issues

    Returns list of error/warning messages.
    """
    return _validate_category("data_quality", sql, dialect)


def validate_domain_specific(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate domain-specific rules.

    Table-specific validation for:
    - Condition, drug, measurement, observation
    - Person, procedure, visit, death domains
    - Cardinality awareness
    - Field validation

    Returns list of error/warning messages.
    """
    return _validate_category("domain_specific", sql, dialect)


def validate_joins(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate join rules.

    Validates:
    - Foreign key relationships
    - Join path correctness
    - Concept relationship direction
    - Cross-table linkage requirements

    Returns list of error/warning messages.
    """
    return _validate_category("joins", sql, dialect)


def validate_temporal(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate temporal rules.

    Validates:
    - Date logic
    - Observation period constraints
    - Temporal consistency across clinical events
    - NULL handling for date columns

    Returns list of error/warning messages.
    """
    return _validate_category("temporal", sql, dialect)


__all__ = [
    # Rule modules
    "anti_patterns",
    "concept_standardization",
    "data_quality",
    "domain_specific",
    "joins",
    "temporal",
    # Validation functions
    "validate_anti_patterns",
    "validate_concept_standardization",
    "validate_data_quality",
    "validate_domain_specific",
    "validate_joins",
    "validate_temporal",
    # Registry access
    "get_all_rules",
    "get_rule",
    "get_rules_by_category",
    # Base classes
    "RuleViolation",
    "Severity",
]

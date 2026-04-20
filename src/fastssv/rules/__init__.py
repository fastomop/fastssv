"""FastSSV validation rules module.

This module auto-imports all rule submodules to trigger rule registration.

Rules are organized by the type of issue they tackle:
- concept_standardization: Rules for standard, valid, and domain-appropriate concepts
- temporal: Rules for temporal logic and observation period validation
- joins: Rules for proper table relationships and join paths
- data_quality: Rules for schema compliance and unmapped data handling
- domain_specific: Table-specific validation rules (measurement, drug, etc.)
- anti_patterns: Common mistakes and anti-patterns to avoid
- performance: Rules for detecting performance issues
- analytics: Rules for analytical methodology recommendations
"""

# Import rule modules to trigger registration via @register decorator
from . import (
    analytics,
    anti_patterns,
    concept_standardization,
    data_quality,
    domain_specific,
    joins,
    performance,
    schema,
    temporal,
)

from fastssv.core.base import RuleViolation, Severity
from fastssv.core.registry import get_all_rules, get_rule, get_rules_by_category


def validate_anti_patterns(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate OMOP query anti-patterns.

    Detects common anti-patterns including:
    - String-based concept identification
    - Improper type concept usage
    - Context-dependent vocabulary lookups

    Returns list of error/warning messages.
    """
    from fastssv.core.helpers import parse_sql

    trees, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    violations = []
    for rule_cls in get_rules_by_category("anti_patterns"):
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")

    return results


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
    from fastssv.core.helpers import parse_sql

    trees, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    violations = []
    for rule_cls in get_rules_by_category("concept_standardization"):
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")

    return results


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
    from fastssv.core.helpers import parse_sql

    trees, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    violations = []
    for rule_cls in get_rules_by_category("data_quality"):
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")

    return results


def validate_domain_specific(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate domain-specific rules.

    Table-specific validation for:
    - Condition, drug, measurement, observation
    - Person, procedure, visit, death domains
    - Cardinality awareness
    - Field validation

    Returns list of error/warning messages.
    """
    from fastssv.core.helpers import parse_sql

    trees, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    violations = []
    for rule_cls in get_rules_by_category("domain_specific"):
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")

    return results


def validate_joins(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate join rules.

    Validates:
    - Foreign key relationships
    - Join path correctness
    - Concept relationship direction
    - Cross-table linkage requirements

    Returns list of error/warning messages.
    """
    from fastssv.core.helpers import parse_sql

    trees, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    violations = []
    for rule_cls in get_rules_by_category("joins"):
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")

    return results


def validate_temporal(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate temporal rules.

    Validates:
    - Date logic
    - Observation period constraints
    - Temporal consistency across clinical events
    - NULL handling for date columns

    Returns list of error/warning messages.
    """
    from fastssv.core.helpers import parse_sql

    trees, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    violations = []
    for rule_cls in get_rules_by_category("temporal"):
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")

    return results


def validate_performance(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate performance rules.

    Detects:
    - CROSS JOIN with large tables
    - Performance anti-patterns

    Returns list of error/warning messages.
    """
    from fastssv.core.helpers import parse_sql

    trees, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    violations = []
    for rule_cls in get_rules_by_category("performance"):
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")

    return results


def validate_analytics(sql: str, dialect: str = "postgres") -> list[str]:
    """Validate analytics rules.

    Checks:
    - Percentile methodology
    - Statistical calculation patterns

    Returns list of error/warning messages.
    """
    from fastssv.core.helpers import parse_sql

    trees, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    violations = []
    for rule_cls in get_rules_by_category("analytics"):
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    results = []
    for v in violations:
        prefix = "Warning: " if v.severity == Severity.WARNING else ""
        results.append(f"{prefix}{v.message}")

    return results


__all__ = [
    # Rule modules
    "analytics",
    "anti_patterns",
    "concept_standardization",
    "data_quality",
    "domain_specific",
    "joins",
    "performance",
    "schema",
    "temporal",
    # Validation functions
    "validate_analytics",
    "validate_anti_patterns",
    "validate_concept_standardization",
    "validate_data_quality",
    "validate_domain_specific",
    "validate_joins",
    "validate_performance",
    "validate_temporal",
    # Registry access
    "get_all_rules",
    "get_rule",
    "get_rules_by_category",
    # Base classes
    "RuleViolation",
    "Severity",
]

"""FastSSV core package - FastOMOP Semantic Static Validator.

A plugin-based semantic validation framework for OMOP CDM SQL queries.
"""

from typing import Dict, List, Literal, Optional

from .core.base import Rule, RuleViolation, Severity
from .core.registry import get_all_rules, get_rule, get_rules_by_category
from .schemas import CDM_SCHEMA, SOURCE_CONCEPT_FIELDS, SOURCE_VOCABS, STANDARD_CONCEPT_FIELDS

# Import rules to trigger registration
from . import rules


ValidatorType = Literal[
    "anti_patterns",
    "concept_standardization",
    "data_quality",
    "domain_specific",
    "joins",
    "temporal",
    "all",
]


def validate_sql(
    sql: str,
    validators: ValidatorType | List[str] = "all",
    dialect: str = "postgres",
    rule_ids: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
) -> Dict[str, List]:
    """Validate SQL query against OMOP CDM rules.

    Args:
        sql: SQL query to validate
        validators: Which validators to run - category name or 'all',
                    or list of validator names
        dialect: SQL dialect for parsing (default: postgres)
        rule_ids: Optional list of specific rule IDs to run (overrides validators)
        categories: Optional list of categories to run (overrides validators)

    Returns:
        Dictionary with validation results:
        {
            'violations': [...],         # List of RuleViolation objects
            'category_errors': {...},    # Errors grouped by category
            'all_errors': [...],         # Combined errors from all validators
        }
    """
    results = {
        "violations": [],
        "category_errors": {
            "anti_patterns": [],
            "concept_standardization": [],
            "data_quality": [],
            "domain_specific": [],
            "joins": [],
            "temporal": [],
        },
        "all_errors": [],
    }

    # Determine which rules to run
    if rule_ids:
        # Specific rules requested
        rule_classes = [get_rule(r) for r in rule_ids]
    elif categories:
        # Specific categories requested
        rule_classes = []
        for cat in categories:
            rule_classes.extend(get_rules_by_category(cat))
    else:
        # Use validators parameter
        if validators == "all":
            run_categories = [
                "anti_patterns",
                "concept_standardization",
                "data_quality",
                "domain_specific",
                "joins",
                "temporal",
            ]
        elif isinstance(validators, str):
            run_categories = [validators]
        else:
            run_categories = validators

        rule_classes = []
        for cat in run_categories:
            rule_classes.extend(get_rules_by_category(cat))

    # Run rules and collect violations
    for rule_cls in rule_classes:
        rule = rule_cls()
        violations = rule.validate(sql, dialect)
        results["violations"].extend(violations)

        # Populate grouped fields
        for v in violations:
            error_str = f"{v.message}"
            if v.severity == Severity.WARNING:
                error_str = f"Warning: {error_str}"

            results["all_errors"].append(error_str)
            category = v.rule_id.split(".", 1)[0]
            if category in results["category_errors"]:
                results["category_errors"][category].append(error_str)

    return results


def validate_sql_structured(
    sql: str,
    dialect: str = "postgres",
    rule_ids: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
) -> List[RuleViolation]:
    """Validate SQL and return structured violations.

    This is the recommended API for new code. Returns RuleViolation objects
    with full metadata instead of string error messages.

    Args:
        sql: SQL query to validate
        dialect: SQL dialect for parsing (default: postgres)
        rule_ids: Optional list of specific rule IDs to run
        categories: Optional list of categories to run (None = all)

    Returns:
        List of RuleViolation objects. Empty list means validation passed.
    """
    # Determine which rules to run
    if rule_ids:
        rule_classes = [get_rule(r) for r in rule_ids]
    elif categories:
        rule_classes = []
        for cat in categories:
            rule_classes.extend(get_rules_by_category(cat))
    else:
        rule_classes = get_all_rules()

    # Run rules and collect violations
    violations = []
    for rule_cls in rule_classes:
        rule = rule_cls()
        violations.extend(rule.validate(sql, dialect))

    return violations


# Category validation functions
from .rules import (
    validate_anti_patterns,
    validate_concept_standardization,
    validate_data_quality,
    validate_domain_specific,
    validate_joins,
    validate_temporal,
)

__all__ = [
    # Main API
    "validate_sql",
    "validate_sql_structured",

    # Core classes
    "Rule",
    "RuleViolation",
    "Severity",

    # Registry
    "get_all_rules",
    "get_rule",
    "get_rules_by_category",

    # Category validators
    "validate_anti_patterns",
    "validate_concept_standardization",
    "validate_data_quality",
    "validate_domain_specific",
    "validate_joins",
    "validate_temporal",

    # Schemas
    "CDM_SCHEMA",
    "STANDARD_CONCEPT_FIELDS",
    "SOURCE_CONCEPT_FIELDS",
    "SOURCE_VOCABS",
]

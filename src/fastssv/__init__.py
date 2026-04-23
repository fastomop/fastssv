"""FastSSV core package - FastOMOP Semantic Static Validator.

A plugin-based semantic validation framework for OMOP CDM SQL queries.
"""

import time
from typing import Dict, List, Literal, Optional

from .core.base import Rule, RuleViolation, Severity
from .core.logging import get_logger, log_rule_execution
from .core.registry import get_all_rules, get_rule, get_rules_by_category
from .schemas import CDM_SCHEMA, SOURCE_CONCEPT_FIELDS, SOURCE_VOCABS, STANDARD_CONCEPT_FIELDS

# Import rules to trigger registration
from . import rules

# Initialize module logger
_logger = get_logger(__name__)


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
    dialect: str = "auto",
    rule_ids: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
) -> Dict[str, List]:
    """Validate SQL query against OMOP CDM rules.

    Args:
        sql: SQL query to validate
        validators: Which validators to run - category name or 'all',
                    or list of validator names
        dialect: SQL dialect for parsing. Pass 'auto' (default) to detect
                 tsql vs postgres from syntax patterns, or an explicit
                 dialect name supported by sqlglot.
        rule_ids: Optional list of specific rule IDs to run (overrides validators)
        categories: Optional list of categories to run (overrides validators)

    Returns:
        Dictionary with validation results:
        {
            'violations': [...],         # List of RuleViolation objects
            'category_errors': {...},    # Errors grouped by category
            'all_errors': [...],         # Combined errors from all validators
            'parse_error': None | str,   # Set when SQL couldn't be parsed
            'dialect': str,              # The dialect actually used
        }
    """
    from fastssv.core.helpers import parse_sql, detect_dialect
    if dialect == "auto":
        dialect = detect_dialect(sql)

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
        "parse_error": None,
        "dialect": dialect,
    }

    # Check parse status up-front. If parsing fails, no rules can run, so
    # we return the parse error rather than a misleading empty result.
    _, parse_error = parse_sql(sql, dialect)
    if parse_error:
        results["parse_error"] = parse_error
        results["all_errors"].append(f"Parse error: {parse_error}")
        results["violations"].append(_make_parse_error_violation(parse_error))
        return results

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


PARSE_ERROR_RULE_ID = "parse.syntax_error"


def _make_parse_error_violation(error_message: str) -> RuleViolation:
    """Build a RuleViolation representing a parse failure.

    Parse errors prevent any rule from running meaningfully, so we surface
    them as a single ERROR-severity violation. Callers can distinguish a
    genuinely-clean query (empty list) from an unparseable one (single
    violation with rule_id == PARSE_ERROR_RULE_ID).
    """
    return RuleViolation(
        rule_id=PARSE_ERROR_RULE_ID,
        severity=Severity.ERROR,
        message=error_message,
        suggested_fix=(
            "Fix the SQL syntax error. Verify the dialect is correct "
            "(try dialect='tsql' for SQL Server syntax like DATEDIFF or GETDATE)."
        ),
        details={"error": error_message},
    )


def validate_sql_structured(
    sql: str,
    dialect: str = "auto",
    rule_ids: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
) -> List[RuleViolation]:
    """Validate SQL and return structured violations.

    This is the recommended API for new code. Returns RuleViolation objects
    with full metadata instead of string error messages.

    Args:
        sql: SQL query to validate
        dialect: SQL dialect for parsing. Pass 'auto' (default) to detect
                 tsql vs postgres from syntax patterns (T-SQL indicators
                 like DATEDIFF, GETDATE, TOP N, @variables → tsql, else
                 postgres). Pass an explicit dialect name to override.
        rule_ids: Optional list of specific rule IDs to run
        categories: Optional list of categories to run (None = all)

    Returns:
        List of RuleViolation objects.
        - Empty list means the SQL parsed cleanly and no rules fired.
        - A single violation with rule_id == "parse.syntax_error" means
          the SQL could not be parsed; rules were not executed.
        - Multiple violations mean one or more rules detected issues.
    """
    from fastssv.core.helpers import parse_sql, detect_dialect
    if dialect == "auto":
        dialect = detect_dialect(sql)

    # Check parse status up-front so callers can distinguish clean SQL from
    # unparseable input. Individual rules also call parse_sql() internally and
    # quietly short-circuit on parse errors, but silent [] would otherwise
    # hide the failure from the caller.
    _, parse_error = parse_sql(sql, dialect)
    if parse_error:
        _logger.warning(f"Parse error (dialect={dialect!r}): {parse_error}")
        return [_make_parse_error_violation(parse_error)]

    # Determine which rules to run
    if rule_ids:
        rule_classes = [get_rule(r) for r in rule_ids]
        _logger.debug(f"Running specific rules: {rule_ids}")
    elif categories:
        rule_classes = []
        for cat in categories:
            rule_classes.extend(get_rules_by_category(cat))
        _logger.debug(f"Running categories: {categories}")
    else:
        rule_classes = get_all_rules()
        _logger.debug(f"Running all {len(rule_classes)} rules")

    # Run rules and collect violations
    violations = []
    for rule_cls in rule_classes:
        rule = rule_cls()

        # Time rule execution if performance logging enabled
        start_time = time.perf_counter()
        rule_violations = rule.validate(sql, dialect)
        duration_ms = (time.perf_counter() - start_time) * 1000

        violations.extend(rule_violations)

        # Log rule execution
        log_rule_execution(_logger, rule.rule_id, len(rule_violations), duration_ms)

    _logger.info(f"Executed {len(rule_classes)} rules, found {len(violations)} violations before deduplication")

    # Deduplicate violations (remove redundant errors for same issue)
    from fastssv.core.deduplication import deduplicate_violations
    violations = deduplicate_violations(violations)

    _logger.info(f"After deduplication: {len(violations)} unique violations")

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
    "PARSE_ERROR_RULE_ID",

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

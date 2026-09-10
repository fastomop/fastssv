"""FastSSV core package - Fast Semantic Static Validator.

A plugin-based semantic validation framework for OMOP CDM SQL queries.
"""

import time
from typing import Dict, List, Literal, Optional

from .core.base import Rule, RuleViolation, Severity
from .core.logging import get_logger, log_rule_execution
from .core.registry import get_all_rules, get_rule, get_rules_by_category
from .schemas import STANDARD_CONCEPT_FIELDS

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
    from fastssv.core.helpers import parse_sql, detect_dialect, looks_like_unrendered_template

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

    # Unrendered SqlRender templates are an input-shape mismatch; rules
    # would just produce noise about placeholder fragments. Short-circuit
    # with one structured WARNING before parsing.
    if looks_like_unrendered_template(sql):
        results["violations"].append(_make_template_skipped_violation())
        return results

    # Check parse status up-front. If parsing fails, no rules can run, so
    # we return the parse error rather than a misleading empty result.
    _, parse_error = parse_sql(sql, dialect)
    if parse_error:
        results["parse_error"] = parse_error
        results["all_errors"].append(f"Parse error: {parse_error}")
        results["violations"].append(_make_parse_error_violation(parse_error, sql))
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
        violations = _run_rule(rule_cls, sql, dialect)
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
NOT_SQL_RULE_ID = "parse.not_sql_input"
TEMPLATE_RULE_ID = "meta.unrendered_sqlrender_template"
RULE_EXECUTION_ERROR_RULE_ID = "meta.rule_execution_error"


def _make_rule_error_violation(rule_id: str, exc: Exception) -> RuleViolation:
    """Build the WARNING emitted when a rule raises instead of returning.

    One misbehaving rule must not abort the whole batch (library/CLI) or
    turn into a 500 (API): the registry runs 150+ rules, any of which can
    hit an AST shape its author didn't anticipate. WARNING rather than
    ERROR because the *query* isn't known to be wrong — the validator is —
    so a clean query shouldn't flip to INVALID over an internal bug.
    """
    return RuleViolation(
        rule_id=RULE_EXECUTION_ERROR_RULE_ID,
        severity=Severity.WARNING,
        message=(
            f"Internal error while running rule '{rule_id}': "
            f"{type(exc).__name__}: {exc}. The rule was skipped; all other "
            "rules still ran."
        ),
        suggested_fix=(
            "FREEFORM: this is a bug in fastssv, not in your SQL. Report it "
            f"(with the SQL and rule id '{rule_id}') at "
            "https://github.com/fastomop/fastSSV/issues."
        ),
        details={"failed_rule_id": rule_id, "error": f"{type(exc).__name__}: {exc}", "category": "internal"},
    )


def _run_rule(rule_cls, sql: str, dialect: str) -> List[RuleViolation]:
    """Instantiate and run one rule, isolating any exception it raises."""
    try:
        return rule_cls().validate(sql, dialect)
    except Exception as exc:
        rule_id = getattr(rule_cls, "rule_id", rule_cls.__name__)
        _logger.exception(f"Rule {rule_id} raised during validation; skipping it")
        return [_make_rule_error_violation(rule_id, exc)]


def _make_template_skipped_violation() -> RuleViolation:
    """Build the single warning emitted when the input is a SqlRender template.

    An unrendered template is a *category mismatch*, not a bug in the
    query: the file is meant to be processed by SqlRender first to
    substitute its ``@<identifier>`` placeholders. Running the rule
    catalog against it would only produce truthful-but-useless
    "table/column doesn't exist in CDM" errors for every placeholder
    name. We short-circuit with one structured WARNING so consumers see
    that fastssv noticed, why it skipped, and what to do about it.
    """
    return RuleViolation(
        rule_id=TEMPLATE_RULE_ID,
        severity=Severity.WARNING,
        message=(
            "Input contains unrendered SqlRender `@<identifier>` placeholders "
            "(e.g. inside a table name or as a schema/column qualifier). "
            "Rule validation was skipped — these files are templates, not "
            "concrete SQL."
        ),
        suggested_fix=(
            "REWRITE: run SqlRender first (``SqlRender::render(sql, ...)`` "
            "in R, or the equivalent Java/Python port) to substitute every "
            "@<param>, then re-submit the resulting SQL for validation."
        ),
        details={"category": "input_shape"},
    )


def _make_parse_error_violation(error_message: str, sql: str = "") -> RuleViolation:
    """Build a RuleViolation representing a parse failure.

    Parse errors prevent any rule from running meaningfully, so we surface
    them as a single ERROR-severity violation. Callers can distinguish a
    genuinely-clean query (empty list) from an unparseable one (single
    violation with rule_id in {PARSE_ERROR_RULE_ID, NOT_SQL_RULE_ID}).

    When the input looks like natural-language prose (e.g. an LLM refusal
    or explanation passed through to the validator by mistake), emit a
    distinct `parse.not_sql_input` violation. The dialect-retry suggestion
    that fits a real syntax error is actively misleading for prose input —
    upstream agent loops would otherwise burn turns retrying with `tsql`,
    `postgres`, etc. when the actual problem is "this isn't SQL at all."
    """
    from fastssv.core.helpers import looks_like_prose

    if sql and looks_like_prose(sql):
        preview = sql.strip().splitlines()[0][:120] if sql.strip() else ""
        return RuleViolation(
            rule_id=NOT_SQL_RULE_ID,
            severity=Severity.ERROR,
            message=(
                "Input does not appear to be a SQL query — looks like "
                "natural-language text (e.g. an explanation or model "
                "refusal). No validation was performed."
            ),
            suggested_fix=(
                "Submit a SQL statement (SELECT / WITH / INSERT / UPDATE "
                "/ DELETE / MERGE / DDL). If this came from an LLM, "
                "re-prompt for SQL — do not retry with a different "
                "dialect; the input is not SQL."
            ),
            details={"error": error_message, "input_preview": preview},
        )

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
    from fastssv.core.helpers import parse_sql, detect_dialect, looks_like_unrendered_template

    if dialect == "auto":
        dialect = detect_dialect(sql)

    # Unrendered SqlRender templates: short-circuit with a single WARNING
    # rather than running the rule catalogue against placeholder fragments.
    if looks_like_unrendered_template(sql):
        _logger.info("Skipped rule execution: input is an unrendered SqlRender template")
        return [_make_template_skipped_violation()]

    # Check parse status up-front so callers can distinguish clean SQL from
    # unparseable input. Individual rules also call parse_sql() internally and
    # quietly short-circuit on parse errors, but silent [] would otherwise
    # hide the failure from the caller.
    _, parse_error = parse_sql(sql, dialect)
    if parse_error:
        _logger.warning(f"Parse error (dialect={dialect!r}): {parse_error}")
        return [_make_parse_error_violation(parse_error, sql)]

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
        # Time rule execution if performance logging enabled
        start_time = time.perf_counter()
        rule_violations = _run_rule(rule_cls, sql, dialect)
        duration_ms = (time.perf_counter() - start_time) * 1000

        violations.extend(rule_violations)

        # Log rule execution
        log_rule_execution(_logger, rule_cls.rule_id, len(rule_violations), duration_ms)

    _logger.info(f"Executed {len(rule_classes)} rules, found {len(violations)} violations before deduplication")

    # Deduplicate violations (remove redundant errors for same issue)
    from fastssv.core.deduplication import deduplicate_violations

    violations = deduplicate_violations(violations)

    _logger.info(f"After deduplication: {len(violations)} unique violations")

    return violations


# Category validation helpers — legacy string-error API. Prefer
# `validate_sql(..., validators=<category>)` or `validate_sql_structured`
# in new code; these wrappers exist for backwards compatibility.
def _validate_category_strings(sql: str, category: str, dialect: str = "postgres") -> List[str]:
    from fastssv.core.helpers import parse_sql

    _, parse_error = parse_sql(sql, dialect)
    if parse_error:
        return [parse_error]

    results: List[str] = []
    for rule_cls in get_rules_by_category(category):
        for v in _run_rule(rule_cls, sql, dialect):
            prefix = "Warning: " if v.severity == Severity.WARNING else ""
            results.append(f"{prefix}{v.message}")
    return results


def validate_anti_patterns(sql: str, dialect: str = "postgres") -> List[str]:
    """Validate OMOP query anti-patterns.

    Detects common anti-patterns including:
    - String-based concept identification
    - Improper type concept usage
    - Context-dependent vocabulary lookups

    Returns list of error/warning messages.
    """
    return _validate_category_strings(sql, "anti_patterns", dialect)


def validate_concept_standardization(sql: str, dialect: str = "postgres") -> List[str]:
    """Validate concept standardization rules.

    Enforces:
    - Standard concept usage
    - Hierarchy expansion
    - Invalid reason checks
    - Domain validation
    - Source concept handling

    Returns list of error/warning messages.
    """
    return _validate_category_strings(sql, "concept_standardization", dialect)


def validate_data_quality(sql: str, dialect: str = "postgres") -> List[str]:
    """Validate data quality rules.

    Checks:
    - Schema validation
    - Unmapped concept handling
    - Negative concept ID validation
    - Column type validation
    - Data quality issues

    Returns list of error/warning messages.
    """
    return _validate_category_strings(sql, "data_quality", dialect)


def validate_domain_specific(sql: str, dialect: str = "postgres") -> List[str]:
    """Validate domain-specific rules.

    Table-specific validation for:
    - Condition, drug, measurement, observation
    - Person, procedure, visit, death domains
    - Cardinality awareness
    - Field validation

    Returns list of error/warning messages.
    """
    return _validate_category_strings(sql, "domain_specific", dialect)


def validate_joins(sql: str, dialect: str = "postgres") -> List[str]:
    """Validate join rules.

    Validates:
    - Foreign key relationships
    - Join path correctness
    - Concept relationship direction
    - Cross-table linkage requirements

    Returns list of error/warning messages.
    """
    return _validate_category_strings(sql, "joins", dialect)


def validate_temporal(sql: str, dialect: str = "postgres") -> List[str]:
    """Validate temporal rules.

    Validates:
    - Date logic
    - Observation period constraints
    - Temporal consistency across clinical events
    - NULL handling for date columns

    Returns list of error/warning messages.
    """
    return _validate_category_strings(sql, "temporal", dialect)


__all__ = [
    # Main API
    "validate_sql",
    "validate_sql_structured",
    "PARSE_ERROR_RULE_ID",
    "NOT_SQL_RULE_ID",
    "TEMPLATE_RULE_ID",
    "RULE_EXECUTION_ERROR_RULE_ID",
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
    "STANDARD_CONCEPT_FIELDS",
]

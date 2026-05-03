"""Cohort Definition Syntax Not Executable SQL Rule.

OMOP semantic rule OMOP_120:
cohort_definition.cohort_definition_syntax stores cohort logic definitions
(often JSON or OHDSI cohort expression format). It should NOT be:
1. Filtered with SQL-like string matching for cohort identification
2. Used in dynamic SQL execution

The Problem:
    cohort_definition.cohort_definition_syntax is a VARCHAR column that stores
    cohort logic metadata, typically in JSON or OHDSI cohort expression format.

    It is NOT executable SQL code.

    Common mistakes:
    1. Filtering with SQL keywords to identify cohort logic:
       WHERE cohort_definition_syntax LIKE '%SELECT%condition_occurrence%'
    2. Filtering with OMOP table names:
       WHERE cohort_definition_syntax LIKE '%drug_exposure%'
    3. Attempting to execute it as dynamic SQL (less common in static queries)

Why this is wrong:
    - cohort_definition_syntax stores cohort DEFINITION metadata (JSON/OHDSI format)
    - It's not SQL code that should be parsed or executed
    - Filtering by SQL keywords/table names is a fundamental misunderstanding
    - Use cohort_definition_name or cohort_definition_id for identification

Violation patterns:
    SELECT cohort_definition_syntax
    FROM cohort_definition
    WHERE cohort_definition_syntax LIKE '%SELECT%condition_occurrence%'
    -- ERROR: Filtering cohort definition metadata by SQL keywords

    SELECT *
    FROM cohort_definition
    WHERE cohort_definition_syntax LIKE '%drug_exposure%'
    -- ERROR: Filtering cohort definition metadata by table names

Correct patterns:
    SELECT cohort_definition_id, cohort_definition_name
    FROM cohort_definition
    WHERE cohort_definition_name LIKE '%diabetes%'
    -- OK: Filtering by cohort name, not the definition syntax

    SELECT cohort_definition_syntax
    FROM cohort_definition
    WHERE cohort_definition_id = 123
    -- OK: Retrieving definition syntax by ID, not filtering it
"""

import logging
import re
from typing import List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

COHORT_DEFINITION = "cohort_definition"
COHORT_DEFINITION_SYNTAX = "cohort_definition_syntax"

SQL_KEYWORDS: Set[str] = {
    "select",
    "insert",
    "update",
    "delete",
    "from",
    "join",
    "where",
    "having",
    "group by",
    "order by",
    "union",
    "intersect",
    "except",
}

OMOP_TABLES: Set[str] = {
    "person",
    "observation_period",
    "visit_occurrence",
    "visit_detail",
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "device_exposure",
    "measurement",
    "observation",
    "death",
    "note",
    "specimen",
    "fact_relationship",
    "location",
    "care_site",
    "provider",
    "payer_plan_period",
    "cost",
    "drug_era",
    "dose_era",
    "condition_era",
}


# --- Helpers -----------------------------------------------------------------


def _extract_string_literal(expr: exp.Expression) -> str:
    """
    Safely extract string literal value from sqlglot expression.
    Returns normalized lowercase string or empty string.
    """
    if isinstance(expr, exp.Literal) and expr.is_string:
        # Normalize whitespace to handle "group   by", newlines, etc.
        return re.sub(r"\s+", " ", expr.this.lower())
    return ""


def _contains_term(pattern: str, terms: Set[str]) -> bool:
    """
    Check if pattern contains any term using word-boundary matching.
    Prevents false positives like 'selective' or 'personality'.
    """
    for term in terms:
        if re.search(rf"\b{re.escape(term)}\b", pattern):
            return True
    return False


def _is_target_column(table: str, column: str, aliases: dict) -> bool:
    """
    Determine whether a column refers to cohort_definition.cohort_definition_syntax.
    """
    if normalize_name(column) != COHORT_DEFINITION_SYNTAX:
        return False

    if table:
        return normalize_name(table) == COHORT_DEFINITION

    # Unqualified column: allow only if cohort_definition is present
    return any(normalize_name(t) == COHORT_DEFINITION for t in aliases.values())


def _analyze_like_node(node: exp.Expression, aliases: dict) -> str:
    """
    Analyze a LIKE/ILIKE expression and return violation message if any.
    """
    check_node = node

    # Handle NOT LIKE / NOT ILIKE
    if isinstance(node, exp.Not):
        inner = node.this
        if isinstance(inner, (exp.Like, exp.ILike)):
            check_node = inner
        else:
            return ""

    elif not isinstance(node, (exp.Like, exp.ILike)):
        return ""

    left = check_node.this
    right = check_node.expression

    if not isinstance(left, exp.Column):
        return ""

    table, column = resolve_table_col(left, aliases)

    if not _is_target_column(table, column, aliases):
        return ""

    pattern = _extract_string_literal(right)
    if not pattern:
        return ""

    has_sql = _contains_term(pattern, SQL_KEYWORDS)
    has_table = _contains_term(pattern, OMOP_TABLES)

    if not (has_sql or has_table):
        return ""

    violation_type = []
    if has_sql:
        violation_type.append("SQL keywords")
    if has_table:
        violation_type.append("OMOP table names")

    return (
        f"cohort_definition_syntax is filtered with {' and '.join(violation_type)}. "
        "This column stores cohort definition metadata (JSON/OHDSI format), not executable SQL. "
        "Use cohort_definition_name or cohort_definition_id for filtering instead."
    )


def _find_violations(tree: exp.Expression, aliases: dict) -> List[str]:
    """
    Traverse AST and collect violations.
    """
    issues: List[str] = []
    seen = set()

    for node in tree.walk():
        msg = _analyze_like_node(node, aliases)
        if msg and msg not in seen:
            issues.append(msg)
            seen.add(msg)

    return issues


# --- Rule --------------------------------------------------------------------


@register
class CohortDefinitionSyntaxNotExecutableSqlRule(Rule):
    """
    OMOP_120: Prevent misuse of cohort_definition_syntax column.
    """

    rule_id = "domain_specific.cohort_definition_syntax_not_executable_sql"
    name = "Cohort Definition Syntax Not Executable SQL"

    description = (
        "cohort_definition_syntax stores cohort definition metadata (JSON/OHDSI format), "
        "not executable SQL. Do not filter it using SQL keywords or OMOP table names."
    )

    severity = Severity.ERROR

    suggested_fix = "REMOVE: text predicates on cohort_definition.cohort_definition_syntax (it stores JSON/OHDSI metadata, not executable SQL). Filter on cohort_definition_id or cohort_definition_name instead."
    example_bad = "SELECT cohort_definition_id FROM cohort_definition\nWHERE cohort_definition_syntax LIKE '%SELECT%';"
    example_good = "SELECT cohort_definition_id, cohort_definition_syntax\nFROM cohort_definition;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter
        if COHORT_DEFINITION_SYNTAX not in sql_lower:
            return []

        if COHORT_DEFINITION not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_120",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["CohortDefinitionSyntaxNotExecutableSqlRule"]

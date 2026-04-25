"""Concept ID String Comparison Rule.

OMOP semantic rule OMOP_157:
All *_concept_id columns are INTEGER type. Comparing them with string literals
(e.g., concept_id = '201826') relies on implicit type casting and may fail or
behave unexpectedly depending on the database engine.

The Problem:
    All columns ending in `_concept_id` store integer values representing OMOP concepts.
    Comparing these columns with quoted string literals forces the database to perform
    implicit type conversion, which:

    - Degrades query performance (string-to-integer conversion for every row)
    - May fail on some database engines with strict type checking
    - Indicates sloppy coding practices
    - Can produce unexpected results depending on database collation/casting rules

Violation patterns:
    SELECT * FROM condition_occurrence WHERE condition_concept_id = '201826'
    -- WARNING: String literal '201826' requires implicit casting

    SELECT * FROM measurement WHERE measurement_concept_id IN ('3004249', '3012888')
    -- WARNING: String literals in IN clause require implicit casting

    SELECT * FROM drug_exposure WHERE drug_concept_id != '1234567'
    -- WARNING: String literal in comparison

Correct patterns:
    SELECT * FROM condition_occurrence WHERE condition_concept_id = 201826
    -- OK: Integer literal, no casting needed

    SELECT * FROM measurement WHERE measurement_concept_id IN (3004249, 3012888)
    -- OK: Integer literals

    SELECT * FROM drug_exposure WHERE drug_concept_id != 1234567
    -- OK: Integer literal

Note:
    This is a WARNING, not an ERROR. Most databases will handle the implicit
    conversion correctly, but it's a code quality issue that should be fixed.
"""

import logging
from typing import Dict, List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

OPERATOR_MAP: Dict[type, str] = {
    exp.EQ: "=",
    exp.NEQ: "!=",
    exp.LT: "<",
    exp.LTE: "<=",
    exp.GT: ">",
    exp.GTE: ">=",
}


# --- Helpers -----------------------------------------------------------------

def _is_concept_id_column(col_name: str) -> bool:
    if not col_name:
        return False
    normalized = normalize_name(col_name)
    return normalized == "concept_id" or normalized.endswith("_concept_id")


def _safe_suggest_value(value: str) -> str:
    """
    Only suggest a replacement if it's a valid integer.
    Otherwise return placeholder.
    """
    return value if value.isdigit() else "<integer_value>"


def _check_comparison(sql: str, node: exp.Expression, aliases: dict) -> List[RuleViolation]:
    violations = []

    left = node.this
    right = node.expression

    if not left or not right:
        return violations

    operator = OPERATOR_MAP.get(type(node), "=")

    # Handle both directions
    for col_node, lit_node in [(left, right), (right, left)]:
        if not isinstance(col_node, exp.Column):
            continue

        if not is_string_literal(lit_node):
            continue

        table, col = resolve_table_col(col_node, aliases)
        if not col:
            col = col_node.name

        if not _is_concept_id_column(col):
            continue

        suggested_value = _safe_suggest_value(lit_node.this)

        # Structured patch: REPLACE the quoted string literal with its bare
        # integer form when the value is digit-only. Try both quote styles.
        # If the literal is not uniquely locatable, fall back to FREEFORM.
        patch = None
        raw = str(lit_node.this) if lit_node.this is not None else ""
        if raw.isdigit():
            for q in ("'", '"'):
                span = locate(sql, f"{q}{raw}{q}")
                if span is not None:
                    patch = patch_replace(span, raw)
                    break

        violations.append(
            RuleViolation(
                rule_id="data_quality.concept_id_string_comparison",
                severity=Severity.WARNING,
                message=(
                    f"Concept ID column compared with string literal: "
                    f"{col_node.sql()} {operator} {lit_node.sql()}"
                ),
                suggested_fix=(
                    f"REPLACE: `{col_node.sql()} {operator} '<digits>'` (string literal) WITH "
                    f"`{col_node.sql()} {operator} {suggested_value}` (integer literal). "
                    f"All *_concept_id columns are INTEGER."
                ),
                suggested_fix_patch=patch,
                details={
                    "column": col,
                    "table": table or "unknown",
                    "operator": operator,
                    "string_value": lit_node.this,
                },
            )
        )

    return violations


def _check_in_clause(sql: str, node: exp.In, aliases: dict) -> List[RuleViolation]:
    violations = []

    col_expr = node.this
    if not isinstance(col_expr, exp.Column):
        return violations

    string_values = [
        val.this for val in (node.expressions or []) if is_string_literal(val)
    ]

    if not string_values:
        return violations

    table, col = resolve_table_col(col_expr, aliases)
    if not col:
        col = col_expr.name

    if not _is_concept_id_column(col):
        return violations

    # Check if parent is NOT (for NOT IN clause)
    is_not = isinstance(node.parent, exp.Not)
    operator = "NOT IN" if is_not else "IN"

    sample = string_values[:3]
    values_display = ", ".join(f"'{v}'" for v in sample)
    if len(string_values) > 3:
        values_display += ", ..."

    suggested = ", ".join(_safe_suggest_value(v) for v in sample)
    if len(string_values) > 3:
        suggested += ", ..."

    # Structured patch: when *every* value in the IN list is digit-only,
    # REPLACE the entire IN expression with the integer-literal form. We
    # locate the original IN SQL fragment in source and substitute with
    # the corrected text. Falls back to FREEFORM for ambiguous matches.
    patch = None
    all_digits = [
        str(val.this) for val in (node.expressions or []) if is_string_literal(val)
    ]
    if all_digits and all(s.isdigit() for s in all_digits):
        # Build a corrected IN body and try to locate the original IN node
        # span in source. We try the node.sql() as a single fragment.
        in_sql = node.sql()
        corrected = in_sql
        for q in ("'", '"'):
            for raw in all_digits:
                corrected = corrected.replace(f"{q}{raw}{q}", raw)
        if corrected != in_sql:
            span = locate(sql, in_sql)
            if span is not None:
                patch = patch_replace(span, corrected)

    violations.append(
        RuleViolation(
            rule_id="data_quality.concept_id_string_comparison",
            severity=Severity.WARNING,
            message=(
                f"Concept ID column compared with string literals in {operator} clause: "
                f"{col_expr.sql()} {operator} ({values_display})"
            ),
            suggested_fix=(
                f"REPLACE: `{col_expr.sql()} {operator} ('<digits>', ...)` (string literals) WITH "
                f"`{col_expr.sql()} {operator} ({suggested})` (integer literals). "
                f"All *_concept_id columns are INTEGER."
            ),
            suggested_fix_patch=patch,
            details={
                "column": col,
                "table": table or "unknown",
                "operator": operator,
                "string_count": len(string_values),
            },
        )
    )

    return violations


def _find_violations(sql: str, tree: exp.Expression) -> List[RuleViolation]:
    violations: List[RuleViolation] = []

    aliases = extract_aliases(tree)

    # Comparisons
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE):
        violations.extend(_check_comparison(sql, node, aliases))

    # IN (handles both IN and NOT IN by checking parent)
    for node in tree.find_all(exp.In):
        violations.extend(_check_in_clause(sql, node, aliases))

    # Deduplicate by message
    unique = {}
    for v in violations:
        unique[v.message] = v

    return list(unique.values())


# --- Rule --------------------------------------------------------------------

@register
class ConceptIdStringComparisonRule(Rule):
    """
    OMOP_157: Prevent comparing integer *_concept_id columns with string literals.
    """

    rule_id = "data_quality.concept_id_string_comparison"
    name = "Concept ID String Comparison"

    description = (
        "All *_concept_id columns are INTEGER type. Comparing them with string literals "
        "requires implicit casting and may lead to incorrect results."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `<col>_concept_id = '<digits>'` (string literal) WITH `<col>_concept_id = <digits>` (integer literal). All *_concept_id columns are INTEGER."
    long_description = (
        "Every *_concept_id column in OMOP is an INTEGER. Comparing it to a "
        "string literal forces the database to implicitly cast one side per "
        "row. In PostgreSQL this usually works but bypasses the concept_id "
        "index; in SQL Server and BigQuery it can raise a conversion error "
        "or, worse, silently evaluate to FALSE for every row. Writing the "
        "literal as a plain integer keeps index usage intact and the "
        "semantics identical across every supported dialect."
    )
    example_bad = (
        "SELECT person_id\n"
        "FROM person\n"
        "WHERE gender_concept_id = '8532';"
    )
    example_good = (
        "SELECT person_id\n"
        "FROM person\n"
        "WHERE gender_concept_id = 8532;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        if "concept_id" not in sql.lower():
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_157",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            violations.extend(_find_violations(sql, tree))

        return violations


__all__ = ["ConceptIdStringComparisonRule"]

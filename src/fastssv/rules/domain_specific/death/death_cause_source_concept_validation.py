"""Death Cause Source Concept Validation Rule.

OMOP semantic rule CLIN_052:
Validates that death.cause_source_concept_id is not used for analytical filtering.
The source concept should only be used for ETL validation or data quality checks.
For cohort identification and analytical queries, use death.cause_concept_id instead.

CLIN_052 (source concept usage constraint for death):
The death_cause_source_concept_id column stores the original source vocabulary concept
mapping. For standard analytical queries and cohort identification, use the standard
cause_concept_id instead.

The Problem:
    Using death_cause_source_concept_id in WHERE clauses or JOINs is incorrect for
    analytical work because:
    - It represents source/local vocabulary codes, not standardized OMOP concepts
    - Analytical queries should use standardized concepts for reproducibility
    - Source concepts are intended for ETL validation, mapping verification, or provenance tracking

Violation patterns:
    SELECT * FROM death WHERE cause_source_concept_id = 123
    -- Should use cause_concept_id instead

    SELECT d.* FROM death d
    WHERE d.cause_source_concept_id IN (456, 789)
    -- Should filter on cause_concept_id

Correct patterns:
    SELECT * FROM death WHERE cause_concept_id = 123
    SELECT d.* FROM death d
    WHERE d.cause_concept_id IN (456, 789)
"""

from typing import Dict, List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


TABLE_NAME = "death"
SOURCE_COLUMN = "cause_source_concept_id"
STANDARD_COLUMN = "cause_concept_id"


def _norm(x: str | None) -> str | None:
    return normalize_name(x) if x else None


def _is_in_or(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Or):
            return True
        parent = parent.parent
    return False


def _is_source_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != SOURCE_COLUMN:
        return False

    if table:
        return _norm(table) == TABLE_NAME

    return TABLE_NAME in {_norm(t) for t in aliases.values()}


def _contains_source_column(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    for col in node.find_all(exp.Column):
        if _is_source_column(col, aliases):
            return True
    return False


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    violations: List[str] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_or(node):
            continue

        # Skip NULL checks
        if isinstance(node, (exp.Is, exp.Not)):
            continue

        if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            if _contains_source_column(node, aliases):
                key = node.sql()
                if key not in seen:
                    seen.add(key)
                    violations.append(
                        f"Filtering on death.{SOURCE_COLUMN} is discouraged for analytical queries. "
                        f"Use death.{STANDARD_COLUMN} instead. Source concepts are intended for ETL validation and provenance."
                    )

        elif isinstance(node, (exp.In, exp.Between)):
            if _contains_source_column(node, aliases):
                key = node.sql()
                if key not in seen:
                    seen.add(key)
                    violations.append(
                        f"Filtering on death.{SOURCE_COLUMN} is discouraged for analytical queries. "
                        f"Use death.{STANDARD_COLUMN} instead. Source concepts are intended for ETL validation and provenance."
                    )

    return violations


@register
class DeathCauseSourceConceptValidationRule(Rule):
    rule_id = "domain_specific.death_cause_source_concept_validation"
    name = "Death Cause Source Concept Not For Analytical Filtering"

    description = (
        "Avoid using death.cause_source_concept_id for analytical filtering. "
        "Use death.cause_concept_id instead."
    )

    severity = Severity.ERROR
    suggested_fix = "Replace with death.cause_concept_id"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree or not has_table_reference(tree, TABLE_NAME):
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


__all__ = ["DeathCauseSourceConceptValidationRule"]

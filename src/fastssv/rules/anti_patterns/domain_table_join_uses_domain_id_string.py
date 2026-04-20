"""Domain Table Join Uses Domain ID String Rule.

OMOP semantic rule OMOP_129:
Joins from concept to domain must use concept.domain_id = domain.domain_id
(both VARCHAR). Using domain.domain_concept_id for this join is incorrect —
domain_concept_id is the concept that represents the domain itself.

The Problem:
    The domain table has two important columns:
    - domain_id (VARCHAR): The unique identifier/code for the domain
      (e.g., "Condition", "Drug", "Procedure", "Measurement")
    - domain_concept_id (INTEGER): A concept_id that represents the domain
      as a concept in the OMOP vocabulary

    When joining concept to domain, you must use the VARCHAR domain_id column,
    NOT the INTEGER domain_concept_id.

Why this is wrong:
    - Type mismatch: domain_id is VARCHAR, domain_concept_id is INTEGER
    - Semantic error: domain_concept_id represents the domain as a concept,
      not as a foreign key relationship
    - The correct join key is domain_id (string to string)

Violation patterns:
    SELECT * FROM concept c
    JOIN domain d ON c.domain_id = d.domain_concept_id
    -- ERROR: Joining VARCHAR to INTEGER, semantically incorrect

    SELECT c.concept_name, d.domain_name
    FROM concept c
    INNER JOIN domain d ON c.domain_id = d.domain_concept_id
    -- ERROR: Wrong join column

Correct patterns:
    SELECT * FROM concept c
    JOIN domain d ON c.domain_id = d.domain_id
    -- OK: Both are VARCHAR, semantically correct

    SELECT c.concept_name, d.domain_name
    FROM concept c
    INNER JOIN domain d ON c.domain_id = d.domain_id
    -- OK: Correct join using string identifier

Note:
    This is an ERROR, not a WARNING. Joining on domain_concept_id instead of
    domain_id will produce incorrect results.
"""

import logging
from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

CONCEPT_TABLE = "concept"
DOMAIN_TABLE = "domain"
DOMAIN_ID_COL = "domain_id"
DOMAIN_CONCEPT_ID_COL = "domain_concept_id"

ERROR_MSG = (
    "JOIN between concept and domain uses domain_concept_id. "
    "Use domain.domain_id instead. "
    "domain_concept_id is not the foreign key for joining."
)


# --- Helpers -----------------------------------------------------------------

def _normalize_table_col(table: str, col: str):
    """Normalize table and column names safely."""
    if not table or not col:
        return None, None
    return normalize_name(table), normalize_name(col)


def _is_target_tables(t1: str, t2: str) -> bool:
    """Check if join is strictly between concept and domain."""
    return {t1, t2} == {CONCEPT_TABLE, DOMAIN_TABLE}


def _is_incorrect_pattern(t1: str, c1: str, t2: str, c2: str) -> bool:
    """Check incorrect join pattern."""
    return (
        t1 == CONCEPT_TABLE
        and c1 == DOMAIN_ID_COL
        and t2 == DOMAIN_TABLE
        and c2 == DOMAIN_CONCEPT_ID_COL
    ) or (
        t2 == CONCEPT_TABLE
        and c2 == DOMAIN_ID_COL
        and t1 == DOMAIN_TABLE
        and c1 == DOMAIN_CONCEPT_ID_COL
    )


def _find_violations(tree: exp.Expression) -> List[str]:
    issues: List[str] = []

    # Fast guard
    if not has_table_reference(tree, CONCEPT_TABLE) or not has_table_reference(tree, DOMAIN_TABLE):
        return []

    aliases = extract_aliases(tree)

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left = eq.this
            right = eq.expression

            if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                continue

            left_table, left_col = resolve_table_col(left, aliases)
            right_table, right_col = resolve_table_col(right, aliases)

            left_table, left_col = _normalize_table_col(left_table, left_col)
            right_table, right_col = _normalize_table_col(right_table, right_col)

            if not left_table or not right_table:
                continue

            if not _is_target_tables(left_table, right_table):
                continue

            if _is_incorrect_pattern(left_table, left_col, right_table, right_col):
                issues.append(ERROR_MSG)

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class DomainTableJoinUsesDomainIdRule(Rule):
    """
    OMOP_129: Ensure concept-domain joins use domain_id, not domain_concept_id.
    """

    rule_id = "anti_patterns.domain_table_join_uses_domain_id"
    name = "Domain Table Join Uses Domain ID"

    description = (
        "Joins from concept to domain must use concept.domain_id = domain.domain_id. "
        "Using domain_concept_id is incorrect."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Change JOIN condition to: concept.domain_id = domain.domain_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter
        if CONCEPT_TABLE not in sql_lower or DOMAIN_TABLE not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_129",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            issues = _find_violations(tree)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["DomainTableJoinUsesDomainIdRule"]
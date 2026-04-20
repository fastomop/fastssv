"""Concept Class Table Join Uses Concept Class ID String Rule.

OMOP semantic rule OMOP_130:
Joins from concept to concept_class must use concept.concept_class_id = concept_class.concept_class_id
(both VARCHAR). Using concept_class.concept_class_concept_id for this join is incorrect —
concept_class_concept_id is the concept that represents the concept class itself.

The Problem:
    The concept_class table has two important columns:
    - concept_class_id (VARCHAR): The unique identifier/code for the concept class
      (e.g., "Clinical Drug", "Ingredient", "Procedure", "Clinical Finding")
    - concept_class_concept_id (INTEGER): A concept_id that represents the concept class
      as a concept in the OMOP vocabulary

    When joining concept to concept_class, you must use the VARCHAR concept_class_id column,
    NOT the INTEGER concept_class_concept_id.

Why this is wrong:
    - Type mismatch: concept_class_id is VARCHAR, concept_class_concept_id is INTEGER
    - Semantic error: concept_class_concept_id represents the concept class as a concept,
      not as a foreign key relationship
    - The correct join key is concept_class_id (string to string)

Violation patterns:
    SELECT * FROM concept c
    JOIN concept_class cc ON c.concept_class_id = cc.concept_class_concept_id
    -- ERROR: Joining VARCHAR to INTEGER, semantically incorrect

    SELECT c.concept_name, cc.concept_class_name
    FROM concept c
    INNER JOIN concept_class cc ON c.concept_class_id = cc.concept_class_concept_id
    -- ERROR: Wrong join column

Correct patterns:
    SELECT * FROM concept c
    JOIN concept_class cc ON c.concept_class_id = cc.concept_class_id
    -- OK: Both are VARCHAR, semantically correct

    SELECT c.concept_name, cc.concept_class_name
    FROM concept c
    INNER JOIN concept_class cc ON c.concept_class_id = cc.concept_class_id
    -- OK: Correct join using string identifier

Note:
    This is an ERROR, not a WARNING. Joining on concept_class_concept_id instead of
    concept_class_id will produce incorrect results.
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
CONCEPT_CLASS_TABLE = "concept_class"
CONCEPT_CLASS_ID_COL = "concept_class_id"
CONCEPT_CLASS_CONCEPT_ID_COL = "concept_class_concept_id"

ERROR_MSG = (
    "JOIN between concept and concept_class uses concept_class_concept_id. "
    "Use concept_class.concept_class_id instead. "
    "concept_class_concept_id is not the foreign key for joining."
)


# --- Helpers -----------------------------------------------------------------

def _normalize_table_col(table: str, col: str):
    """Normalize table and column names safely."""
    if not table or not col:
        return None, None
    return normalize_name(table), normalize_name(col)


def _is_target_tables(t1: str, t2: str) -> bool:
    """Check if join is strictly between concept and concept_class."""
    return {t1, t2} == {CONCEPT_TABLE, CONCEPT_CLASS_TABLE}


def _is_incorrect_pattern(t1: str, c1: str, t2: str, c2: str) -> bool:
    """Check incorrect join pattern."""
    return (
        t1 == CONCEPT_TABLE
        and c1 == CONCEPT_CLASS_ID_COL
        and t2 == CONCEPT_CLASS_TABLE
        and c2 == CONCEPT_CLASS_CONCEPT_ID_COL
    ) or (
        t2 == CONCEPT_TABLE
        and c2 == CONCEPT_CLASS_ID_COL
        and t1 == CONCEPT_CLASS_TABLE
        and c1 == CONCEPT_CLASS_CONCEPT_ID_COL
    )


def _find_violations(tree: exp.Expression) -> List[str]:
    issues: List[str] = []

    # Fast guard
    if not has_table_reference(tree, CONCEPT_TABLE) or not has_table_reference(tree, CONCEPT_CLASS_TABLE):
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
class ConceptClassTableJoinUsesConceptClassIdRule(Rule):
    """
    OMOP_130: Ensure concept-concept_class joins use concept_class_id, not concept_class_concept_id.
    """

    rule_id = "anti_patterns.concept_class_table_join_uses_concept_class_id"
    name = "Concept Class Table Join Uses Concept Class ID"

    description = (
        "Joins from concept to concept_class must use concept.concept_class_id = concept_class.concept_class_id. "
        "Using concept_class_concept_id is incorrect."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Change JOIN condition to: concept.concept_class_id = concept_class.concept_class_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter
        if CONCEPT_TABLE not in sql_lower or CONCEPT_CLASS_TABLE not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_130",
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


__all__ = ["ConceptClassTableJoinUsesConceptClassIdRule"]
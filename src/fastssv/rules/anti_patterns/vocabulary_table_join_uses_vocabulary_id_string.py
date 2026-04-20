"""Vocabulary Table Join Uses Vocabulary ID String Rule.

OMOP semantic rule OMOP_128:
Joins from concept to vocabulary must use concept.vocabulary_id = vocabulary.vocabulary_id
(both VARCHAR). Using vocabulary.vocabulary_concept_id for this join is incorrect —
vocabulary_concept_id is the concept that represents the vocabulary itself.

The Problem:
    The vocabulary table has two important columns:
    - vocabulary_id (VARCHAR): The unique identifier/code for the vocabulary
      (e.g., "SNOMED", "LOINC", "RxNorm")
    - vocabulary_concept_id (INTEGER): A concept_id that represents the vocabulary
      as a concept in the OMOP vocabulary

    When joining concept to vocabulary, you must use the VARCHAR vocabulary_id column,
    NOT the INTEGER vocabulary_concept_id.

Why this is wrong:
    - Type mismatch: vocabulary_id is VARCHAR, vocabulary_concept_id is INTEGER
    - Semantic error: vocabulary_concept_id represents the vocabulary as a concept,
      not as a foreign key relationship
    - The correct join key is vocabulary_id (string to string)

Violation patterns:
    SELECT * FROM concept c
    JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_concept_id
    -- ERROR: Joining VARCHAR to INTEGER, semantically incorrect

    SELECT c.concept_name, v.vocabulary_name
    FROM concept c
    INNER JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_concept_id
    -- ERROR: Wrong join column

Correct patterns:
    SELECT * FROM concept c
    JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_id
    -- OK: Both are VARCHAR, semantically correct

    SELECT c.concept_name, v.vocabulary_name
    FROM concept c
    INNER JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_id
    -- OK: Correct join using string identifier

Note:
    This is an ERROR, not a WARNING. Joining on vocabulary_concept_id instead of
    vocabulary_id will produce incorrect results.
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


CONCEPT_TABLE = "concept"
VOCABULARY_TABLE = "vocabulary"
VOCABULARY_ID_COL = "vocabulary_id"
VOCABULARY_CONCEPT_ID_COL = "vocabulary_concept_id"


ERROR_MSG = (
    "JOIN between concept and vocabulary uses vocabulary_concept_id. "
    "Use vocabulary.vocabulary_id instead. "
    "vocabulary_concept_id is not the foreign key for joining."
)


# --- Helpers -----------------------------------------------------------------

def _find_violations(tree: exp.Expression) -> List[str]:
    issues: List[str] = []

    if not has_table_reference(tree, CONCEPT_TABLE) or not has_table_reference(tree, VOCABULARY_TABLE):
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

            if not left_table or not right_table:
                continue

            left_table_norm = normalize_name(left_table)
            right_table_norm = normalize_name(right_table)
            left_col_norm = normalize_name(left_col)
            right_col_norm = normalize_name(right_col)

            # Ensure join is between concept and vocabulary
            tables = {left_table_norm, right_table_norm}
            if tables != {CONCEPT_TABLE, VOCABULARY_TABLE}:
                continue

            # Check incorrect join pattern
            if (
                left_table_norm == CONCEPT_TABLE
                and left_col_norm == VOCABULARY_ID_COL
                and right_table_norm == VOCABULARY_TABLE
                and right_col_norm == VOCABULARY_CONCEPT_ID_COL
            ) or (
                right_table_norm == CONCEPT_TABLE
                and right_col_norm == VOCABULARY_ID_COL
                and left_table_norm == VOCABULARY_TABLE
                and left_col_norm == VOCABULARY_CONCEPT_ID_COL
            ):
                issues.append(ERROR_MSG)

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class VocabularyTableJoinUsesVocabularyIdStringRule(Rule):
    """
    OMOP_128: Ensure concept-vocabulary joins use vocabulary_id, not vocabulary_concept_id.
    """

    rule_id = "anti_patterns.vocabulary_table_join_uses_vocabulary_id_string"
    name = "Vocabulary Table Join Uses Vocabulary ID String"

    description = (
        "Joins from concept to vocabulary must use concept.vocabulary_id = vocabulary.vocabulary_id. "
        "Using vocabulary_concept_id is incorrect."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Change JOIN condition to: concept.vocabulary_id = vocabulary.vocabulary_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()
        if CONCEPT_TABLE not in sql_lower or VOCABULARY_TABLE not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_128",
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


__all__ = ["VocabularyTableJoinUsesVocabularyIdStringRule"]
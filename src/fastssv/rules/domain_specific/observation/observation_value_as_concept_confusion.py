"""Observation Value As Concept Confusion Validation Rule.

OMOP semantic rule CLIN_034 (partial):
Detects when the same concept_id is used for both observation_concept_id and value_as_concept_id.

CLIN_034 (observation_value_as_concept_domain_constraint - question/answer confusion):
observation_concept_id represents the "question" (what is being observed).
value_as_concept_id represents the "answer" (the result of the observation).
Using the same concept_id for both is logically incorrect.

The Problem:
    observation_concept_id = "What are you measuring?" (e.g., "Blood pressure")
    value_as_concept_id = "What is the answer?" (e.g., "High", "Low", "Normal")

    Using the same concept_id for both means you're saying:
    "I'm measuring Blood Pressure, and the answer is Blood Pressure" - which is nonsensical.

    Common mistakes:
    - Copying the same concept_id to both columns
    - Not understanding the difference between observation type and observation result
    - Using observation concepts as answers instead of answer-set concepts

Violation patterns:
    SELECT * FROM observation
    WHERE observation_concept_id = 4058286
      AND value_as_concept_id = 4058286
    -- ERROR: Same concept used as both question and answer

    SELECT * FROM observation
    WHERE observation_concept_id IN (4058286, 3004249)
      AND value_as_concept_id IN (4058286, 3016502)
    -- ERROR: 4058286 appears in both lists (overlap)

Correct patterns:
    SELECT * FROM observation
    WHERE observation_concept_id = 4058286
      AND value_as_concept_id = 45877994
    -- OK: Different concepts for question and answer

    SELECT * FROM observation
    WHERE observation_concept_id = 4058286
      AND value_as_number > 120
    -- OK: Using numeric value instead of concept value
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register


TABLE_NAME = "observation"
OBSERVATION_CONCEPT_ID = "observation_concept_id"
VALUE_AS_CONCEPT_ID = "value_as_concept_id"


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_observation_column(col: exp.Column, aliases: Dict[str, str], col_name: str) -> bool:
    table, column = resolve_table_col(col, aliases)

    if _norm(column) != col_name:
        return False

    if table:
        return _norm(table) == TABLE_NAME

    return TABLE_NAME in {_norm(t) for t in aliases.values()}


def _extract_int(node: exp.Expression) -> Optional[int]:
    if isinstance(node, exp.Neg):
        inner = node.this
        if isinstance(inner, exp.Literal) and inner.is_int:
            try:
                return -int(inner.this)
            except Exception:
                return None
        return None

    if isinstance(node, exp.Literal) and node.is_int:
        try:
            return int(node.this)
        except Exception:
            return None

    return None


def _collect_ids_from_expr(
    expr: exp.Expression,
    aliases: Dict[str, str],
    col_name: str,
) -> Set[int]:
    ids: Set[int] = set()

    for node in expr.walk():
        if isinstance(node, (exp.EQ, exp.In)):
            if isinstance(node, exp.EQ):
                pairs = [(node.this, node.expression), (node.expression, node.this)]
                for col_node, val_node in pairs:
                    if isinstance(col_node, exp.Column) and _is_observation_column(col_node, aliases, col_name):
                        v = _extract_int(val_node)
                        if v is not None:
                            ids.add(v)

            else:
                col_node = node.this
                if isinstance(col_node, exp.Column) and _is_observation_column(col_node, aliases, col_name):
                    for val in node.expressions or []:
                        v = _extract_int(val)
                        if v is not None:
                            ids.add(v)

    return ids


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    violations: List[str] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if not isinstance(node, exp.And):
            continue

        left = node.this
        right = node.expression

        left_obs = _collect_ids_from_expr(left, aliases, OBSERVATION_CONCEPT_ID)
        left_val = _collect_ids_from_expr(left, aliases, VALUE_AS_CONCEPT_ID)

        right_obs = _collect_ids_from_expr(right, aliases, OBSERVATION_CONCEPT_ID)
        right_val = _collect_ids_from_expr(right, aliases, VALUE_AS_CONCEPT_ID)

        overlap = (left_obs & right_val) | (left_val & right_obs)

        if overlap:
            key = tuple(sorted(overlap))
            if key in seen:
                continue
            seen.add(key)

            violations.append(
                f"Concept(s) {sorted(overlap)} used for both {OBSERVATION_CONCEPT_ID} "
                f"and {VALUE_AS_CONCEPT_ID} within the same AND condition. "
                f"This likely confuses the observation (question) with its value (answer)."
            )

    return violations


@register
class ObservationValueAsConceptConfusionRule(Rule):
    rule_id = "semantic.observation_value_as_concept_confusion"
    name = "Observation Value As Concept Confusion"

    description = (
        "Detects when the same concept_id is used for both observation_concept_id "
        "and value_as_concept_id within the same logical condition."
    )

    severity = Severity.ERROR
    suggested_fix = (
        "Use different concepts for observation_concept_id (question) "
        "and value_as_concept_id (answer)"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not uses_table(tree, TABLE_NAME):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for message in issues:
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["ObservationValueAsConceptConfusionRule"]
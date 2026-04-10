"""Measurement Operator Concept Validation Rule.

OMOP semantic rule CLIN_026:
Validates that measurement.operator_concept_id uses only valid operator concepts.

CLIN_026 (operator_concept_id constraint):
operator_concept_id must reference one of the 5 standard operator concepts:
- 4171756 (<)
- 4172704 (>)
- 4171755 (=)
- 4171754 (<=)
- 4172703 (>=)

The Problem:
    operator_concept_id indicates the comparison operator for value_as_number.
    Only 5 specific concept_ids are valid operators in OMOP CDM.
    Using any other concept_id is incorrect and will cause data integrity issues.

    Common mistakes:
    - Using random concept_ids as operators
    - Using measurement concept_ids instead of operator concept_ids
    - Hardcoding invalid operator values

Violation patterns:
    SELECT * FROM measurement WHERE operator_concept_id = 201826
    -- 201826 is not a valid operator concept

    SELECT * FROM measurement WHERE operator_concept_id = 999999
    -- 999999 is not in the whitelist

Correct patterns:
    SELECT * FROM measurement WHERE operator_concept_id = 4172704  -- >
    SELECT * FROM measurement WHERE operator_concept_id = 4171756  -- <
    SELECT * FROM measurement WHERE operator_concept_id = 4171755  -- =
    SELECT * FROM measurement WHERE operator_concept_id IN (4171754, 4172703)  -- <=, >=
"""

from typing import Dict, List, Optional, Set, Tuple

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


# --- Constants -------------------------------------------------------------

TABLE_NAME = "measurement"
COLUMN_NAME = "operator_concept_id"

VALID_OPERATORS = {
    4171756,  # <
    4172704,  # >
    4171755,  # =
    4171754,  # <=
    4172703,  # >=
}

OPERATOR_NAMES = {
    4171756: "<",
    4172704: ">",
    4171755: "=",
    4171754: "<=",
    4172703: ">=",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_operator_concept_id_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != COLUMN_NAME:
        return False

    if table:
        return _norm(table) == TABLE_NAME

    return TABLE_NAME in {_norm(t) for t in aliases.values()}


def _extract_int(node: exp.Expression) -> Optional[int]:
    """Extract integer from literal, including negatives."""
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


def _get_valid_operators_string() -> str:
    return ", ".join(
        f"{cid} ({OPERATOR_NAMES[cid]})" for cid in sorted(VALID_OPERATORS)
    )


# --- Detection -------------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[int, str]]:
    issues: List[Tuple[int, str]] = []
    seen: Set[Tuple[int, str]] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        # --- Binary comparisons ---
        if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                if not _is_operator_concept_id_column(col_node, aliases):
                    continue

                concept_id = _extract_int(val_node)
                if concept_id is None:
                    continue

                key = (concept_id, "comparison")
                if key in seen:
                    continue

                if concept_id not in VALID_OPERATORS:
                    seen.add(key)
                    issues.append((
                        concept_id,
                        f"operator_concept_id compared to invalid value {concept_id}. "
                        f"Valid operators are: {_get_valid_operators_string()}."
                    ))

        # --- IN ---
        elif isinstance(node, exp.In):
            col_node = node.this
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_operator_concept_id_column(col_node, aliases):
                continue

            invalid_values = []

            for val in node.expressions or []:
                concept_id = _extract_int(val)
                if concept_id is None:
                    continue

                key = (concept_id, "in")
                if concept_id not in VALID_OPERATORS and key not in seen:
                    seen.add(key)
                    invalid_values.append(concept_id)

            if invalid_values:
                issues.append((
                    -1,
                    f"operator_concept_id IN clause contains invalid values: {invalid_values}. "
                    f"Valid operators are: {_get_valid_operators_string()}."
                ))

    return issues


# --- Rule ------------------------------------------------------------------

@register
class MeasurementOperatorConceptValidationRule(Rule):
    """Validate measurement.operator_concept_id uses valid operator concepts."""

    rule_id = "semantic.measurement_operator_concept_validation"
    name = "Measurement Operator Concept Validation"

    description = (
        "Ensures measurement.operator_concept_id uses only valid operator concepts."
    )

    severity = Severity.ERROR
    suggested_fix = "Use one of the valid operator concept_ids"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not uses_table(tree, TABLE_NAME):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for concept_id, message in issues:
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        details={
                            "invalid_value": concept_id,
                            "valid_operators": list(VALID_OPERATORS),
                            "operator_names": OPERATOR_NAMES,
                        },
                    )
                )

        return violations


__all__ = ["MeasurementOperatorConceptValidationRule"]
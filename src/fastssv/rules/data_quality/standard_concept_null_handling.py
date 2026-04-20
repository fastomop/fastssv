"""Standard Concept NULL Handling Validation Rule.

OMOP semantic rule VOCAB_009:
Non-standard concepts have standard_concept IS NULL (not 'N' or '').
Using = NULL or invalid values returns zero rows.

The Problem:
    In OMOP CDM, concept.standard_concept has three possible values:
    - 'S' = Standard concept
    - 'C' = Classification concept
    - NULL = Non-standard concept

    Common mistakes:
    1. Using standard_concept = NULL instead of IS NULL
       (SQL equality with NULL always returns false)
    2. Using standard_concept = '' (empty string - no concepts have this value)
    3. Using standard_concept = 'N' (this value doesn't exist - non-standard is NULL)

Violation patterns:
    SELECT * FROM concept
    WHERE standard_concept = NULL  -- WRONG: use IS NULL

    SELECT * FROM concept
    WHERE standard_concept = ''  -- WRONG: empty string invalid

    SELECT * FROM concept
    WHERE standard_concept = 'N'  -- WRONG: should be NULL

Correct patterns:
    SELECT * FROM concept
    WHERE standard_concept IS NULL  -- Non-standard concepts

    SELECT * FROM concept
    WHERE standard_concept = 'S'  -- Standard concepts only

    SELECT * FROM concept
    WHERE standard_concept IN ('S', 'C')  -- Standard or classification
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    is_in_where_or_join_clause,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_TABLE = "concept"
STANDARD_CONCEPT = "standard_concept"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_standard_concept_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    """Check if column is standard_concept."""
    table, col_name = resolve_table_col(col, aliases)
    # standard_concept is unique to concept table, so table check is optional
    if _norm(table) and _norm(table) != CONCEPT_TABLE:
        return False
    return _norm(col_name) == STANDARD_CONCEPT


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


def _extract_intent_value(value: str) -> str:
    """Normalize string value for comparison."""
    return value.strip().upper()


# --- Detection -------------------------------------------------------------

def _detect_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Dict[str, object]]:
    """Detect problematic standard_concept usage."""
    violations: List[Dict[str, object]] = []
    seen: Set[str] = set()

    # --- Comparison operators ---
    comparison_nodes = []
    comparison_nodes.extend(tree.find_all(exp.EQ))
    comparison_nodes.extend(tree.find_all(exp.NEQ))
    comparison_nodes.extend(tree.find_all(exp.GT))
    comparison_nodes.extend(tree.find_all(exp.GTE))
    comparison_nodes.extend(tree.find_all(exp.LT))
    comparison_nodes.extend(tree.find_all(exp.LTE))

    for node in comparison_nodes:
        if not is_in_where_or_join_clause(node):
            continue

        pairs = [(node.this, node.expression), (node.expression, node.this)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_standard_concept_column(col_node, aliases):
                continue

            # --- NULL misuse ---
            if isinstance(val_node, exp.Null):
                key = f"null_{type(node).__name__}_{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                violations.append({
                    "issue": "null_equality",
                    "operator": type(node).__name__,
                    "value": None,
                    "context": node.sql(),
                })
                continue

            # --- String misuse ---
            value = _extract_string_literal(val_node)
            if value is None:
                continue

            norm_val = _extract_intent_value(value)

            if norm_val == "":
                key = f"empty_{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                violations.append({
                    "issue": "empty_string",
                    "operator": type(node).__name__,
                    "value": value,
                    "context": node.sql(),
                })

            elif norm_val == "N":
                key = f"invalid_n_{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                violations.append({
                    "issue": "invalid_n",
                    "operator": type(node).__name__,
                    "value": value,
                    "context": node.sql(),
                })

    # --- IN clause ---
    for node in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(node):
            continue

        if not isinstance(node.this, exp.Column):
            continue

        if not _is_standard_concept_column(node.this, aliases):
            continue

        for expr in node.expressions or []:
            value = _extract_string_literal(expr)
            if value is None:
                continue

            norm_val = _extract_intent_value(value)

            if norm_val == "N":
                key = f"in_n_{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                violations.append({
                    "issue": "invalid_n_in_list",
                    "operator": "IN",
                    "value": value,
                    "context": node.sql(),
                })

            elif norm_val == "":
                key = f"in_empty_{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                violations.append({
                    "issue": "empty_string_in_list",
                    "operator": "IN",
                    "value": value,
                    "context": node.sql(),
                })

    return violations


# --- Rule ------------------------------------------------------------------

@register
class StandardConceptNullHandlingRule(Rule):
    """Validate correct usage of concept.standard_concept."""

    rule_id = "data_quality.standard_concept_null_handling"
    name = "Standard Concept NULL Handling"

    description = (
        "Ensures correct handling of concept.standard_concept values. "
        "Non-standard concepts are represented by NULL, not 'N' or ''. "
        "Invalid comparisons may return zero rows."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use IS NULL for non-standard concepts, IS NOT NULL for standard/classification, "
        "or filter explicitly for valid values ('S', 'C')."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "standard_concept" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, CONCEPT_TABLE):
                continue

            aliases = extract_aliases(tree)
            detected = _detect_violations(tree, aliases)

            for v in detected:
                issue = v["issue"]
                operator = v["operator"]
                value = v["value"]

                if issue == "null_equality":
                    if operator == "EQ":
                        message = (
                            "Using standard_concept = NULL is invalid. "
                            "Use IS NULL to find non-standard concepts."
                        )
                        fix = "Replace with standard_concept IS NULL"
                    else:
                        message = (
                            "Invalid comparison with NULL on standard_concept. "
                            "Use IS NULL or IS NOT NULL."
                        )
                        fix = "Use IS NULL or IS NOT NULL"

                elif issue == "empty_string":
                    message = (
                        "standard_concept = '' is invalid. "
                        "Non-standard concepts are NULL."
                    )
                    fix = "Replace with standard_concept IS NULL"

                elif issue == "invalid_n":
                    message = (
                        "standard_concept = 'N' is invalid. "
                        "Non-standard concepts use NULL. Valid values are 'S' and 'C'."
                    )
                    fix = "Replace with standard_concept IS NULL"

                elif issue == "invalid_n_in_list":
                    message = (
                        "'N' in standard_concept IN clause is invalid. "
                        "Use IS NULL for non-standard concepts."
                    )
                    fix = "Remove 'N' and handle NULL separately"

                elif issue == "empty_string_in_list":
                    message = (
                        "Empty string in standard_concept IN clause is invalid."
                    )
                    fix = "Remove empty string from IN clause"

                else:
                    continue

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=Severity.WARNING,
                        suggested_fix=fix,
                        details={
                            "issue": issue,
                            "operator": operator,
                            "value": value,
                            "context": v["context"],
                        },
                    )
                )

        return violations


__all__ = ["StandardConceptNullHandlingRule"]
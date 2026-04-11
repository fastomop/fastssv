"""Domain ID Validation Rule.

OMOP semantic rule VOCAB_006:
domain_id values are case-sensitive strings with specific canonical casing.

The Problem:
    OMOP domain_id values have specific canonical casing (typically Title Case):
    - 'Condition' (not 'condition', 'CONDITION', or 'Conditions')
    - 'Drug' (not 'drug' or 'DRUG')
    - 'Procedure' (not 'procedure' or 'PROCEDURE')
    - 'Measurement' (not 'measurement' or 'MEASUREMENT')
    - 'Observation' (not 'observation' or 'OBSERVATION')
    - 'Device' (not 'device' or 'DEVICE')
    - 'Spec Anatomic Site' (mixed case with space)
    - 'Meas Value' (mixed case with space)
    - 'Type Concept' (mixed case with space)

    String comparisons are case-sensitive, so using wrong casing
    will cause the query to return zero results.

Common mistakes:
    - 'condition' instead of 'Condition'
    - 'DRUG' instead of 'Drug'
    - 'procedure' instead of 'Procedure'
    - 'measurement' instead of 'Measurement'
    - 'observation' instead of 'Observation'

Violation pattern:
    SELECT * FROM concept
    WHERE domain_id = 'condition'  -- WRONG: should be 'Condition'
    AND standard_concept = 'S'

Correct pattern:
    SELECT * FROM concept
    WHERE domain_id = 'Condition'  -- Correct casing
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CANONICAL_DOMAINS: Dict[str, str] = {
    "condition": "Condition",
    "drug": "Drug",
    "procedure": "Procedure",
    "measurement": "Measurement",
    "observation": "Observation",
    "device": "Device",
    "specanatomicsite": "Spec Anatomic Site",
    "measvalue": "Meas Value",
    "route": "Route",
    "unit": "Unit",
    "visit": "Visit",
    "typeconcept": "Type Concept",
    "race": "Race",
    "ethnicity": "Ethnicity",
    "gender": "Gender",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_for_matching(value: str) -> str:
    return value.lower().replace(" ", "")


def _get_canonical_domain(value: str) -> Optional[str]:
    return CANONICAL_DOMAINS.get(_normalize_for_matching(value))


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


def _is_domain_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    _, col_name = resolve_table_col(col, aliases)
    return _norm(col_name) == "domain_id"


def _is_wrapped_in_function(node: exp.Column) -> bool:
    """Skip if column is inside a SQL function (UPPER, LOWER, etc.).

    Excludes logical/comparison operators which are also Func subclasses.
    """
    parent = node.parent
    while parent:
        # Check if it's a function, but exclude logical/comparison operators
        if isinstance(parent, exp.Func) and not isinstance(parent, (
            exp.And, exp.Or, exp.Not,
            exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
            exp.In, exp.Between, exp.Like, exp.ILike,
        )):
            return True
        parent = parent.parent
    return False


# --- Extraction ------------------------------------------------------------

def _extract_domain_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str]]:
    filters: List[Tuple[str, str]] = []

    for node in tree.walk():

        # Skip NULL checks
        if isinstance(node, (exp.Is, exp.Not)):
            continue

        # --- EQ / NEQ ---
        if isinstance(node, (exp.EQ, exp.NEQ)):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if isinstance(col_node, exp.Column) and not _is_wrapped_in_function(col_node):
                    if not _is_domain_column(col_node, aliases):
                        continue

                    value = _extract_string_literal(val_node)
                    if value is not None:
                        filters.append((value, node.sql()))

        # --- IN ---
        elif isinstance(node, exp.In):
            if not node.expressions:
                continue

            if isinstance(node.this, exp.Column) and not _is_wrapped_in_function(node.this):
                if not _is_domain_column(node.this, aliases):
                    continue

                for expr in node.expressions:
                    value = _extract_string_literal(expr)
                    if value is not None:
                        filters.append((value, node.sql()))

        # --- LIKE / ILIKE ---
        elif isinstance(node, (exp.Like, exp.ILike)):
            if isinstance(node.this, exp.Column) and not _is_wrapped_in_function(node.this):
                if not _is_domain_column(node.this, aliases):
                    continue

                value = _extract_string_literal(node.expression)
                if value is not None:
                    filters.append((value, node.sql()))

    return filters


# --- Validation Logic ------------------------------------------------------

def _check_domain(value: str) -> Optional[Dict[str, str]]:
    canonical = _get_canonical_domain(value)

    # Unknown domain → ignore (could be valid/custom)
    if canonical is None:
        return None

    if value == canonical:
        return None

    return {
        "provided": value,
        "expected": canonical,
    }


# --- Rule ------------------------------------------------------------------

@register
class DomainIdCaseSensitivityRule(Rule):
    """Validate domain_id casing."""

    rule_id = "data_quality.domain_id_case_sensitivity"
    name = "Domain ID Case Sensitivity"

    description = (
        "Ensures domain_id values use correct canonical casing. "
        "Incorrect values may return zero results due to case sensitivity."
    )

    severity = Severity.ERROR
    suggested_fix = "Use canonical OMOP domain_id values with correct casing."

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "domain_id" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            filters = _extract_domain_filters(tree, aliases)

            seen: Set[str] = set()

            for value, context in filters:
                error = _check_domain(value)
                if not error:
                    continue

                provided = error["provided"]
                expected = error["expected"]

                key = f"{provided}|{expected}"
                if key in seen:
                    continue
                seen.add(key)

                violations.append(
                    self.create_violation(
                        message=(
                            f"Incorrect domain_id casing: '{provided}'. "
                            f"Expected '{expected}'. Case-sensitive comparison may fail."
                        ),
                        severity=Severity.ERROR,
                        suggested_fix=f"Replace '{provided}' with '{expected}'",
                        details={
                            "provided": provided,
                            "expected": expected,
                            "context": context,
                        },
                    )
                )

        return violations


__all__ = ["DomainIdCaseSensitivityRule"]
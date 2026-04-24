"""Negative Concept ID Validation Rule.

OMOP semantic rule OMOP_050:
All concept_id values in OMOP are non-negative integers (>= 0).
Negative concept_id values are never valid and indicate an error.

The Problem:
    OMOP concept_id values range from 0 (unmapped) to positive integers.
    Negative values are not allowed and will never return results.

    Common mistakes:
    - Using -1 as a sentinel/null value
    - Typos or sign errors
    - Copy-paste from non-OMOP systems

Violation pattern:
    SELECT * FROM condition_occurrence WHERE condition_concept_id = -1
    -- Will always return 0 rows; negative concept_ids don't exist

Correct pattern:
    SELECT * FROM condition_occurrence WHERE condition_concept_id = 201826
    -- Or for unmapped: condition_concept_id = 0
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
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_ID_COLUMNS = {
    # Core
    "concept_id",

    # Clinical
    "condition_concept_id",
    "drug_concept_id",
    "procedure_concept_id",
    "measurement_concept_id",
    "observation_concept_id",
    "device_concept_id",
    "visit_concept_id",
    "visit_detail_concept_id",
    "specimen_concept_id",
    "episode_concept_id",
    "cause_concept_id",

    # Source
    "condition_source_concept_id",
    "drug_source_concept_id",
    "procedure_source_concept_id",
    "measurement_source_concept_id",
    "observation_source_concept_id",
    "device_source_concept_id",
    "visit_source_concept_id",
    "visit_detail_source_concept_id",
    "specimen_source_concept_id",
    "episode_source_concept_id",
    "cause_source_concept_id",

    # Type
    "condition_type_concept_id",
    "drug_type_concept_id",
    "procedure_type_concept_id",
    "measurement_type_concept_id",
    "observation_type_concept_id",
    "device_type_concept_id",
    "visit_type_concept_id",
    "visit_detail_type_concept_id",
    "specimen_type_concept_id",
    "note_type_concept_id",
    "death_type_concept_id",
    "episode_type_concept_id",

    # Person
    "gender_concept_id",
    "race_concept_id",
    "ethnicity_concept_id",
    "gender_source_concept_id",
    "race_source_concept_id",
    "ethnicity_source_concept_id",

    # Auxiliary
    "route_concept_id",
    "dose_unit_concept_id",
    "unit_concept_id",
    "operator_concept_id",
    "value_as_concept_id",
    "qualifier_concept_id",
    "modifier_concept_id",
    "anatomic_site_concept_id",
    "disease_status_concept_id",
    "specialty_concept_id",
    "note_encoding_concept_id",
    "language_concept_id",
    "condition_status_concept_id",

    # Payer
    "payer_concept_id",
    "plan_concept_id",
    "sponsor_concept_id",
    "stop_reason_concept_id",
    "payer_source_concept_id",
    "plan_source_concept_id",
    "sponsor_source_concept_id",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_concept_id_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    _, col_name = resolve_table_col(col, aliases)
    return _norm(col_name) in CONCEPT_ID_COLUMNS


def _extract_int(node: exp.Expression) -> Optional[int]:
    """Strict integer extraction (no floats)."""
    if isinstance(node, exp.Neg):
        inner = node.this
        if isinstance(inner, exp.Literal) and inner.is_int:
            return -int(inner.this)
        return None

    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)

    return None


# --- Detection -------------------------------------------------------------

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE, exp.In, exp.Between)):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        # --- Binary comparisons ---
        if isinstance(node, (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue
                if not _is_concept_id_column(col_node, aliases):
                    continue

                value = _extract_int(val_node)
                if value is not None and value < 0:
                    _, col_name = resolve_table_col(col_node, aliases)

                    key = f"{node.sql()}"
                    if key in seen:
                        continue
                    seen.add(key)

                    issues.append(
                        f"Invalid negative concept_id: {col_name} {node.key} {value}. "
                        f"Concept IDs must be >= 0."
                    )

        # --- IN clause ---
        elif isinstance(node, exp.In):
            col_node = node.this

            if not isinstance(col_node, exp.Column):
                continue
            if not _is_concept_id_column(col_node, aliases):
                continue

            negatives = []
            for val in node.expressions or []:
                v = _extract_int(val)
                if v is not None and v < 0:
                    negatives.append(v)

            if negatives:
                _, col_name = resolve_table_col(col_node, aliases)

                key = f"{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                issues.append(
                    f"Invalid negative values in {col_name} {node.key}: {sorted(negatives)}. "
                    f"Concept IDs must be >= 0."
                )

        # --- BETWEEN ---
        elif isinstance(node, exp.Between):
            col_node = node.this

            if not isinstance(col_node, exp.Column):
                continue
            if not _is_concept_id_column(col_node, aliases):
                continue

            low = _extract_int(node.args.get("low"))
            high = _extract_int(node.args.get("high"))

            if (low is not None and low < 0) or (high is not None and high < 0):
                _, col_name = resolve_table_col(col_node, aliases)

                key = f"{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                issues.append(
                    f"Invalid BETWEEN range for {col_name}: ({low if low is not None else '?'}, "
                    f"{high if high is not None else '?'}). Concept IDs must be >= 0."
                )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class NegativeConceptIdValidationRule(Rule):
    """Validates concept_id values are non-negative."""

    rule_id = "data_quality.negative_concept_id_validation"
    name = "Negative Concept ID Validation"
    description = "Concept IDs must be non-negative integers (>= 0)."
    severity = Severity.ERROR
    suggested_fix = "Use valid non-negative concept_id values. Use 0 for unmapped."
    long_description = (
        "Every concept_id in OMOP is a non-negative integer (>= 0); 0 "
        "represents unmapped records, positive values are real concepts. "
        "Negative literals never match any concept and are usually the "
        "result of either a sign error or a confusion with ROW_NUMBER-"
        "style synthetic IDs from another system. Replace the literal "
        "with the correct non-negative concept_id."
    )
    example_bad = (
        "SELECT *\n"
        "FROM condition_occurrence\n"
        "WHERE condition_concept_id = -1;"
    )
    example_good = (
        "SELECT *\n"
        "FROM condition_occurrence\n"
        "WHERE condition_concept_id = 201820;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Optional fast-path optimization
        if "concept_id" not in sql:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["NegativeConceptIdValidationRule"]

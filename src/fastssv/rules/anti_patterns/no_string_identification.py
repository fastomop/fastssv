"""No String Identification Rule.

OMOP vocabulary rule:
Do NOT identify clinical concepts using string matching on *_source_value columns.
Use *_concept_id instead.
"""

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
from fastssv.core.registry import register

# Pairs of (table_name, column_name) for source value columns
SOURCE_VALUE_COLUMNS = {
    # Clinical event tables
    ("condition_occurrence", "condition_source_value"),
    ("drug_exposure", "drug_source_value"),
    ("drug_exposure", "route_source_value"),
    ("drug_exposure", "dose_unit_source_value"),
    ("procedure_occurrence", "procedure_source_value"),
    ("procedure_occurrence", "modifier_source_value"),
    ("measurement", "measurement_source_value"),
    ("measurement", "unit_source_value"),
    ("measurement", "value_source_value"),
    ("observation", "observation_source_value"),
    ("observation", "unit_source_value"),
    ("observation", "qualifier_source_value"),
    ("device_exposure", "device_source_value"),
    ("visit_occurrence", "visit_source_value"),
    ("visit_occurrence", "admitted_from_source_value"),
    ("visit_occurrence", "discharged_to_source_value"),
    ("visit_detail", "visit_detail_source_value"),
    ("visit_detail", "admitted_from_source_value"),
    ("visit_detail", "discharged_to_source_value"),
    # Person and death
    ("person", "gender_source_value"),
    ("person", "race_source_value"),
    ("person", "ethnicity_source_value"),
    ("death", "cause_source_value"),
    # Specimen
    ("specimen", "specimen_source_value"),
    ("specimen", "unit_source_value"),
    ("specimen", "anatomic_site_source_value"),
    ("specimen", "disease_status_source_value"),
    # Episode
    ("episode", "episode_source_value"),
    # Note
    ("note", "note_source_value"),
    # Payer
    ("payer_plan_period", "payer_source_value"),
    ("payer_plan_period", "plan_source_value"),
    ("payer_plan_period", "sponsor_source_value"),
    ("payer_plan_period", "stop_reason_source_value"),
}

STRING_MATCH_EXP_TYPES = (exp.Like, exp.ILike, exp.RegexpLike)


def _check_string_match_violations(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[RuleViolation]:
    """Check for LIKE/ILIKE/REGEXP violations on source_value columns."""
    violations: List[RuleViolation] = []

    for node in tree.walk():
        # Handle both positive and negative: LIKE, NOT LIKE, ILIKE, NOT ILIKE
        is_not = False
        check_node = node

        if isinstance(node, exp.Not):
            inner = node.this
            if isinstance(inner, STRING_MATCH_EXP_TYPES):
                is_not = True
                check_node = inner
            else:
                continue
        elif not isinstance(node, STRING_MATCH_EXP_TYPES):
            continue

        left = check_node.this
        right = check_node.expression

        if not isinstance(left, exp.Column):
            continue

        table, col = resolve_table_col(left, aliases)
        key = (table, col)

        not_prefix = "NOT " if is_not else ""
        op_name = check_node.key.upper() if hasattr(check_node, 'key') else "LIKE"

        # Check if it's a source_value column
        if key in SOURCE_VALUE_COLUMNS or col.endswith("_source_value"):
            violations.append(RuleViolation(
                rule_id="vocabulary.no_string_identification",
                severity=Severity.ERROR,
                message=f"String matching on source value: {left.sql()} {not_prefix}{op_name} {right.sql()}",
                suggested_fix="Use *_concept_id or *_source_concept_id instead of string matching",
                details={"column": f"{table}.{col}" if table else col, "operation": f"{not_prefix}{op_name}"},
            ))

    return violations


def _check_equality_violations(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[RuleViolation]:
    """Check for equality comparison violations (col = 'string') on source_value columns."""
    violations: List[RuleViolation] = []

    for eq in tree.find_all(exp.EQ):
        left = eq.left
        right = eq.right

        # Normalize direction: Column = 'string'
        if isinstance(right, exp.Column) and is_string_literal(left):
            left, right = right, left

        if not (isinstance(left, exp.Column) and is_string_literal(right)):
            continue

        table, col = resolve_table_col(left, aliases)
        key = (table, col)

        # Check if it's a source_value column
        if key in SOURCE_VALUE_COLUMNS or col.endswith("_source_value"):
            violations.append(RuleViolation(
                rule_id="vocabulary.no_string_identification",
                severity=Severity.ERROR,
                message=f"String equality on source value: {left.sql()} = {right.sql()}",
                suggested_fix="Use *_concept_id instead",
                details={"column": f"{table}.{col}" if table else col, "operation": "="},
            ))

    return violations


def _check_in_clause_violations(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[RuleViolation]:
    """Check for IN clause violations (col IN ('val1', 'val2')) on source_value columns."""
    violations: List[RuleViolation] = []

    for in_expr in tree.find_all(exp.In):
        # Handle NOT IN as well
        is_not = isinstance(in_expr.parent, exp.Not)

        col_expr = in_expr.this
        if not isinstance(col_expr, exp.Column):
            continue

        # Check if any values in IN clause are strings
        has_string_values = False
        string_values = []
        for val in in_expr.expressions or []:
            if is_string_literal(val):
                has_string_values = True
                string_values.append(val.sql())

        if not has_string_values:
            continue

        table, col = resolve_table_col(col_expr, aliases)
        key = (table, col)

        not_prefix = "NOT " if is_not else ""
        values_str = ", ".join(string_values[:3])
        if len(string_values) > 3:
            values_str += ", ..."

        # Check if it's a source_value column
        if key in SOURCE_VALUE_COLUMNS or col.endswith("_source_value"):
            violations.append(RuleViolation(
                rule_id="vocabulary.no_string_identification",
                severity=Severity.ERROR,
                message=f"String IN clause on source value: {col_expr.sql()} {not_prefix}IN ({values_str})",
                suggested_fix="Use *_concept_id instead",
                details={"column": f"{table}.{col}" if table else col, "operation": f"{not_prefix}IN"},
            ))

    return violations


@register
class NoStringIdentificationRule(Rule):
    """Prevents string matching on *_source_value columns."""

    rule_id = "vocabulary.no_string_identification"
    name = "No String Identification"
    description = (
        "Prevents using string matching (LIKE, =, IN) on *_source_value columns "
        "to identify clinical concepts. Use *_concept_id instead."
    )
    severity = Severity.ERROR
    suggested_fix = "Use *_concept_id or *_source_concept_id instead of string matching"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        all_violations: List[RuleViolation] = []

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)

            all_violations.extend(_check_string_match_violations(tree, aliases))
            all_violations.extend(_check_equality_violations(tree, aliases))
            all_violations.extend(_check_in_clause_violations(tree, aliases))

        return all_violations


__all__ = ["NoStringIdentificationRule", "SOURCE_VALUE_COLUMNS"]

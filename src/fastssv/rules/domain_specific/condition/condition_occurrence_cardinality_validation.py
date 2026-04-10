"""Condition Occurrence Cardinality Validation Rule.

OMOP semantic rule CLIN_056: condition_occurrence_multiple_records_per_person

A person can have multiple records in condition_occurrence for the same
condition_concept_id (e.g., recurring diagnoses across visits). Queries should
not assume one record per person per condition unless explicitly using
condition_era or applying DISTINCT/GROUP BY logic.

The Problem:
    Joining person to condition_occurrence without aggregation can produce
    multiple rows per person, leading to incorrect counts or analysis. For example:

    - Patient A has 3 condition_occurrence records for diabetes (across 3 visits)
    - Query joins person to condition_occurrence: SELECT p.person_id, co.condition_start_date
    - Result: 3 rows for Patient A instead of 1
    - Counting rows gives "3 patients" when only 1 patient exists

Detection heuristics:
    - Query joins person to condition_occurrence on person_id
    - No GROUP BY clause present
    - No DISTINCT in SELECT
    - No aggregation functions (COUNT, MIN, MAX, etc.)

Violation pattern:
    SELECT p.person_id, co.condition_start_date
    FROM person p
    JOIN condition_occurrence co ON p.person_id = co.person_id
    WHERE co.condition_concept_id = 201826
    -- Returns multiple rows per person without aggregation

Correct patterns:
    -- Use GROUP BY to aggregate
    SELECT co.person_id, MIN(co.condition_start_date) AS first_diagnosis
    FROM condition_occurrence co
    WHERE co.condition_concept_id = 201826
    GROUP BY co.person_id

    -- Use DISTINCT for unique persons
    SELECT DISTINCT p.person_id
    FROM person p
    JOIN condition_occurrence co ON p.person_id = co.person_id

    -- Use condition_era for consolidated periods
    SELECT ce.person_id, ce.condition_era_start_date
    FROM condition_era ce
    WHERE ce.condition_concept_id = 201826
"""

from typing import Dict, List, Optional, Set

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

TABLE_PERSON = "person"
TABLE_CONDITION = "condition_occurrence"
PERSON_ID = "person_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _resolve_alias(table_or_alias: Optional[str], aliases: Dict[str, str]) -> Optional[str]:
    """Resolve a table name or alias to its canonical alias."""
    if not table_or_alias:
        return None

    for alias, table in aliases.items():
        if _norm(alias) == _norm(table_or_alias) or _norm(table) == _norm(table_or_alias):
            return alias
    return None


def _get_aliases_for_table(target: str, aliases: Dict[str, str]) -> Set[str]:
    return {
        alias for alias, table in aliases.items()
        if _norm(table) == _norm(target) and alias != table
    }


def _has_person_and_condition(aliases: Dict[str, str]) -> bool:
    tables = {_norm(t) for t in aliases.values()}
    return TABLE_PERSON in tables and TABLE_CONDITION in tables


def _has_person_id_join(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Detect ANY person_id join between person and condition_occurrence."""
    person_aliases = _get_aliases_for_table(TABLE_PERSON, aliases)
    condition_aliases = _get_aliases_for_table(TABLE_CONDITION, aliases)

    if not person_aliases or not condition_aliases:
        return False

    def _matches(col: exp.Column, expected_aliases: Set[str]) -> bool:
        table, column = resolve_table_col(col, aliases)
        alias = _resolve_alias(table, aliases)
        return _norm(column) == PERSON_ID and alias in expected_aliases

    # Check JOIN + WHERE
    for eq in tree.find_all(exp.EQ):
        if not (isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)):
            continue

        if (_matches(eq.this, person_aliases) and _matches(eq.expression, condition_aliases)) or \
           (_matches(eq.this, condition_aliases) and _matches(eq.expression, person_aliases)):
            return True

    # Check USING(person_id)
    for select in tree.find_all(exp.Select):
        seen_tables: Set[str] = set()

        # Get FROM table
        from_clause = select.args.get("from_")
        if from_clause and isinstance(from_clause.this, exp.Table):
            seen_tables.add(from_clause.this.alias_or_name)

        # Check joins
        for join in select.args.get("joins", []):
            using = join.args.get("using")
            if using:
                cols = set()
                if isinstance(using, exp.Tuple):
                    cols = {_norm(e.name) for e in using.expressions if isinstance(e, exp.Identifier)}
                elif isinstance(using, list):
                    cols = {_norm(e.name) for e in using if isinstance(e, exp.Identifier)}
                elif isinstance(using, exp.Identifier):
                    cols = {_norm(using.name)}

                if PERSON_ID in cols and isinstance(join.this, exp.Table):
                    right_alias = join.this.alias_or_name
                    # Check if joining person to condition_occurrence
                    has_person = any(t in person_aliases for t in seen_tables)
                    has_condition = any(t in condition_aliases for t in seen_tables)
                    is_person = right_alias in person_aliases
                    is_condition = right_alias in condition_aliases

                    if (has_person and is_condition) or (has_condition and is_person):
                        return True

            if isinstance(join.this, exp.Table):
                seen_tables.add(join.this.alias_or_name)

    return False


def _has_top_level_aggregation(select: exp.Select) -> bool:
    """Check if SELECT ensures deduplication."""
    if select.args.get("group"):
        return True
    if select.args.get("distinct"):
        return True

    AGG = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)

    return any(isinstance(node, AGG) for node in select.expressions)


def _is_person_level_query(select: exp.Select, aliases: Dict[str, str]) -> bool:
    """
    Detect if query appears to intend one row per person.
    Heuristic: selecting person columns but not condition columns.
    """
    person_aliases = _get_aliases_for_table(TABLE_PERSON, aliases)
    condition_aliases = _get_aliases_for_table(TABLE_CONDITION, aliases)

    has_person_cols = False
    has_condition_cols = False

    for col in select.find_all(exp.Column):
        table, _ = resolve_table_col(col, aliases)
        alias = _resolve_alias(table, aliases)

        if alias in person_aliases:
            has_person_cols = True
        if alias in condition_aliases:
            has_condition_cols = True

    return has_person_cols and not has_condition_cols


# --- Detection -------------------------------------------------------------

def _detect_violation(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    if not _has_person_and_condition(aliases):
        return False

    if not _has_person_id_join(tree, aliases):
        return False

    for select in tree.find_all(exp.Select):
        if _has_top_level_aggregation(select):
            return False

    return True


# --- Rule ------------------------------------------------------------------

@register
class ConditionOccurrenceCardinalityValidationRule(Rule):
    """Warn about unintended fan-out when joining person to condition_occurrence."""

    rule_id = "semantic.condition_occurrence_cardinality_validation"
    name = "Condition Occurrence Cardinality Risk"

    description = (
        "Joining person to condition_occurrence without aggregation can produce multiple rows per person. "
        "This may lead to incorrect counts if a person has multiple condition records."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use GROUP BY person_id, DISTINCT, or condition_era to avoid duplicate rows per person."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            if _detect_violation(tree, aliases):
                violations.append(
                    self.create_violation(
                        message=(
                            "Query joins person to condition_occurrence without aggregation. "
                            "A person can have multiple condition_occurrence records for the same condition, "
                            "which may produce more rows than expected. Consider using GROUP BY, DISTINCT, or "
                            "condition_era for consolidated periods."
                        ),
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["ConditionOccurrenceCardinalityValidationRule"]
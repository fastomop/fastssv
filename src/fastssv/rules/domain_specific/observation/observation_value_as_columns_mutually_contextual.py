"""Observation Value As Columns Mutually Contextual Rule.

OMOP semantic rule OMOP_141:
In the observation table, value_as_number, value_as_string, and value_as_concept_id
represent the same result in different formats. A query should use the appropriate
column based on the observation_concept_id's expected data type, not combine them
with AND.

The Problem:
    The observation table stores observation results in multiple format columns:
    - value_as_number: for numeric results (e.g., 98.6, 120, 7.2)
    - value_as_string: for text results (e.g., "Positive", "Negative", "High")
    - value_as_concept_id: for coded results (e.g., concept_id for "Normal")

    For a given observation row, typically only ONE of these columns is populated;
    the others are NULL. They represent alternative formats for the same result,
    not complementary data points.

    Common mistakes:
    1. Using AND between multiple value_as_* columns
       - Assumes multiple columns can be populated simultaneously
       - Results in queries that match almost zero rows
       - Indicates misunderstanding of observation table structure

    2. Not understanding mutual exclusivity
       - These are alternative representations, not additive fields
       - Similar to how measurement has value_as_number OR value_as_concept_id

    3. Treating them as independent filters
       - value_as_number AND value_as_string is almost always wrong
       - Should use the appropriate column for the observation type

Why this is wrong:
    Using AND conditions on multiple value_as_* columns:
    - Matches almost no rows (typically only one column is populated)
    - Indicates conceptual misunderstanding
    - Produces unexpected empty or near-empty result sets
    - Should use OR if checking alternative representations
    - Should use single appropriate column for the observation type

Violation patterns:
    SELECT * FROM observation
    WHERE value_as_number > 100 AND value_as_string = 'High'
    -- WARNING: Both columns rarely populated together

    SELECT * FROM observation
    WHERE value_as_number BETWEEN 90 AND 120
      AND value_as_concept_id = 45884084
    -- WARNING: Numeric and concept values are alternative representations

    SELECT * FROM observation
    WHERE value_as_string = 'Positive'
      AND value_as_number > 0
      AND value_as_concept_id IS NOT NULL
    -- WARNING: All three columns used with AND

Correct patterns:
    SELECT * FROM observation
    WHERE observation_concept_id = 4058286
      AND value_as_concept_id = 45884084
    -- OK: Using concept-based answer for concept-based observation

    SELECT * FROM observation
    WHERE observation_concept_id = 3004249
      AND value_as_number > 6.5
    -- OK: Using numeric value for numeric lab result

    SELECT * FROM observation
    WHERE value_as_number > 100 OR value_as_string = 'High'
    -- OK: OR allows either representation

    SELECT * FROM observation
    WHERE value_as_number IS NOT NULL AND value_as_string IS NOT NULL
    -- OK: Data quality check (using IS NOT NULL, not value comparisons)

Note:
    This is a WARNING, not an ERROR. Some rare data quality checks or special
    cases might legitimately filter on multiple value_as_* columns. However,
    in typical analytical queries, using AND on multiple value_as_* columns
    indicates a conceptual error.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

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

OBSERVATION_TABLE = "observation"
VALUE_AS_NUMBER = "value_as_number"
VALUE_AS_STRING = "value_as_string"
VALUE_AS_CONCEPT_ID = "value_as_concept_id"

VALUE_COLUMNS = {VALUE_AS_NUMBER, VALUE_AS_STRING, VALUE_AS_CONCEPT_ID}


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_observation(table: Optional[str]) -> bool:
    return table == OBSERVATION_TABLE


def _is_value_column(col: Optional[str]) -> Optional[str]:
    col_norm = _norm(col)
    return col_norm if col_norm in VALUE_COLUMNS else None


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    return {
        _norm(cte.alias_or_name)
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _resolve_column(
    column: exp.Column,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> Tuple[Optional[str], Optional[str]]:
    table, col = resolve_table_col(column, aliases)
    table = _norm(table)
    col = _norm(col)

    if table in cte_names:
        return None, None

    return table, col


def _flatten_and(node: exp.Expression) -> List[exp.Expression]:
    """
    Flatten nested AND expressions into a list of conditions.
    """
    if isinstance(node, exp.And):
        return _flatten_and(node.this) + _flatten_and(node.expression)
    return [node]


def _has_value_comparison(
    node: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    has_observation: bool,
) -> Optional[str]:
    """
    Detect comparison involving value_as_* columns.
    Only considers column-to-value or column-to-column comparisons.
    """
    if isinstance(
        node,
        (
            exp.GT, exp.GTE, exp.LT, exp.LTE,
            exp.EQ, exp.NEQ,
            exp.Between, exp.In, exp.Like, exp.ILike,
        ),
    ):
        for col in node.find_all(exp.Column):
            t, c = _resolve_column(col, aliases, cte_names)

            if not c:
                continue

            value_col = _is_value_column(c)
            if not value_col:
                continue

            # Qualified OR safe unqualified usage
            if _is_observation(t) or (not t and has_observation):
                return value_col

    return None


def _analyze_boolean_scope(
    root: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    has_observation: bool,
) -> Set[str]:
    """
    Analyze a boolean expression (WHERE or ON) at one scope level.
    Avoid descending into subqueries.
    """
    value_columns_used: Set[str] = set()

    # Flatten AND conditions
    conditions = _flatten_and(root)

    for cond in conditions:
        # Skip subqueries to avoid cross-scope contamination
        if isinstance(cond, (exp.Subquery, exp.Exists)):
            continue

        value_col = _has_value_comparison(cond, aliases, cte_names, has_observation)
        if value_col:
            value_columns_used.add(value_col)

    return value_columns_used


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> List[str]:
    issues: List[str] = []

    if not has_table_reference(tree, OBSERVATION_TABLE):
        return issues

    if OBSERVATION_TABLE in cte_names:
        return issues

    has_observation = True

    # --- WHERE clauses ---
    for where in tree.find_all(exp.Where):
        value_columns_used = _analyze_boolean_scope(
            where.this, aliases, cte_names, has_observation
        )

        if len(value_columns_used) >= 2:
            cols = ", ".join(sorted(value_columns_used))
            issues.append(
                f"WHERE clause uses AND conditions on multiple value_as_* columns: {cols}. "
                f"Typically only one is populated per row. Use OR or select the appropriate column."
            )

    # --- JOIN ON clauses ---
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        value_columns_used = _analyze_boolean_scope(
            on_clause, aliases, cte_names, has_observation
        )

        if len(value_columns_used) >= 2:
            cols = ", ".join(sorted(value_columns_used))
            issues.append(
                f"JOIN ON clause uses AND conditions on multiple value_as_* columns: {cols}. "
                f"Typically only one is populated per row."
            )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class ObservationValueAsColumnsMutuallyContextualRule(Rule):
    """
    OMOP_141: Warn when multiple value_as_* columns are used with AND.
    """

    rule_id = "domain_specific.observation_value_as_columns_mutually_contextual"
    name = "Observation Value As Columns Mutually Contextual"

    description = (
        "value_as_number, value_as_string, and value_as_concept_id represent the same "
        "value in different formats. Only one is typically populated per row."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `value_as_number AND value_as_string` WITH a single predicate matching the value type for that observation_concept_id, OR use `OR` if both representations are intentional. Per row only one value_as_* column is populated in OMOP."
    example_bad = (
        "SELECT person_id FROM observation\n"
        "WHERE value_as_number > 5 AND value_as_string = 'positive';"
    )
    example_good = (
        "SELECT person_id FROM observation\n"
        "WHERE value_as_number > 5;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if OBSERVATION_TABLE not in sql_lower:
            return []

        if not any(col in sql_lower for col in VALUE_COLUMNS):
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_141",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, OBSERVATION_TABLE):
                continue

            aliases = extract_aliases(tree)
            cte_names = _extract_cte_names(tree)

            issues = _find_violations(tree, aliases, cte_names)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["ObservationValueAsColumnsMutuallyContextualRule"]

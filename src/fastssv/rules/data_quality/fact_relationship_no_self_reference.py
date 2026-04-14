"""Fact Relationship No Self-Reference Rule.

OMOP semantic rule OMOP_255:
Self-referential relationships in fact_relationship (where fact_id_1 = fact_id_2)
should be rare and may indicate data quality issues.

The Problem:
    The fact_relationship table links two clinical events together via a relationship.
    In most cases, linking an event to itself doesn't make semantic sense:

    - A measurement "preceded by" itself 
    - A condition "followed by" itself 
    - A procedure "causally related to" itself

    While there might be extremely rare valid cases, queries that explicitly
    filter for or create self-referential relationships typically indicate:
    - Data quality issues
    - Logic errors in ETL processes
    - Incorrect query logic

Violation patterns:
    -- WRONG: Explicitly querying for self-references
    SELECT * FROM fact_relationship
    WHERE fact_id_1 = fact_id_2;

    -- WRONG: Using equality in JOIN that creates self-reference
    SELECT *
    FROM fact_relationship fr
    JOIN measurement m1 ON fr.fact_id_1 = m1.measurement_id
    JOIN measurement m2 ON fr.fact_id_2 = m2.measurement_id
    WHERE m1.measurement_id = m2.measurement_id;

    -- WRONG: Comparing same column values
    SELECT * FROM fact_relationship fr
    WHERE fr.fact_id_1 = fr.fact_id_2;

Correct patterns:
    -- CORRECT: Normal relationship between different events
    SELECT * FROM fact_relationship
    WHERE fact_id_1 = 100
      AND fact_id_2 = 200;

    -- CORRECT: JOINs that don't create self-references
    SELECT *
    FROM fact_relationship fr
    JOIN measurement m1 ON fr.fact_id_1 = m1.measurement_id
    JOIN measurement m2 ON fr.fact_id_2 = m2.measurement_id
    WHERE m1.measurement_id != m2.measurement_id;
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

FACT_RELATIONSHIP = "fact_relationship"

FACT_ID_1 = "fact_id_1"
FACT_ID_2 = "fact_id_2"


# --- Normalized Constants --------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


NORM_FACT_RELATIONSHIP = _norm(FACT_RELATIONSHIP)
NORM_FACT_ID_1 = _norm(FACT_ID_1)
NORM_FACT_ID_2 = _norm(FACT_ID_2)

NORM_FACT_ID_NAMES = {NORM_FACT_ID_1, NORM_FACT_ID_2}


# --- Helpers ---------------------------------------------------------------

def _normalize_aliases(aliases: Dict[str, str]) -> Dict[str, str]:
    return {_norm(k): _norm(v) for k, v in aliases.items()}


def _get_fact_relationship_aliases(aliases: Dict[str, str]) -> Set[str]:
    return {k for k, v in aliases.items() if v == NORM_FACT_RELATIONSHIP}


def _resolve_fact_id_column(
    col: exp.Column,
    aliases: Dict[str, str],
    fr_aliases: Set[str],
) -> Optional[Tuple[str, str]]:
    """
    Returns (alias, column_name) if column is fact_id_1 or fact_id_2
    from fact_relationship. Otherwise returns None.
    """
    table, col_name = resolve_table_col(col, aliases)

    if not col_name:
        return None

    col_norm = _norm(col_name)
    if col_norm not in NORM_FACT_ID_NAMES:
        return None

    # Qualified column
    if table:
        table_norm = _norm(table)
        if table_norm in fr_aliases:
            return table_norm, col_norm
        return None

    # Unqualified column → only if exactly one fact_relationship alias
    if len(fr_aliases) == 1:
        return next(iter(fr_aliases)), col_norm

    return None


def _detect_self_reference_comparisons(
    select: exp.Select,
    aliases: Dict[str, str],
) -> List[str]:
    """
    Detect fact_id_1 = fact_id_2 within the SAME fact_relationship alias.
    """
    violations: List[str] = []

    fr_aliases = _get_fact_relationship_aliases(aliases)
    if not fr_aliases:
        return violations

    # Scan WHERE + JOIN conditions
    for node in select.walk():
        if not isinstance(node, exp.EQ):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        right = node.expression

        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        left_res = _resolve_fact_id_column(left, aliases, fr_aliases)
        right_res = _resolve_fact_id_column(right, aliases, fr_aliases)

        if not left_res or not right_res:
            continue

        left_alias, left_col = left_res
        right_alias, right_col = right_res

        # Critical: must be SAME alias (same table instance)
        if left_alias != right_alias:
            continue

        # Check for fact_id_1 vs fact_id_2
        if {left_col, right_col} == NORM_FACT_ID_NAMES:
            violations.append(
                f"{left_alias}.fact_id_1 = {left_alias}.fact_id_2"
            )

    return violations


# --- Rule ------------------------------------------------------------------

@register
class FactRelationshipNoSelfReferenceRule(Rule):
    """Detects self-referential patterns in fact_relationship queries."""

    rule_id = "data_quality.fact_relationship_no_self_reference"
    name = "Fact Relationship No Self-Reference"

    description = (
        "Detects patterns where fact_relationship queries filter for self-referential "
        "relationships (fact_id_1 = fact_id_2). Self-linking relationships should be "
        "rare and may indicate data quality issues or incorrect query logic."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Remove self-referential filters (fact_id_1 = fact_id_2). "
        "If intentional, verify this is a valid use case."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if FACT_RELATIONSHIP not in sql_lower:
            return []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            if not uses_table(tree, FACT_RELATIONSHIP):
                continue

            raw_aliases = extract_aliases(tree)
            aliases = _normalize_aliases(raw_aliases)

            seen_patterns: Set[str] = set()

            # Scope per SELECT (prevents subquery leakage)
            for select in tree.find_all(exp.Select):
                detected = _detect_self_reference_comparisons(select, aliases)

                if not detected:
                    continue

                for pattern in detected:
                    if pattern in seen_patterns:
                        continue

                    seen_patterns.add(pattern)

                    message = (
                        f"Self-referential pattern detected in fact_relationship: {pattern}. "
                        f"This may indicate incorrect logic or data quality issues."
                    )

                    violations.append(
                        self.create_violation(
                            message=message,
                            suggested_fix=self.suggested_fix,
                            details={
                                "pattern": pattern,
                                "recommendation": (
                                    "Ensure fact_id_1 and fact_id_2 refer to different records "
                                    "unless self-relationships are explicitly intended."
                                ),
                            },
                        )
                    )

        return violations


__all__ = ["FactRelationshipNoSelfReferenceRule"]
"""Ambiguous Column Reference Rule.

OMOP semantic rule GAP_037:
When multiple OMOP tables are joined, common columns like person_id, provider_id,
care_site_id, visit_occurrence_id, and visit_detail_id exist in many tables.
Referencing these without a table alias prefix is ambiguous and will cause SQL
errors or unpredictable behavior.

The Problem:
    Unqualified column references in multi-table queries create ambiguity:

    SELECT person_id, condition_concept_id
    FROM condition_occurrence co
    JOIN person p ON co.person_id = p.person_id
    WHERE person_id = 12345
    -- WRONG: Which person_id? co.person_id or p.person_id?

    Common ambiguous columns in OMOP:
    - person_id: In nearly every clinical table + person table
    - provider_id: In clinical event tables + provider table
    - care_site_id: In clinical events, visit_occurrence, care_site, person
    - visit_occurrence_id: In clinical events + visit_occurrence
    - visit_detail_id: In clinical events + visit_detail

    This causes:
    1. SQL errors: Database rejects query due to ambiguous column
    2. Unpredictable behavior: Database picks wrong table's column
    3. Silent bugs: Query executes but returns wrong data

Common mistakes:
    1. Forgetting to add table prefix in WHERE clause
    2. Copying column names to SELECT without qualification
    3. Using unqualified columns in GROUP BY / ORDER BY
    4. Assuming database will pick the "right" table

Violation pattern:
    SELECT person_id, condition_concept_id
    FROM condition_occurrence co
    JOIN person p ON co.person_id = p.person_id
    WHERE person_id = 12345
    -- WRONG: Ambiguous person_id reference

    SELECT provider_id, COUNT(*)
    FROM drug_exposure de
    JOIN provider pr ON de.provider_id = pr.provider_id
    GROUP BY provider_id
    -- WRONG: Ambiguous provider_id in GROUP BY

Correct pattern:
    SELECT co.person_id, co.condition_concept_id
    FROM condition_occurrence co
    JOIN person p ON co.person_id = p.person_id
    WHERE co.person_id = 12345
    -- CORRECT: Qualified with table alias

    SELECT pr.provider_id, COUNT(*)
    FROM drug_exposure de
    JOIN provider pr ON de.provider_id = pr.provider_id
    GROUP BY pr.provider_id
    -- CORRECT: Qualified in GROUP BY

Note: This rule only applies to multi-table queries (2+ tables).
Single-table queries cannot have ambiguous columns.
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

AMBIGUOUS_COLUMNS: Set[str] = {
    "person_id",
    "provider_id",
    "care_site_id",
    "visit_occurrence_id",
    "visit_detail_id",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_ambiguous_column(col_name: Optional[str]) -> bool:
    return _norm(col_name) in AMBIGUOUS_COLUMNS if col_name else False


def _get_table_count(select: exp.Select) -> int:
    """
    Count distinct tables referenced in this SELECT.
    Includes tables from FROM, JOINs, and nested expressions.
    """
    tables: Set[str] = set()

    for table in select.find_all(exp.Table):
        if table.name:
            tables.add(_norm(table.name))

    return len(tables)


def _is_qualified_column(col: exp.Column) -> bool:
    """Check if column has a table or alias qualifier."""
    return bool(col.table)


def _is_within_select_scope(col: exp.Column, select: exp.Select) -> bool:
    """
    Ensure the column belongs to this SELECT (not a nested subquery).
    """
    parent_select = col.find_ancestor(exp.Select)
    return parent_select is select


def _is_in_join_clause(col: exp.Column) -> bool:
    """Check if column is inside a JOIN ON clause."""
    return col.find_ancestor(exp.Join) is not None


def _find_unqualified_ambiguous_columns(select: exp.Select) -> List[exp.Column]:
    """
    Find unqualified ambiguous columns within a SELECT scope.
    """
    results: List[exp.Column] = []

    for col in select.find_all(exp.Column):
        # Ensure column belongs to this SELECT only
        if not _is_within_select_scope(col, select):
            continue

        # Skip JOIN ON clause (handled separately / always qualified in practice)
        if _is_in_join_clause(col):
            continue

        # Check ambiguity + qualification
        if _is_ambiguous_column(col.name) and not _is_qualified_column(col):
            results.append(col)

    return results


# --- Rule ------------------------------------------------------------------

@register
class AmbiguousColumnReferenceRule(Rule):
    """
    Detect unqualified ambiguous column references in multi-table queries.

    NOTE:
    This rule uses a heuristic based on known OMOP columns that commonly
    appear across multiple tables. It does not perform full schema resolution.
    """

    rule_id = "anti_patterns.ambiguous_column_reference"
    name = "Ambiguous Column Reference"

    description = (
        "Detects unqualified column references (e.g., person_id instead of "
        "co.person_id) in multi-table queries where the column likely appears "
        "in multiple OMOP tables. This can lead to SQL errors or incorrect results."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Qualify the column with a table name or alias "
        "(e.g., co.person_id). Always use explicit qualifiers in multi-table queries."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            for select_idx, select in enumerate(tree.find_all(exp.Select)):
                table_count = _get_table_count(select)

                # Only enforce for multi-table queries
                if table_count < 2:
                    continue

                seen: Set[Tuple[str, int]] = set()

                unqualified_cols = _find_unqualified_ambiguous_columns(select)

                for col in unqualified_cols:
                    col_name = _norm(col.name)

                    key = (col_name, select_idx)
                    if key in seen:
                        continue
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                f"Ambiguous column reference '{col.name}' in a multi-table query. "
                                f"This column commonly exists in multiple OMOP tables and should be "
                                f"qualified (e.g., alias.{col.name})."
                            ),
                            severity=self.severity,
                            suggested_fix=self.suggested_fix,
                            details={
                                "issue": "ambiguous_column_reference",
                                "column": col.name,
                                "table_count": table_count,
                                "select_index": select_idx,
                            },
                        )
                    )

        return violations


__all__ = ["AmbiguousColumnReferenceRule"]
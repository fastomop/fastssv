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
from fastssv.schemas import get_table_columns


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


def _get_tables_in_scope(select: exp.Select) -> Set[str]:
    """Normalized table names referenced in this SELECT's FROM/JOINs."""
    tables: Set[str] = set()
    for table in select.find_all(exp.Table):
        if table.name:
            tables.add(_norm(table.name))
    return tables


def _column_is_genuinely_ambiguous(col_name: str, tables_in_scope: Set[str]) -> bool:
    """True only if the column actually exists in >= 2 tables in scope.

    A query like `FROM drug_exposure JOIN concept ON ...` uses 2 tables, but
    person_id only exists in drug_exposure (concept has no person_id). So
    `person_id` is NOT ambiguous there.
    """
    tables_with_col = 0
    for table_name in tables_in_scope:
        cols = get_table_columns(table_name)
        if col_name in cols:
            tables_with_col += 1
            if tables_with_col >= 2:
                return True
    return False


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
    long_description = (
        "Columns like person_id, visit_occurrence_id, provider_id, and "
        "care_site_id appear in many OMOP tables. In a multi-table join, an "
        "unqualified reference is either a hard error (ambiguous column) or "
        "a silent bug where the parser resolves it to the first matching "
        "table — which may not be the one the author intended. Always "
        "qualify every column in a multi-table query with its table alias."
    )
    example_bad = (
        "SELECT person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN person p ON co.person_id = p.person_id\n"
        "WHERE person_id = 1;"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN person p ON co.person_id = p.person_id\n"
        "WHERE co.person_id = 1;"
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
                tables_in_scope = _get_tables_in_scope(select)
                table_count = len(tables_in_scope)

                # Only enforce for multi-table queries
                if table_count < 2:
                    continue

                seen: Set[Tuple[str, int]] = set()

                unqualified_cols = _find_unqualified_ambiguous_columns(select)

                for col in unqualified_cols:
                    col_name = _norm(col.name)

                    # Only warn if the column actually exists in >= 2 tables
                    # in scope. Otherwise it's not genuinely ambiguous.
                    if not _column_is_genuinely_ambiguous(col_name, tables_in_scope):
                        continue

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

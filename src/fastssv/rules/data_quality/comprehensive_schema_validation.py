"""Comprehensive OMOP Schema Validation Rule.

Validates that every table and column referenced in a query exists in the
OMOP CDM v5.4 schema. Scope-aware: skips CTE names, subquery aliases, and
SELECT-clause column aliases (those are derived, not physical references).

Source of truth is ``fastssv.schemas.CDM_COLUMN_TYPES``; this rule reads
it through the package boundary.
"""

from typing import List, Set
from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import extract_aliases, normalize_name, parse_sql
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register
from fastssv.schemas import CDM_COLUMN_TYPES, get_table_columns


# Schema predicates derived from CDM_COLUMN_TYPES. Inlining them as small
# helpers keeps the validation logic below readable.


def _is_valid_table(table_name: str) -> bool:
    return bool(table_name) and table_name.lower() in CDM_COLUMN_TYPES


def _is_valid_column(table_name: str, column_name: str) -> bool:
    if not (table_name and column_name):
        return False
    return column_name.lower() in CDM_COLUMN_TYPES.get(table_name.lower(), {})


def _get_all_tables() -> Set[str]:
    return set(CDM_COLUMN_TYPES.keys())


def _norm(x: str) -> str:
    return normalize_name(x) if x else ""


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    """Extract all CTE names from WITH clauses.

    CTEs are query-scoped tables and should not be validated against OMOP schema.
    """
    cte_names = set()
    for cte in tree.find_all(exp.CTE):
        if cte.alias:
            cte_names.add(_norm(cte.alias))
    return cte_names


def _extract_subquery_aliases(tree: exp.Expression) -> Set[str]:
    """Extract all subquery aliases.

    Subqueries with aliases are derived tables, not physical tables.
    """
    subquery_aliases = set()
    for subquery in tree.find_all(exp.Subquery):
        if subquery.alias:
            subquery_aliases.add(_norm(subquery.alias))
    return subquery_aliases


def _extract_select_aliases(tree: exp.Expression) -> Set[str]:
    """Extract all column aliases defined in SELECT clauses.

    These are derived expressions (COUNT(*) AS x, MONTH(...) AS y), not physical columns.
    They should not be validated against the schema.
    """
    select_aliases = set()

    # Find all SELECT statements
    for select in tree.find_all(exp.Select):
        # Get all expressions in the SELECT clause
        for expr in select.expressions:
            # Check if it has an alias
            if isinstance(expr, exp.Alias):
                select_aliases.add(_norm(expr.alias))

    return select_aliases


@register
class ComprehensiveSchemaValidationRule(Rule):
    """Validates all table and column references against OMOP CDM schema.

    Layer: SCHEMA
    Severity: ERROR (always - schema violations indicate incorrect queries)

    Scope-aware validation:
    - Only validates references to physical OMOP CDM tables
    - Excludes CTEs (query-scoped tables)
    - Excludes subqueries and derived tables
    - Excludes SELECT clause aliases (derived expressions)
    """

    rule_id = "data_quality.schema_validation"
    name = "OMOP Schema Validation"
    description = (
        "Validates that all referenced tables and columns exist in OMOP CDM 5.4 schema. "
        "Schema violations indicate queries that will fail at runtime or produce incorrect results. "
        "Only validates physical table references - excludes CTEs, subqueries, and derived expressions."
    )
    severity = Severity.ERROR
    suggested_fix = "REPLACE: the misspelled / nonexistent table or column with the correct OMOP CDM v5.4 name. Common cases: 'cohort_result' → 'cohort'; '<event>_start_date' → '<event>_date' for procedure/measurement/observation/specimen/note; v5.3 'admitting_source_concept_id' → v5.4 'admitted_from_concept_id'."
    long_description = (
        "Every table and column referenced in the query must exist in the "
        "OMOP CDM 5.4 specification. This rule catches typos "
        "(e.g. 'cohort_result' instead of 'cohort'), tables from other "
        "schemas or vocabulary extensions that aren't part of CDM 5.4, and "
        "non-existent columns on otherwise-valid tables. It operates only "
        "on physical references; CTEs, subquery aliases, and computed "
        "expressions are deliberately ignored so compound queries don't "
        "raise false positives."
    )
    example_bad = "SELECT person_id, cohort_start_date\nFROM cohort_result\nWHERE cohort_definition_id = 1;"
    example_good = (
        "SELECT condition_occurrence_id, person_id, condition_start_date\n"
        "FROM condition_occurrence\n"
        "WHERE condition_concept_id = 201820;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL against OMOP schema with scope awareness."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if not tree:
                continue

            # Extract CTEs, subqueries, and aliases for scope awareness
            cte_names = _extract_cte_names(tree)
            subquery_aliases = _extract_subquery_aliases(tree)
            select_aliases = _extract_select_aliases(tree)
            table_aliases = extract_aliases(tree)

            # Track which tables/columns we've already reported to avoid duplicates
            reported_tables = set()
            reported_columns = set()

            # Validate all table references (excluding CTEs and subqueries)
            for table in tree.find_all(exp.Table):
                table_name = _norm(table.name)

                if not table_name:
                    continue

                # Skip schema-qualified tables (@vocab.concept -> concept)
                if "." in table_name:
                    table_name = table_name.split(".")[-1]

                # Skip CTEs - they're query-scoped tables, not physical tables
                if table_name in cte_names:
                    continue

                # Skip subquery aliases - they're derived tables
                if table_name in subquery_aliases:
                    continue

                table_key = table_name
                if table_key in reported_tables:
                    continue

                if not _is_valid_table(table_name):
                    # Check for similar table names
                    all_tables = _get_all_tables()
                    similar = [t for t in all_tables if table_name in t or t in table_name]

                    # Structured patch: when there is exactly one similar
                    # table and the source contains a unique occurrence of
                    # the misspelled name (case-insensitive whole word
                    # match handled by locate()), emit a REPLACE patch.
                    # Otherwise leave the violation FREEFORM.
                    patch = None
                    if len(similar) == 1:
                        span = locate(sql, table_name)
                        if span is not None:
                            patch = patch_replace(span, similar[0])

                    violations.append(
                        self.create_violation(
                            message=f"Table '{table_name}' does not exist in OMOP CDM 5.4 schema.",
                            severity=Severity.ERROR,
                            suggested_fix_patch=patch,
                            details={
                                "layer": "schema",
                                "type": "invalid_table",
                                "table": table_name,
                                "similar_tables": similar[:3] if similar else [],
                            },
                        )
                    )
                    reported_tables.add(table_key)

            # Validate all column references (only from physical tables)
            for column in tree.find_all(exp.Column):
                col_name = _norm(column.name)
                if not col_name:
                    continue

                # Skip SELECT clause aliases - they're derived expressions, not physical columns
                if col_name in select_aliases:
                    continue

                # Get table reference
                table_ref = _norm(column.table) if column.table else None

                # Resolve table name through aliases
                if table_ref:
                    # Check if this is a CTE or subquery
                    if table_ref in cte_names or table_ref in subquery_aliases:
                        continue

                    if table_ref in table_aliases:
                        resolved_table = _norm(table_aliases[table_ref])
                        # Handle schema-qualified tables
                        if "." in resolved_table:
                            resolved_table = resolved_table.split(".")[-1]
                    else:
                        resolved_table = table_ref

                    # Skip if resolved table is a CTE or subquery
                    if resolved_table in cte_names or resolved_table in subquery_aliases:
                        continue
                else:
                    # No table qualifier - try to infer from context (single table in FROM)
                    physical_tables = []
                    for t in tree.find_all(exp.Table):
                        t_name = _norm(t.name)
                        if t_name and t_name not in cte_names and t_name not in subquery_aliases:
                            if _is_valid_table(t_name):
                                physical_tables.append(t_name)

                    # If there's exactly one physical table, assume it's that one
                    if len(physical_tables) == 1:
                        resolved_table = physical_tables[0]
                    else:
                        # Can't determine table, skip validation
                        continue

                # Skip if table is invalid (already reported)
                if not _is_valid_table(resolved_table):
                    continue

                column_key = f"{resolved_table}.{col_name}"
                if column_key in reported_columns:
                    continue

                if not _is_valid_column(resolved_table, col_name):
                    # Get valid columns for suggestions
                    valid_cols = get_table_columns(resolved_table)
                    similar = [c for c in valid_cols if col_name in c or c in col_name]

                    # Structured patch: REPLACE the misspelled column when
                    # exactly one similar candidate exists *and* the column
                    # appears uniquely in source. Locating the bare column
                    # token must avoid false matches in unrelated tables;
                    # a unique whole-string match is the safest heuristic.
                    patch = None
                    if len(similar) == 1:
                        span = locate(sql, col_name)
                        if span is not None:
                            patch = patch_replace(span, similar[0])

                    violations.append(
                        self.create_violation(
                            message=f"Column '{col_name}' does not exist in table '{resolved_table}'.",
                            severity=Severity.ERROR,
                            suggested_fix_patch=patch,
                            details={
                                "layer": "schema",
                                "type": "invalid_column",
                                "table": resolved_table,
                                "column": col_name,
                                "similar_columns": similar[:3] if similar else [],
                            },
                        )
                    )
                    reported_columns.add(column_key)

        return violations


__all__ = ["ComprehensiveSchemaValidationRule"]

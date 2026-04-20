"""Comprehensive OMOP Schema Validation Rule.

Layer: SCHEMA
Validates all table and column references against OMOP CDM 5.4 schema.
This is fundamental data model validation - violations are always errors.

Fixed to handle:
1. CTE awareness - CTEs are query-scoped tables, not physical tables
2. SELECT alias handling - aliases are derived, not physical columns
3. Scope-aware validation - only validate references to physical CDM tables
"""

from typing import List, Set, Dict
from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import extract_aliases, normalize_name, parse_sql
from fastssv.core.omop_schema import is_valid_table, is_valid_column, get_table_columns, get_all_tables
from fastssv.core.registry import register


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


def _is_in_select_clause(node: exp.Expression) -> bool:
    """Check if a column reference is in a SELECT clause (as output).

    Columns in SELECT output are being defined, not referenced from schema.
    We only want to validate columns referenced FROM tables.
    """
    parent = node.parent
    while parent:
        # If we're directly under a Select's expressions, we're in SELECT output
        if isinstance(parent, exp.Select):
            # Check if this node is in the SELECT expressions (not WHERE, JOIN, etc.)
            if hasattr(parent, 'expressions') and node in parent.walk():
                # Walk up from node to see if we're in the expressions list
                temp = node
                while temp and temp.parent != parent:
                    temp = temp.parent
                if temp and hasattr(parent, 'expressions') and temp in parent.expressions:
                    return True
            return False
        parent = parent.parent
    return False


def _is_column_from_physical_table(column: exp.Column, aliases: Dict[str, str],
                                   cte_names: Set[str], subquery_aliases: Set[str]) -> bool:
    """Check if a column reference is from a physical CDM table.

    Returns False if:
    - Column is from a CTE
    - Column is from a subquery
    - Column has no table qualifier
    - Table is not in OMOP schema
    """
    if not column.table:
        return False

    table_ref = _norm(column.table)

    # Check if it's a CTE
    if table_ref in cte_names:
        return False

    # Check if it's a subquery alias
    if table_ref in subquery_aliases:
        return False

    # Resolve through aliases
    if table_ref in aliases:
        resolved_table = _norm(aliases[table_ref])
        # Handle schema-qualified tables
        if "." in resolved_table:
            resolved_table = resolved_table.split(".")[-1]
    else:
        resolved_table = table_ref

    # Check if it's a CTE (after alias resolution)
    if resolved_table in cte_names or resolved_table in subquery_aliases:
        return False

    # Check if it's a valid OMOP table
    return is_valid_table(resolved_table)


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

    rule_id = "schema.comprehensive_validation"
    name = "OMOP Schema Validation"
    description = (
        "Validates that all referenced tables and columns exist in OMOP CDM 5.4 schema. "
        "Schema violations indicate queries that will fail at runtime or produce incorrect results. "
        "Only validates physical table references - excludes CTEs, subqueries, and derived expressions."
    )
    severity = Severity.ERROR
    suggested_fix = "Ensure all table and column names match the OMOP CDM 5.4 schema"

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

                if not is_valid_table(table_name):
                    # Check for similar table names
                    all_tables = get_all_tables()
                    similar = [t for t in all_tables if table_name in t or t in table_name]

                    violations.append(self.create_violation(
                        message=f"Table '{table_name}' does not exist in OMOP CDM 5.4 schema.",
                        severity=Severity.ERROR,
                        details={
                            "layer": "schema",
                            "type": "invalid_table",
                            "table": table_name,
                            "similar_tables": similar[:3] if similar else [],
                        }
                    ))
                    reported_tables.add(table_key)

            # Validate all column references (only from physical tables)
            for column in tree.find_all(exp.Column):
                col_name = _norm(column.name)
                if not col_name:
                    continue

                # Skip SELECT clause aliases - they're derived expressions, not physical columns
                if col_name in select_aliases:
                    continue

                # Skip columns defined in SELECT output (being defined, not referenced)
                if _is_in_select_clause(column):
                    continue

                # Only validate columns from physical CDM tables
                if not _is_column_from_physical_table(column, table_aliases, cte_names, subquery_aliases):
                    continue

                # Get table reference
                table_ref = _norm(column.table) if column.table else None

                # Resolve table name through aliases
                if table_ref and table_ref in table_aliases:
                    resolved_table = _norm(table_aliases[table_ref])
                    # Handle schema-qualified tables
                    if "." in resolved_table:
                        resolved_table = resolved_table.split(".")[-1]
                else:
                    resolved_table = table_ref

                if not resolved_table:
                    continue

                # Skip if table is invalid (already reported)
                if not is_valid_table(resolved_table):
                    continue

                column_key = f"{resolved_table}.{col_name}"
                if column_key in reported_columns:
                    continue

                if not is_valid_column(resolved_table, col_name):
                    # Get valid columns for suggestions
                    valid_cols = get_table_columns(resolved_table)
                    similar = [c for c in valid_cols if col_name in c or c in col_name]

                    violations.append(self.create_violation(
                        message=f"Column '{col_name}' does not exist in table '{resolved_table}'.",
                        severity=Severity.ERROR,
                        details={
                            "layer": "schema",
                            "type": "invalid_column",
                            "table": resolved_table,
                            "column": col_name,
                            "similar_columns": similar[:3] if similar else [],
                        }
                    ))
                    reported_columns.add(column_key)

        return violations


__all__ = ["ComprehensiveSchemaValidationRule"]

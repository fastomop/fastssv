"""Comprehensive OMOP Schema Validation Rule.

Validates that every table and column referenced in a query exists in the
OMOP CDM v5.4 schema. Scope-aware: skips CTE names, subquery aliases, and
SELECT-clause column aliases (those are derived, not physical references).

Source of truth is ``fastssv.schemas.CDM_COLUMN_TYPES``; this rule reads
it through the package boundary.
"""

from typing import Dict, List, Optional, Set
from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
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


_TIME_UNIT_KEYWORDS = frozenset(
    {
        "day",
        "days",
        "month",
        "months",
        "year",
        "years",
        "hour",
        "hours",
        "minute",
        "minutes",
        "second",
        "seconds",
        "week",
        "weeks",
        "quarter",
        "quarters",
        "millisecond",
        "milliseconds",
        "microsecond",
        "microseconds",
        "nanosecond",
        "nanoseconds",
        "epoch",
    }
)
_DATE_FN_TYPES = (
    exp.DateDiff,
    exp.DateAdd,
    exp.DateSub,
    exp.DateTrunc,
    exp.TimestampDiff,
    exp.TimestampAdd,
    exp.TimestampSub,
    exp.Extract,
)


def _is_time_unit_arg(column: exp.Column) -> bool:
    """True for unqualified ``Column`` nodes that are actually unit keywords
    (``day``, ``month`` …) inside a date function. sqlglot parses
    ``DATEDIFF(day, x, y)`` with ``day`` as a ``Column`` node; treating it
    as a real column reference yields false-positive
    "Column 'day' does not exist in table …" errors.
    """
    if column.table:
        return False
    if column.name.lower() not in _TIME_UNIT_KEYWORDS:
        return False
    parent = column.parent
    return isinstance(parent, _DATE_FN_TYPES)


def _local_aliases(select: exp.Select) -> Dict[str, str]:
    """Aliases declared by ``select``'s own FROM/JOIN — excluding tables
    nested inside subqueries or further CTE bodies inside this scope.

    Required because a query that reuses an alias across CTEs (`omop.concept c`
    in one CTE, `cond_occ c` in the outer SELECT) collapses under the global
    ``extract_aliases`` — last-write-wins on a flat dict — which then
    misattributes column references and yields false-positive
    "column does not exist in table X" errors.
    """
    aliases: Dict[str, str] = {}
    from_node = select.args.get("from_") or select.args.get("from")
    scopes = [from_node] + list(select.args.get("joins") or [])
    for scope_node in scopes:
        if scope_node is None:
            continue
        for tbl in scope_node.find_all(exp.Table):
            # Restrict to tables whose immediate enclosing Select is `select`
            # (i.e. not buried in a Subquery or nested SELECT inside this scope).
            if tbl.find_ancestor(exp.Select) is not select:
                continue
            real = _norm(tbl.name)
            alias = tbl.alias_or_name
            if alias:
                aliases[_norm(alias)] = real
            aliases[real] = real
    return aliases


def _resolve_column_table(column: exp.Column) -> Optional[str]:
    """Resolve ``column.table`` to a real table name using scope-local aliases.

    Walks up enclosing ``Select`` scopes (innermost first) so correlated
    subqueries can still see outer-scope aliases, but inner scopes shadow.
    Returns ``None`` if no enclosing Select exists; returns the literal
    table_ref if it isn't bound by any visible alias (the caller decides
    whether to treat that as an unknown table).
    """
    table_ref = _norm(column.table) if column.table else None
    if not table_ref:
        return None

    select = column.find_ancestor(exp.Select)
    while select is not None:
        local = _local_aliases(select)
        if table_ref in local:
            return local[table_ref]
        select = select.find_ancestor(exp.Select)
    return table_ref


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

            # Extract CTEs, subqueries, and SELECT aliases for scope awareness.
            # Column resolution uses _resolve_column_table for scope-local lookup
            # (the global extract_aliases is intentionally not used here — it
            # collapses reused aliases across CTEs).
            cte_names = _extract_cte_names(tree)
            subquery_aliases = _extract_subquery_aliases(tree)
            select_aliases = _extract_select_aliases(tree)

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

                # Skip unit keywords that sqlglot parses as Column nodes
                # (e.g. ``DATEDIFF(day, ...)`` -> Column(day) inside DateDiff).
                if _is_time_unit_arg(column):
                    continue

                # Get table reference
                table_ref = _norm(column.table) if column.table else None

                # Resolve table name through scope-local aliases.
                if table_ref:
                    # Skip CTE / subquery references at the alias-name layer.
                    if table_ref in cte_names or table_ref in subquery_aliases:
                        continue

                    resolved_table = _resolve_column_table(column) or table_ref
                    if "." in resolved_table:
                        resolved_table = resolved_table.split(".")[-1]

                    # After resolution, the underlying name may itself be a
                    # CTE / subquery (alias points at one).
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

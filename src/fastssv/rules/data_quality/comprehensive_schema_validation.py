"""OMOP Schema Validation Rule.

Checks every table and column a statement references against the OMOP CDM v5.4 field-level specification
(``fastssv.schemas.CDM_COLUMN_TYPES``, 39 tables).

Columns. A column that a CDM table does not have makes the statement fail on any conformant CDM: ERROR. The
exception is a table the specification places in its own cohort schema (``databaseSchema`` = cohort in the
field-level CSV: cohort, cohort_definition). Analytic tools create their own tables under those names, so a column
missing from the CDM definition is a WARNING that the column was not checked. The
reference is resolved the way the engine resolves it: a qualified name through the alias of its own SELECT (then
enclosing SELECTs, for correlated subqueries), through CTEs and derived tables including ``SELECT *``; an
unqualified name against every source of its SELECT and of the enclosing ones. It is reported only when every
source that could supply the name is known not to. ``USING (col)`` names and ``INSERT INTO t (col, ...)`` lists are
checked the same way. A name the specification renamed in v5.4, or keeps on a neighbouring table, is reported with
that replacement.

Tables. The schema is closed: a table that is neither in the specification nor defined in the same input
(CTE, CREATE, SELECT INTO, temp table) is an ERROR. This is a declared policy, like the write protection: a
database may hold results or staging tables FastSSV cannot see, but an analytics query that reaches outside the
CDM cannot be validated, and agent-written SQL that invents a table (``condition``, ``drug_concept``) fails. The
message names the nearest CDM table when the name is one or two edits away (``condition_occurence``), and says
when v5.4 removed the table (``attribute_definition``).

Not checked: CTE names, derived tables, tables created in the same batch, T-SQL ``#temp`` tables, the targets of
CREATE / DROP / ALTER / ANALYZE / TRUNCATE, and system catalogues.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    _find_cte,
    _provides,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    _query_branches,
    _star_qualifiers,
    is_schema_qualified,
    normalize_name,
    parse_sql,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.references import CDM_FIELD_LEVEL, CDM_V54_CHANGES, DQD_FIELD_THRESHOLDS, cdm_table, section
from fastssv.core.registry import register
from fastssv.core.validation_context import get_validation_context
from fastssv.schemas import CDM_COLUMN_TYPES

from ._dq_schema_hints import REMOVED_IN_V54, column_hint, near_miss_table, replacement_column
from ._dq_table_schemas import TABLE_DATABASE_SCHEMA

# Tables the specification places outside the CDM and vocabulary schemas (cohort, cohort_definition). Analytic tools
# create their own tables under these names with columns of their own, so a missing column is not certain to fail.
_TOOL_SCHEMA_TABLES = frozenset(t for t, schema in TABLE_DATABASE_SCHEMA.items() if schema not in ("cdm", "vocab"))

# Database system catalogues: their tables are metadata views, never CDM tables.
_SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog", "sys"})

# Statements whose target table is being defined or maintained, not read or written row by row.
_DDL_TARGET_PARENTS = (exp.Create, exp.Drop, exp.Alter, exp.Analyze, exp.TruncateTable)
_DML = (exp.Insert, exp.Update, exp.Delete, exp.Merge)

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

_CTE_BOUND = "<cte>"  # alias bound to a CTE: its columns come from the CTE body, not from the catalogue


def _norm(x: Optional[str]) -> str:
    return normalize_name(x) if x else ""


def _is_valid_table(name: str) -> bool:
    return bool(name) and name in CDM_COLUMN_TYPES


def _is_valid_column(table: str, column: str) -> bool:
    return column in CDM_COLUMN_TYPES.get(table, {})


def _is_time_unit_arg(column: exp.Column) -> bool:
    """sqlglot parses the unit of ``DATEDIFF(day, x, y)`` as a Column node; it is a keyword."""
    return not column.table and column.name.lower() in _TIME_UNIT_KEYWORDS and isinstance(column.parent, _DATE_FN_TYPES)


def _is_temp(table: exp.Table) -> bool:
    return table.name.startswith("#") or (
        isinstance(table.this, exp.Identifier) and bool(table.this.args.get("temporary"))
    )


def _dml_target(table: exp.Table) -> Optional[exp.Expression]:
    """The INSERT / UPDATE / DELETE / MERGE statement that writes ``table``, if it is that statement's target."""
    node, parent = table, table.parent
    if isinstance(parent, exp.Schema):
        node, parent = parent, parent.parent
    return parent if isinstance(parent, _DML) and parent.this is node else None


def _is_ddl_target(table: exp.Table) -> bool:
    parent = table.parent
    if isinstance(parent, exp.Schema):
        parent = parent.parent
    return isinstance(parent, _DDL_TARGET_PARENTS)


class _Scope:
    """Names a statement defines for itself: CTEs, derived-table aliases and batch-local tables."""

    def __init__(self, tree: exp.Expression, local_tables: Set[str]):
        self.cte_names = {_norm(c.alias) for c in tree.find_all(exp.CTE) if c.alias}
        self.subquery_aliases = {_norm(s.alias) for s in tree.find_all(exp.Subquery) if s.alias}
        self.local_tables = local_tables

    def is_unknown_source(self, src: exp.Expression) -> bool:
        """A FROM / JOIN source whose columns the catalogue does not describe."""
        if not isinstance(src, exp.Table):
            return True
        name = _norm(src.name)
        return (
            (name in self.cte_names and not is_schema_qualified(src))
            or name in self.subquery_aliases
            or name in self.local_tables
            or _is_temp(src)
            or not _is_valid_table(name)
        )


def _local_aliases(select: exp.Select, scope: _Scope) -> Dict[str, str]:
    """alias / name -> CDM table (or ``_CTE_BOUND``) for the base tables in ``select``'s own FROM / JOIN."""
    aliases: Dict[str, str] = {}
    for src in _select_sources(select):
        if not isinstance(src, exp.Table) or not src.name:
            continue
        name = _norm(src.name)
        # A CTE shadows unqualified names only: `omop.drug_exposure` under `WITH drug_exposure AS (...)` is the table.
        real = _CTE_BOUND if (name in scope.cte_names and not is_schema_qualified(src)) else name
        aliases[_norm(src.alias_or_name)] = real
        aliases.setdefault(real, real)
    return aliases


def _outer_scope(select: exp.Select) -> Optional[exp.Select]:
    """The enclosing SELECT whose sources ``select`` can see. A correlated subquery sees the outer query; a CTE
    body or a non-LATERAL derived table does not."""
    node = select.parent
    while node is not None:
        if isinstance(node, exp.CTE):
            return None
        if isinstance(node, exp.Subquery) and isinstance(node.parent, (exp.From, exp.Join)):
            return None
        if isinstance(node, exp.Select):
            return node
        node = node.parent
    return None


def _alias_columns(alias: Optional[exp.Expression]) -> Optional[List[str]]:
    if isinstance(alias, exp.TableAlias) and alias.columns:
        return [_norm(c.name) for c in alias.columns]
    return None


def _renames_columns(select: exp.Select, src: exp.Expression) -> bool:
    """``AS t(a, b)`` on the source or on the CTE it reads renames output columns, so projections cannot be read."""
    if _alias_columns(src.args.get("alias")) is not None:
        return True
    if isinstance(src, exp.Table) and not is_schema_qualified(src):
        cte = _find_cte(select, _norm(src.name))
        return cte is not None and _alias_columns(cte.args.get("alias")) is not None
    return False


def _lacks(select: exp.Select, src: exp.Expression, column: str, scope: "_Scope") -> bool:
    """True only when ``src`` is known not to output ``column``."""
    if _renames_columns(select, src):
        return False
    if isinstance(src, exp.Table) and _source_query(select, src) is None:
        return not scope.is_unknown_source(src) and not _is_valid_column(_norm(src.name), column)
    return _provides(select, src, column, 0) is False


def _star_tables(select: exp.Select, src: exp.Expression, depth: int = 0) -> Set[str]:
    """CDM tables whose columns a derived source passes on through ``SELECT *``."""
    if depth > 12:
        return set()
    query = _source_query(select, src)
    if query is None:
        name = _norm(src.name) if isinstance(src, exp.Table) else ""
        return {name} if _is_valid_table(name) else set()
    out: Set[str] = set()
    for branch in _query_branches(query):
        stars = _star_qualifiers(branch)
        for inner in _select_sources(branch):
            if None in stars or _norm(inner.alias_or_name) in stars:
                out |= _star_tables(branch, inner, depth + 1)
    return out


def _statement_sources(stmt: exp.Expression) -> Optional[List[exp.Expression]]:
    """Row sources an UPDATE / DELETE / MERGE / INSERT exposes to names outside any SELECT: its target plus
    UPDATE ... FROM, DELETE ... USING and MERGE ... USING. None when the shape is not modelled."""
    target = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
    sources: List[exp.Expression] = [target]
    if isinstance(stmt, exp.Update):
        from_node = stmt.args.get("from_") or stmt.args.get("from")
        if from_node is not None:
            sources.append(from_node.this)
        if stmt.args.get("joins"):
            return None
    elif isinstance(stmt, exp.Delete):
        using = stmt.args.get("using")
        sources.extend(using if isinstance(using, list) else [using] if using is not None else [])
    elif isinstance(stmt, exp.Merge):
        sources.append(stmt.args.get("using"))
    elif not isinstance(stmt, exp.Insert):
        return None
    return sources


def _output_alias_reference(column: exp.Column, select: exp.Select) -> bool:
    """An unqualified name that equals an output alias of its own SELECT, read outside the expression that defines
    it. PostgreSQL binds such a name to the output column in ORDER BY and GROUP BY; DuckDB, Snowflake and BigQuery
    also do in HAVING, WHERE or later select-list items. It is not a base-column reference on any of them."""
    name = _norm(column.name)
    for proj in select.expressions:
        if isinstance(proj, exp.Alias) and _norm(proj.alias) == name:
            node: Optional[exp.Expression] = column
            while node is not None and node is not select:
                if node is proj:
                    return False
                node = node.parent
            return True
    return False


def _orders_set_operation(column: exp.Column) -> bool:
    """A column in the ORDER BY of a UNION / INTERSECT / EXCEPT names an output column."""
    node = column.parent
    while node is not None and not isinstance(node, exp.Select):
        if isinstance(node, exp.Order) and isinstance(node.parent, exp.SetOperation):
            return True
        node = node.parent
    return False


@register
class ComprehensiveSchemaValidationRule(Rule):
    """Validates table and column references against the OMOP CDM v5.4 specification."""

    rule_id = "data_quality.schema_validation"
    name = "OMOP Schema Validation"
    description = (
        "Checks every referenced table and column against the OMOP CDM v5.4 specification. A column a CDM table "
        "does not have, a misspelled CDM table and a table v5.4 removed are errors: the statement fails on a "
        "conformant CDM. A table that is neither in the CDM nor defined in the same input is an error too."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the nonexistent table or column WITH its OMOP CDM v5.4 name. Columns renamed in v5.4: "
        "'admitting_source_concept_id' -> 'admitted_from_concept_id', 'discharge_to_concept_id' -> "
        "'discharged_to_concept_id', 'visit_detail_parent_id' -> 'parent_visit_detail_id'. procedure_occurrence, "
        "measurement, observation, specimen and note have '<event>_date', not '<event>_start_date'."
    )
    long_description = (
        "Every column read from a CDM table must exist in the OMOP CDM v5.4 specification; a column that does "
        "not makes the statement fail. The rule resolves each reference the way the engine does: through the "
        "alias of its own SELECT, through CTEs and derived tables (including SELECT *), and through enclosing "
        "queries for correlated subqueries. It also checks JOIN ... USING names and INSERT column lists. When "
        "the wrong name is one that v5.4 renamed (admitting_source_concept_id -> admitted_from_concept_id), or "
        "one that a neighbouring table holds (episode_event has no person_id; episode does), the finding says "
        "so. The schema is closed: a table that is neither in the specification nor defined in the same "
        "input (CTE, CREATE TABLE, SELECT INTO, temp table) is an error, by declared policy. The database may "
        "hold results or staging tables, but a query that reads them cannot be validated against the CDM. The "
        "finding names the nearest CDM table for a near miss (condition_occurence) and says when v5.4 removed "
        "the table (attribute_definition). COHORT and COHORT_DEFINITION are placed in a separate cohort schema by the "
        "field-level specification, and analytic tools create their own tables under those names, so a column "
        "missing from their CDM definition is a warning too."
    )
    example_bad = "SELECT person_id, procedure_concept_id\nFROM procedure_occurrence\nWHERE procedure_start_date >= DATE '2020-01-01';"
    example_good = (
        "SELECT person_id, procedure_concept_id\nFROM procedure_occurrence\nWHERE procedure_date >= DATE '2020-01-01';"
    )
    extra_examples = (
        RuleExample(
            sql="SELECT person_id\nFROM condition_occurence\nWHERE condition_concept_id = 201826;",
            severity=Severity.ERROR,
            note="Misspelled CDM table (condition_occurence): one edit from condition_occurrence.",
        ),
        RuleExample(
            sql="SELECT visit_occurrence_id\nFROM visit_occurrence\nWHERE admitting_source_concept_id = 8870;",
            severity=Severity.ERROR,
            note="CDM v5.3 column name; v5.4 renamed it to admitted_from_concept_id.",
        ),
        RuleExample(
            sql="SELECT ee.event_id, p.year_of_birth\nFROM episode_event ee\nJOIN person p ON ee.person_id = p.person_id;",
            severity=Severity.ERROR,
            note="episode_event has no person_id; join episode on episode_id and read episode.person_id.",
        ),
        RuleExample(
            sql="SELECT attribute_name\nFROM attribute_definition;",
            severity=Severity.ERROR,
            note="ATTRIBUTE_DEFINITION was removed from the CDM in v5.4.",
        ),
        RuleExample(
            sql="SELECT analysis_id, count_value\nFROM results.analysis_results\nWHERE analysis_id = 1;",
            severity=Severity.ERROR,
            note="A table outside the CDM. The schema is closed: define it in the same input or read a CDM table.",
        ),
        RuleExample(
            sql="SELECT cd.cohort_definition_id, cd.cohort_name\nFROM cohort_definition cd;",
            severity=Severity.WARNING,
            note="cohort_definition is in the separate cohort schema; a tool's own table may have cohort_name.",
        ),
    )
    references = (
        CDM_FIELD_LEVEL,
        DQD_FIELD_THRESHOLDS,
        cdm_table("procedure_occurrence"),
        cdm_table("episode_event"),
        section(CDM_V54_CHANGES, "visit_occurrence", "VISIT_OCCURRENCE"),
        section(CDM_V54_CHANGES, "visit_detail", "VISIT_DETAIL"),
        section(CDM_V54_CHANGES, "summary", "Summary"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        local_tables: Set[str] = set(get_validation_context().local_tables)
        for tree in trees:
            if tree is None:
                continue
            for create in tree.find_all(exp.Create):
                target = create.this.this if isinstance(create.this, exp.Schema) else create.this
                if isinstance(target, exp.Table) and target.name:
                    local_tables.add(_norm(target.name))

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            scope = _Scope(tree, local_tables)
            violations.extend(self._check_tables(sql, tree, scope))
            reported: Set[Tuple[str, ...]] = set()
            violations.extend(self._check_columns(sql, tree, scope, reported))
            violations.extend(self._check_using(sql, tree, scope, reported))
            violations.extend(self._check_insert_columns(sql, tree, scope, reported))
        return violations

    # --- tables ---------------------------------------------------------------------------------------------

    def _check_tables(self, sql: str, tree: exp.Expression, scope: _Scope) -> List[RuleViolation]:
        out: List[RuleViolation] = []
        cdm_schemas = {
            _norm(t.db)
            for t in tree.find_all(exp.Table)
            if _is_valid_table(_norm(t.name)) and not (_norm(t.name) in scope.cte_names and not is_schema_qualified(t))
        }
        seen: Set[str] = set()
        for table in tree.find_all(exp.Table):
            name = _norm(table.name)
            if not name or name in seen or _is_ddl_target(table) or _is_temp(table):
                continue
            # BigQuery's parser folds `information_schema.tables` into one dotted table name.
            in_catalogue = _norm(table.db) in _SYSTEM_SCHEMAS or name.split(".")[0] in _SYSTEM_SCHEMAS
            if in_catalogue or name in scope.local_tables or name in scope.subquery_aliases:
                continue
            if name in scope.cte_names and not is_schema_qualified(table):
                continue
            if _is_valid_table(name):
                continue
            seen.add(name)

            # A near-miss suggestion only makes sense where a CDM table is expected: no schema, or the schema the
            # statement reads its CDM tables from.
            expected_here = not is_schema_qualified(table) or not cdm_schemas or _norm(table.db) in cdm_schemas
            similar = near_miss_table(name)
            written = _dml_target(table) is not None
            if name in REMOVED_IN_V54:
                out.append(
                    self._table_violation(
                        name,
                        Severity.ERROR,
                        f"Table '{name}' does not exist in OMOP CDM 5.4 schema: {REMOVED_IN_V54[name]}.",
                        "REMOVE: the read of this table; a CDM v5.4 database does not have it.",
                        [],
                    )
                )
            elif expected_here and similar:
                span = locate(sql, name) if not written else None
                out.append(
                    self._table_violation(
                        name,
                        Severity.ERROR,
                        f"Table '{name}' does not exist in OMOP CDM 5.4 schema. Did you mean '{similar}'?",
                        f"REPLACE: `{name}` WITH `{similar}`.",
                        [similar],
                        patch_replace(span, similar) if span is not None else None,
                    )
                )
            else:
                out.append(
                    self._table_violation(
                        name,
                        Severity.ERROR,
                        f"Table '{name}' does not exist in the OMOP CDM 5.4 schema and is not defined in this input.",
                        "REPLACE: the table WITH the OMOP CDM v5.4 table that holds the data, or define it in the same "
                        "input (CTE or CREATE TABLE).",
                        [],
                    )
                )
        return out

    def _table_violation(
        self,
        name: str,
        severity: Severity,
        message: str,
        fix: str,
        similar: List[str],
        patch: Optional[dict] = None,
    ) -> RuleViolation:
        return self.create_violation(
            message=message,
            severity=severity,
            suggested_fix=fix,
            suggested_fix_patch=patch,
            details={
                "layer": "schema",
                "type": "invalid_table",
                "table": name,
                "similar_tables": similar,
            },
        )

    # --- columns --------------------------------------------------------------------------------------------

    def _column_violation(
        self, sql: str, tables: List[str], column: str, reported: Set[Tuple[str, ...]], via: Optional[str] = None
    ) -> Optional[RuleViolation]:
        key = (*sorted(tables), column)
        if key in reported:
            return None
        reported.add(key)
        table = tables[0]
        if via is not None:
            message = f"Column '{column}' does not exist in '{via}', which reads {', '.join(tables)}."
        elif len(tables) == 1:
            message = f"Column '{column}' does not exist in table '{table}'."
        else:
            message = f"Column '{column}' does not exist in any table read here ({', '.join(tables)})."
        hints = [h for h in (column_hint(t, column) for t in tables) if h]
        candidates = {replacement_column(t, column) for t in tables} - {None}
        patch = None
        fix = self.suggested_fix
        if len(candidates) == 1:
            new = next(iter(candidates))
            fix = f"REPLACE: `{column}` WITH `{new}`."
            span = locate(sql, column)
            if span is not None:
                patch = patch_replace(span, new)
        elif hints:
            fix = hints[0]
        if hints:
            message = f"{message} {hints[0]}"
        tool_tables = sorted(set(tables) & _TOOL_SCHEMA_TABLES)
        if tool_tables:
            shown = ", ".join(tool_tables)
            message = (
                f"Column '{column}' is not in the CDM v5.4 definition of {shown}. The specification places "
                f"{shown} in a separate cohort schema, and analytic tools create their own tables under that name, "
                "so the table may be a tool's own with this column; the column was not checked."
            )
            fix = (
                f"No change is needed if this {shown} table is a tool's own table that has `{column}`. If it is the "
                "CDM table, use a column of its CDM v5.4 definition."
            )
            patch = None
        return self.create_violation(
            message=message,
            severity=Severity.WARNING if tool_tables else Severity.ERROR,
            suggested_fix=fix,
            suggested_fix_patch=patch,
            details={
                "layer": "schema",
                "type": "unchecked_column" if tool_tables else "invalid_column",
                "table": table,
                "tables": tables,
                "column": column,
                "similar_columns": sorted(candidates),
            },
        )

    def _qualified(self, column: exp.Column, scope: _Scope) -> Optional[Tuple[List[str], Optional[str]]]:
        """(CDM tables the qualifier reads, derived alias or None) when the column is known to be missing."""
        qualifier, name = _norm(column.table), _norm(column.name)
        select = column.find_ancestor(exp.Select)
        if select is None:
            stmt = column.find_ancestor(*_DML)
            for src in (_statement_sources(stmt) or []) if stmt is not None else []:
                if isinstance(src, exp.Table) and _norm(src.alias_or_name) == qualifier:
                    table = _norm(src.name)
                    if not scope.is_unknown_source(src) and not _is_valid_column(table, name):
                        return [table], None
            return None

        current: Optional[exp.Select] = select
        while current is not None:
            aliases = _local_aliases(current, scope)
            if qualifier in aliases:
                table = aliases[qualifier]
                if table != _CTE_BOUND:
                    if table in scope.local_tables or not _is_valid_table(table) or _is_valid_column(table, name):
                        return None
                    return [table], None
                break
            if any(_norm(s.alias_or_name) == qualifier for s in _select_sources(current)):
                break
            current = current.find_ancestor(exp.Select)

        owner, src = _source_by_qualifier(select, qualifier)
        if owner is None or src is None or isinstance(src, exp.Table) and _source_query(owner, src) is None:
            return None
        if not _lacks(owner, src, name, scope):
            return None
        tables = sorted(_star_tables(owner, src))
        return (tables, qualifier) if tables else None

    def _unqualified(self, column: exp.Column, select: exp.Select, scope: _Scope) -> Optional[List[str]]:
        """CDM tables to report an unqualified name against: every visible scope is fully known and none has it."""
        name = _norm(column.name)
        innermost: Optional[List[str]] = None
        current: Optional[exp.Select] = select
        while current is not None:
            sources = _select_sources(current)
            if name in {_norm(s.alias_or_name) for s in sources}:
                return None  # whole-row reference to a table alias: to_jsonb(p), COUNT(p)
            found: List[str] = []
            for src in sources:
                if scope.is_unknown_source(src):
                    if not _lacks(current, src, name, scope):
                        return None  # the name may come from a source whose columns are not known
                    found.extend(_star_tables(current, src))
                else:
                    table = _norm(src.name)
                    if _is_valid_column(table, name):
                        return None
                    found.append(table)
            if innermost is None and found:
                innermost = sorted(set(found))
            current = _outer_scope(current)

        stmt = select.find_ancestor(exp.Update, exp.Delete, exp.Merge)
        if stmt is not None and innermost is not None:
            sources = _statement_sources(stmt)
            if sources is None:
                return None
            for src in sources:
                if scope.is_unknown_source(src) or _is_valid_column(_norm(src.name), name):
                    return None
        return innermost

    def _check_columns(
        self, sql: str, tree: exp.Expression, scope: _Scope, reported: Set[Tuple[str, ...]]
    ) -> List[RuleViolation]:
        out: List[RuleViolation] = []
        for column in tree.find_all(exp.Column):
            name = _norm(column.name)
            if not name or name == "*" or isinstance(column.this, exp.Star):
                continue
            if _is_time_unit_arg(column) or _orders_set_operation(column):
                continue

            via: Optional[str] = None
            if column.table:
                found = self._qualified(column, scope)
                if found is None:
                    continue
                tables, via = found
            else:
                select = column.find_ancestor(exp.Select)
                if select is None:
                    stmt = column.find_ancestor(*_DML)
                    sources = _statement_sources(stmt) if stmt is not None else None
                    if not sources or len(sources) != 1 or scope.is_unknown_source(sources[0]):
                        continue
                    table = _norm(sources[0].name)
                    if _is_valid_column(table, name):
                        continue
                    tables = [table]
                else:
                    if _output_alias_reference(column, select):
                        continue
                    resolved = self._unqualified(column, select, scope)
                    if resolved is None:
                        continue
                    tables = resolved
            violation = self._column_violation(sql, tables, name, reported, via)
            if violation is not None:
                out.append(violation)
        return out

    def _check_using(
        self, sql: str, tree: exp.Expression, scope: _Scope, reported: Set[Tuple[str, ...]]
    ) -> List[RuleViolation]:
        """``JOIN ... USING (col)``: the name must be a column of both sides."""
        out: List[RuleViolation] = []
        for select in tree.find_all(exp.Select):
            sources = _select_sources(select)
            joins = select.args.get("joins") or []
            for position, join in enumerate(joins, start=1):
                for ident in join.args.get("using") or []:
                    name = _norm(ident.name)
                    left, right = sources[:position], sources[position]
                    missing: List[exp.Expression] = []
                    if _lacks(select, right, name, scope):
                        missing.append(right)
                    if left and all(_lacks(select, s, name, scope) for s in left):
                        missing.extend(left)
                    tables = sorted({t for s in missing for t in _star_tables(select, s)})
                    if tables:
                        violation = self._column_violation(sql, tables, name, reported)
                        if violation is not None:
                            out.append(violation)
        return out

    def _check_insert_columns(
        self, sql: str, tree: exp.Expression, scope: _Scope, reported: Set[Tuple[str, ...]]
    ) -> List[RuleViolation]:
        """``INSERT INTO <CDM table> (col, ...)``: every listed column must exist."""
        out: List[RuleViolation] = []
        for insert in tree.find_all(exp.Insert):
            target = insert.this
            if not isinstance(target, exp.Schema) or not isinstance(target.this, exp.Table):
                continue
            if scope.is_unknown_source(target.this):
                continue
            table = _norm(target.this.name)
            for ident in target.expressions:
                name = _norm(ident.name)
                if name and not _is_valid_column(table, name):
                    violation = self._column_violation(sql, [table], name, reported)
                    if violation is not None:
                        out.append(violation)
        return out


__all__ = ["ComprehensiveSchemaValidationRule"]

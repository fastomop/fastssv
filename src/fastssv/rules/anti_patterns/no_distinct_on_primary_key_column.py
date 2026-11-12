"""No DISTINCT on Primary Key Column Rule.

A primary key is unique within its table (CDM v5.4 field-level specification, isPrimaryKey). A SELECT over a single
CDM table, with no joins, that projects the table's primary key therefore returns unique rows, and DISTINCT removes
nothing (PostgreSQL: DISTINCT removes duplicate rows). The rule reports only that SELECT DISTINCT; a redundant
DISTINCT never changes the result, so the rule stays narrow rather than flag every equivalent spelling of it.

    SELECT DISTINCT condition_occurrence_id FROM condition_occurrence WHERE condition_concept_id = 201826;

The key may be projected as is, under a cast, or through ``*`` / ``t.*``, and the table may be read through CTEs and
derived tables that themselves read one source without joins or set operations. Once a join, a set operation or a
row generator enters, the key can repeat (``person JOIN condition_occurrence`` yields one row per condition), so
DISTINCT may be needed and nothing is reported. Session-local temporary tables (``#t``) are not taken for the CDM
table.
"""

import re
from typing import List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    _explicit_projection,
    _query_branches,
    _source_query,
    _star_qualifiers,
    normalize_name,
    parse_sql,
)
from fastssv.core.patch import remove as patch_remove
from fastssv.core.references import CDM_FIELD_LEVEL
from fastssv.core.registry import register
from fastssv.rules.anti_patterns._ap_cdm_fields import PRIMARY_KEYS
from fastssv.rules.anti_patterns.destructive_operations_on_clinical_tables import _is_temporary_name


def _single_source(select: exp.Select) -> Optional[exp.Expression]:
    if select.args.get("joins") or isinstance(select.parent, exp.SetOperation):
        return None
    from_node = select.args.get("from_") or select.args.get("from")
    return from_node.this if isinstance(from_node, exp.From) else None


def _key_table(select: exp.Select, name: str, depth: int = 0) -> Optional[str]:
    """The CDM table whose primary key column ``name`` of ``select`` is, when ``select`` (and every query it reads
    the column through) reads exactly one source with no join; None otherwise."""
    if depth > 8:
        return None
    src = _single_source(select)
    if src is None:
        return None
    query = _source_query(select, src)
    if query is None:
        if not isinstance(src, exp.Table) or not isinstance(src.this, exp.Identifier) or _is_temporary_name(src):
            return None
        table = normalize_name(src.name)
        return table if PRIMARY_KEYS.get(table) == name else None
    branches = _query_branches(query)
    if len(branches) != 1:
        return None
    branch = branches[0]
    if branch.args.get("group") is not None:
        return None
    proj = _explicit_projection(branch, name)
    if proj is not None:
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        while isinstance(inner, (exp.Cast, exp.TryCast, exp.Paren)):
            inner = inner.this
        return _key_table(branch, normalize_name(inner.name), depth + 1) if isinstance(inner, exp.Column) else None
    return _key_table(branch, name, depth + 1) if _star_qualifiers(branch) else None


def _projected_key(select: exp.Select, exprs: List[exp.Expression]) -> Optional[Tuple[str, str]]:
    """(key column as written, table) when one of ``exprs`` is the primary key of the SELECT's single source."""
    src = _single_source(select)
    if src is None:
        return None
    for expr in exprs:
        expr = expr.this if isinstance(expr, exp.Alias) else expr
        while isinstance(expr, (exp.Cast, exp.TryCast, exp.Paren)):
            expr = expr.this
        if isinstance(expr, exp.Star) or (isinstance(expr, exp.Column) and isinstance(expr.this, exp.Star)):
            table = _star_key(select, src)
            if table:
                return f"{expr.sql()} (includes {PRIMARY_KEYS[table]})", table
            continue
        if isinstance(expr, exp.Column):
            if expr.table and normalize_name(expr.table) != normalize_name(src.alias_or_name):
                continue
            table = _key_table(select, normalize_name(expr.name))
            if table:
                return expr.name, table
    return None


def _star_key(select: exp.Select, src: exp.Expression) -> Optional[str]:
    for table, key in PRIMARY_KEYS.items():
        if _key_table(select, key) == table:
            return table
    return None


_DISTINCT_KEYWORD = re.compile(r"\bdistinct\b\s*", re.IGNORECASE)


def _distinct_removal_patch(sql: str):
    """REMOVE patch for the DISTINCT keyword when the text has exactly one; otherwise None (FREEFORM)."""
    matches = list(_DISTINCT_KEYWORD.finditer(sql))
    if len(matches) != 1:
        return None
    return patch_remove((matches[0].start(), matches[0].end()))


@register
class NoDistinctOnPrimaryKeyColumnRule(Rule):
    """Redundant DISTINCT over a single table's primary key."""

    rule_id = "anti_patterns.no_distinct_on_primary_key_column"
    name = "No DISTINCT on Primary Key Column"

    description = (
        "Detects SELECT DISTINCT over the primary key of a single CDM table read without joins. Every row is "
        "already unique, so DISTINCT is redundant."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "REMOVE: DISTINCT. The SELECT reads one table without joins and includes its primary key, so its rows are "
        "already unique."
    )
    long_description = (
        "A primary key is unique within its table. A SELECT over that table alone, with no joins, that projects "
        "the primary key (as is, under a cast, or through * / t.*) therefore returns unique rows, and DISTINCT "
        "never removes one; it only adds a sort or hash step. The table may be read through CTEs and derived tables that themselves "
        "read one source without joins or set operations. The primary keys are those of the CDM v5.4 field-level "
        "specification. Queries that join other sources are not flagged: a join can repeat the key."
    )
    example_bad = (
        "SELECT DISTINCT condition_occurrence_id\nFROM condition_occurrence\nWHERE condition_concept_id = 201826;"
    )
    example_good = "SELECT condition_occurrence_id\nFROM condition_occurrence\nWHERE condition_concept_id = 201826;"
    references = (
        CDM_FIELD_LEVEL,
        Reference(
            "PostgreSQL documentation: SELECT, DISTINCT clause",
            "https://www.postgresql.org/docs/current/sql-select.html#SQL-DISTINCT",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees or []:
            if tree is None:
                continue
            for select in tree.find_all(exp.Select):
                found = self._check(sql, select)
                if found is not None:
                    violations.append(found)
        return violations

    def _check(self, sql: str, select: exp.Select) -> Optional[RuleViolation]:
        distinct = select.args.get("distinct")
        if isinstance(distinct, exp.Distinct) and not distinct.args.get("on"):
            key = _projected_key(select, list(select.expressions))
            if key:
                return self._violation(sql, key, "DISTINCT", _distinct_removal_patch(sql))
        return None

    def _violation(self, sql: str, key: Tuple[str, str], form: str, patch) -> RuleViolation:
        column, table = key
        fix = f"REMOVE: DISTINCT; '{column}' is the primary key of {table}."
        return self.create_violation(
            message=(
                f"Redundant {form} on primary key column '{column}'. A primary key is unique, and this SELECT reads "
                f"only `{table}` without joins, so no row can repeat."
            ),
            suggested_fix=fix,
            suggested_fix_patch=patch,
            details={"column": column, "table": table, "form": form},
        )


__all__ = ["NoDistinctOnPrimaryKeyColumnRule"]

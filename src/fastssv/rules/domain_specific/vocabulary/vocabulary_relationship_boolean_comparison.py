"""Relationship Flag Comparison Rule.

relationship.is_hierarchical and relationship.defines_ancestry are varchar(1) columns holding '1' or '0'
(OMOP CDM v5.4). Despite their yes/no meaning they are strings, not booleans:

* any other string ('true', 'Y', 'yes', '') or integer (2, -1) never occurs, in every dialect: ``=`` / ``IN``
  against it match no rows, ``<>`` / ``NOT IN`` filter nothing;
* in PostgreSQL and BigQuery an integer (``= 1``) or boolean (``= TRUE``) operand, or using the column itself as a
  condition (``WHERE is_hierarchical``), is a type error: neither casts between a string column and an integer or
  boolean implicitly. Engines that coerce implicitly (SQL Server, ...) accept ``= 1``.

Correct:
    SELECT relationship_id FROM relationship WHERE is_hierarchical = '1'
    SELECT relationship_id FROM relationship WHERE defines_ancestry IN ('0', '1')

Violations:
    WHERE is_hierarchical = 'true'      -- no row holds 'true'
    WHERE defines_ancestry = 2          -- no such value
    WHERE is_hierarchical = 1           -- PostgreSQL: operator does not exist: character varying = integer
    WHERE is_hierarchical               -- PostgreSQL: argument of WHERE must be type boolean
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    _explicit_projection,
    _pick_source,
    _query_branches,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    column_origins,
    extract_aliases,
    normalize_name,
    parse_sql,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.references import cdm_table
from fastssv.core.registry import register

RELATIONSHIP_TABLE = "relationship"
FLAG_COLUMNS = {"is_hierarchical", "defines_ancestry"}
VALID_VALUES = {"0", "1"}
# Engines without an implicit cast between a string column and an integer / boolean operand.
_STRICT_TYPING_DIALECTS = {"postgres": "PostgreSQL", "postgresql": "PostgreSQL", "bigquery": "BigQuery"}

_TRUE_STRINGS = {"y", "yes", "true", "t"}
_FALSE_STRINGS = {"n", "no", "false", "f"}


def _cast_on_the_way(col: exp.Column, depth: int = 0) -> bool:
    """True when a CTE / derived table between ``col`` and the base table projects the flag through a cast
    (``is_hierarchical::int AS is_hierarchical``): the value reaching ``col`` is no longer varchar."""
    if depth > 12:
        return False
    select = col.find_ancestor(exp.Select)
    if select is None:
        return False
    name = normalize_name(col.name)
    if col.table:
        scope, src = _source_by_qualifier(select, normalize_name(col.table))
    else:
        scope, src = select, _pick_source(select, _select_sources(select), name, depth)
    if scope is None or src is None:
        return False
    query = _source_query(scope, src)
    if query is None:
        return False
    for branch in _query_branches(query):
        proj = _explicit_projection(branch, name)
        if proj is None:
            continue
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        while isinstance(expr, exp.Paren):
            expr = expr.this
        if isinstance(expr, (exp.Cast, exp.TryCast)):
            return True
        if isinstance(expr, exp.Column) and _cast_on_the_way(expr, depth + 1):
            return True
    return False


def _is_flag_column(col: exp.Column, aliases: Dict[str, str], cte_names: Set[str], tables: Set[str]) -> bool:
    name = normalize_name(col.name)
    if name not in FLAG_COLUMNS:
        return False
    if _cast_on_the_way(col):
        return False
    origins = column_origins(col)
    if origins is not None:
        return (RELATIONSHIP_TABLE, name) in origins
    if col.table:
        qualifier = normalize_name(col.table)
        if qualifier in cte_names:
            return False
        return normalize_name(aliases.get(qualifier, qualifier)) == RELATIONSHIP_TABLE
    return tables == {RELATIONSHIP_TABLE}


def _canonical(value: str) -> Optional[str]:
    norm = value.strip().lower()
    if norm in _TRUE_STRINGS:
        return "1"
    if norm in _FALSE_STRINGS:
        return "0"
    return None


def _impossible(col_name: str, shown: str, op: str) -> str:
    """Message for a value the flag never holds, worded for what the operator then does."""
    head = f"relationship.{col_name} holds only the strings '1' and '0'; {shown} never occurs, so "
    if op in ("EQ", "IN"):
        return head + "the comparison matches no rows. Use '1' (true) or '0' (false)."
    if op in ("NEQ", "NOT_IN"):
        return head + "the comparison is true for every row and filters nothing. Use '1' (true) or '0' (false)."
    return head + "an ordering comparison against it is meaningless. Use = '1' (true) or = '0' (false)."


def _judge(col_name: str, value: exp.Expression, typing_dialect: Optional[str], op: str) -> Optional[str]:
    """Message when ``value`` cannot correctly be compared with the varchar(1) flag, else None. ``typing_dialect``
    names the engine that rejects varchar-vs-integer/boolean comparisons, or is None where they coerce."""
    if isinstance(value, exp.Literal) and value.is_string:
        if value.this in VALID_VALUES:
            return None
        return _impossible(col_name, f"the string '{value.this}'", op)
    if isinstance(value, exp.Literal):
        if value.this in VALID_VALUES:
            if typing_dialect is None:
                return None
            engine_error = (
                "operator does not exist: character varying = integer"
                if typing_dialect == "PostgreSQL"
                else "no implicit cast between a string column and an integer"
            )
            return (
                f"relationship.{col_name} is varchar(1); comparing it with the integer {value.this} is a type error "
                f"in {typing_dialect} ({engine_error}). Use '{value.this}'."
            )
        return _impossible(col_name, f"{value.this} (an invalid value)", op)
    if isinstance(value, exp.Neg) and isinstance(value.this, exp.Literal):
        return _impossible(col_name, f"{value.sql()} (an invalid value)", op)
    if isinstance(value, exp.Boolean) and typing_dialect is not None:
        return (
            f"relationship.{col_name} is varchar(1), not boolean; comparing it with {value.sql()} is a type error in "
            f"{typing_dialect}. Use '{'1' if value.this else '0'}'."
        )
    return None


def _like_matches(pattern: str, value: str, case_insensitive: bool) -> bool:
    """SQL LIKE semantics (``%`` any run, ``_`` one character, default backslash escape)."""
    regex, i = "", 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            regex += re.escape(pattern[i + 1])
            i += 2
            continue
        regex += ".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
        i += 1
    return re.fullmatch(regex, value, re.IGNORECASE if case_insensitive else 0) is not None


def _used_as_condition(col: exp.Column) -> bool:
    parent = col.parent
    if isinstance(parent, (exp.Where, exp.Having, exp.And, exp.Or)):
        return True
    if isinstance(parent, exp.Not):
        return True
    if isinstance(parent, exp.Join):
        return parent.args.get("on") is col
    return False


def _find_violations(
    tree: exp.Expression, strict_typing: Optional[str]
) -> List[Tuple[str, Optional[exp.Expression], Optional[str]]]:
    """(message, offending literal, replacement text) per violation."""
    aliases = extract_aliases(tree)
    cte_names = {normalize_name(c.alias_or_name) for c in tree.find_all(exp.CTE) if c.alias_or_name}
    tables = {normalize_name(t.name) for t in tree.find_all(exp.Table) if normalize_name(t.name) not in cte_names}
    out: List[Tuple[str, Optional[exp.Expression], Optional[str]]] = []

    def flag_column(node: Optional[exp.Expression]) -> Optional[exp.Column]:
        # LOWER / UPPER / TRIM keep a varchar a varchar: the value set stays '0' / '1'.
        while isinstance(node, (exp.Lower, exp.Upper, exp.Trim, exp.Paren)):
            node = node.this
        if isinstance(node, exp.Column) and _is_flag_column(node, aliases, cte_names, tables):
            return node
        return None

    def flag(col: exp.Expression) -> bool:
        return flag_column(col) is not None

    for comp in tree.find_all(exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE):
        for side, value in ((comp.this, comp.expression), (comp.expression, comp.this)):
            col = flag_column(side)
            if col is None:
                continue
            message = _judge(normalize_name(col.name), value, strict_typing, type(comp).__name__.upper())
            if message:
                replacement = None
                if isinstance(value, exp.Literal) and value.is_string:
                    replacement = _canonical(value.this)
                elif isinstance(value, exp.Literal) and value.this in VALID_VALUES:
                    replacement = value.this
                elif isinstance(value, exp.Boolean):
                    replacement = "1" if value.this else "0"
                out.append((message, value, f"'{replacement}'" if replacement else None))

    for match in tree.find_all(exp.Like, exp.ILike):
        col = flag_column(match.this)
        pattern = match.expression
        if col is None or not (isinstance(pattern, exp.Literal) and pattern.is_string):
            continue
        if not any(_like_matches(pattern.this, v, isinstance(match, exp.ILike)) for v in VALID_VALUES):
            name = normalize_name(col.name)
            out.append((_impossible(name, f"a value matching '{pattern.this}'", "EQ"), None, None))

    for in_expr in tree.find_all(exp.In):
        col = flag_column(in_expr.this)
        if col is None:
            continue
        name = normalize_name(col.name)
        op = "NOT_IN" if isinstance(in_expr.parent, exp.Not) else "IN"
        messages = [m for m in (_judge(name, v, strict_typing, op) for v in in_expr.expressions or []) if m]
        if messages:
            values = ", ".join(v.sql() for v in in_expr.expressions or [])
            out.append((f"IN list ({values}) has invalid values: " + " ".join(dict.fromkeys(messages)), None, None))

    if strict_typing:
        for is_node in tree.find_all(exp.Is):
            col = flag_column(is_node.this)
            if col is not None and isinstance(is_node.expression, exp.Boolean):
                name = normalize_name(col.name)
                out.append(
                    (
                        f"relationship.{name} is varchar(1), not boolean; `IS {is_node.expression.sql()}` is a type "
                        f"error in {strict_typing}. Write `{name} = '{'1' if is_node.expression.this else '0'}'`.",
                        None,
                        None,
                    )
                )
        for col in tree.find_all(exp.Column):
            if flag(col) and _used_as_condition(col):
                name = normalize_name(col.name)
                out.append(
                    (
                        f"relationship.{name} is varchar(1), not boolean; using it directly as a condition is a type "
                        f"error in {strict_typing} (the condition must be boolean). Write `{name} = '1'`.",
                        None,
                        None,
                    )
                )
    return out


@register
class RelationshipBooleanComparisonRule(Rule):
    """Validate comparisons on relationship.is_hierarchical / defines_ancestry (varchar(1) '1'/'0')."""

    rule_id = "domain_specific.vocabulary_relationship_boolean_comparison"
    name = "Relationship Flag Comparison"
    description = (
        "relationship.is_hierarchical and defines_ancestry are varchar(1) holding '1' or '0'. Other values never "
        "occur; in PostgreSQL and BigQuery integer or boolean operands, or using the column as a condition, are type "
        "errors."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the comparison value WITH the string '1' (true) or '0' (false), e.g. `is_hierarchical = '1'`."
    )
    long_description = (
        "In OMOP CDM v5.4 relationship.is_hierarchical and relationship.defines_ancestry are varchar(1) columns "
        "whose values are '1' and '0'. 'true', 'Y', 'yes' or 2 never occur: `=` against them matches no row and "
        "`<>` filters nothing. PostgreSQL and BigQuery also refuse to compare a string column with an integer or "
        "boolean, so `is_hierarchical = 1`, `= TRUE` and a bare `WHERE is_hierarchical` fail there; engines with "
        "implicit coercion accept `= 1`. Comparing with the strings '1' / '0' works everywhere."
    )
    example_bad = "SELECT relationship_id FROM relationship\nWHERE is_hierarchical = 'yes';"
    example_good = "SELECT relationship_id FROM relationship\nWHERE is_hierarchical = '1';"
    extra_examples = (
        RuleExample(
            sql="SELECT relationship_id FROM relationship\nWHERE defines_ancestry = 1;",
            severity=Severity.ERROR,
            note="integer operand: a type error in PostgreSQL and BigQuery",
        ),
        RuleExample(
            sql="SELECT relationship_id FROM relationship\nWHERE is_hierarchical;",
            severity=Severity.ERROR,
            note="the varchar flag used directly as a boolean condition",
        ),
    )
    references = (
        cdm_table("relationship"),
        Reference(
            "PostgreSQL 8.3 release notes: non-character types are no longer cast to text implicitly",
            "https://www.postgresql.org/docs/8.3/release-8-3.html",
        ),
        Reference(
            "PostgreSQL: SELECT, WHERE clause",
            "https://www.postgresql.org/docs/current/sql-select.html#SQL-WHERE",
        ),
        Reference(
            "BigQuery: Conversion rules, comparison of casting and coercion",
            "https://cloud.google.com/bigquery/docs/reference/standard-sql/conversion_rules#comparison_chart",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []
        lowered = sql.lower()
        if RELATIONSHIP_TABLE not in lowered or not any(col in lowered for col in FLAG_COLUMNS):
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        strict_typing = _STRICT_TYPING_DIALECTS.get(normalize_name(dialect or "postgres"))
        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees:
            if not tree:
                continue
            for message, value_node, replacement in _find_violations(tree, strict_typing):
                if message in seen:
                    continue
                seen.add(message)
                patch = None
                if value_node is not None and replacement is not None:
                    span = locate(sql, value_node.sql())
                    if span is not None:
                        patch = patch_replace(span, replacement)
                violations.append(self.create_violation(message=message, suggested_fix_patch=patch))
        return violations


__all__ = ["RelationshipBooleanComparisonRule"]

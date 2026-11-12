"""No String Identification Rule.

``*_source_value`` columns keep the verbatim text of the source data. The CDM conventions say they are "generally
not to be used by any standardized analytics" and are "provided for convenience and quality assurance (QA)
purposes": the values are not harmonised, so a string filter on one selects whatever a single site happened to
record. The rule warns when a query compares such a column with a string literal while the same table offers a
concept column for the same fact.

Reported: ``=``, ``<>``, ``IN`` / ``NOT IN``, ``BETWEEN``, ``LIKE`` / ``ILIKE`` / ``SIMILAR TO`` / regex matches
against string literals, with the column read directly or through ``LOWER`` / ``UPPER`` / ``TRIM`` / ``LEFT`` /
``SUBSTRING`` / casts, and followed through CTEs, derived tables and renaming aliases.

Not reported:
- source-value columns with no concept counterpart in their table (``person_source_value``,
  ``provider_source_value``, ``care_site_source_value``, ``location_source_value`` ...): the string is the only
  way to use them;
- blank-literal checks (``= ''``, ``<> ''``), which name no value;
- a SELECT that also pins the paired concept column to 0: the source value is then the only information the
  record carries, which is the QA use the conventions describe;
- columns of tables outside the CDM.

The finding is a WARNING: the sources discourage the practice, they do not make it an error.
"""

from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    _pick_source,
    _select_sources,
    _source_query,
    extract_aliases,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_column_source,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.references import BOOK_CDM, CDM_CONVENTIONS, section
from fastssv.core.registry import register
from fastssv.rules.anti_patterns._ap_cdm_fields import SOURCE_VALUE_PAIRS

RULE_ID = "anti_patterns.no_string_identification"

_WRAPPERS = (exp.Cast, exp.TryCast, exp.Paren, exp.Lower, exp.Upper, exp.Trim, exp.Left, exp.Right, exp.Substring)
_TRIM_FUNCS = {"btrim", "ltrim", "rtrim"}
_PATTERN_OPS = {
    exp.Like: "LIKE",
    exp.ILike: "ILIKE",
    exp.SimilarTo: "SIMILAR TO",
    exp.RegexpLike: "~",
    exp.RegexpILike: "~*",
}


def _peel(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    """The column or literal under casts, parentheses and case / trim / substring functions."""
    while node is not None:
        if isinstance(node, _WRAPPERS):
            node = node.this
        elif isinstance(node, exp.Anonymous) and node.name.lower() in _TRIM_FUNCS and node.expressions:
            node = node.expressions[0]
        else:
            break
    return node


def _names_a_value(node: Optional[exp.Expression]) -> bool:
    node = _peel(node)
    return node is not None and is_string_literal(node) and bool(str(node.this).strip().strip("%"))


def _source_value_pair(node: exp.Expression, aliases: Dict[str, str]) -> Optional[Tuple[str, str, Tuple]]:
    """(table, column, (concept column, source concept column)) when ``node`` reads a CDM source-value column that
    has a concept counterpart."""
    col = _peel(node)
    if not isinstance(col, exp.Column):
        return None
    table, column = resolve_column_source(col, aliases)
    key = (normalize_name(table or ""), normalize_name(column or ""))
    pair = SOURCE_VALUE_PAIRS.get(key)
    return (key[0], key[1], pair) if pair else None


def _reads_base_table(col: exp.Column, table: str, column: str) -> bool:
    """Whether ``col`` is spelled as the base column and its SELECT reads ``table`` itself (not a CTE or derived
    table), so the paired concept column can be written in the same place."""
    select = col.find_ancestor(exp.Select)
    if select is None or normalize_name(col.name) != column:
        return False
    sources = _select_sources(select)
    if col.table:
        matches = [s for s in sources if normalize_name(s.alias_or_name) == normalize_name(col.table)]
        src = matches[0] if len(matches) == 1 else None
    else:
        src = _pick_source(select, sources, column, 0)
    return isinstance(src, exp.Table) and _source_query(select, src) is None and normalize_name(src.name) == table


def _pins_concept_to_zero(col: exp.Column, table: str, concept_col: Optional[str], aliases: Dict[str, str]) -> bool:
    """Whether the SELECT that owns ``col`` filters the paired concept column of the same table to 0."""
    select = col.find_ancestor(exp.Select)
    if select is None or concept_col is None:
        return False
    for eq in select.find_all(exp.EQ):
        if eq.find_ancestor(exp.Select) is not select:
            continue
        for side, other in ((eq.left, eq.right), (eq.right, eq.left)):
            side = _peel(side)
            other = _peel(other)
            if not isinstance(side, exp.Column) or not isinstance(other, exp.Literal) or other.is_string:
                continue
            if str(other.this) == "0" and resolve_column_source(side, aliases) == (table, concept_col):
                return True
    return False


@register
class NoStringIdentificationRule(Rule):
    """String filter on a *_source_value column that has a concept counterpart."""

    rule_id = RULE_ID
    name = "No String Identification"
    description = (
        "Warns when a *_source_value column is compared with a string literal although its table has a concept "
        "column for the same fact. Source values are verbatim source text and are not harmonised across data "
        "sources."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the string filter on `<x>_source_value` WITH a filter on the paired `<x>_concept_id` (standard "
        "concept), or on `<x>_source_concept_id` when the analysis is specific to the source vocabulary."
    )
    long_description = (
        "`*_source_value` columns store the verbatim code or text of the source data. The CDM conventions say "
        "they are generally not to be used by standardized analytics and are provided for convenience and "
        "quality assurance: the values are not harmonised, so the same string filter selects different records "
        "at different sites. The rule warns when such a column is compared with a string literal and the table "
        "has a concept column for the same fact. It stays silent for source values that have no concept "
        "counterpart (person_source_value, provider_source_value, care_site_source_value, "
        "location_source_value), for blank-literal checks, and when the same SELECT filters the paired concept "
        "column to 0, where the source value is the only information the record carries."
    )
    example_bad = "SELECT person_id\nFROM condition_occurrence\nWHERE condition_source_value LIKE '%diabetes%';"
    example_good = "SELECT person_id\nFROM condition_occurrence\nWHERE condition_concept_id = 201820;"
    references = (
        section(
            CDM_CONVENTIONS,
            "source-values-source-concept-ids-and-standard-concept-ids",
            "Source values, source concept ids and standard concept ids",
        ),
        section(BOOK_CDM, "general-naming-conventions-of-fields", "General naming conventions of fields"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "_source_value" not in sql.lower():
            # A CTE can rename the column, but the base column is still spelled out somewhere.
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            aliases = extract_aliases(tree)
            for node in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.Between, *_PATTERN_OPS):
                violation = self._check(sql, node, aliases)
                if violation is not None:
                    violations.append(violation)
        return violations

    def _check(self, sql: str, node: exp.Expression, aliases: Dict[str, str]) -> Optional[RuleViolation]:
        column_side = node.this
        if isinstance(node, (exp.EQ, exp.NEQ)):
            literals = [node.expression]
            if _source_value_pair(column_side, aliases) is None:
                column_side, literals = node.expression, [node.this]
            op = "=" if isinstance(node, exp.EQ) else "<>"
        elif isinstance(node, exp.In):
            literals = list(node.expressions or [])
            op = "NOT IN" if isinstance(node.parent, exp.Not) else "IN"
        elif isinstance(node, exp.Between):
            literals = [node.args.get("low"), node.args.get("high")]
            op = "BETWEEN"
        else:
            literals = [node.expression]
            op = ("NOT " if isinstance(node.parent, exp.Not) else "") + _PATTERN_OPS[type(node)]

        found = _source_value_pair(column_side, aliases)
        if found is None or not any(_names_a_value(lit) for lit in literals):
            return None
        table, column, (concept_col, source_concept_col) = found
        col = _peel(column_side)
        if _pins_concept_to_zero(col, table, concept_col, aliases):
            return None

        targets = " or ".join(f"`{c}`" for c in (concept_col, source_concept_col) if c)
        shown = ", ".join(lit.sql() for lit in literals[:3] if lit is not None) + (", ..." if len(literals) > 3 else "")
        patch = None
        if (
            concept_col
            and op in ("=", "LIKE", "ILIKE")
            and col is column_side
            and _reads_base_table(col, table, column)
        ):
            span = locate(sql, node.sql())
            if span is not None:
                qualifier = f"{col.table}." if col.table else ""
                patch = patch_replace(span, f"{qualifier}{concept_col} = <expected_concept_id>")
        return self.create_violation(
            message=(
                f"String filter on source value: {column_side.sql()} {op} {shown}. `{table}.{column}` holds verbatim "
                f"source text that is not harmonised across data sources, so the filter selects whatever one site "
                f"recorded. Filter on {targets} instead."
            ),
            suggested_fix=(
                f"REPLACE: the `{column}` string filter WITH a filter on {targets} "
                "(the standard concept for analytics; the source concept when the source vocabulary matters)."
            ),
            suggested_fix_patch=patch,
            details={"column": f"{table}.{column}", "operation": op, "concept_column": concept_col},
        )


__all__ = ["NoStringIdentificationRule"]

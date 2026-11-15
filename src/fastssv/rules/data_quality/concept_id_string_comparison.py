"""Concept ID String Comparison Rule.

Every column that holds a concept_id is an INTEGER in CDM v5.4. Comparing one with a quoted literal that is not an
integer (``gender_concept_id = 'FEMALE'``, ``= ''``) compares the column with a value its CDM type cannot hold: an
ERROR on every dialect. A quoted integer (``gender_concept_id = '8507'``, ``IN ('201826', ...)``) names a valid
concept id but as text; it relies on the engine coercing the literal, which BigQuery does not do ("String literals
don't coerce to numeric types"), so it is a WARNING. An integer literal is
``column_type_validation.is_integer_input``: optional surrounding whitespace, an optional sign, decimal digits.

Checked columns: CONCEPT.concept_id and every CDM v5.4 column with a foreign key to CONCEPT (generated from the
field-level CSV), when the column's lineage is that CDM column; a value recast in a CTE (``CAST(gender_concept_id AS
VARCHAR) AS gender_concept_id``) or a column of a non-CDM table is not. Unrendered SqlRender parameters
(``'@gender_concept_ids'``) are skipped: their rendered value is unknown.

Comparisons: ``=``, ``<>``, ``<``, ``<=``, ``>``, ``>=``, ``IN`` / ``NOT IN`` and ``BETWEEN``, in any clause.
"""

import re
from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    DERIVED,
    _explicit_projection,
    _pick_source,
    _query_branches,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    column_origins,
    is_string_literal,
    normalize_name,
    parse_sql,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.references import CDM_FIELD_LEVEL, cdm_table
from fastssv.core.registry import register

from ._dq_concept_columns import CONCEPT_ID_COLUMNS
from .column_type_validation import is_integer_input  # one integer literal syntax for both rules

OPERATORS = {exp.EQ: "=", exp.NEQ: "<>", exp.LT: "<", exp.LTE: "<=", exp.GT: ">", exp.GTE: ">="}
_PLACEHOLDER_RE = re.compile(r"@\w+")
_INTEGER_TYPES = {exp.DataType.Type.INT, exp.DataType.Type.BIGINT, exp.DataType.Type.SMALLINT}


def _recast_to_non_integer(col: exp.Column, depth: int) -> bool:
    """Whether a CTE / derived-table projection on ``col``'s lineage casts it to a non-integer type.

    ``column_origins`` looks through casts, so the cast has to be checked separately."""
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
            if expr.to.this not in _INTEGER_TYPES:
                return True
            expr = expr.this
        if isinstance(expr, exp.Column) and _recast_to_non_integer(expr, depth + 1):
            return True
    return False


def _concept_id_column(node: Optional[exp.Expression]) -> Optional[exp.Column]:
    """``node`` when it reads, unchanged, a CDM column that holds a concept_id."""
    if not isinstance(node, exp.Column):
        return None
    origins = column_origins(node)
    if not origins or any(t == DERIVED or (t, c) not in CONCEPT_ID_COLUMNS for t, c in origins):
        return None
    return None if _recast_to_non_integer(node, 0) else node


def _quoted(node: Optional[exp.Expression]) -> Optional[str]:
    if node is None or not is_string_literal(node):
        return None
    value = str(node.this or "")
    return None if _PLACEHOLDER_RE.fullmatch(value) else value


def _comparisons(tree: exp.Expression) -> List[Tuple[exp.Expression, List[exp.Expression], str]]:
    out: List[Tuple[exp.Expression, List[exp.Expression], str]] = []
    for node in tree.find_all(*OPERATORS, exp.In, exp.Between):
        if isinstance(node, exp.In):
            op = "NOT IN" if isinstance(node.parent, exp.Not) else "IN"
            out.append((node.this, list(node.expressions or []), op))
        elif isinstance(node, exp.Between):
            out.append(
                (node.this, [b for b in (node.args.get("low"), node.args.get("high")) if b is not None], "BETWEEN")
            )
        else:
            out.append((node.this, [node.expression], OPERATORS[type(node)]))
            out.append((node.expression, [node.this], OPERATORS[type(node)]))
    return out


@register
class ConceptIdStringComparisonRule(Rule):
    """Integer concept_id columns compared with quoted literals."""

    rule_id = "data_quality.concept_id_string_comparison"
    name = "Concept ID String Comparison"

    description = (
        "Concept id columns are INTEGER in the CDM. A quoted value that is not an integer ('FEMALE') is not a "
        "concept id (ERROR); a quoted integer ('8532') relies on implicit coercion, which BigQuery does not do "
        "(WARNING)."
    )
    severity = Severity.WARNING
    suggested_fix = "REPLACE: `<col>_concept_id = '<digits>'` (string literal) WITH `<col>_concept_id = <digits>` (integer literal). Concept id columns are INTEGER."
    long_description = (
        "Every column that holds a concept_id is an INTEGER in CDM v5.4. A quoted literal that is not an integer "
        "('FEMALE', '', '1.5') is not a value of that type, so the comparison is an ERROR on every dialect; an "
        "integer literal is optional whitespace, an optional sign and decimal digits. A quoted integer ('8532') "
        "names a concept id as text and depends on the engine coercing it: BigQuery does not coerce string "
        "literals to numbers, so it is a portability WARNING. Write the value as an integer literal; to find a "
        "concept by name, join concept."
    )
    example_bad = "SELECT person_id\nFROM person\nWHERE gender_concept_id = '8532';"
    example_good = "SELECT person_id\nFROM person\nWHERE gender_concept_id = 8532;"
    extra_examples = (
        RuleExample(
            sql="SELECT person_id\nFROM person\nWHERE gender_concept_id = 'FEMALE';",
            severity=Severity.ERROR,
            note="Not an integer, so not a concept id: the concept id for FEMALE is 8532.",
        ),
    )
    references = (
        cdm_table("person"),
        Reference(
            "BigQuery documentation: Conversion rules, Literal coercion",
            "https://cloud.google.com/bigquery/docs/reference/standard-sql/conversion_rules#literal_coercion",
        ),
        cdm_table("concept"),
        CDM_FIELD_LEVEL,
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql or "concept_id" not in sql.lower():
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            seen: Set[Tuple[str, str]] = set()
            for col_side, values, op in _comparisons(tree):
                column = _concept_id_column(col_side)
                if column is None:
                    continue
                quoted = [v for v in (_quoted(n) for n in values) if v is not None]
                if not quoted:
                    continue
                key = (column.sql(), op + "|".join(quoted))
                if key in seen:
                    continue
                seen.add(key)
                violations.append(self._violation(sql, column, op, quoted))
        return violations

    def _violation(self, sql: str, column: exp.Column, op: str, quoted: List[str]) -> RuleViolation:
        col = column.sql()
        non_integer = [v for v in quoted if not is_integer_input(v)]
        shown = ", ".join(f"'{v}'" for v in quoted[:3]) + (", ..." if len(quoted) > 3 else "")
        if non_integer:
            severity = Severity.ERROR
            message = (
                f"Concept id column compared with a value that is not an integer: {col} {op} {shown}. The CDM "
                f"defines {col} as INTEGER, and '{non_integer[0]}' is not a concept id."
            )
        else:
            severity = Severity.WARNING
            message = (
                f"Concept ID column compared with string literal: {col} {op} {shown}. {col} is INTEGER; the "
                f"comparison relies on coercing the literal, which BigQuery does not do."
            )
        patch = None
        if not non_integer and len(quoted) == 1:
            for q in ("'", '"'):
                span = locate(sql, f"{q}{quoted[0]}{q}")
                if span is not None:
                    patch = patch_replace(span, quoted[0].strip())
                    break
        replacement = ", ".join(
            v.strip() if v in quoted and v not in non_integer else "<integer_value>" for v in quoted[:3]
        )
        return self.create_violation(
            message=message,
            severity=severity,
            suggested_fix=(
                f"REPLACE: the quoted value(s) WITH integer literal(s): `{col} {op} {replacement}`. Concept id columns "
                f"are INTEGER; to select a concept by name, join concept."
            ),
            suggested_fix_patch=patch,
            details={"column": column.name, "operator": op, "string_count": len(quoted)},
        )


__all__ = ["ConceptIdStringComparisonRule"]

"""Column Type Validation Rule.

Flags comparisons between a CDM column and a literal, or between two CDM columns, whose types the OMOP CDM v5.4
field-level specification defines as incompatible. The verdict rests on the CDM's declared column types, so it is the
same on every SQL dialect: whether a given engine would cast its way past the mismatch is not the question.

ERROR, a value that is not of the column's CDM type:
    * varchar column against a numeric or boolean literal (``domain_id = 19``, ``value_as_string > 100``,
      ``standard_concept = TRUE``);
    * date / datetime column against a numeric literal, or against a quoted word that is not a date
      (``death_date = 'unknown'``);
    * integer column against a quoted literal that is not an integer (``year_of_birth = 'unknown'``,
      ``year_of_birth = '1980.5'``), float column against a quoted literal that is not a number. An integer literal is
      ``is_integer_input``: optional surrounding whitespace, an optional sign, decimal digits;
    * column against column across type families (``person_id = person_source_value``,
      ``measurement_event_id = specimen_source_id``).

WARNING: a result value stored as text (``value_as_string``) ordered against a quoted number
(``value_as_string > '100'``): the types agree, but the comparison orders text, so '9' > '100'.

Not flagged:
    * quoted integers against integer columns and quoted dates against date columns (``year_of_birth = '1980'``);
    * any quoted literal against a ``*_concept_id`` column (data_quality.concept_id_string_comparison);
    * relationship.is_hierarchical and relationship.defines_ancestry
      (domain_specific.vocabulary_relationship_boolean_comparison);
    * other varchar columns ordered against quoted text (code ranges such as ``concept_code BETWEEN '354.0' AND
      '355.99'`` are ordered as text on purpose; note_nlp.offset has data_quality.note_nlp_offset_is_character_position);
    * unrendered template parameters (``'@start_date'``), explicit casts on either side, columns whose lineage is
      computed or unknown, and T-SQL ``#temp`` tables.

Comparisons are ``=``, ``<>``, ``<``, ``<=``, ``>``, ``>=``, ``IN (...)``, ``IN (SELECT ...)`` and ``BETWEEN``,
wherever they appear (WHERE, JOIN ... ON, HAVING, CASE, the select list). Columns are resolved through lineage.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.deduplication import predicate_key
from fastssv.core.references import CDM_FIELD_LEVEL, cdm_table
from fastssv.core.helpers import (
    DERIVED,
    _explicit_projection,
    _pick_source,
    _query_branches,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_column_source,
)
from fastssv.core.registry import register
from fastssv.schemas import (
    CDM_COLUMN_TYPES,
    DATE,
    DATETIME,
    FLOAT,
    INTEGER,
    VARCHAR,
    are_types_compatible,
    get_column_type,
)

_COMPARISONS = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)
_ORDERING = (exp.LT, exp.LTE, exp.GT, exp.GTE)
_NUMERIC_TYPES = {INTEGER, FLOAT}
_DATE_TYPES = {DATE, DATETIME}

_INTEGER_RE = re.compile(r"\s*[+-]?\d+\s*")


def is_integer_input(text: str) -> bool:
    """Whether quoted ``text`` is an integer literal: optional surrounding whitespace, optional sign, decimal digits."""
    return _INTEGER_RE.fullmatch(text) is not None


_FLOAT_RE = re.compile(r"\s*[+-]?((\d[\d_]*)?\.?\d[\d_]*([eE][+-]?\d+)?|\d[\d_]*\.|nan|inf|infinity)\s*", re.IGNORECASE)
_PLAIN_NUMBER_RE = re.compile(r"\s*[+-]?\d+(\.\d+)?\s*")
# Rendered later by SqlRender, Jinja, a driver or psql: the compared value is unknown.
_TEMPLATE_RE = re.compile(r"[@{}$%:?<>\[\]]")
_WORDS_RE = re.compile(r"[A-Za-z ]*")
# PostgreSQL special date/time input strings (Table "Special Date/Time Inputs").
_SPECIAL_DATES = {"epoch", "infinity", "now", "today", "tomorrow", "yesterday", "allballs"}

# Where the specification keeps the value a mistyped comparison was after.
_HINTS: Dict[Tuple[str, str], str] = {
    ("observation", "value_as_string"): (
        "Numeric results are in observation.value_as_number; value_as_string holds text. A bare CAST of "
        "value_as_string fails on the first non-numeric row, so guard it (TRY_CAST, or a regex test) if it is needed."
    ),
    ("specimen", "specimen_source_id"): (
        "specimen_source_id is the source system's identifier (varchar); the CDM key of a specimen is "
        "specimen.specimen_id."
    ),
    ("person", "person_source_value"): "The CDM key of a person is person.person_id.",
}

# Columns the specification defines as a result value stored as text (value_as_string): their content is compared
# as a value, so ordering it against a number orders text. Codes (concept_code) are ordered as text on purpose.
_TEXT_VALUE_COLUMNS = frozenset(
    (t, "value_as_string") for t, cols in CDM_COLUMN_TYPES.items() if "value_as_string" in cols
)

# '0' / '1' flags: domain_specific.vocabulary_relationship_boolean_comparison judges them with their value set.
_SKIPPED_COLUMNS = frozenset({("relationship", "is_hierarchical"), ("relationship", "defines_ancestry")})


def _operand(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _literal(node: Optional[exp.Expression]) -> Optional[Tuple[str, str]]:
    """("string" | "integer" | "float" | "boolean", text) for a literal operand, signed numbers included."""
    node = _operand(node)
    if isinstance(node, exp.Boolean):
        return "boolean", "TRUE" if node.this else "FALSE"
    sign = ""
    if isinstance(node, exp.Neg):
        node, sign = _operand(node.this), "-"
        if not isinstance(node, exp.Literal) or node.is_string:
            return None
    if not isinstance(node, exp.Literal):
        return None
    if node.is_string:
        return "string", str(node.this)
    return ("integer" if node.is_int else "float"), sign + str(node.this)


def _is_concept_id(column: str) -> bool:
    return column == "concept_id" or column.endswith("_concept_id")


def _hint(table: str, column: str) -> str:
    return f" {_HINTS[(table, column)]}" if (table, column) in _HINTS else ""


def _literal_problem(
    table: str, column: str, col_type: str, kind: str, text: str, ordering: bool
) -> Optional[Tuple[str, str]]:
    """(class, explanation) when comparing ``table.column`` with this literal is wrong, else None.

    Classes: "varchar_number", "date_number", "number_text", "date_text", "text_order"."""
    if kind == "boolean":
        if col_type != VARCHAR:
            return None
        return (
            "varchar_number",
            f"The CDM defines {table}.{column} as varchar, so a boolean is not one of its values. Quote the value the "
            f"column holds.{_hint(table, column)}",
        )
    if kind != "string":
        if col_type == VARCHAR:
            return (
                "varchar_number",
                f"The CDM defines {table}.{column} as varchar, so a number is not one of its values. Quote the value, "
                f"or compare the column that holds numbers.{_hint(table, column)}",
            )
        if col_type in _DATE_TYPES:
            return (
                "date_number",
                f"The CDM defines {table}.{column} as {col_type}, so a number is not one of its values. Write a date "
                f"literal such as DATE '2020-01-01', or extract the part to compare (EXTRACT(YEAR FROM {column})).",
            )
        return None

    if _TEMPLATE_RE.search(text):
        return None
    if col_type in _NUMERIC_TYPES:
        if _is_concept_id(column):
            return None
        if is_integer_input(text) if col_type == INTEGER else _FLOAT_RE.fullmatch(text):
            return None
        return (
            "number_text",
            f"The CDM defines {table}.{column} as {col_type}, and '{text}' is not {'an' if col_type == INTEGER else 'a'} "
            f"{col_type}.",
        )
    if col_type in _DATE_TYPES:
        if not _WORDS_RE.fullmatch(text) or text.strip().lower() in _SPECIAL_DATES:
            return None
        return (
            "date_text",
            f"The CDM defines {table}.{column} as {col_type}, and '{text}' is not a date.",
        )
    if ordering and (table, column) in _TEXT_VALUE_COLUMNS and _PLAIN_NUMBER_RE.fullmatch(text):
        return (
            "text_order",
            f"{column} is varchar, so the comparison with '{text}' orders text: '9' > '100'. The query runs and "
            f"returns rows by string order; whether that is intended depends on the data.{_hint(table, column)}",
        )
    return None


def _is_recast(col: exp.Column, depth: int = 0) -> bool:
    """Whether a CTE / derived-table projection on ``col``'s lineage casts it: the value no longer has the base
    column's type. ``column_origins`` looks through casts, so this has to be checked separately."""
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
        expr = _operand(proj.this if isinstance(proj, exp.Alias) else proj)
        if isinstance(expr, (exp.Cast, exp.TryCast)):
            return True
        if isinstance(expr, exp.Column) and _is_recast(expr, depth + 1):
            return True
    return False


def _reads_temp_table(col: exp.Column) -> bool:
    """The column is read straight from a T-SQL ``#temp`` table, which only shares its name with a CDM table."""
    select = col.find_ancestor(exp.Select)
    if select is None:
        return False
    if col.table:
        _, src = _source_by_qualifier(select, normalize_name(col.table))
    else:
        src = _pick_source(select, _select_sources(select), normalize_name(col.name), 0)
    return (
        isinstance(src, exp.Table)
        and isinstance(src.this, exp.Identifier)
        and (bool(src.this.args.get("temporary")) or src.name.startswith("#"))
    )


def _column_type(
    node: Optional[exp.Expression], aliases: Dict[str, str], cache: Dict[int, Optional[Tuple[str, str, str]]]
) -> Optional[Tuple[str, str, str]]:
    """(table, column, type) for a bare column operand that reads one CDM column."""
    node = _operand(node)
    if not isinstance(node, exp.Column):
        return None
    if id(node) not in cache:
        table, column = resolve_column_source(node, aliases)
        table, column = normalize_name(table or ""), normalize_name(column or "")
        col_type = get_column_type(table, column) if table and table != DERIVED else None
        if (table, column) in _SKIPPED_COLUMNS:
            col_type = None
        if col_type and (_is_recast(node) or _reads_temp_table(node)):
            col_type = None
        cache[id(node)] = (table, column, col_type) if col_type else None
    return cache[id(node)]


def _subquery_output(query: exp.Expression) -> Optional[exp.Expression]:
    """The single output column of an ``IN (SELECT ...)`` subquery, when it is a bare column."""
    branches = _query_branches(query)
    if len(branches) != 1 or len(branches[0].expressions) != 1:
        return None
    proj = branches[0].expressions[0]
    expr = _operand(proj.this if isinstance(proj, exp.Alias) else proj)
    return expr if isinstance(expr, exp.Column) else None


def _comparisons(tree: exp.Expression) -> List[Tuple[exp.Expression, exp.Expression, List[exp.Expression], bool]]:
    """(comparison node, left operand, right operands, is an ordering comparison) for every comparison in the
    statement."""
    out: List[Tuple[exp.Expression, exp.Expression, List[exp.Expression], bool]] = []
    for node in tree.find_all(*_COMPARISONS, exp.In, exp.Between):
        if isinstance(node, exp.In):
            rights = list(node.expressions or [])
            query = node.args.get("query")
            if query is not None:
                inner = _subquery_output(query.this if isinstance(query, exp.Subquery) else query)
                if inner is not None:
                    rights.append(inner)
            out.append((node, node.this, rights, False))
        elif isinstance(node, exp.Between):
            bounds = [b for b in (node.args.get("low"), node.args.get("high")) if b is not None]
            out.append((node, node.this, bounds, True))
        else:
            ordering = isinstance(node, _ORDERING)
            out.append((node, node.this, [node.expression], ordering))
            out.append((node, node.expression, [node.this], ordering))
    return out


def _predicate(node: exp.Expression) -> Dict[str, str]:
    return {"predicate": predicate_key(node)} if isinstance(node, exp.EQ) else {}


@register
class ColumnTypeValidationRule(Rule):
    """Comparisons whose operand types do not compare."""

    rule_id = "data_quality.column_type_validation"
    name = "Column Type Validation (SCHEMA Layer)"
    description = (
        "Flags comparisons whose operands are not of the types the CDM defines: a varchar or date column against a "
        "number, an integer column against a quoted non-integer, a date column against a quoted non-date, and two "
        "columns of different type families (integer = varchar). ERROR on every dialect. Also warns when "
        "value_as_string is ordered against a quoted number."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the literal WITH one of the column's type (quoted text for varchar columns, a number for numeric "
        "columns, DATE 'YYYY-MM-DD' for date columns), or the column WITH the one of the matching type "
        "(person_id, not person_source_value). CAST explicitly when a cross-type comparison is intended."
    )
    long_description = (
        "The OMOP CDM v5.4 field-level specification declares every column's type: *_id and *_concept_id columns are "
        "integers, *_source_value columns and vocabulary identifiers such as domain_id are varchar, *_date columns "
        "are dates. A comparison whose operand is not a value of the column's type is an ERROR on every dialect: "
        "domain_id = 19 (varchar against a number), condition_start_date >= 2020 (date against a number), "
        "year_of_birth = 'unknown' (integer against text that is not an integer) and person_id = "
        "person_source_value (integer against varchar). The verdict follows the CDM's types, not whether an engine "
        "casts implicitly. A quoted literal that is a value of the column's type is accepted: year_of_birth = '1980' "
        "and condition_start_date >= '2020-01-01'. An integer literal is optional whitespace, an optional sign and "
        "decimal digits. value_as_string compared with < or > against a quoted number (value_as_string > '100') "
        "orders text, so '9' > '100': a WARNING. Quoted literals on *_concept_id columns are left to "
        "data_quality.concept_id_string_comparison."
    )
    example_bad = "SELECT co.condition_concept_id\nFROM condition_occurrence co\nJOIN person p ON co.person_id = p.person_source_value;"
    example_good = (
        "SELECT co.condition_concept_id\nFROM condition_occurrence co\nJOIN person p ON co.person_id = p.person_id;"
    )
    extra_examples = (
        RuleExample(
            sql="SELECT concept_id, concept_name\nFROM concept\nWHERE domain_id = 19;",
            severity=Severity.ERROR,
            note="Number against a varchar column: the CDM defines domain_id as varchar, holding 'Condition', not 19.",
        ),
        RuleExample(
            sql="SELECT person_id\nFROM person\nWHERE year_of_birth = 'unknown';",
            severity=Severity.ERROR,
            note="Quoted non-integer against an integer column: the CDM defines year_of_birth as integer.",
        ),
        RuleExample(
            sql="SELECT person_id\nFROM condition_occurrence\nWHERE condition_start_date >= 2020;",
            severity=Severity.ERROR,
            note="Number against a date column: the CDM defines condition_start_date as date; use DATE '2020-01-01'.",
        ),
        RuleExample(
            sql="SELECT person_id\nFROM observation\nWHERE value_as_string > '100';",
            severity=Severity.WARNING,
            note="A varchar column ordered against a quoted number compares text: '9' > '100'. Use value_as_number.",
        ),
    )
    references = (
        CDM_FIELD_LEVEL,
        cdm_table("person"),
        cdm_table("concept"),
        cdm_table("condition_occurrence"),
        cdm_table("observation"),
        cdm_table("specimen"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            aliases = extract_aliases(tree)
            typed: Dict[int, Optional[Tuple[str, str, str]]] = {}

            seen: Set[Tuple[str, ...]] = set()
            for node, left, rights, ordering in _comparisons(tree):
                mine = _column_type(left, aliases, typed)
                if mine is None:
                    continue
                table, column, col_type = mine
                for right in rights:
                    other = _column_type(right, aliases, typed)
                    if other is not None:
                        o_table, o_column, o_type = other
                        if are_types_compatible(col_type, o_type):
                            continue
                        key = tuple(sorted([f"{table}.{column}", f"{o_table}.{o_column}"]))
                        if key in seen:
                            continue
                        seen.add(key)
                        severity = Severity.ERROR
                        consequence = (
                            "The CDM defines the two columns with types that do not compare, so their values never "
                            "match as defined."
                        )
                        violations.append(
                            self.create_violation(
                                message=(
                                    f"Type mismatch: {table}.{column} ({col_type}) compared with "
                                    f"{o_table}.{o_column} ({o_type}). {consequence}"
                                    f"{_hint(table, column) or _hint(o_table, o_column)}"
                                ),
                                severity=severity,
                                suggested_fix=(
                                    "REPLACE: one side WITH the column of the matching type, OR CAST one side "
                                    "explicitly (`CAST(<col> AS <type>)`) if the cross-type comparison is intended."
                                ),
                                details={
                                    "layer": "schema",
                                    "type": "column_type_mismatch",
                                    "left_table": table,
                                    "left_column": column,
                                    "left_type": col_type,
                                    "right_table": o_table,
                                    "right_column": o_column,
                                    "right_type": o_type,
                                    **_predicate(node),
                                },
                            )
                        )
                        continue

                    literal = _literal(right)
                    if literal is None:
                        continue
                    kind, text = literal
                    found = _literal_problem(table, column, col_type, kind, text, ordering)
                    if found is None:
                        continue
                    problem, explanation = found
                    key = (table, column, kind, text if kind == "string" else "")
                    if key in seen:
                        continue
                    seen.add(key)
                    severity = Severity.WARNING if problem == "text_order" else Severity.ERROR
                    shown = f"'{text}'" if kind == "string" else f"{kind} literal {text}"
                    violations.append(
                        self.create_violation(
                            message=f"Type mismatch: {table}.{column} ({col_type}) compared with {shown}. {explanation}",
                            severity=severity,
                            suggested_fix=(
                                "REPLACE: the literal WITH one of the column's type, OR CAST explicitly if the "
                                "cross-type comparison is intended."
                                if problem != "text_order"
                                else "REPLACE: the text comparison WITH one on a numeric column, or compare "
                                "CAST(<col> AS NUMERIC) guarded against non-numeric rows."
                            ),
                            details={
                                "layer": "schema",
                                "type": "type_mismatch" if problem != "text_order" else "text_ordering",
                                "table": table,
                                "column": column,
                                "column_type": col_type,
                                "literal_type": "varchar" if kind == "string" else kind,
                                **_predicate(node),
                            },
                        )
                    )

        return violations


__all__ = ["ColumnTypeValidationRule"]

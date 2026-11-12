"""Free-Text Column Misuse Rule.

The CDM v5.4 specification keeps a number of varchar columns that are neither source values nor keys: text
copied from the source (condition_occurrence.stop_reason, drug_exposure.sig, note.note_text), identifiers issued
outside OMOP (drug_exposure.lot_number, provider.npi, specimen.specimen_source_id), address parts
(location.state, location.zip) and NLP annotations (note_nlp.term_modifiers). None of them has a mapping into the
vocabulary; the concept of a row is in its *_concept_id column. The list is generated from the field-level CSV
(``_dq_free_text.py``).

Flagged, as WARNINGs because the query runs and what it returns depends on the stored text:
    * equality between a free-text column and a column of the concept or concept_synonym table, in any clause and
      through IN (SELECT ...), CTEs, derived tables, casts and LOWER / UPPER / TRIM: the strings match only by
      coincidence;
    * ``CAST(<free-text column> AS <numeric type>)``: the cast fails on the first value that is not a number
      (TRY_CAST and SAFE_CAST are not flagged);
    * note_nlp.term_modifiers equated with another varchar column: it holds several modifiers concatenated in one
      string.

Not flagged: text filters (=, LIKE, IS NULL) against literals, joins of a free-text column to a non-vocabulary
table, and CAST of note_nlp.offset (a character position, cast on purpose). Comparisons of these columns with a
number or an integer concept_id are type errors and are reported by data_quality.column_type_validation.
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import RuleExample, Rule, RuleViolation, Severity
from fastssv.core.helpers import DERIVED, column_origins, parse_sql
from fastssv.core.references import CDM_FIELD_LEVEL, cdm_table
from fastssv.core.registry import register
from fastssv.schemas import VARCHAR, get_column_type

from ._dq_free_text import FREE_TEXT_COLUMNS

_VOCABULARY_TEXT_TABLES = {"concept", "concept_synonym"}
_TEXT_WRAPPERS = (exp.Cast, exp.Paren, exp.Lower, exp.Upper, exp.Trim)
_NUMERIC_TYPES = {
    exp.DataType.Type.INT,
    exp.DataType.Type.BIGINT,
    exp.DataType.Type.SMALLINT,
    exp.DataType.Type.TINYINT,
    exp.DataType.Type.DECIMAL,
    exp.DataType.Type.DOUBLE,
    exp.DataType.Type.FLOAT,
}
_CAST_EXEMPT = {("note_nlp", "offset")}
_TERM_MODIFIERS = ("note_nlp", "term_modifiers")


def _peel(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, _TEXT_WRAPPERS) and not isinstance(node, exp.TryCast):
        node = node.this
    return node


def _origins(node: Optional[exp.Expression]) -> Optional[Set[Tuple[str, str]]]:
    node = _peel(node)
    if isinstance(node, exp.Column):
        origins = column_origins(node)
        if origins and all(t != DERIVED for t, _ in origins):
            return origins
    return None


def _free_text(node: Optional[exp.Expression]) -> Optional[Tuple[str, str]]:
    """The free-text CDM column every origin of ``node`` is, else None."""
    origins = _origins(node)
    if origins and len(origins) == 1:
        origin = next(iter(origins))
        return origin if origin in FREE_TEXT_COLUMNS else None
    return None


def _vocabulary_text(node: Optional[exp.Expression]) -> Optional[Tuple[str, str]]:
    """A varchar column of concept / concept_synonym; integer columns there are a type question, not a text one."""
    origins = _origins(node)
    if origins and all(t in _VOCABULARY_TEXT_TABLES and get_column_type(t, c) == VARCHAR for t, c in origins):
        return sorted(origins)[0]
    return None


def _text_origins(node: Optional[exp.Expression]) -> bool:
    """``node`` reads varchar CDM columns only. term_modifiers against a column of another type (concept_id) is a type
    mismatch, which data_quality.column_type_validation reports."""
    origins = _origins(node)
    return bool(origins) and all(get_column_type(t, c) == VARCHAR for t, c in origins)


def _subquery_output(node: exp.In) -> Optional[exp.Expression]:
    query = node.args.get("query")
    if query is None:
        return None
    query = query.this if isinstance(query, exp.Subquery) else query
    if not isinstance(query, exp.Select) or len(query.expressions) != 1:
        return None
    proj = query.expressions[0]
    return proj.this if isinstance(proj, exp.Alias) else proj


def _pairs(tree: exp.Expression) -> List[Tuple[exp.Expression, exp.Expression]]:
    out: List[Tuple[exp.Expression, exp.Expression]] = []
    for node in tree.find_all(exp.EQ, exp.In):
        if isinstance(node, exp.EQ):
            out.append((node.this, node.expression))
        else:
            inner = _subquery_output(node)
            if inner is not None:
                out.append((node.this, inner))
    return out


@register
class FreeTextColumnMisuseRule(Rule):
    """Free-text CDM columns matched against the vocabulary, cast to numbers, or used as composite keys."""

    rule_id = "data_quality.free_text_column_misuse"
    name = "Free-Text Column Misuse"

    description = (
        "Warns when a CDM free-text column (varchar, neither a source value nor a key: stop_reason, lot_number, sig, "
        "location.state, location.zip, note_nlp.term_modifiers, ...) is matched against the concept table, cast to "
        "a number, or (term_modifiers) equated with another text column. These columns have no vocabulary mapping."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "REMOVE: the match between the free-text column and the concept table; the column has no vocabulary "
        "mapping, and the concept of the row is in its *_concept_id column. Treat free-text columns as text "
        "(LIKE, IS NULL, string equality); guard any numeric cast (TRY_CAST, or a regex test)."
    )
    long_description = (
        "The CDM v5.4 specification keeps varchar columns that are neither source values nor keys: text from the "
        "source (stop_reason, sig, note_text), identifiers issued outside OMOP (lot_number, npi, "
        "specimen_source_id), address parts (location.state, location.zip) and NLP annotations "
        "(note_nlp.term_modifiers). None has a mapping into the vocabulary. Joining one to concept_name or "
        "concept_code returns rows only where two strings happen to coincide; casting one to a number fails on "
        "the first value that is not a number; note_nlp.term_modifiers concatenates several modifiers, so "
        "equating it with another column compares whole lists. The query runs either way, so these are warnings. "
        "Comparing these columns with a number is a type error, reported by data_quality.column_type_validation."
    )

    example_bad = (
        "SELECT co.person_id\nFROM condition_occurrence co\nJOIN concept c ON co.stop_reason = c.concept_name;"
    )
    example_good = (
        "SELECT co.person_id\nFROM condition_occurrence co\nJOIN concept c ON co.condition_concept_id = c.concept_id;"
    )
    extra_examples = (
        RuleExample(
            sql="SELECT de.person_id\nFROM drug_exposure de\nWHERE CAST(de.lot_number AS INTEGER) > 1000;",
            severity=Severity.WARNING,
            note="Free-text lot number cast to a number: the cast fails on the first lot number that is not numeric.",
        ),
        RuleExample(
            sql=(
                "SELECT a.note_id\n"
                "FROM note_nlp a\n"
                "JOIN note_nlp b ON a.note_id = b.note_id AND a.term_modifiers = b.term_modifiers;"
            ),
            severity=Severity.WARNING,
            note="note_nlp.term_modifiers equated with another column: it holds several modifiers in one string.",
        ),
    )
    references = (
        CDM_FIELD_LEVEL,
        cdm_table("condition_occurrence"),
        cdm_table("location"),
        cdm_table("note_nlp"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []
        lowered = sql.lower()
        if not any(column in lowered for _, column in FREE_TEXT_COLUMNS):
            return []
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            seen: Set[Tuple[str, ...]] = set()

            for left, right in _pairs(tree):
                for text_side, other in ((left, right), (right, left)):
                    free = _free_text(text_side)
                    if free is None:
                        continue
                    vocab = _vocabulary_text(other)
                    table, column = free
                    if vocab is not None:
                        key = ("vocabulary", table, column, *vocab)
                        if key in seen:
                            continue
                        seen.add(key)
                        violations.append(
                            self.create_violation(
                                message=(
                                    f"{table}.{column} is free text with no vocabulary mapping, matched against "
                                    f"{vocab[0]}.{vocab[1]}: rows match only where the two strings happen to "
                                    f"coincide. The concept of a {table} row is in its *_concept_id column."
                                ),
                                suggested_fix=(
                                    f"REMOVE: the match of `{column}` against the vocabulary. Join concept on the "
                                    f"row's *_concept_id column instead, or treat `{column}` as text."
                                ),
                                details={"type": "vocabulary_match", "table": table, "column": column},
                            )
                        )
                    elif free == _TERM_MODIFIERS and _text_origins(other):
                        key = ("modifiers", table, column)
                        if key in seen:
                            continue
                        seen.add(key)
                        violations.append(
                            self.create_violation(
                                message=(
                                    "note_nlp.term_modifiers is equated with another column. It concatenates "
                                    "several modifiers (negation, subject, conditional, ...) in one string, so the "
                                    "equality compares whole lists rather than one modifier."
                                ),
                                suggested_fix=(
                                    "REPLACE: the column equality on term_modifiers WITH a test of the modifier "
                                    "needed (`term_modifiers LIKE '%negation=false%'`)."
                                ),
                                details={"type": "modifiers_equality", "table": table, "column": column},
                            )
                        )

            for cast in tree.find_all(exp.Cast):
                if isinstance(cast, exp.TryCast) or cast.args.get("safe"):
                    continue
                to = cast.args.get("to")
                if not isinstance(to, exp.DataType) or to.this not in _NUMERIC_TYPES:
                    continue
                free = _free_text(cast.this)
                if free is None or free in _CAST_EXEMPT:
                    continue
                table, column = free
                key = ("cast", table, column)
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    self.create_violation(
                        message=(
                            f"{table}.{column} is free text cast to {to.sql(dialect=dialect)}. The cast fails on the "
                            "first value that is not a number, so whether the query runs depends on the data."
                        ),
                        suggested_fix=(
                            f"REPLACE: `CAST({column} AS ...)` WITH a guarded conversion (TRY_CAST, or a regex test "
                            f"before the cast), or treat `{column}` as text."
                        ),
                        details={"type": "cast_to_numeric", "table": table, "column": column},
                    )
                )

        return violations


__all__ = ["FreeTextColumnMisuseRule"]

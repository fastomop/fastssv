"""Note NLP Term Modifiers is Free Text Rule.

OMOP semantic rule GAP_014:
note_nlp.term_modifiers is a free-text VARCHAR field storing additional NLP
contextual attributes (e.g., 'subject=patient, certainty=positive'). It should
not be used in JOINs to concept or treated as a concept_id.

The Problem:
    The note_nlp.term_modifiers column stores unstructured key-value pairs as
    free text, not concept IDs or structured data. Developers might mistakenly:
    1. Join it to concept.concept_id (treating it as a foreign key)
    2. Cast it to integer (treating it as numeric)
    3. Use it in equality joins expecting structured data

    This is semantically incorrect because term_modifiers contains unparsed
    text annotations, not standardized concept identifiers.

Common mistakes:
    1. JOIN to concept: JOIN concept c ON nn.term_modifiers = c.concept_id
    2. CAST to integer: CAST(term_modifiers AS INT)
    3. General JOINs: JOIN other_table ON nn.term_modifiers = ...

Violation pattern:
    SELECT *
    FROM note_nlp nn
    JOIN concept c ON nn.term_modifiers = c.concept_id
    -- WRONG: term_modifiers is free text, not a concept_id!

Correct pattern:
    SELECT *
    FROM note_nlp
    WHERE term_modifiers LIKE '%certainty=positive%'
    -- Correct: Text search on free-text field
"""
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

NOTE_NLP = "note_nlp"
TERM_MODIFIERS = "term_modifiers"
CONCEPT = "concept"

NUMERIC_TYPES = {
    "INT",
    "INTEGER",
    "BIGINT",
    "SMALLINT",
    "NUMERIC",
    "DECIMAL",
    "FLOAT",
    "DOUBLE",
    "REAL",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    return _norm(name.split(".")[-1]) if name else None


def _is_note_nlp(table: Optional[str]) -> bool:
    return _normalize_table(table) == NOTE_NLP


def _is_concept(table: Optional[str]) -> bool:
    return _normalize_table(table) == CONCEPT


def _is_term_modifiers(col: Optional[str]) -> bool:
    return _norm(col) == TERM_MODIFIERS


def _has_note_nlp(tree: exp.Expression) -> bool:
    return any(_normalize_table(t.name) == NOTE_NLP for t in tree.find_all(exp.Table))


def _is_term_modifiers_ref(
    col: exp.Column,
    aliases: Dict[str, str],
    has_note_nlp: bool,
) -> Tuple[bool, Optional[str], Optional[str]]:
    table, column = resolve_table_col(col, aliases)

    if table and _is_note_nlp(table) and _is_term_modifiers(column):
        return True, table, column

    if not table and has_note_nlp and _is_term_modifiers(column):
        return True, NOTE_NLP, column

    return False, None, None


def _is_numeric_type(type_expr: exp.Expression) -> bool:
    if not type_expr:
        return False
    type_str = str(type_expr).upper()
    tokens = set(type_str.replace("(", " ").replace(")", " ").split())
    return any(t in NUMERIC_TYPES for t in tokens)


# --- Detection -------------------------------------------------------------

def _detect_join_usage(tree, aliases, has_note_nlp):
    results = []

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left, right = eq.this, eq.expression

            for node, other in [(left, right), (right, left)]:
                if not isinstance(node, exp.Column):
                    continue

                is_term, table, col = _is_term_modifiers_ref(node, aliases, has_note_nlp)

                if not is_term:
                    continue

                other_table, other_col = None, None
                if isinstance(other, exp.Column):
                    other_table, other_col = resolve_table_col(other, aliases)

                kind = "join_to_concept" if _is_concept(other_table) else "join"

                results.append((
                    kind,
                    table,
                    col,
                    _normalize_table(other_table) if other_table else None,
                    _norm(other_col) if other_col else None,
                ))

    return results


def _detect_cast_usage(tree, aliases, has_note_nlp):
    results = []

    # CAST / TRY_CAST
    for cast_node in tree.find_all(exp.Cast, exp.TryCast):
        for col_node in cast_node.find_all(exp.Column):
            is_term, table, col = _is_term_modifiers_ref(col_node, aliases, has_note_nlp)

            if is_term and _is_numeric_type(cast_node.to):
                results.append(("cast_to_numeric", table, col, str(cast_node.to)))

    # CONVERT (SQL Server)
    for func in tree.find_all(exp.Anonymous):
        if str(func.this).lower() != "convert":
            continue

        args = func.expressions
        if len(args) < 2:
            continue

        target_type = args[0]
        value_expr = args[1]

        for col_node in value_expr.find_all(exp.Column):
            is_term, table, col = _is_term_modifiers_ref(col_node, aliases, has_note_nlp)

            if is_term and _is_numeric_type(target_type):
                results.append(("cast_to_numeric", table, col, str(target_type)))

    return results


# --- Rule ------------------------------------------------------------------

@register
class NoteNlpTermModifiersIsFreeTextRule(Rule):
    """Prevent misuse of term_modifiers as structured or numeric."""

    rule_id = "data_quality.note_nlp_term_modifiers_is_free_text"
    name = "Note NLP Term Modifiers is Free Text"

    description = (
        "note_nlp.term_modifiers is free-text and must not be used as "
        "structured identifiers or numeric values."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use LIKE or full-text search. Do not JOIN or CAST term_modifiers."
    )
    long_description = (
        "note_nlp.term_modifiers is free-text storing pipe-delimited "
        "modifier descriptors like 'negated=true|subject=patient'. It has "
        "no mapping into the concept table and is not a structured "
        "identifier. Joining it to concept or casting it to a number "
        "returns zero or meaningless rows. Use LIKE for substring matches, "
        "or parse the pipe-separated structure at the application layer."
    )
    example_bad = (
        "SELECT nnlp.note_nlp_id\n"
        "FROM note_nlp nnlp\n"
        "JOIN concept c ON nnlp.term_modifiers = c.concept_name;"
    )
    example_good = (
        "SELECT nnlp.note_nlp_id\n"
        "FROM note_nlp nnlp\n"
        "JOIN concept c ON nnlp.note_nlp_concept_id = c.concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "note_nlp" not in sql.lower() or "term_modifiers" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            has_note_nlp = _has_note_nlp(tree)

            findings = []
            findings += _detect_join_usage(tree, aliases, has_note_nlp)
            findings += _detect_cast_usage(tree, aliases, has_note_nlp)

            for f in findings:
                kind, table, col = f[0], f[1], f[2]

                if kind.startswith("join"):
                    other_table, other_col = f[3], f[4]
                    key = f"{kind}|{table}|{col}|{other_table}|{other_col}"

                    if key in seen:
                        continue
                    seen.add(key)

                    if kind == "join_to_concept":
                        message = (
                            f"{table}.{col} joined to {other_table}.{other_col}. "
                            "term_modifiers is free text, not a concept_id."
                        )
                        fix = "Use LIKE instead of joining to concept."
                    else:
                        message = (
                            f"{table}.{col} used in JOIN condition. "
                            "term_modifiers is not a relational key."
                        )
                        fix = self.suggested_fix

                else:
                    to_type = f[3]
                    key = f"{kind}|{table}|{col}|{to_type}"

                    if key in seen:
                        continue
                    seen.add(key)

                    message = (
                        f"{table}.{col} cast to {to_type}. "
                        "term_modifiers is free text, not numeric."
                    )
                    fix = self.suggested_fix

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        suggested_fix=fix,
                        details={
                            "type": kind,
                            "table": table,
                            "column": col,
                        },
                    )
                )

        return violations


__all__ = ["NoteNlpTermModifiersIsFreeTextRule"]

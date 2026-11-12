"""Note NLP Snippet Misuse Rule.

CDM v5.4 defines ``note_nlp.snippet`` as "a small window of text surrounding the term" and
``note_nlp.lexical_variant`` as "raw text extracted from the NLP tool". Both are free text kept for context. The
coded result of the extraction is ``note_nlp_concept_id``. Selecting findings by matching those strings misses
spelling, abbreviation and casing variants ('DM', 'diabetic', 'Diabetes') and catches substrings ('prediabetes'), so
the result depends on how clinicians happened to write rather than on what was extracted.

Fires when, in a WHERE or JOIN ON clause, a note_nlp snippet / lexical_variant column (followed through casts,
LOWER / UPPER / TRIM, CTEs and derived tables) is:
    * compared with ``=`` or ``IN`` to a non-empty string literal;
    * matched with LIKE / ILIKE (also ``LIKE ANY``), a regular expression (``~``, ``~*``, ``REGEXP_CONTAINS``) or
      ``SIMILAR TO`` against a pattern that has characters other than ``%`` and ``_``;
    * searched with POSITION / STRPOS / CHARINDEX for a non-empty literal;
    * equated to ``concept.concept_name`` or ``concept_synonym.concept_synonym_name``;
and the same note_nlp row is not also selected by ``note_nlp_concept_id`` (``=``, ``IN``, a join). Matching the
text of rows already selected by concept (a negation or context cue in the window), or of unmapped rows
(``note_nlp_concept_id = 0``), is what the context columns are for.

Not flagged: projecting the text, presence checks (``IS NOT NULL``, ``<> ''``, ``LIKE '%'``), length or other
computed uses, and text logic in SELECT / CASE.

This is a WARNING: text search is the only option where the NLP pipeline left note_nlp_concept_id unmapped.
"""

from typing import List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import is_in_where_or_join_clause, is_string_literal, parse_sql
from fastssv.core.references import cdm_table
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import Read, RowPath, in_negated_scope, read_of, same_row

NOTE_NLP = "note_nlp"
TEXT_COLUMNS = {"snippet", "lexical_variant"}
CONCEPT_COLUMN = "note_nlp_concept_id"
CONCEPT_NAME_COLUMNS = {("concept", "concept_name"), ("concept_synonym", "concept_synonym_name")}
_TEXT_WRAPPERS = (exp.Lower, exp.Upper, exp.Trim, exp.Paren, exp.Cast, exp.TryCast)
_PATTERN_MATCHES = tuple(
    getattr(exp, name) for name in ("Like", "ILike", "RegexpLike", "RegexpILike", "SimilarTo") if hasattr(exp, name)
)


def _unwrap(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, _TEXT_WRAPPERS):
        node = node.this
    return node


def _text_read(node: Optional[exp.Expression]) -> Optional[Read]:
    """The note_nlp free-text column ``node`` reads (through text normalisers and lineage)."""
    read = read_of(_unwrap(node))
    if read is None or read[1] != NOTE_NLP or read[2] not in TEXT_COLUMNS:
        return None
    return read


def _non_empty_text(node: Optional[exp.Expression]) -> bool:
    return (
        node is not None
        and (is_string_literal(node) or isinstance(node, exp.RawString))
        and str(node.this).strip() != ""
    )


def _real_pattern(node: Optional[exp.Expression]) -> bool:
    if isinstance(node, exp.Any):
        node = node.this
        while isinstance(node, exp.Paren):
            node = node.this
        values = node.expressions if isinstance(node, exp.Array) else [node]
        return any(_real_pattern(v) for v in values)
    return _non_empty_text(node) and str(node.this).strip().strip("%_") != ""


def _is_concept_name(node: Optional[exp.Expression]) -> bool:
    read = read_of(_unwrap(node))
    return read is not None and (read[1], read[2]) in CONCEPT_NAME_COLUMNS


def _concept_selected_rows(tree: exp.Expression) -> List[RowPath]:
    """note_nlp rows that a filter or join also selects by note_nlp_concept_id."""
    rows: List[RowPath] = []
    for node in tree.find_all(exp.EQ, exp.In):
        if not is_in_where_or_join_clause(node) or in_negated_scope(node):
            continue
        sides = [node.this] if isinstance(node, exp.In) else [node.this, node.expression]
        for side in sides:
            read = read_of(side)
            if read is not None and read[1:] == (NOTE_NLP, CONCEPT_COLUMN):
                rows.append(read[0])
    return rows


def _find_violations(tree: exp.Expression) -> List[str]:
    found: List[tuple] = []

    def add(read: Read, msg: str) -> None:
        found.append((read[0], msg))

    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue
        for side, other in ((eq.this, eq.expression), (eq.expression, eq.this)):
            read = _text_read(side)
            if read is None:
                continue
            col = read[2]
            if _is_concept_name(other):
                add(
                    read,
                    f"note_nlp.{col} joined to a concept name. {col} is free text; "
                    "join on note_nlp.note_nlp_concept_id = concept.concept_id instead.",
                )
            elif _non_empty_text(_unwrap(other)):
                add(
                    read,
                    f"note_nlp.{col} matched to a text literal. {col} is free text kept for context; "
                    "filter on note_nlp_concept_id to find the extracted concept.",
                )

    for in_node in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_node):
            continue
        read = _text_read(in_node.this)
        if read is not None and any(_non_empty_text(e) for e in in_node.expressions or []):
            add(
                read,
                f"note_nlp.{read[2]} matched to a list of text literals. {read[2]} is free text and a list of spellings "
                "misses lexical variation; filter on note_nlp_concept_id instead.",
            )

    for match in tree.find_all(*_PATTERN_MATCHES):
        if not is_in_where_or_join_clause(match):
            continue
        read = _text_read(match.this)
        if read is not None and _real_pattern(match.expression):
            add(
                read,
                f"note_nlp.{read[2]} searched with LIKE / a pattern match. Pattern matches on free text miss variants "
                "and catch substrings; filter on note_nlp_concept_id instead.",
            )

    for pos in tree.find_all(exp.StrPosition):
        if not is_in_where_or_join_clause(pos):
            continue
        read = _text_read(pos.this)
        if read is not None and _non_empty_text(_unwrap(pos.args.get("substr"))):
            add(
                read,
                f"note_nlp.{read[2]} searched for a substring. Substring searches on free text miss variants and catch "
                "longer words; filter on note_nlp_concept_id instead.",
            )

    selected = _concept_selected_rows(tree)
    issues: List[str] = []
    for row, msg in found:
        if any(same_row(row, s) for s in selected):
            continue
        if msg not in issues:
            issues.append(msg)
    return issues


@register
class NoteNlpSnippetMisuseRule(Rule):
    """Flag selecting note_nlp findings by matching snippet / lexical_variant text."""

    rule_id = "domain_specific.note_nlp_snippet_misuse"
    name = "Note NLP Snippet Misuse"

    description = (
        "note_nlp.snippet and lexical_variant are free text kept for context. Selecting rows by matching them "
        "against text literals, patterns or concept names, without also selecting by note_nlp_concept_id, misses "
        "variants; use note_nlp_concept_id."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "REPLACE: text matches on note_nlp.snippet / note_nlp.lexical_variant WITH "
        "`WHERE note_nlp.note_nlp_concept_id = <id>` (or IN (...)). Keep the text columns in SELECT for context, or as "
        "an extra filter on rows already selected by concept."
    )
    long_description = (
        "CDM v5.4 keeps note_nlp.snippet (the text window around the term) and lexical_variant (the raw extracted "
        "text) for context; the coded result is note_nlp_concept_id. A filter that selects rows by matching the text "
        "misses spelling and abbreviation variants and catches substrings. Matching text on rows already selected by "
        "note_nlp_concept_id (for example a negation cue in the window), or on unmapped rows (concept 0), is not "
        "reported."
    )
    example_bad = "SELECT note_nlp_id FROM note_nlp\nWHERE snippet LIKE '%diabetes%';"
    example_good = "SELECT note_nlp_id, snippet FROM note_nlp\nWHERE note_nlp_concept_id = 201820;"
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT n.note_id, c.concept_id\n"
                "FROM note_nlp n\n"
                "JOIN concept c ON LOWER(n.lexical_variant) = LOWER(c.concept_name);"
            ),
            severity=Severity.WARNING,
            note="lexical_variant joined to concept names instead of note_nlp_concept_id = concept_id",
        ),
    )
    references = (cdm_table("note_nlp"),)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []
        lowered = sql.lower()
        if not any(col in lowered for col in TEXT_COLUMNS):
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees:
            if not tree:
                continue
            for msg in _find_violations(tree):
                if msg in seen:
                    continue
                seen.add(msg)
                violations.append(self.create_violation(message=msg, severity=self.severity))
        return violations


__all__ = ["NoteNlpSnippetMisuseRule"]

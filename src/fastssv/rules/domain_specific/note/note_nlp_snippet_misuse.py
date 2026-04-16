"""Note NLP Snippet Misuse Rule.

OMOP semantic rule OMOP_152:
The note_nlp table contains NLP-extracted clinical entities from unstructured notes.
The snippet and lexical_variant columns contain free-text excerpts for human context,
not standardized clinical codes. These should NOT be used for structured data matching.

The Problem:
    note_nlp columns and their purposes:
    - snippet: Short text excerpt around the NLP-extracted term (for context)
    - lexical_variant: The exact text string found in the note
    - note_nlp_concept_id: Standardized OMOP concept_id for the extracted entity

    Common mistakes:
    1. Text matching on snippet/lexical_variant instead of using concept_id
    2. Joining to concept table on text columns instead of concept_id
    3. Filtering by exact text matches that miss lexical variations
    4. Treating unstructured text as structured identifiers

Why this is wrong:
    Free-text columns are unsuitable for clinical identification:
    - Lexical variations: "diabetes", "DM", "diabetic", "diabetes mellitus"
    - Case sensitivity: "Diabetes" vs "diabetes"
    - Substring matching issues: "diabetes" in "prediabetes"
    - No standardization: same concept expressed many ways
    - Performance: text searches are slow compared to integer concept_id lookups
    - Accuracy: text matching misses related concepts in hierarchy

    The note_nlp_concept_id is the standardized clinical code that should be used
    for all structured queries and analysis.

Violation patterns:
    SELECT * FROM note_nlp WHERE snippet = 'diabetes'
    -- WARNING: Text matching on free-text field, use note_nlp_concept_id

    SELECT * FROM note_nlp WHERE lexical_variant = 'DM'
    -- WARNING: Text matching on lexical variant, use note_nlp_concept_id

    SELECT * FROM note_nlp WHERE snippet LIKE '%diabetes%'
    -- WARNING: LIKE pattern on free text, use concept_id for clinical search

    SELECT n.* FROM note_nlp n
    JOIN concept c ON n.snippet = c.concept_name
    -- WARNING: Joining on text column instead of note_nlp_concept_id

    SELECT * FROM note_nlp
    WHERE lexical_variant IN ('diabetes', 'DM', 'diabetic')
    -- WARNING: Text list matching, use concept_id or concept hierarchy

Correct patterns:
    SELECT * FROM note_nlp WHERE note_nlp_concept_id = 201826
    -- OK: Using standardized concept_id

    SELECT snippet, note_nlp_concept_id FROM note_nlp
    WHERE note_nlp_concept_id IN (201826, 443238)
    -- OK: Displaying snippet for context, filtering by concept_id

    SELECT n.* FROM note_nlp n
    JOIN concept c ON n.note_nlp_concept_id = c.concept_id
    -- OK: Joining on concept_id

    SELECT * FROM note_nlp WHERE snippet IS NOT NULL
    -- OK: Checking presence, not matching text

    SELECT * FROM note_nlp WHERE term_exists = 'Y'
    AND note_nlp_concept_id = 201826
    -- OK: Using concept_id with term existence flag

Note:
    This is a WARNING, not an error. Text searches on snippet/lexical_variant
    may be intentional for exploratory analysis or debugging. However, production
    queries should always use note_nlp_concept_id for clinical identification.
"""

import logging
from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import extract_aliases, normalize_name, parse_sql
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

NOTE_NLP_TABLE = "note_nlp"
TEXT_COLUMNS = {"snippet", "lexical_variant"}


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    return {
        _norm(cte.alias_or_name)
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _collect_tables(tree: exp.Expression, cte_names: Set[str]) -> Set[str]:
    tables = set()
    for tbl in tree.find_all(exp.Table):
        name = _norm(tbl.name)
        if name and name not in cte_names:
            tables.add(name)
    return tables


def _is_note_nlp_table(name: Optional[str]) -> bool:
    return name == NOTE_NLP_TABLE


def _is_text_column(name: Optional[str]) -> bool:
    return name in TEXT_COLUMNS


def _resolve_table_name(
    table_name: Optional[str],
    aliases: Dict[str, str],
) -> Optional[str]:
    if not table_name:
        return None

    table_name = _norm(table_name)
    resolved = aliases.get(table_name)

    if resolved:
        return _norm(resolved)

    return table_name


def _is_safe_usage(node: exp.Expression) -> bool:
    """
    Safe usages:
    - IS NULL / IS NOT NULL
    - string formatting functions (CONCAT, COALESCE)
    """
    if isinstance(node, exp.Is):
        return True

    if isinstance(node, (exp.Concat, exp.Coalesce)):
        return True

    return False


def _column_matches_note_nlp(
    col: exp.Column,
    aliases: Dict[str, str],
    cte_names: Set[str],
    tables_in_query: Set[str],
) -> Optional[str]:
    col_name = _norm(col.name)
    table_name = _resolve_table_name(col.table, aliases)

    # Unqualified heuristic
    if not table_name:
        if tables_in_query == {NOTE_NLP_TABLE} and _is_text_column(col_name):
            table_name = NOTE_NLP_TABLE
        else:
            return None

    if table_name in cte_names:
        return None

    if _is_note_nlp_table(table_name) and _is_text_column(col_name):
        return col_name

    return None


def _expression_contains_target_column(
    node: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    tables_in_query: Set[str],
) -> Optional[str]:
    for col in node.find_all(exp.Column):
        match = _column_matches_note_nlp(col, aliases, cte_names, tables_in_query)
        if match:
            return match
    return None


def _check_comparison(
    comparison: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    tables_in_query: Set[str],
) -> Optional[str]:
    left = comparison.this
    right = comparison.expression

    if not left or not right:
        return None

    # Safe usage check (FIXED)
    if _is_safe_usage(comparison):
        return None

    # Check both sides for presence of target column
    for side, other in [(left, right), (right, left)]:
        col_name = _expression_contains_target_column(
            side, aliases, cte_names, tables_in_query
        )

        if not col_name:
            continue

        # If comparing to anything other than NULL → unsafe
        if not isinstance(other, exp.Null):
            if isinstance(other, exp.Column):
                return (
                    f"Column '{col_name}' is free text. "
                    f"Join on note_nlp_concept_id instead of text columns."
                )

            return (
                f"Column '{col_name}' is free text for context, not structured data. "
                f"Use note_nlp_concept_id for clinical identification instead of text matching."
            )

    return None


def _check_like_pattern(
    like_expr: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    tables_in_query: Set[str],
) -> Optional[str]:
    col_name = _expression_contains_target_column(
        like_expr.this, aliases, cte_names, tables_in_query
    )

    if not col_name:
        return None

    return (
        f"Column '{col_name}' is free text. "
        f"LIKE patterns on unstructured text may be unreliable. "
        f"Use note_nlp_concept_id for clinical searches."
    )


def _check_in_clause(
    in_expr: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    tables_in_query: Set[str],
) -> Optional[str]:
    col_name = _expression_contains_target_column(
        in_expr.this, aliases, cte_names, tables_in_query
    )

    if not col_name:
        return None

    return (
        f"Column '{col_name}' is free text. "
        f"IN/NOT IN on text misses lexical variation. "
        f"Use note_nlp_concept_id or concept hierarchy."
    )


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> List[str]:
    issues: List[str] = []

    tables_in_query = _collect_tables(tree, cte_names)

    # Comparisons
    for comp in tree.find_all(exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE):
        msg = _check_comparison(comp, aliases, cte_names, tables_in_query)
        if msg:
            issues.append(msg)

    # LIKE
    for like in tree.find_all(exp.Like, exp.ILike):
        msg = _check_like_pattern(like, aliases, cte_names, tables_in_query)
        if msg:
            issues.append(msg)

    # IN clauses (NOT IN is exp.Not wrapping exp.In)
    for in_expr in tree.find_all(exp.In):
        msg = _check_in_clause(in_expr, aliases, cte_names, tables_in_query)
        if msg:
            issues.append(msg)

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------

@register
class NoteNlpSnippetMisuseRule(Rule):
    """
    OMOP_152: Validate misuse of note_nlp text columns for matching.
    """

    rule_id = "domain_specific.note.note_nlp_snippet_misuse"
    name = "Note NLP Snippet Misuse"

    description = (
        "note_nlp.snippet and lexical_variant are free text for context, "
        "not structured data. Use note_nlp_concept_id instead."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use note_nlp_concept_id instead of text matching."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if NOTE_NLP_TABLE not in sql_lower:
            return []

        if not any(col in sql_lower for col in TEXT_COLUMNS):
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_152",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            cte_names = _extract_cte_names(tree)

            issues = _find_violations(tree, aliases, cte_names)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["NoteNlpSnippetMisuseRule"]
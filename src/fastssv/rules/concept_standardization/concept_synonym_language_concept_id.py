"""Concept Synonym Language Concept ID Rule.

OMOP semantic rule OMOP_127:
concept_synonym.language_concept_id specifies the language of the synonym.
For English-language queries, filter language_concept_id = 4180186 (English).
Without this filter, results may include synonyms in other languages.

The Problem:
    The concept_synonym table stores synonym names in multiple languages:
    - English (language_concept_id = 4180186)
    - Spanish, German, French, etc. (other language_concept_id values)

    When searching for synonyms by name (LIKE '%heart attack%'), you may
    inadvertently retrieve synonyms in multiple languages if you don't
    filter by language_concept_id.

Common mistake:
    Developers search concept_synonym_name without considering language,
    leading to unexpected multilingual results.

Violation patterns:
    SELECT concept_id FROM concept_synonym
    WHERE concept_synonym_name LIKE '%heart attack%'
    -- WARNING: May return synonyms in Spanish, German, etc.

    SELECT * FROM concept_synonym
    WHERE concept_synonym_name ILIKE '%diabetes%'
    -- WARNING: No language filter

Correct patterns:
    SELECT concept_id FROM concept_synonym
    WHERE concept_synonym_name LIKE '%heart attack%'
    AND language_concept_id = 4180186  -- English
    -- OK: Explicitly filtering for English synonyms

    SELECT * FROM concept_synonym
    WHERE concept_synonym_name LIKE '%myocardial infarction%'
    AND language_concept_id IN (4180186, 4175777)  -- English and Spanish
    -- OK: Explicitly choosing languages

    SELECT * FROM concept_synonym cs
    JOIN concept c ON cs.language_concept_id = c.concept_id
    WHERE cs.concept_synonym_name LIKE '%infection%'
    AND c.concept_name = 'English'
    -- OK: Filtering language via join

Note:
    This is a WARNING, not an ERROR. Some queries legitimately want
    multilingual results. This rule reminds you to consider language filtering.
"""

import logging
from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


TABLE_NAME = "concept_synonym"
SYNONYM_NAME_COL = "concept_synonym_name"
LANGUAGE_COL = "language_concept_id"


# --- Helpers -----------------------------------------------------------------

def _is_target_column(table: str, column: str, tree: exp.Expression) -> bool:
    if normalize_name(column) != SYNONYM_NAME_COL:
        return False

    if table:
        return normalize_name(table) == TABLE_NAME

    return has_table_reference(tree, TABLE_NAME)


def _has_like_on_synonym_name(tree: exp.Expression, aliases: dict) -> bool:
    """Check LIKE/ILIKE usage in filtering context."""
    for node in tree.find_all((exp.Like, exp.ILike, exp.Not)):
        check_node = node

        if isinstance(node, exp.Not):
            inner = node.this
            if isinstance(inner, (exp.Like, exp.ILike)):
                check_node = inner
            else:
                continue

        left = check_node.this
        if not isinstance(left, exp.Column):
            continue

        table, column = resolve_table_col(left, aliases)

        if not _is_target_column(table, column, tree):
            continue

        return True

    return False


def _has_language_filter(tree: exp.Expression, aliases: dict) -> bool:
    """Check if language_concept_id is used in filtering or join."""
    # EQ conditions
    for eq in tree.find_all(exp.EQ):
        for col_expr in (eq.this, eq.expression):
            if not isinstance(col_expr, exp.Column):
                continue

            table, column = resolve_table_col(col_expr, aliases)

            if normalize_name(column) != LANGUAGE_COL:
                continue

            if table and normalize_name(table) != TABLE_NAME:
                continue

            return True

    # IN conditions
    for in_expr in tree.find_all(exp.In):
        col_expr = in_expr.this
        if isinstance(col_expr, exp.Column):
            table, column = resolve_table_col(col_expr, aliases)

            if normalize_name(column) == LANGUAGE_COL:
                if table and normalize_name(table) != TABLE_NAME:
                    continue
                return True

    # JOIN conditions
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if not on:
            continue

        for eq in on.find_all(exp.EQ):
            for col_expr in (eq.this, eq.expression):
                if not isinstance(col_expr, exp.Column):
                    continue

                table, column = resolve_table_col(col_expr, aliases)

                if normalize_name(column) != LANGUAGE_COL:
                    continue

                if table and normalize_name(table) != TABLE_NAME:
                    continue

                return True

    return False


def _find_violations(tree: exp.Expression) -> List[str]:
    issues: List[str] = []

    aliases = extract_aliases(tree)

    if not has_table_reference(tree, TABLE_NAME):
        return []

    if not _has_like_on_synonym_name(tree, aliases):
        return []

    if _has_language_filter(tree, aliases):
        return []

    issues.append(
        "concept_synonym is queried with LIKE on concept_synonym_name but without "
        "filtering language_concept_id. This may return multilingual results. "
        "Add: AND language_concept_id = 4180186 (for English)."
    )

    return issues


# --- Rule --------------------------------------------------------------------

@register
class ConceptSynonymLanguageConceptIdRule(Rule):
    rule_id = "concept_standardization.concept_synonym_language_concept_id"
    name = "Concept Synonym Language Concept ID"

    description = (
        "concept_synonym stores synonyms in multiple languages. When searching by "
        "concept_synonym_name, filter by language_concept_id."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Add: AND language_concept_id = 4180186 (English), unless multilingual results are intended."
    )
    long_description = (
        "The concept_synonym table stores synonym names in many languages: "
        "English (language_concept_id = 4180186), Spanish, German, French, "
        "Dutch, etc. Searching concept_synonym_name with a free-text match "
        "(LIKE / equality) without filtering by language inadvertently "
        "returns concepts whose non-English synonyms contain the query "
        "string, which is almost never the intent in English-speaking "
        "deployments. Add an explicit language filter, or use IN to allow "
        "a deliberate multilingual scope."
    )
    example_bad = (
        "SELECT cs.concept_id\n"
        "FROM concept_synonym cs\n"
        "WHERE cs.concept_synonym_name LIKE '%diabetes%';"
    )
    example_good = (
        "SELECT cs.concept_id\n"
        "FROM concept_synonym cs\n"
        "WHERE cs.concept_synonym_name LIKE '%diabetes%'\n"
        "  AND cs.language_concept_id = 4180186;  -- English"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if TABLE_NAME not in sql_lower or SYNONYM_NAME_COL not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_127",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            issues = _find_violations(tree)

            for msg in issues:
                violations.append(
                    self.create_violation(message=msg, severity=self.severity)
                )

        return violations


__all__ = ["ConceptSynonymLanguageConceptIdRule"]

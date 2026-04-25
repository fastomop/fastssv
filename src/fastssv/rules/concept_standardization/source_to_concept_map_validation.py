"""Source to Concept Map Validation Rule.

OMOP semantic rule OMOP_058:
When using source_to_concept_map, always filter by source_vocabulary_id.
Without it, the same source_code from different vocabularies may return incorrect mappings.

The Problem:
    source_to_concept_map contains mappings from many source vocabularies.
    The same source_code can exist in multiple vocabularies with different meanings.

    Example: Code "250" exists in:
    - ICD-9-CM: Diabetes mellitus
    - ICD-10-CM: Different condition
    - Local hospital codes: Something else entirely

    Without source_vocabulary_id filter, you get ALL mappings for "250",
    leading to incorrect concept assignments.

Violation pattern:
    SELECT target_concept_id
    FROM source_to_concept_map
    WHERE source_code = '250.00'
    -- Returns mappings from ALL vocabularies!

Correct pattern:
    SELECT target_concept_id
    FROM source_to_concept_map
    WHERE source_code = '250.00'
      AND source_vocabulary_id = 'ICD9CM'
    -- Returns only ICD-9-CM mapping
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.patch import add as patch_add, locate
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

SOURCE_TO_CONCEPT_MAP = "source_to_concept_map"
SOURCE_CODE = "source_code"
SOURCE_VOCABULARY_ID = "source_vocabulary_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_stcm(table: Optional[str]) -> bool:
    return _norm(table) == SOURCE_TO_CONCEPT_MAP


def _is_literal(node: exp.Expression) -> bool:
    return isinstance(node, exp.Literal)


def _extract_filter_columns(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Dict[str, Set[str]]:
    """
    Collect filtered columns per table.

    Returns:
        Dict[table_name -> set(column_names)]
    """
    result: Dict[str, Set[str]] = {}

    # Check both WHERE and JOIN clauses
    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.In, exp.Like, exp.ILike)):
            continue

        # --- Equality ---
        if isinstance(node, exp.EQ):
            left, right = node.this, node.expression

            # Check for column-to-column (JOIN conditions)
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                # Check both sides - either could be source_code
                for col_node in [left, right]:
                    table, col = resolve_table_col(col_node, aliases)
                    if _norm(col) == SOURCE_CODE:
                        # This column is source_code, track it
                        if not table:
                            unique_tables = set(aliases.values())
                            if len(unique_tables) == 1:
                                table = list(unique_tables)[0]

                        if table:
                            table = _norm(table)
                            result.setdefault(table, set()).add(SOURCE_CODE)
                continue  # Done processing this column-to-column comparison

            # Check for literal comparisons
            if isinstance(left, exp.Column) and _is_literal(right):
                table, col = resolve_table_col(left, aliases)
            elif isinstance(right, exp.Column) and _is_literal(left):
                table, col = resolve_table_col(right, aliases)
            else:
                continue

            # Handle unqualified columns - assign to the only table if there's just one
            if not table:
                # Get unique table names (excluding aliases)
                unique_tables = set(aliases.values())
                if len(unique_tables) == 1:
                    table = list(unique_tables)[0]
                else:
                    continue  # ambiguous, skip

            table = _norm(table)
            col = _norm(col)
            if table and col:
                result.setdefault(table, set()).add(col)

        # --- IN ---
        elif isinstance(node, exp.In):
            col_node = node.this

            if not isinstance(col_node, exp.Column):
                continue

            # Only consider literal IN lists
            if not all(isinstance(v, exp.Literal) for v in (node.expressions or [])):
                continue

            table, col = resolve_table_col(col_node, aliases)

            # Handle unqualified
            if not table:
                unique_tables = set(aliases.values())
                if len(unique_tables) == 1:
                    table = list(unique_tables)[0]
                else:
                    continue

            table = _norm(table)
            col = _norm(col)
            if table and col:
                result.setdefault(table, set()).add(col)

        # --- LIKE / ILIKE ---
        elif isinstance(node, (exp.Like, exp.ILike)):
            left = node.this
            right = node.expression

            if not (isinstance(left, exp.Column) and _is_literal(right)):
                continue

            table, col = resolve_table_col(left, aliases)

            # Handle unqualified
            if not table:
                unique_tables = set(aliases.values())
                if len(unique_tables) == 1:
                    table = list(unique_tables)[0]
                else:
                    continue

            table = _norm(table)
            col = _norm(col)
            if table and col:
                result.setdefault(table, set()).add(col)

    return result


# --- Detection -------------------------------------------------------------

def _find_source_code_predicate(
    tree: exp.Expression, aliases: Dict[str, str]
) -> Optional[tuple]:
    """Return (predicate_sql, qualifier) for the first source_code filter
    on source_to_concept_map. Qualifier is the alias/table prefix (or None
    when unqualified).
    """
    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.In, exp.Like, exp.ILike)):
            continue

        col_node = None
        if isinstance(node, exp.EQ):
            for cand in (node.this, node.expression):
                if isinstance(cand, exp.Column):
                    table, col = resolve_table_col(cand, aliases)
                    if _norm(col) == SOURCE_CODE and _is_stcm(table or ""):
                        col_node = cand
                        break
        elif isinstance(node, exp.In):
            cand = node.this
            if isinstance(cand, exp.Column):
                table, col = resolve_table_col(cand, aliases)
                if _norm(col) == SOURCE_CODE and _is_stcm(table or ""):
                    col_node = cand
        elif isinstance(node, (exp.Like, exp.ILike)):
            cand = node.this
            if isinstance(cand, exp.Column):
                table, col = resolve_table_col(cand, aliases)
                if _norm(col) == SOURCE_CODE and _is_stcm(table or ""):
                    col_node = cand

        if col_node is None:
            continue

        qualifier = col_node.table if col_node.table else None
        return node.sql(), qualifier

    return None


def _find_violations(
    sql: str, tree: exp.Expression, aliases: Dict[str, str]
) -> List[tuple]:
    """Return list of (message, patch_or_none) tuples."""
    issues: List[tuple] = []
    seen: Set[str] = set()

    filters_by_table = _extract_filter_columns(tree, aliases)

    for table_name, cols in filters_by_table.items():
        if not _is_stcm(table_name):
            continue

        has_code = SOURCE_CODE in cols
        has_vocab = SOURCE_VOCABULARY_ID in cols

        if has_code and not has_vocab:
            key = f"{table_name}:source_code"
            if key in seen:
                continue
            seen.add(key)

            message = (
                f"Filtering source_to_concept_map.{SOURCE_CODE} without "
                f"{SOURCE_VOCABULARY_ID}. "
                f"Source codes are not unique across vocabularies. "
                f"Add source_vocabulary_id to disambiguate."
            )

            # Build a structured ADD patch right after the source_code
            # predicate. The vocabulary_id value is unknown at static-analysis
            # time, so emit a `<vocab>` placeholder for the outer LLM /
            # operator to fill in.
            patch = None
            pred = _find_source_code_predicate(tree, aliases)
            if pred is not None:
                predicate_sql, qualifier = pred
                span = locate(sql, predicate_sql)
                if span is not None:
                    qual = f"{qualifier}." if qualifier else ""
                    patch = patch_add(
                        span[1],
                        f" AND {qual}source_vocabulary_id = '<vocab>'",
                    )

            issues.append((message, patch))

    return issues


# --- Rule ------------------------------------------------------------------

@register
class SourceToConceptMapValidationRule(Rule):
    """Ensures proper filtering of source_to_concept_map."""

    rule_id = "concept_standardization.source_to_concept_map_validation"
    name = "Source to Concept Map Validation"
    description = (
        "Requires source_vocabulary_id when filtering source_code "
        "to avoid ambiguity across vocabularies."
    )
    severity = Severity.WARNING
    suggested_fix = "ADD: `AND source_vocabulary_id = '<vocab>'` alongside any source_code filter. source_code is unique only within a source_vocabulary_id."
    long_description = (
        "source_to_concept_map is the per-site translation table from "
        "source codes to OMOP standard concepts. Source codes are not "
        "globally unique: 'R51' can exist in ICD10CM (headache), ICD9CM, "
        "and some local billing vocabularies. Filtering on source_code "
        "alone can pick up matches from unrelated vocabularies, returning "
        "incorrect target_concept_ids. Always pair source_code with "
        "source_vocabulary_id so the match is unambiguous."
    )
    example_bad = (
        "SELECT target_concept_id\n"
        "FROM source_to_concept_map\n"
        "WHERE source_code = 'R51';"
    )
    example_good = (
        "SELECT target_concept_id\n"
        "FROM source_to_concept_map\n"
        "WHERE source_code = 'R51'\n"
        "  AND source_vocabulary_id = 'ICD10CM';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        if "source_to_concept_map" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, SOURCE_TO_CONCEPT_MAP):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(sql, tree, aliases)

            for msg, patch in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix_patch=patch,
                    )
                )

        return violations


__all__ = ["SourceToConceptMapValidationRule"]

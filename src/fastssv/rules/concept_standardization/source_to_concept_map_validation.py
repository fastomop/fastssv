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
    uses_table,
)
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

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues = []
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

            issues.append(
                f"Filtering source_to_concept_map.{SOURCE_CODE} without "
                f"{SOURCE_VOCABULARY_ID}. "
                f"Source codes are not unique across vocabularies. "
                f"Add source_vocabulary_id to disambiguate."
            )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class SourceToConceptMapValidationRule(Rule):
    """Ensures proper filtering of source_to_concept_map."""

    rule_id = "semantic.source_to_concept_map_validation"
    name = "Source to Concept Map Validation"
    description = (
        "Requires source_vocabulary_id when filtering source_code "
        "to avoid ambiguity across vocabularies."
    )
    severity = Severity.WARNING 
    suggested_fix = (
        "Add source_vocabulary_id filter alongside source_code."
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

            if not uses_table(tree, SOURCE_TO_CONCEPT_MAP):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["SourceToConceptMapValidationRule"]
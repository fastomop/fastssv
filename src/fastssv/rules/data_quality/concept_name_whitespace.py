"""Concept Name Whitespace Rule.

OMOP semantic rule VOCAB_037:
Some concept_name values in the OMOP vocabulary may have trailing whitespace.
Queries using exact equality (=) on concept_name should use TRIM() or RTRIM()
to avoid silent mismatches.

The Problem:
    OMOP vocabulary data sometimes contains concept names with trailing whitespace:
    - 'Type 2 diabetes mellitus ' (note the trailing space)
    - 'Metformin  ' (multiple trailing spaces)

    When queries use exact equality (=) without trimming, they may fail to match:
    - concept_name = 'Metformin' won't match 'Metformin  ' (with trailing spaces)
    - This causes silent failures - no error, just missing results

    This is particularly problematic because:
    - Users don't expect whitespace in concept names
    - The mismatch is invisible in most query tools
    - Data quality varies across vocabulary versions

Violation patterns:
    -- WRONG: Exact equality without TRIM
    SELECT concept_id
    FROM concept
    WHERE concept_name = 'Type 2 diabetes mellitus'
      AND standard_concept = 'S'
    -- May miss concepts with trailing whitespace

    -- WRONG: IN clause without TRIM
    SELECT concept_id
    FROM concept
    WHERE concept_name IN ('Metformin', 'Insulin', 'Aspirin')
    -- May miss some concepts

    -- WRONG: Using in JOIN condition
    SELECT *
    FROM drug_exposure de
    JOIN concept c
      ON c.concept_name = 'Metformin'
      AND c.concept_id = de.drug_concept_id
    -- Brittle join condition

Correct patterns:
    -- CORRECT: Use TRIM/RTRIM
    SELECT concept_id
    FROM concept
    WHERE TRIM(concept_name) = 'Type 2 diabetes mellitus'
      AND standard_concept = 'S'

    -- CORRECT: Use RTRIM (right trim only)
    SELECT concept_id
    FROM concept
    WHERE RTRIM(concept_name) = 'Type 2 diabetes mellitus'
      AND standard_concept = 'S'

    -- CORRECT: Use BTRIM (PostgreSQL)
    SELECT concept_id
    FROM concept
    WHERE BTRIM(concept_name) = 'Type 2 diabetes mellitus'
      AND standard_concept = 'S'

    -- CORRECT: Use LIKE (less precise but handles whitespace)
    SELECT concept_id
    FROM concept
    WHERE concept_name LIKE 'Type 2 diabetes mellitus'
      AND standard_concept = 'S'

    -- ACCEPTABLE: Inequality operators (!=, <, >, etc.)
    SELECT concept_id
    FROM concept
    WHERE concept_name != 'Unknown'
    -- Not recommended but doesn't cause silent failures
"""

from typing import List, Optional, Set

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

TRIM_FUNCTIONS = {"trim", "rtrim", "ltrim", "btrim"}


# --- Helpers ---------------------------------------------------------------

def _norm(val: Optional[str]) -> Optional[str]:
    return normalize_name(val) if val else None


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        if hasattr(node, "name"):
            return node.name
        return str(node.this)
    return None


def _get_table_alias(table: exp.Table) -> str:
    alias_expr = table.args.get("alias")
    return alias_expr.name if alias_expr else table.name


def _is_concept_name_column(col: exp.Column, aliases: dict) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    # Check column name first
    if _norm(col_name) != "concept_name":
        return False

    # If table is explicitly specified, it must be "concept"
    if table:
        return _norm(table) == "concept"

    # For unqualified columns, check if concept table exists in query
    return "concept" in [_norm(v) for v in aliases.values()]


def _is_wrapped_in_trim(node: exp.Expression) -> bool:
    """Check if node is wrapped anywhere inside a TRIM-like function."""
    current = node.parent

    while current:
        if isinstance(current, exp.Func):
            func_name = _norm(current.sql_name())
            if func_name in TRIM_FUNCTIONS:
                return True
        current = current.parent

    return False


# --- Core detection --------------------------------------------------------

def _find_concept_name_equalities(
    tree: exp.Expression,
    aliases: dict,
) -> List[str]:
    violations: List[str] = []

    # --- EQ ---
    for eq in tree.find_all(exp.EQ):
        left, right = eq.this, eq.expression

        for col_side, val_side in [(left, right), (right, left)]:
            if not isinstance(col_side, exp.Column):
                continue

            if not _is_concept_name_column(col_side, aliases):
                continue

            # If either side is TRIM-wrapped → OK
            if _is_wrapped_in_trim(col_side) or _is_wrapped_in_trim(val_side):
                continue

            violations.append(eq.sql())
            break

    # --- IN ---
    for in_expr in tree.find_all(exp.In):
        col = in_expr.this

        if not isinstance(col, exp.Column):
            continue

        if not _is_concept_name_column(col, aliases):
            continue

        # Column wrapped in TRIM → OK
        if _is_wrapped_in_trim(col):
            continue

        # If ANY value is TRIM-wrapped → consider safe
        if any(_is_wrapped_in_trim(expr) for expr in in_expr.expressions or []):
            continue

        violations.append(in_expr.sql())

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ConceptNameWhitespaceRule(Rule):
    """Detect concept_name equality filters without TRIM."""

    rule_id = "data_quality.concept_name_whitespace"
    name = "Concept Name Whitespace"

    description = (
        "concept_name values may contain trailing whitespace. "
        "Exact equality without TRIM may silently fail."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use TRIM(concept_name) = 'value' or RTRIM(concept_name) = 'value', "
        "or use LIKE for safer matching."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "concept_name" not in sql.lower():
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

            # Safer: detect concept table directly from AST
            has_concept = any(
                _norm(t.name) == "concept"
                for t in tree.find_all(exp.Table)
            )

            if not has_concept:
                continue

            contexts = _find_concept_name_equalities(tree, aliases)

            for context in contexts:
                if context in seen:
                    continue
                seen.add(context)

                violations.append(
                    self.create_violation(
                        message=(
                            "concept_name used with exact equality without TRIM. "
                            "Trailing whitespace may cause mismatches."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=(
                            "Use TRIM(concept_name) = 'value' or RTRIM(concept_name) = 'value'"
                        ),
                        details={"context": context},
                    )
                )

        return violations


__all__ = ["ConceptNameWhitespaceRule"]

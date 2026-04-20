"""Concept Alias Reuse Validation Rule.

OMOP semantic rule JOIN_009:
When a clinical table joins to concept for both the primary *_concept_id AND the
*_source_concept_id, each join must use a separate alias for the concept table.
Reusing the same alias corrupts the ON clause for one of the joins.

The Problem:
    OMOP clinical tables have both standard concept_id columns (e.g., condition_concept_id)
    and source concept_id columns (e.g., condition_source_concept_id). When you need to
    join to concept for BOTH, you must use separate aliases. Reusing the same alias causes:

    1. Ambiguous references: Which c.concept_id does the ON clause refer to?
    2. Last-join-wins: Second JOIN overwrites/conflicts with first JOIN
    3. Wrong data returned: You get source when you wanted standard (or vice versa)
    4. Silent errors: SQL doesn't error, but results are semantically incorrect

Violation pattern:
    SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    JOIN concept c ON co.condition_source_concept_id = c.concept_id
    -- WRONG: Same alias 'c' used for both standard and source concept joins

Correct pattern:
    SELECT c1.concept_name AS standard_name, c2.concept_name AS source_name
    FROM condition_occurrence co
    JOIN concept c1 ON co.condition_concept_id = c1.concept_id
    JOIN concept c2 ON co.condition_source_concept_id = c2.concept_id
"""

from typing import Dict, List, Optional, Set, Tuple

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


# --- Constants -------------------------------------------------------------

CONCEPT = "concept"
CONCEPT_ID = "concept_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_concept(table: Optional[str]) -> bool:
    return _norm(table) == CONCEPT


def _is_source_concept_id(col: Optional[str]) -> bool:
    """Check if column is a source concept_id (e.g., condition_source_concept_id)."""
    c = _norm(col)
    return c is not None and c.endswith("_source_concept_id")


def _is_type_concept_id(col: Optional[str]) -> bool:
    """Check if column is a type concept_id (e.g., condition_type_concept_id)."""
    c = _norm(col)
    return c is not None and c.endswith("_type_concept_id")


def _is_primary_concept_id(col: Optional[str]) -> bool:
    """Check if column is a primary concept_id (e.g., condition_concept_id)."""
    c = _norm(col)
    if not c:
        return False

    return (c.endswith("_concept_id") and
            not c.endswith("_source_concept_id") and
            not c.endswith("_type_concept_id"))


def _is_concept_id(col: Optional[str]) -> bool:
    return _norm(col) == CONCEPT_ID


def _is_concept_id_like(col: Optional[str]) -> bool:
    """Check if column is any type of concept_id column."""
    return (_is_primary_concept_id(col) or
            _is_source_concept_id(col) or
            _is_type_concept_id(col))


def _extract_all_equalities(tree: exp.Expression) -> List[exp.EQ]:
    """
    Extract equality conditions from JOIN ON and WHERE clauses.
    """
    return list(tree.find_all(exp.EQ))


# --- Detection -------------------------------------------------------------

def _detect_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Tuple[
    List[Tuple[str, str, List[str], str]],  # errors: (alias, source, cols, error_type)
    List[Tuple[str, str, List[str]]],       # warnings: (alias, source, cols)
]:
    """
    Detect concept alias reuse issues.

    Returns:
        errors: critical violations (alias, source_table, columns, error_type)
        warnings: non-critical but suspicious patterns (alias, source_table, columns)
    """

    # concept_alias -> [(source_table_alias, source_column)]
    alias_usage: Dict[str, List[Tuple[str, str]]] = {}

    for eq in _extract_all_equalities(tree):
        left, right = eq.this, eq.expression

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt_norm = _normalize_table(lt)
        rt_norm = _normalize_table(rt)

        concept_alias = None
        source_alias = None
        source_col = None
        concept_col = None

        # Identify concept side and ensure it joins on concept_id
        if _is_concept(rt_norm) and _is_concept_id(rc):
            concept_alias = right.table or rt
            source_alias = left.table or lt
            source_col = lc
            concept_col = rc

        elif _is_concept(lt_norm) and _is_concept_id(lc):
            concept_alias = left.table or lt
            source_alias = right.table or rt
            source_col = rc
            concept_col = lc

        if not (concept_alias and source_alias and source_col):
            continue

        concept_alias = str(concept_alias)
        source_alias = str(source_alias)

        if concept_alias not in alias_usage:
            alias_usage[concept_alias] = []

        alias_usage[concept_alias].append((source_alias, source_col))

    errors: List[Tuple[str, str, List[str], str]] = []
    warnings: List[Tuple[str, str, List[str]]] = []

    seen_errors: Set[Tuple[str, str, Tuple[str, ...]]] = set()
    seen_warnings: Set[Tuple[str, str, Tuple[str, ...]]] = set()

    for concept_alias, usages in alias_usage.items():
        if len(usages) <= 1:
            continue

        # Group by source table alias
        by_source: Dict[str, List[str]] = {}
        for source_alias, col in usages:
            by_source.setdefault(source_alias, []).append(col)

        # --- Per-source-table analysis ---
        for source_alias, cols in by_source.items():
            unique_cols = sorted(set(cols))

            if len(unique_cols) <= 1:
                continue

            has_primary = any(_is_primary_concept_id(c) for c in unique_cols)
            has_source = any(_is_source_concept_id(c) for c in unique_cols)
            has_type = any(_is_type_concept_id(c) for c in unique_cols)

            key = (concept_alias, source_alias, tuple(unique_cols))

            # ERROR: mixing primary + source concept_id (JOIN_009)
            if has_primary and has_source:
                if key not in seen_errors:
                    errors.append((concept_alias, source_alias, unique_cols, "primary_source"))
                    seen_errors.add(key)

            # ERROR: mixing primary + type concept_id (JOIN_010)
            elif has_primary and has_type:
                if key not in seen_errors:
                    errors.append((concept_alias, source_alias, unique_cols, "primary_type"))
                    seen_errors.add(key)

            # WARNING: multiple concept_id columns (other combinations)
            elif any(_is_concept_id_like(c) for c in unique_cols):
                if key not in seen_warnings:
                    warnings.append((concept_alias, source_alias, unique_cols))
                    seen_warnings.add(key)

        # --- Cross-table reuse detection (WARNING) ---
        source_tables = sorted(set(s for s, _ in usages))
        if len(source_tables) > 1:
            key = (concept_alias, "MULTI_TABLE", tuple(source_tables))
            if key not in seen_warnings:
                warnings.append((concept_alias, "multiple_tables", source_tables))
                seen_warnings.add(key)

    return errors, warnings


# --- Rule ------------------------------------------------------------------

@register
class ConceptAliasReuseValidationRule(Rule):
    """
    Production-grade OMOP concept alias reuse validation.

    Enforces:
    - ERROR: same alias for *_concept_id and *_source_concept_id (JOIN_009)
    - ERROR: same alias for *_concept_id and *_type_concept_id (JOIN_010)
    - WARNING: same alias reused for multiple concept_id columns
    - WARNING: same alias reused across multiple source tables
    """

    rule_id = "joins.concept_alias_reuse_validation"
    name = "Concept Alias Reuse Validation"

    description = (
        "Ensures each concept join uses a distinct alias when joining multiple "
        "concept_id columns (primary, source, type). Prevents ambiguous joins, "
        "semantic confusion, and incorrect results."
    )

    severity = Severity.ERROR

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        if "concept" not in sql.lower():
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree or not has_table_reference(tree, CONCEPT):
                continue

            aliases = extract_aliases(tree)
            errors, warnings = _detect_violations(tree, aliases)

            # --- ERRORS ---
            for concept_alias, source_table, columns, error_type in errors:
                col_list = ", ".join(columns)

                # Generate context-aware messages based on error type
                if error_type == "primary_source":
                    message = (
                        f"Alias '{concept_alias}' reused for primary and source concept_id "
                        f"columns in table '{source_table}' ({col_list}). "
                        f"Standard and source concepts represent different semantics."
                    )
                    suggested_fix = (
                        "Use separate aliases for standard and source concepts:\n"
                        f"  JOIN concept c_standard ON {source_table}.{columns[0]} = c_standard.concept_id\n"
                        f"  JOIN concept c_source ON {source_table}.{columns[1]} = c_source.concept_id"
                    )
                    details_type = "primary_source_alias_conflict"
                elif error_type == "primary_type":
                    message = (
                        f"Alias '{concept_alias}' reused for primary and type concept_id "
                        f"columns in table '{source_table}' ({col_list}). "
                        f"Clinical concepts and type concepts (provenance) are semantically different."
                    )
                    suggested_fix = (
                        "Use separate aliases for clinical and type concepts:\n"
                        f"  JOIN concept c_clinical ON {source_table}.{columns[0]} = c_clinical.concept_id\n"
                        f"  JOIN concept c_type ON {source_table}.{columns[1]} = c_type.concept_id\n\n"
                        f"Note: Type concepts represent provenance (e.g., 'EHR', 'Claim'), not clinical meaning."
                    )
                    details_type = "primary_type_alias_conflict"
                else:
                    message = (
                        f"Alias '{concept_alias}' reused for multiple concept_id columns "
                        f"in table '{source_table}' ({col_list})."
                    )
                    suggested_fix = (
                        "Use separate aliases:\n"
                        f"  JOIN concept c1 ON {source_table}.{columns[0]} = c1.concept_id\n"
                        f"  JOIN concept c2 ON {source_table}.{columns[1]} = c2.concept_id"
                    )
                    details_type = "alias_conflict"

                violations.append(
                    self.create_violation(
                        message=message,
                        suggested_fix=suggested_fix,
                        details={
                            "type": details_type,
                            "alias": concept_alias,
                            "table": source_table,
                            "columns": columns,
                            "error_type": error_type,
                        },
                    )
                )

            # --- WARNINGS ---
            for concept_alias, source_table, columns in warnings:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=(
                            f"Alias '{concept_alias}' reused across multiple concept joins "
                            f"({', '.join(columns)})."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=(
                            "Consider using separate aliases for clarity:\n"
                            f"  JOIN concept c1 ON ... = c1.concept_id\n"
                            f"  JOIN concept c2 ON ... = c2.concept_id"
                        ),
                        details={
                            "type": "alias_reuse_warning",
                            "alias": concept_alias,
                            "context": source_table,
                            "columns": columns,
                        },
                    )
                )

        return violations


__all__ = ["ConceptAliasReuseValidationRule"]

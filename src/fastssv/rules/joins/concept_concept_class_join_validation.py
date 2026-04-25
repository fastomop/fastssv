"""Concept to Concept Class Join Validation Rule.

OMOP semantic rule JOIN_013:
concept joins to concept_class via concept.concept_class_id = concept_class.concept_class_id
(VARCHAR to VARCHAR). Joining on concept_id = concept_class_concept_id or
concept_class_id = concept_class_name is incorrect.

The Problem:
    The concept_class table is a reference table in OMOP that describes concept classes
    (e.g., 'Clinical Drug', 'Ingredient', 'Procedure', 'Clinical Finding'). The concept
    table references concept_class via the concept_class_id column (VARCHAR FK to
    concept_class.concept_class_id).

    Common mistakes:
    1. Joining concept_id (INTEGER) to concept_class_concept_id (INTEGER)
       - Type matches but semantics are wrong
    2. Joining concept_class_id to concept_class_name
       - Both VARCHAR but joining FK to description instead of PK
    3. Other column pairs that are semantically incorrect

Violation pattern:
    SELECT * FROM concept c
    JOIN concept_class cc ON c.concept_id = cc.concept_class_concept_id
    -- WRONG: concept_id is not the concept_class reference

Correct pattern:
    SELECT * FROM concept c
    JOIN concept_class cc ON c.concept_class_id = cc.concept_class_id
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
from fastssv.core.patch import build_join_replace_patch
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT = "concept"
CONCEPT_CLASS = "concept_class"
CONCEPT_CLASS_ID = "concept_class_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_concept(table: Optional[str]) -> bool:
    return _norm(table) == CONCEPT


def _is_concept_class(table: Optional[str]) -> bool:
    return _norm(table) == CONCEPT_CLASS


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_eq_groups(tree: exp.Expression) -> List[List[exp.EQ]]:
    """
    Group equality conditions from JOIN ON and WHERE clauses.
    Each group represents a logical join/filter context.
    """
    groups: List[List[exp.EQ]] = []

    # JOIN ON groups
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause:
            eqs = list(on_clause.find_all(exp.EQ))
            if eqs:
                groups.append(eqs)

    # WHERE group
    where = tree.args.get("where")
    if where:
        eqs = list(where.find_all(exp.EQ))
        if eqs:
            groups.append(eqs)

    return groups


def _is_valid_column_join(
    lt: Optional[str],
    rt: Optional[str],
) -> bool:
    """
    Ensure we are dealing with a real join:
    - both sides have tables
    - tables are different
    """
    return lt is not None and rt is not None and lt != rt


# --- Detection -------------------------------------------------------------

def _detect_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Tuple[
    List[Tuple[str, str, str, str]],
    List[Tuple[str, str, str, str]],
]:
    errors: List[Tuple[str, str, str, str]] = []
    warnings: List[Tuple[str, str, str, str]] = []

    seen_errors: Set[Tuple[str, str, str, str]] = set()
    seen_warnings: Set[Tuple[str, str, str, str]] = set()

    groups = _extract_eq_groups(tree)

    for group in groups:
        has_correct_join = False
        candidates: List[Tuple[str, str, str, str]] = []

        for eq in group:
            left, right = eq.this, eq.expression

            # Only consider column-to-column joins
            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue

            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            if not (lt and lc and rt and rc):
                continue

            lt_norm = _normalize_table(lt)
            rt_norm = _normalize_table(rt)

            # Ensure it's a real join (not filter or same-table condition)
            if not _is_valid_column_join(lt_norm, rt_norm):
                continue

            for (t1, c1, t2, c2) in [
                (lt_norm, lc, rt_norm, rc),
                (rt_norm, rc, lt_norm, lc),
            ]:
                if _is_concept(t1) and _is_concept_class(t2):

                    if _is_col(c1, CONCEPT_CLASS_ID) and _is_col(c2, CONCEPT_CLASS_ID):
                        has_correct_join = True
                    else:
                        candidates.append((t1, c1, t2, c2))

        if not candidates:
            continue

        for c_table, c_col, cc_table, cc_col in candidates:
            key = (c_table, c_col, cc_table, cc_col)

            # --- ERROR: incorrect FK usage ---
            if _is_col(c_col, CONCEPT_CLASS_ID) and not _is_col(cc_col, CONCEPT_CLASS_ID):
                if key not in seen_errors:
                    errors.append(key)
                    seen_errors.add(key)

            # --- WARNING: suspicious relationship ---
            elif not has_correct_join:
                if key not in seen_warnings:
                    warnings.append(key)
                    seen_warnings.add(key)

    return errors, warnings


# --- Rule ------------------------------------------------------------------

@register
class ConceptConceptClassJoinValidationRule(Rule):
    """
    Production-grade validation for concept ↔ concept_class joins.

    Enforces:
    - ERROR: concept.concept_class_id must join to concept_class.concept_class_id
    - WARNING: suspicious joins involving concept_class without proper alignment
    """

    rule_id = "joins.concept_concept_class_join_validation"
    name = "Concept to Concept Class Join Validation"

    description = (
        "If concept is joined to concept_class, the relationship should use "
        "concept.concept_class_id = concept_class.concept_class_id."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: the concept ↔ concept_class ON clause WITH `concept.concept_class_id = concept_class.concept_class_id`. Joining via vocabulary_id, domain_id, or any other column is incorrect — concept_class.concept_class_id is the only FK target."

    example_bad = (
        "SELECT c.concept_id FROM concept c\n"
        "JOIN concept_class cc ON c.vocabulary_id = cc.concept_class_id;"
    )
    example_good = (
        "SELECT c.concept_id FROM concept c\n"
        "JOIN concept_class cc ON c.concept_class_id = cc.concept_class_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-filter
        sql_lower = sql.lower()
        if "concept" not in sql_lower or "concept_class" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not (has_table_reference(tree, CONCEPT) and has_table_reference(tree, CONCEPT_CLASS)):
                continue

            aliases = extract_aliases(tree)
            errors, warnings = _detect_violations(tree, aliases)

            # --- ERRORS ---
            for c_table, c_col, cc_table, cc_col in errors:
                fix_text = (
                    f"REPLACE: `{c_table}.{c_col} = {cc_table}.{cc_col}` "
                    f"WITH `{c_table}.concept_class_id = {cc_table}.concept_class_id`."
                )
                violations.append(
                    self.create_violation(
                        message=(
                            f"Invalid join: {c_table}.{c_col} = {cc_table}.{cc_col}. "
                            f"Expected {c_table}.concept_class_id = {cc_table}.concept_class_id."
                        ),
                        suggested_fix=fix_text,
                        suggested_fix_patch=build_join_replace_patch(
                            sql, c_table, c_col, cc_table, cc_col,
                            "concept_class_id", "concept_class_id", fix_text,
                            aliases=aliases,
                        ),
                        details={
                            "type": "invalid_concept_concept_class_join",
                            "concept_column": c_col,
                            "concept_class_column": cc_col,
                        },
                    )
                )

            # --- WARNINGS ---
            for c_table, c_col, cc_table, cc_col in warnings:
                fix_text = (
                    f"REPLACE: `{c_table}.{c_col} = {cc_table}.{cc_col}` "
                    f"WITH `{c_table}.concept_class_id = {cc_table}.concept_class_id`."
                )
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=(
                            f"Suspicious join: {c_table}.{c_col} = {cc_table}.{cc_col}. "
                            f"Expected concept.concept_class_id = concept_class.concept_class_id."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=fix_text,
                        suggested_fix_patch=build_join_replace_patch(
                            sql, c_table, c_col, cc_table, cc_col,
                            "concept_class_id", "concept_class_id", fix_text,
                            aliases=aliases,
                        ),
                        details={
                            "type": "suspicious_concept_concept_class_join",
                        },
                    )
                )

        return violations


__all__ = ["ConceptConceptClassJoinValidationRule"]

"""Concept to Vocabulary Join Validation Rule.

OMOP semantic rule JOIN_011:
concept joins to vocabulary via concept.vocabulary_id = vocabulary.vocabulary_id
(VARCHAR to VARCHAR). Joining on concept_id = vocabulary_concept_id or
vocabulary_id = vocabulary_name is incorrect.

The Problem:
    The vocabulary table is a reference table in OMOP that describes vocabularies
    (e.g., 'SNOMED', 'ICD10CM', 'RxNorm'). The concept table references vocabulary
    via the vocabulary_id column (VARCHAR FK to vocabulary.vocabulary_id).

    Common mistakes:
    1. Joining concept_id (INTEGER) to vocabulary_concept_id (INTEGER)
       - Type matches but semantics are wrong
    2. Joining vocabulary_id to vocabulary_name
       - Both VARCHAR but joining FK to description instead of PK
    3. Other column pairs that are semantically incorrect

Violation pattern:
    SELECT * FROM concept c
    JOIN vocabulary v ON c.concept_id = v.vocabulary_concept_id
    -- WRONG: concept_id is not the vocabulary reference

Correct pattern:
    SELECT * FROM concept c
    JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_id
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.patch import build_join_replace_patch
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
VOCABULARY = "vocabulary"
VOCABULARY_ID = "vocabulary_id"
CONCEPT_ID = "concept_id"
VOCABULARY_CONCEPT_ID = "vocabulary_concept_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_concept(table: Optional[str]) -> bool:
    return _norm(table) == CONCEPT


def _is_vocabulary(table: Optional[str]) -> bool:
    return _norm(table) == VOCABULARY


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_eq_groups(tree: exp.Expression) -> List[List[exp.EQ]]:
    """
    Group equality conditions by JOIN and WHERE clauses.
    Each group represents a logical join context.
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


# --- Detection -------------------------------------------------------------

def _detect_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Tuple[
    List[Tuple[str, str, str, str]],  # errors
    List[Tuple[str, str, str, str]],  # warnings
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

            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue

            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            if not (lt and lc and rt and rc):
                continue

            lt = _normalize_table(lt)
            rt = _normalize_table(rt)

            for (t1, c1, t2, c2) in [
                (lt, lc, rt, rc),
                (rt, rc, lt, lc),
            ]:
                if _is_concept(t1) and _is_vocabulary(t2):

                    # Correct join
                    if _is_col(c1, VOCABULARY_ID) and _is_col(c2, VOCABULARY_ID):
                        has_correct_join = True

                    else:
                        candidates.append((t1, c1, t2, c2))

        # Evaluate group
        if not candidates:
            continue

        for concept_table, concept_col, vocab_table, vocab_col in candidates:

            key = (concept_table, concept_col, vocab_table, vocab_col)

            # --- ERROR: clearly wrong joins ---
            if (
                (_is_col(concept_col, CONCEPT_ID) and _is_col(vocab_col, VOCABULARY_CONCEPT_ID))
                or (_is_col(concept_col, VOCABULARY_ID) and not _is_col(vocab_col, VOCABULARY_ID))
            ):
                if key not in seen_errors:
                    errors.append(key)
                    seen_errors.add(key)

            # --- WARNING: suspicious but not strictly invalid ---
            elif not has_correct_join:
                if key not in seen_warnings:
                    warnings.append(key)
                    seen_warnings.add(key)

    return errors, warnings


# --- Rule ------------------------------------------------------------------

@register
class ConceptVocabularyJoinValidationRule(Rule):
    """
    Production-grade validation for concept ↔ vocabulary joins.

    Enforces:
    - ERROR: clearly incorrect joins (e.g., concept_id ↔ vocabulary_concept_id)
    - WARNING: suspicious joins without vocabulary_id alignment
    """

    rule_id = "joins.concept_vocabulary_join_validation"
    name = "Concept to Vocabulary Join Validation"

    description = (
        "If concept is joined to vocabulary, the join should use vocabulary_id. "
        "Other joins may be incorrect or ambiguous."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: the concept ↔ vocabulary ON clause WITH `concept.vocabulary_id = vocabulary.vocabulary_id`. Joining via domain_id, concept_class_id, or any other column is incorrect — vocabulary.vocabulary_id is the only FK target."

    example_bad = (
        "SELECT c.concept_id FROM concept c\n"
        "JOIN vocabulary v ON c.domain_id = v.vocabulary_id;"
    )
    example_good = (
        "SELECT c.concept_id FROM concept c\n"
        "JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        if "concept" not in sql.lower() or "vocabulary" not in sql.lower():
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not (has_table_reference(tree, CONCEPT) and has_table_reference(tree, VOCABULARY)):
                continue

            aliases = extract_aliases(tree)

            errors, warnings = _detect_violations(tree, aliases)

            # --- ERRORS ---
            for c_table, c_col, v_table, v_col in errors:
                fix_text = (
                    f"REPLACE: `{c_table}.{c_col} = {v_table}.{v_col}` "
                    f"WITH `{c_table}.vocabulary_id = {v_table}.vocabulary_id`."
                )
                violations.append(
                    self.create_violation(
                        message=(
                            f"Invalid join: {c_table}.{c_col} = {v_table}.{v_col}. "
                            f"Use vocabulary_id = vocabulary_id."
                        ),
                        suggested_fix=fix_text,
                        suggested_fix_patch=build_join_replace_patch(
                            sql, c_table, c_col, v_table, v_col,
                            "vocabulary_id", "vocabulary_id", fix_text,
                            aliases=aliases,
                        ),
                        details={
                            "type": "invalid_concept_vocabulary_join",
                            "concept_column": c_col,
                            "vocabulary_column": v_col,
                        },
                    )
                )

            # --- WARNINGS ---
            for c_table, c_col, v_table, v_col in warnings:
                fix_text = (
                    f"REPLACE: `{c_table}.{c_col} = {v_table}.{v_col}` "
                    f"WITH `{c_table}.vocabulary_id = {v_table}.vocabulary_id`."
                )
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=(
                            f"Suspicious join: {c_table}.{c_col} = {v_table}.{v_col}. "
                            f"Expected vocabulary_id alignment."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=fix_text,
                        suggested_fix_patch=build_join_replace_patch(
                            sql, c_table, c_col, v_table, v_col,
                            "vocabulary_id", "vocabulary_id", fix_text,
                            aliases=aliases,
                        ),
                        details={
                            "type": "suspicious_concept_vocabulary_join",
                        },
                    )
                )

        return violations


__all__ = ["ConceptVocabularyJoinValidationRule"]

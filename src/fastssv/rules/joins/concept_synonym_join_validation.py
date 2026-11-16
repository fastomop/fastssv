"""Concept Synonym Join Validation Rule.

OMOP semantic rule JOIN_029:
concept_synonym rows belong to a concept through the foreign key
concept_synonym.concept_id = concept.concept_id (CDM v5.4).
concept_synonym.language_concept_id = concept.concept_id is also a valid foreign
key (the synonym's language concept).

ERROR: a concept_synonym ↔ concept equality on a key column that is not one of
those foreign keys (e.g. cs.language_concept_id = c.vocabulary_id,
cs.concept_id = c.concept_code), when that concept alias is not also joined on
concept_id in the same SELECT.

WARNING: a text match between the two tables (concept_synonym_name against
concept_name / concept_code, bare or wrapped in LOWER / UPPER / TRIM). It is a
legitimate lexical lookup when deliberate (find the concepts whose synonym equals
another concept's name), but it is not the key: names are not unique, so used in
place of concept_id it links unrelated concepts. A text predicate on a pair that is
already joined on concept_id is a filter and is not reported.

Violation patterns:
    SELECT * FROM concept_synonym cs
    JOIN concept c ON cs.language_concept_id = c.vocabulary_id   -- ERROR

    SELECT * FROM concept_synonym cs
    JOIN concept c ON cs.concept_synonym_name = c.concept_name   -- WARNING

Correct patterns:
    SELECT * FROM concept_synonym cs
    JOIN concept c ON cs.concept_id = c.concept_id

    SELECT cs.concept_synonym_name, lang.concept_name FROM concept_synonym cs
    JOIN concept lang ON lang.concept_id = cs.language_concept_id
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import CDM_CONVENTIONS, cdm_table, section
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
)
from fastssv.core.patch import build_join_replace_patch
from fastssv.core.registry import register
from fastssv.schemas.cdm_column_types import VARCHAR, get_column_type

from ._join_scope import hoist_joins, scope_qualifier, select_equalities, shadowed_tables


# --- Constants -------------------------------------------------------------

CONCEPT_SYNONYM = "concept_synonym"
CONCEPT = "concept"
CONCEPT_ID = "concept_id"
LANGUAGE_CONCEPT_ID = "language_concept_id"


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_concept_synonym(t: Optional[str]) -> bool:
    return _norm(t) == CONCEPT_SYNONYM


def _is_concept(t: Optional[str]) -> bool:
    return _norm(t) == CONCEPT


def _is_concept_id(c: Optional[str]) -> bool:
    return _norm(c) == CONCEPT_ID


# --- Detection -------------------------------------------------------------


def _is_text_match(cs_col: str, c_col: str) -> bool:
    return get_column_type(CONCEPT_SYNONYM, cs_col) == VARCHAR and get_column_type(CONCEPT, c_col) == VARCHAR


def _detect(tree: exp.Expression) -> List[Tuple[str, str, str, str, bool]]:
    """(left table, left column, right table, right column, is_text_match) for concept_synonym <-> concept equalities
    whose concept source is not also linked by concept_id in the same SELECT (an id join already pins the pair, so
    extra predicates on it are filters). Operands are resolved by lineage, through casts, LOWER / UPPER / TRIM and
    IN-subqueries."""
    violations: List[Tuple[str, str, str, str, bool]] = []
    seen: Set[Tuple[str, str, str, str, bool]] = set()
    shadowed = shadowed_tables(tree)

    for select in tree.find_all(exp.Select):
        linked: Set[Tuple[str, str]] = set()
        invalid: List[Tuple[Tuple[str, str], Tuple[str, str, str, str, bool]]] = []
        for eq in select_equalities(select, shadowed, text=True):
            left, right = eq.left, eq.right
            if left.origin is None or right.origin is None:
                continue
            lt, lc = left.origin
            rt, rc = right.origin
            is_cs_to_c = _is_concept_synonym(lt) and _is_concept(rt)
            is_c_to_cs = _is_concept(lt) and _is_concept_synonym(rt)
            if not (is_cs_to_c or is_c_to_cs):
                continue
            cs_side, c_side = (left, right) if is_cs_to_c else (right, left)
            cs_col, c_col = cs_side.name, c_side.name
            pair = (scope_qualifier(cs_side, select) or CONCEPT_SYNONYM, scope_qualifier(c_side, select) or CONCEPT)
            if cs_col == LANGUAGE_CONCEPT_ID and _is_concept_id(c_col):
                # Valid FK to the synonym's language concept; neither a synonym link nor an error.
                continue
            if _is_concept_id(cs_col) and _is_concept_id(c_col):
                if eq.conjunct:
                    linked.add(pair)
                continue
            key = (lt, lc, rt, rc, _is_text_match(cs_col, c_col))
            invalid.append((pair, key))
        for pair, key in invalid:
            if pair not in linked and key not in seen:
                violations.append(key)
                seen.add(key)
    return violations


# --- Rule ------------------------------------------------------------------


@register
class ConceptSynonymJoinValidationRule(Rule):
    """
    Ensure concept_synonym joins to concept using concept_id.
    """

    rule_id = "joins.concept_synonym_join_validation"
    name = "Concept Synonym Join Validation"

    description = (
        "Ensures concept_synonym joins to concept via concept_id (or language_concept_id = concept_id to look up "
        "the synonym's language). Key-column mismatches are ERRORs; a text match on names is a WARNING because it is "
        "a lexical lookup, not the key, and names are not unique."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: the join target WITH `concept_synonym.concept_id = concept.concept_id`. A name match (concept_synonym_name = concept_name) is only a lexical lookup; names are not unique."
    example_bad = "SELECT cs.concept_synonym_name\nFROM concept c\nJOIN concept_synonym cs ON c.vocabulary_id = cs.language_concept_id;"
    example_good = (
        "SELECT cs.concept_synonym_name\nFROM concept c\nJOIN concept_synonym cs ON c.concept_id = cs.concept_id;"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT c.concept_id, c.concept_name\n"
                "FROM concept_synonym cs\n"
                "JOIN concept c ON cs.concept_synonym_name = c.concept_name;"
            ),
            severity=Severity.WARNING,
            note="A text match on names in place of the concept_id key.",
        ),
    )
    references = (cdm_table("concept_synonym"), section(CDM_CONVENTIONS, "concept-synonyms", "Concept Synonyms"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if CONCEPT_SYNONYM not in sql_lower or CONCEPT not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            tree = hoist_joins(tree)
            # Only used to locate the predicate's text for the REPLACE patch.
            aliases = extract_aliases(tree)
            detected = _detect(tree)

            for lt, lc, rt, rc, text_match in detected:
                left = f"{lt}.{lc}" if lt != "unknown" else lc
                right = f"{rt}.{rc}" if rt != "unknown" else rc
                details = {
                    "type": "concept_synonym_text_match" if text_match else "concept_synonym_invalid_join",
                    "left_table": lt,
                    "left_column": lc,
                    "right_table": rt,
                    "right_column": rc,
                }
                if text_match:
                    # A deliberate lexical lookup and a mistaken key look identical, so this cannot be an ERROR.
                    violations.append(
                        self.create_violation(
                            message=(
                                f"Text match between concept_synonym and concept: {left} = {right}. This is a "
                                f"lexical lookup, not the concept_synonym.concept_id = concept.concept_id key; "
                                f"names are not unique, so it can link unrelated concepts."
                            ),
                            severity=Severity.WARNING,
                            suggested_fix=(
                                f"REPLACE: `{left} = {right}` WITH `{lt}.concept_id = {rt}.concept_id` if the "
                                f"synonym's own concept is meant; keep the text match only for a deliberate "
                                f"lexical lookup."
                            ),
                            details=details,
                        )
                    )
                    continue
                msg = f"Invalid join: {left} → {right}. concept_synonym must join to concept via concept_id."
                fix_text = f"REPLACE: `{left} = {right}` WITH `{lt}.concept_id = {rt}.concept_id`."
                patch = build_join_replace_patch(sql, lt, lc, rt, rc, CONCEPT_ID, CONCEPT_ID, fix_text, aliases=aliases)
                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=patch,
                        details=details,
                    )
                )

        return violations


__all__ = ["ConceptSynonymJoinValidationRule"]

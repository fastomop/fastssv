"""Concept Relationship to Relationship Join Validation Rule.

OMOP semantic rule JOIN_014:
concept_relationship joins to relationship via concept_relationship.relationship_id =
relationship.relationship_id (VARCHAR to VARCHAR). Joining on concept_id_1 or concept_id_2
to relationship_concept_id or other columns is incorrect.

The Problem:
    The relationship table is a reference table in OMOP that describes relationship types
    (e.g., 'Maps to', 'Subsumes', 'Is a', 'Has form'). The concept_relationship table
    references relationship via the relationship_id column (VARCHAR FK to
    relationship.relationship_id).

    Common mistakes:
    1. Joining concept_id_1 or concept_id_2 (INTEGER) to relationship_concept_id (INTEGER)
       - Type matches but semantics are wrong
       - concept_id_1/2 are the concepts being related, not the relationship type
    2. Joining relationship_id to relationship_name
       - Both VARCHAR but joining FK to description instead of PK
    3. Other column pairs that are semantically incorrect

Violation pattern:
    SELECT * FROM concept_relationship cr
    JOIN relationship r ON cr.concept_id_1 = r.relationship_concept_id
    -- WRONG: concept_id_1 is one of the concepts being related, not the relationship type

Correct pattern:
    SELECT * FROM concept_relationship cr
    JOIN relationship r ON cr.relationship_id = r.relationship_id
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

CONCEPT_RELATIONSHIP = "concept_relationship"
RELATIONSHIP = "relationship"
RELATIONSHIP_ID = "relationship_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_concept_relationship(table: Optional[str]) -> bool:
    return _norm(table) == CONCEPT_RELATIONSHIP


def _is_relationship(table: Optional[str]) -> bool:
    return _norm(table) == RELATIONSHIP


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _is_column_equality(eq: exp.EQ) -> bool:
    return isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)


def _extract_join_conditions(join: exp.Join) -> List[exp.EQ]:
    on_clause = join.args.get("on")
    if not on_clause:
        return []
    return [eq for eq in on_clause.find_all(exp.EQ) if _is_column_equality(eq)]


def _extract_where_conditions(tree: exp.Expression) -> List[exp.EQ]:
    where = tree.args.get("where")
    if not where:
        return []
    return [eq for eq in where.find_all(exp.EQ) if _is_column_equality(eq)]


def _involves_target_tables(
    lt: Optional[str],
    rt: Optional[str],
) -> bool:
    lt = _normalize_table(lt)
    rt = _normalize_table(rt)
    return (
        (_is_concept_relationship(lt) and _is_relationship(rt)) or
        (_is_concept_relationship(rt) and _is_relationship(lt))
    )


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

    found_any_join_between_tables = False
    found_valid_fk_join = False

    # --- 1. Explicit JOINs -------------------------------------------------
    for join in tree.find_all(exp.Join):
        conditions = _extract_join_conditions(join)

        for eq in conditions:
            left, right = eq.this, eq.expression

            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            if not (lt and lc and rt and rc):
                continue

            if not _involves_target_tables(lt, rt):
                continue

            found_any_join_between_tables = True

            lt_norm = _normalize_table(lt)
            rt_norm = _normalize_table(rt)

            for (t1, c1, t2, c2) in [
                (lt_norm, lc, rt_norm, rc),
                (rt_norm, rc, lt_norm, lc),
            ]:
                if _is_concept_relationship(t1) and _is_relationship(t2):

                    if _is_col(c1, RELATIONSHIP_ID) and _is_col(c2, RELATIONSHIP_ID):
                        found_valid_fk_join = True
                    else:
                        key = (t1, c1, t2, c2)
                        # ERROR: FK column misused (relationship_id to wrong column)
                        if _is_col(c1, RELATIONSHIP_ID) and not _is_col(c2, RELATIONSHIP_ID):
                            if key not in seen_errors:
                                errors.append(key)
                                seen_errors.add(key)
                        # WARNING: Other suspicious joins
                        else:
                            if key not in seen_warnings:
                                warnings.append(key)
                                seen_warnings.add(key)

    # --- 2. WHERE-based implicit joins -------------------------------------
    where_conditions = _extract_where_conditions(tree)

    for eq in where_conditions:
        left, right = eq.this, eq.expression

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not (lt and lc and rt and rc):
            continue

        if not _involves_target_tables(lt, rt):
            continue

        found_any_join_between_tables = True

        lt_norm = _normalize_table(lt)
        rt_norm = _normalize_table(rt)

        for (t1, c1, t2, c2) in [
            (lt_norm, lc, rt_norm, rc),
            (rt_norm, rc, lt_norm, lc),
        ]:
            if _is_concept_relationship(t1) and _is_relationship(t2):

                if _is_col(c1, RELATIONSHIP_ID) and _is_col(c2, RELATIONSHIP_ID):
                    found_valid_fk_join = True
                else:
                    key = (t1, c1, t2, c2)
                    # ERROR: FK column misused (relationship_id to wrong column)
                    if _is_col(c1, RELATIONSHIP_ID) and not _is_col(c2, RELATIONSHIP_ID):
                        if key not in seen_errors:
                            errors.append(key)
                            seen_errors.add(key)
                    # WARNING: Other suspicious joins
                    else:
                        if key not in seen_warnings:
                            warnings.append(key)
                            seen_warnings.add(key)

    # --- 3. Global missing FK join -----------------------------------------
    # Only warn if no specific errors or warnings were found
    if found_any_join_between_tables and not found_valid_fk_join and not errors and not warnings:
        key = (CONCEPT_RELATIONSHIP, "UNKNOWN", RELATIONSHIP, "UNKNOWN")
        if key not in seen_warnings:
            warnings.append(key)
            seen_warnings.add(key)

    return errors, warnings


# --- Rule ------------------------------------------------------------------

@register
class ConceptRelationshipRelationshipJoinValidationRule(Rule):
    """Validate concept_relationship ↔ relationship joins."""

    rule_id = "joins.concept_relationship_relationship_join_validation"
    name = "Concept Relationship to Relationship Join Validation"

    description = (
        "When concept_relationship is joined with relationship, "
        "it must use relationship_id on both sides."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use: concept_relationship.relationship_id = relationship.relationship_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-filter
        sql_lower = sql.lower()
        if "concept_relationship" not in sql_lower or "relationship" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not (has_table_reference(tree, CONCEPT_RELATIONSHIP) and has_table_reference(tree, RELATIONSHIP)):
                continue

            aliases = extract_aliases(tree)
            errors, warnings = _detect_violations(tree, aliases)

            # --- ERRORS ---
            for cr_table, cr_col, r_table, r_col in errors:
                violations.append(
                    self.create_violation(
                        message=(
                            f"Invalid FK join between concept_relationship and relationship: "
                            f"{cr_table}.{cr_col} = {r_table}.{r_col}. "
                            f"Expected relationship_id = relationship_id."
                        ),
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "invalid_fk_join",
                            "concept_relationship_column": cr_col,
                            "relationship_column": r_col,
                        },
                    )
                )

            # --- WARNINGS ---
            for cr_table, cr_col, r_table, r_col in warnings:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=(
                            "Suspicious join between concept_relationship and relationship. "
                            "Expected relationship_id = relationship_id."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "missing_fk_join",
                        },
                    )
                )

        return violations


__all__ = ["ConceptRelationshipRelationshipJoinValidationRule"]

"""Concept Primary Key Join Validation Rule.

OMOP semantic rule JOIN_008:
All joins TO the concept table must target concept.concept_id (the primary key).
Joining to concept.concept_name, concept.concept_code, concept.vocabulary_id, or
concept.domain_id as the join predicate is incorrect.

The Problem:
    The concept table has a primary key (concept_id) and several descriptive columns
    (concept_name, concept_code, vocabulary_id, domain_id, etc.). Joining on
    descriptive columns instead of the primary key causes:

    1. Non-unique matches: concept_name is not unique, causing cartesian joins
    2. String matching issues: Case sensitivity, trailing spaces, encoding
    3. Performance: String joins are much slower than integer joins
    4. Semantic incorrectness: Foreign keys should reference primary keys

Violation pattern:
    SELECT * FROM drug_exposure de
    JOIN concept c ON de.drug_source_value = c.concept_name
    -- WRONG: Joining on concept_name instead of concept_id

Correct pattern:
    SELECT * FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
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


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _is_concept_id_like(col: Optional[str]) -> bool:
    """
    Detect OMOP concept_id-like columns (e.g., condition_concept_id).
    """
    c = _norm(col)
    return c is not None and c.endswith("_concept_id")


def _is_vocab_safe_column(col: Optional[str]) -> bool:
    """
    Columns commonly used in legitimate vocabulary joins.
    """
    return _norm(col) in {
        "concept_code",
        "vocabulary_id",
        "concept_class_id",
        "domain_id",
    }


def _extract_all_equalities(tree: exp.Expression) -> List[exp.EQ]:
    """
    Extract equality conditions from:
    - JOIN ON clauses
    - WHERE clauses (implicit joins)
    """
    return list(tree.find_all(exp.EQ))


def _group_equalities_by_join(
    tree: exp.Expression,
) -> List[List[exp.EQ]]:
    """
    Group equality conditions by logical AND blocks.
    Helps detect composite joins like:
      ON a = b AND c = d
    """
    groups: List[List[exp.EQ]] = []

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        eqs = list(on_clause.find_all(exp.EQ))
        if eqs:
            groups.append(eqs)

    # Also include WHERE-level equalities as a single group
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
    List[Tuple[str, str, str, str]],   # errors
    List[Tuple[str, str, str, str]],   # warnings
]:
    errors: List[Tuple[str, str, str, str]] = []
    warnings: List[Tuple[str, str, str, str]] = []

    seen_errors: Set[Tuple[str, str, str, str]] = set()
    seen_warnings: Set[Tuple[str, str, str, str]] = set()

    groups = _group_equalities_by_join(tree)

    for group in groups:
        resolved = []

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

            resolved.append((lt, lc, rt, rc))

        # Evaluate each condition within its join group
        for lt, lc, rt, rc in resolved:
            for (t1, c1, t2, c2) in [
                (lt, lc, rt, rc),
                (rt, rc, lt, lc),
            ]:

                if not _is_concept(t2):
                    continue

                # --- ERROR: *_concept_id must join to concept_id ---
                if _is_concept_id_like(c1):
                    if not _is_col(c2, CONCEPT_ID):
                        key = (t1, c1, t2, c2)
                        if key not in seen_errors:
                            errors.append(key)
                            seen_errors.add(key)

                # --- WARNING: suspicious vocabulary join ---
                elif _is_vocab_safe_column(c2):
                    # Check if vocabulary_id also present in same join group
                    has_vocab_constraint = any(
                        _norm(col2) == "vocabulary_id"
                        for (_, _, t2b, col2) in resolved
                        if _is_concept(t2b)
                    )

                    if not has_vocab_constraint:
                        key = (t1, c1, t2, c2)
                        if key not in seen_warnings:
                            warnings.append(key)
                            seen_warnings.add(key)

    return errors, warnings


# --- Rule ------------------------------------------------------------------

@register
class ConceptJoinValidationRule(Rule):
    """
    Production-grade OMOP concept join validation.

    Enforces:
    - *_concept_id must join to concept.concept_id (ERROR)
    - concept_code joins without vocabulary_id are suspicious (WARNING)
    """

    rule_id = "joins.concept_join_validation"
    name = "Concept Join Validation"

    description = (
        "Ensures correct joins to the OMOP concept table. "
        "Columns ending with '_concept_id' must join to concept.concept_id. "
        "Vocabulary-based joins (e.g., concept_code) should include vocabulary_id."
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
            if not tree:
                continue

            if not has_table_reference(tree, CONCEPT):
                continue

            aliases = extract_aliases(tree)

            errors, warnings = _detect_violations(tree, aliases)

            # --- ERRORS ---
            for other_table, other_col, concept_table, concept_col in errors:
                violations.append(
                    self.create_violation(
                        message=(
                            f"Invalid join: {other_table}.{other_col} must join to "
                            f"{concept_table}.concept_id, not {concept_col}."
                        ),
                        suggested_fix=(
                            f"Use:\n"
                            f"  {other_table}.{other_col} = {concept_table}.concept_id"
                        ),
                        details={
                            "type": "invalid_concept_id_join",
                            "other_table": other_table,
                            "other_column": other_col,
                            "concept_column": concept_col,
                        },
                    )
                )

            # --- WARNINGS ---
            for other_table, other_col, concept_table, concept_col in warnings:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=(
                            f"Potentially ambiguous join using {concept_table}.{concept_col} "
                            f"without vocabulary_id constraint."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=(
                            f"Add vocabulary_id constraint to ensure unique matches:\n"
                            f"  JOIN {concept_table} ON {other_table}.{other_col} = {concept_table}.{concept_col}\n"
                            f"  AND {concept_table}.vocabulary_id = '<vocabulary>'"
                        ),
                        details={
                            "type": "ambiguous_vocabulary_join",
                            "column": concept_col,
                        },
                    )
                )

        return violations


__all__ = ["ConceptJoinValidationRule"]
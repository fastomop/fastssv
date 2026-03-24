"""Concept to Domain Join Validation Rule.

OMOP semantic rule JOIN_012:
concept joins to domain via concept.domain_id = domain.domain_id
(VARCHAR to VARCHAR). Joining on concept_id = domain_concept_id or
domain_id = domain_name is incorrect.

The Problem:
    The domain table is a reference table in OMOP that describes domains
    (e.g., 'Condition', 'Drug', 'Procedure', 'Measurement'). The concept table
    references domain via the domain_id column (VARCHAR FK to domain.domain_id).

    Common mistakes:
    1. Joining concept_id (INTEGER) to domain_concept_id (INTEGER)
       - Type matches but semantics are wrong
    2. Joining domain_id to domain_name
       - Both VARCHAR but joining FK to description instead of PK
    3. Other column pairs that are semantically incorrect

Violation pattern:
    SELECT * FROM concept c
    JOIN domain d ON c.concept_id = d.domain_concept_id
    -- WRONG: concept_id is not the domain reference

Correct pattern:
    SELECT * FROM concept c
    JOIN domain d ON c.domain_id = d.domain_id
"""

from typing import Dict, List, Optional, Set, Tuple

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

CONCEPT = "concept"
DOMAIN = "domain"
DOMAIN_ID = "domain_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_concept(table: Optional[str]) -> bool:
    return _norm(table) == CONCEPT


def _is_domain(table: Optional[str]) -> bool:
    return _norm(table) == DOMAIN


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
        relationship_candidates: List[Tuple[str, str, str, str]] = []

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
                if _is_concept(t1) and _is_domain(t2):

                    # Correct relationship join
                    if _is_col(c1, DOMAIN_ID) and _is_col(c2, DOMAIN_ID):
                        has_correct_join = True
                    else:
                        relationship_candidates.append((t1, c1, t2, c2))

        if not relationship_candidates:
            continue

        for c_table, c_col, d_table, d_col in relationship_candidates:
            key = (c_table, c_col, d_table, d_col)

            # --- ERROR: incorrect FK usage ---
            if _is_col(c_col, DOMAIN_ID) and not _is_col(d_col, DOMAIN_ID):
                if key not in seen_errors:
                    errors.append(key)
                    seen_errors.add(key)

            # --- WARNING: suspicious relationship (only if no correct join exists) ---
            elif not has_correct_join:
                if key not in seen_warnings:
                    warnings.append(key)
                    seen_warnings.add(key)

    return errors, warnings


# --- Rule ------------------------------------------------------------------

@register
class ConceptDomainJoinValidationRule(Rule):
    """
    Production-grade validation for concept ↔ domain joins.

    Enforces:
    - ERROR: concept.domain_id must join to domain.domain_id
    - WARNING: suspicious joins involving domain without proper alignment
    """

    rule_id = "joins.concept_domain_join_validation"
    name = "Concept to Domain Join Validation"

    description = (
        "If concept is joined to domain, the relationship should use "
        "concept.domain_id = domain.domain_id."
    )

    severity = Severity.ERROR

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-filter
        sql_lower = sql.lower()
        if "concept" not in sql_lower or "domain" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not (uses_table(tree, CONCEPT) and uses_table(tree, DOMAIN)):
                continue

            aliases = extract_aliases(tree)
            errors, warnings = _detect_violations(tree, aliases)

            # --- ERRORS ---
            for c_table, c_col, d_table, d_col in errors:
                violations.append(
                    self.create_violation(
                        message=(
                            f"Invalid join: {c_table}.{c_col} = {d_table}.{d_col}. "
                            f"Expected {c_table}.domain_id = {d_table}.domain_id."
                        ),
                        suggested_fix=(
                            f"{c_table}.domain_id = {d_table}.domain_id"
                        ),
                        details={
                            "type": "invalid_concept_domain_join",
                            "concept_column": c_col,
                            "domain_column": d_col,
                        },
                    )
                )

            # --- WARNINGS ---
            for c_table, c_col, d_table, d_col in warnings:
                violations.append(
                    RuleViolation(
                        rule_id=self.rule_id,
                        message=(
                            f"Suspicious join: {c_table}.{c_col} = {d_table}.{d_col}. "
                            f"Expected alignment on domain_id."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=(
                            f"{c_table}.domain_id = {d_table}.domain_id"
                        ),
                        details={
                            "type": "suspicious_concept_domain_join",
                        },
                    )
                )

        return violations


__all__ = ["ConceptDomainJoinValidationRule"]
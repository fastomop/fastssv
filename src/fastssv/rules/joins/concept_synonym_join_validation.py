"""Concept Synonym Join Validation Rule.

OMOP semantic rule JOIN_029:
concept_synonym joins to concept via concept_synonym.concept_id = concept.concept_id.
Joining on concept_synonym_name = concept_name or any other column is incorrect and
produces unreliable results because synonyms are alternative names.

The Problem:
    The concept_synonym table provides alternative names for concepts:
    - concept.concept_id = 123 → "Type 2 diabetes mellitus" (primary name)
    - concept_synonym.concept_id = 123, concept_synonym_name = "Diabetes mellitus type 2"
    - concept_synonym.concept_id = 123, concept_synonym_name = "T2DM"

    Joining on name strings is unreliable because:
    1. Names are not unique identifiers
    2. Synonyms may match concept names from different concepts
    3. String matching is error-prone (case sensitivity, whitespace)

Violation pattern:
    SELECT * FROM concept_synonym cs
    JOIN concept c ON cs.concept_synonym_name = c.concept_name
    -- WRONG: Names are not unique identifiers!

Correct pattern:
    SELECT * FROM concept_synonym cs
    JOIN concept c ON cs.concept_id = c.concept_id
"""

from typing import Dict, List, Optional, Set, Tuple

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

CONCEPT_SYNONYM = "concept_synonym"
CONCEPT = "concept"
CONCEPT_ID = "concept_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_concept_synonym(t: Optional[str]) -> bool:
    return _norm(t) == CONCEPT_SYNONYM


def _is_concept(t: Optional[str]) -> bool:
    return _norm(t) == CONCEPT


def _is_concept_id(c: Optional[str]) -> bool:
    return _norm(c) == CONCEPT_ID


def _extract_eq_conditions(tree: exp.Expression) -> List[exp.EQ]:
    """Extract column-to-column equality conditions."""
    eqs: List[exp.EQ] = []

    has_join_on = False

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause:
            has_join_on = True
            for eq in on_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    eqs.append(eq)

    if not has_join_on:
        where_clause = tree.find(exp.Where)
        if where_clause:
            for eq in where_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    eqs.append(eq)

    return eqs


# --- Detection -------------------------------------------------------------

def _detect(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """
    Detect invalid or missing joins between concept_synonym and concept.
    """
    violations: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    # --- Discover tables ---------------------------------------------------
    tables_present: Set[str] = set()

    for table in tree.find_all(exp.Table):
        t = _norm(table.name)
        if t:
            tables_present.add(t)

    if CONCEPT_SYNONYM not in tables_present or CONCEPT not in tables_present:
        return violations

    found_valid_join = False
    found_any_join = False

    # --- Analyze joins -----------------------------------------------------
    for eq in _extract_eq_conditions(tree):
        lt, lc = resolve_table_col(eq.this, aliases)
        rt, rc = resolve_table_col(eq.expression, aliases)

        lt_norm = _norm(lt)
        rt_norm = _norm(rt)
        lc_norm = _norm(lc)
        rc_norm = _norm(rc)

        # Defensive checks
        if not lc_norm or not rc_norm:
            continue

        if lt_norm and rt_norm and lt_norm == rt_norm:
            continue

        is_cs_to_c = _is_concept_synonym(lt_norm) and _is_concept(rt_norm)
        is_c_to_cs = _is_concept(lt_norm) and _is_concept_synonym(rt_norm)

        if not (is_cs_to_c or is_c_to_cs):
            continue

        found_any_join = True

        # Valid join
        if _is_concept_id(lc_norm) and _is_concept_id(rc_norm):
            found_valid_join = True
            continue

        # Invalid join
        key = (lt_norm or "unknown", lc_norm, rt_norm or "unknown", rc_norm)
        if key not in seen:
            violations.append(key)
            seen.add(key)

    # --- Missing or invalid join cases ------------------------------------

    # Only add a generic violation if we haven't detected specific violations
    if not found_valid_join and not violations:
        if found_any_join:
            # Joins exist but none are valid - add generic violation
            key = (CONCEPT_SYNONYM, "INVALID", CONCEPT, "INVALID")
            if key not in seen:
                violations.append(key)
        # If no join at all, that's OK - tables can be present without joining

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
        "Ensures concept_synonym joins to concept via concept_id. "
        "Joining on names or other columns is unreliable because names are not unique."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Join concept_synonym to concept using concept_id: "
        "concept_synonym.concept_id = concept.concept_id"
    )

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

            aliases = extract_aliases(tree)
            detected = _detect(tree, aliases)

            for lt, lc, rt, rc in detected:

                if lc == "NONE":
                    msg = (
                        f"{CONCEPT_SYNONYM} and {CONCEPT} are used but not joined. "
                        f"Missing join condition using concept_id."
                    )
                elif lc == "INVALID":
                    msg = (
                        f"Invalid join between {CONCEPT_SYNONYM} and {CONCEPT}. "
                        f"Must use concept_id."
                    )
                else:
                    left = f"{lt}.{lc}" if lt != "unknown" else lc
                    right = f"{rt}.{rc}" if rt != "unknown" else rc

                    msg = (
                        f"Invalid join: {left} → {right}. "
                        f"concept_synonym must join to concept via concept_id."
                    )

                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "concept_synonym_invalid_join",
                            "left_table": lt,
                            "left_column": lc,
                            "right_table": rt,
                            "right_column": rc,
                        },
                    )
                )

        return violations


__all__ = ["ConceptSynonymJoinValidationRule"]
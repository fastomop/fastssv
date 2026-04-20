"""Person ID Join Validation Rule.

OMOP semantic rule JOIN_026:
person_id columns across all tables reference person.person_id. Joining a person_id
column to another table's primary key (e.g., visit_occurrence_id, condition_occurrence_id,
measurement_id) is always wrong — these are different ID spaces.

The Problem:
    Even if numeric values overlap (person_id=123, visit_occurrence_id=123 both exist),
    they represent completely different entities:
    - person_id = 123 → Patient identifier
    - visit_occurrence_id = 123 → Visit identifier

    These are unrelated despite having the same number.

Violation pattern:
    SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.person_id = vo.visit_occurrence_id
    -- WRONG: person_id should join to person_id, not visit_occurrence_id

Correct pattern:
    SELECT * FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.person_id = vo.person_id
    -- OR use the proper FK:
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
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

PERSON_ID = "person_id"


# --- Helpers ---------------------------------------------------------------

def _normalize_optional(x: Optional[str]) -> Optional[str]:
    """Normalize column/table name, returning None if input is None."""
    return normalize_name(x) if x else None


def _is_person_id(col: Optional[str]) -> bool:
    """Check if column name is person_id."""
    return _normalize_optional(col) == PERSON_ID


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


def _extract_using_columns(tree: exp.Expression) -> Set[str]:
    """Extract normalized column names from USING clauses."""
    cols: Set[str] = set()

    for join in tree.find_all(exp.Join):
        using = join.args.get("using")
        if not using:
            continue

        if isinstance(using, exp.Tuple):
            for col in using.expressions:
                if isinstance(col, exp.Column):
                    cols.add(_normalize_optional(col.name))
        elif isinstance(using, exp.Identifier):
            cols.add(_normalize_optional(using.name))

    return cols


# --- Detection -------------------------------------------------------------

def _detect_invalid_person_id_joins(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """Detect invalid joins where person_id is joined to non-person_id columns.

    Returns:
        List of tuples: (left_table, left_col, right_table, right_col)
    """
    violations: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    using_cols = _extract_using_columns(tree)

    for eq in _extract_eq_conditions(tree):
        left, right = eq.this, eq.expression

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        lc_norm = _normalize_optional(lc)
        rc_norm = _normalize_optional(rc)

        # Skip unresolved or non-column comparisons
        if not lc_norm or not rc_norm:
            continue

        # Skip same-table comparisons (not joins)
        if lt and rt and _normalize_optional(lt) == _normalize_optional(rt):
            continue

        # Skip USING(person_id) - assumed valid
        if lc_norm in using_cols and rc_norm in using_cols:
            continue

        # --- Detect invalid person_id joins --------------------------------

        left_is_pid = _is_person_id(lc_norm)
        right_is_pid = _is_person_id(rc_norm)

        if left_is_pid and not right_is_pid:
            key = (lt or "unknown", lc_norm, rt or "unknown", rc_norm)
        elif right_is_pid and not left_is_pid:
            key = (lt or "unknown", lc_norm, rt or "unknown", rc_norm)
        else:
            continue

        if key not in seen:
            violations.append(key)
            seen.add(key)

    return violations


# --- Rule ------------------------------------------------------------------

@register
class PersonIdJoinValidationRule(Rule):
    """
    Ensure person_id only joins to person_id across tables.
    """

    rule_id = "joins.person_id_join_validation"
    name = "Person ID Join Validation"

    description = (
        "Ensures person_id columns only join to other person_id columns. "
        "Prevents incorrect joins to unrelated primary keys such as "
        "visit_occurrence_id or condition_occurrence_id."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Join person_id only with person_id. "
        "If linking tables, use the correct foreign key (e.g., visit_occurrence_id)."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        if PERSON_ID not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            detected = _detect_invalid_person_id_joins(tree, aliases)

            for lt, lc, rt, rc in detected:

                lt_disp = lt if lt and lt != "unknown" else ""
                rt_disp = rt if rt and rt != "unknown" else ""

                if _is_person_id(lc):
                    person_side = f"{lt_disp}.{lc}" if lt_disp else lc
                    other_side = f"{rt_disp}.{rc}" if rt_disp else rc
                    other_col = rc
                else:
                    person_side = f"{rt_disp}.{rc}" if rt_disp else rc
                    other_side = f"{lt_disp}.{lc}" if lt_disp else lc
                    other_col = lc

                msg = (
                    f"Invalid join: {person_side} → {other_side}. "
                    f"person_id must only join to person_id, not {other_col}."
                )

                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "person_id_cross_match",
                            "left_table": lt,
                            "left_column": lc,
                            "right_table": rt,
                            "right_column": rc,
                        },
                    )
                )

        return violations


__all__ = ["PersonIdJoinValidationRule"]
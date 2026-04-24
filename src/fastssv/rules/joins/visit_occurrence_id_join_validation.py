"""Visit Occurrence ID Join Validation Rule.

OMOP semantic rule JOIN_027:
visit_occurrence_id columns across all tables reference visit_occurrence.visit_occurrence_id.
Joining a visit_occurrence_id column to another table's column that is not visit_occurrence_id
(e.g., person_id, condition_occurrence_id, visit_detail_id) is always wrong — these are
different ID spaces.

The Problem:
    Even if numeric values overlap (visit_occurrence_id=123, person_id=123 both exist),
    they represent completely different entities:
    - visit_occurrence_id = 123 → A specific visit/encounter
    - person_id = 123 → A patient identifier

    These are unrelated despite having the same number.

Violation pattern:
    SELECT * FROM drug_exposure de
    JOIN visit_occurrence vo ON de.visit_occurrence_id = vo.person_id
    -- WRONG: visit_occurrence_id should join to visit_occurrence_id, not person_id

Correct pattern:
    SELECT * FROM drug_exposure de
    JOIN visit_occurrence vo ON de.visit_occurrence_id = vo.visit_occurrence_id
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

VISIT_OCCURRENCE_ID = "visit_occurrence_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_visit_occurrence_id(col: Optional[str]) -> bool:
    return _norm(col) == VISIT_OCCURRENCE_ID


def _extract_eq_conditions(tree: exp.Expression) -> List[exp.EQ]:
    """Extract column-to-column equality conditions from JOIN ON or WHERE."""
    eqs: List[exp.EQ] = []

    has_join_on = False

    # Prefer explicit JOIN conditions
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause:
            has_join_on = True
            for eq in on_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    eqs.append(eq)

    # Fallback to implicit joins (WHERE)
    if not has_join_on:
        where_clause = tree.find(exp.Where)
        if where_clause:
            for eq in where_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    eqs.append(eq)

    return eqs


def _extract_using_columns(tree: exp.Expression) -> Set[str]:
    """Extract normalized column names used in USING clauses."""
    cols: Set[str] = set()

    for join in tree.find_all(exp.Join):
        using = join.args.get("using")
        if not using:
            continue

        if isinstance(using, exp.Tuple):
            for col in using.expressions:
                if isinstance(col, exp.Column):
                    cols.add(_norm(col.name))
        elif isinstance(using, exp.Identifier):
            cols.add(_norm(using.name))

    return cols


# --- Detection -------------------------------------------------------------

def _detect(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """
    Detect invalid joins where visit_occurrence_id is matched with non-visit_occurrence_id.

    Returns:
        List of (left_table, left_column, right_table, right_column)
    """
    violations: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    using_cols = _extract_using_columns(tree)

    for eq in _extract_eq_conditions(tree):
        left, right = eq.this, eq.expression

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        lc_norm = _norm(lc)
        rc_norm = _norm(rc)

        # --- Defensive guards ---------------------------------------------

        # Skip unresolved or malformed columns
        if not lc_norm or not rc_norm:
            continue

        # Skip same-table comparisons (not joins)
        if lt and rt and _norm(lt) == _norm(rt):
            continue

        # Skip USING-based equality (safe assumption, avoids double counting)
        if lc_norm in using_cols and rc_norm in using_cols:
            continue

        # --- Core validation ----------------------------------------------

        left_is_voi = _is_visit_occurrence_id(lc_norm)
        right_is_voi = _is_visit_occurrence_id(rc_norm)

        if left_is_voi and not right_is_voi:
            key = (lt or "unknown", lc_norm, rt or "unknown", rc_norm)

        elif right_is_voi and not left_is_voi:
            key = (lt or "unknown", lc_norm, rt or "unknown", rc_norm)

        else:
            continue

        if key not in seen:
            violations.append(key)
            seen.add(key)

    return violations


# --- Rule ------------------------------------------------------------------

@register
class VisitOccurrenceIdJoinValidationRule(Rule):
    """
    Ensure visit_occurrence_id only joins to visit_occurrence_id across tables.
    """

    rule_id = "joins.visit_occurrence_id_join_validation"
    name = "Visit Occurrence ID Join Validation"

    description = (
        "Ensures visit_occurrence_id columns only join to other visit_occurrence_id columns. "
        "Prevents incorrect joins to unrelated identifiers such as person_id or "
        "condition_occurrence_id."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Join visit_occurrence_id only with visit_occurrence_id. "
        "If linking across domains, use the correct foreign key (e.g., person_id)."
    )
    example_bad = (
        "SELECT * FROM visit_occurrence vo\n"
        "JOIN condition_occurrence co ON vo.visit_occurrence_id = co.condition_occurrence_id;"
    )
    example_good = (
        "SELECT * FROM visit_occurrence vo\n"
        "JOIN condition_occurrence co ON vo.visit_occurrence_id = co.visit_occurrence_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-check
        if VISIT_OCCURRENCE_ID not in sql.lower():
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

                lt_disp = lt if lt and lt != "unknown" else ""
                rt_disp = rt if rt and rt != "unknown" else ""

                if _is_visit_occurrence_id(lc):
                    visit_side = f"{lt_disp}.{lc}" if lt_disp else lc
                    other_side = f"{rt_disp}.{rc}" if rt_disp else rc
                    other_col = rc
                else:
                    visit_side = f"{rt_disp}.{rc}" if rt_disp else rc
                    other_side = f"{lt_disp}.{lc}" if lt_disp else lc
                    other_col = lc

                message = (
                    f"Invalid join: {visit_side} → {other_side}. "
                    f"visit_occurrence_id must only join to visit_occurrence_id, not {other_col}."
                )

                violations.append(
                    self.create_violation(
                        message=message,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "visit_occurrence_id_cross_match",
                            "left_table": lt,
                            "left_column": lc,
                            "right_table": rt,
                            "right_column": rc,
                        },
                    )
                )

        return violations


__all__ = ["VisitOccurrenceIdJoinValidationRule"]

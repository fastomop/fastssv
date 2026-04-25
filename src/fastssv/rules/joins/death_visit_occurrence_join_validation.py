"""Death to Visit Occurrence Join Validation Rule.

OMOP semantic rule JOIN_021:
death joins to visit_occurrence ONLY via person_id. Joining on any other
columns (including temporal date columns) is structurally incorrect.

The Problem:
    The death table has a unique structure with person_id as both primary key
    and the ONLY foreign key to other clinical tables. It has NO visit_occurrence_id,
    provider_id, or care_site_id columns.

    The ONLY valid join is:
    death.person_id = visit_occurrence.person_id

    Common mistakes:
    1. Temporal joins using dates (death_date = visit_end_date)
       - Structurally invalid even if temporally meaningful
       - Temporal correlations should use WHERE clause, not JOIN ON
    2. Joining death_type_concept_id to visit_concept_id
       - Both are concept IDs but serve different semantic purposes
    3. Using any column pair other than person_id

Violation pattern:
    SELECT *
    FROM death d
    JOIN visit_occurrence vo ON d.death_date = vo.visit_end_date
    -- WRONG: Temporal join, not structural FK!

Correct pattern:
    SELECT
      d.person_id,
      vo.visit_occurrence_id,
      d.death_date,
      vo.visit_end_date
    FROM death d
    JOIN visit_occurrence vo ON d.person_id = vo.person_id
    WHERE d.death_date = vo.visit_end_date  -- Temporal filter in WHERE
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

DEATH = "death"
VISIT_OCCURRENCE = "visit_occurrence"
PERSON_ID = "person_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_death(table: Optional[str]) -> bool:
    return _normalize_table(table) == DEATH


def _is_visit_occurrence(table: Optional[str]) -> bool:
    return _normalize_table(table) == VISIT_OCCURRENCE


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_eq_conditions(tree: exp.Expression) -> List[exp.EQ]:
    """Extract column-to-column equality conditions from JOIN ON and implicit joins."""
    eqs = []

    # 1. Check explicit JOIN ON clauses
    has_joins_with_on = False
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause:
            has_joins_with_on = True
            for eq in on_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    eqs.append(eq)

    # 2. Check implicit joins in WHERE clause (only if no ON clauses exist)
    if not has_joins_with_on:
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
    errors = []
    seen: Set[Tuple[str, str, str, str]] = set()

    found_any_relation = False
    found_valid_fk = False

    # --- 1. USING clause support -------------------------------------------
    for join in tree.find_all(exp.Join):
        using = join.args.get("using")
        if using:
            for col in using:
                if _norm(col.name) == PERSON_ID:
                    found_valid_fk = True
                    found_any_relation = True

    # --- 2. Equality joins (JOIN + WHERE) -----------------------------------
    for eq in _extract_eq_conditions(tree):
        left, right = eq.this, eq.expression

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt_norm = _normalize_table(lt)
        rt_norm = _normalize_table(rt)

        # Only consider death ↔ visit_occurrence
        if not ((_is_death(lt_norm) and _is_visit_occurrence(rt_norm)) or
                (_is_death(rt_norm) and _is_visit_occurrence(lt_norm))):
            continue

        found_any_relation = True

        # Normalize direction
        if _is_death(lt_norm):
            death_col, vo_col = lc, rc
        else:
            death_col, vo_col = rc, lc

        death_col_norm = _norm(death_col)
        vo_col_norm = _norm(vo_col)

        # --- correct FK ---
        if death_col_norm == PERSON_ID and vo_col_norm == PERSON_ID:
            found_valid_fk = True
            continue

        # --- incorrect join ---
        key = (DEATH, death_col_norm, VISIT_OCCURRENCE, vo_col_norm)
        if key not in seen:
            errors.append(key)
            seen.add(key)

    # --- 3. Missing join detection -----------------------------------------
    if has_table_reference(tree, DEATH) and has_table_reference(tree, VISIT_OCCURRENCE):
        if found_any_relation and not found_valid_fk and not errors:
            # Generic error only when we detected a join but couldn't identify columns
            key = (DEATH, "INVALID", VISIT_OCCURRENCE, "INVALID")
            if key not in seen:
                errors.append(key)
                seen.add(key)

        elif not found_any_relation:
            key = (DEATH, "NONE", VISIT_OCCURRENCE, "NONE")
            if key not in seen:
                errors.append(key)
                seen.add(key)

    return errors


# --- Rule ------------------------------------------------------------------

@register
class DeathVisitOccurrenceJoinValidationRule(Rule):
    """Validate death ↔ visit_occurrence joins via person_id."""

    rule_id = "joins.death_visit_occurrence_join_validation"
    name = "Death to Visit Occurrence Join Validation"

    description = (
        "Ensures death joins to visit_occurrence using person_id. "
        "Flags missing or invalid joins."
    )

    severity = Severity.ERROR

    suggested_fix = "ADD: `death.person_id = visit_occurrence.person_id` to the join condition. death has no FK to visit_occurrence directly; the link goes through person_id."
    example_bad = (
        "SELECT * FROM death d\n"
        "JOIN visit_occurrence vo ON d.death_date = vo.visit_start_date;"
    )
    example_good = (
        "SELECT * FROM death d\n"
        "JOIN visit_occurrence vo ON d.person_id = vo.person_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if "death" not in sql_lower or "visit_occurrence" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            if not (has_table_reference(tree, DEATH) and has_table_reference(tree, VISIT_OCCURRENCE)):
                continue

            aliases = extract_aliases(tree)
            errors = _detect(tree, aliases)

            for death, death_col, vo, vo_col in errors:
                patch = None
                if death_col == "NONE":
                    msg = (
                        "death and visit_occurrence are used but not joined. "
                        "Missing join condition."
                    )
                elif death_col == "INVALID":
                    msg = (
                        "Invalid join between death and visit_occurrence. "
                        "Expected person_id = person_id."
                    )
                else:
                    msg = (
                        f"Invalid FK join between death and visit_occurrence: "
                        f"{death}.{death_col} = {vo}.{vo_col}. "
                        f"Expected person_id = person_id."
                    )
                    fix_text = (
                        f"REPLACE: `{death}.{death_col} = {vo}.{vo_col}` "
                        f"WITH `{death}.person_id = {vo}.person_id`."
                    )
                    patch = build_join_replace_patch(
                        sql, death, death_col, vo, vo_col,
                        PERSON_ID, PERSON_ID,
                        fix_text,
                        aliases=aliases,
                    )

                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=patch,
                        details={
                            "type": "invalid_fk_join",
                            "death_column": death_col,
                            "visit_occurrence_column": vo_col,
                        },
                    )
                )

        return violations


__all__ = ["DeathVisitOccurrenceJoinValidationRule"]

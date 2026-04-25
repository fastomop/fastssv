"""Person to Location Join Validation Rule.

OMOP semantic rule JOIN_004:
person joins to location via person.location_id = location.location_id.
Joining on person_id, person_source_value, or any other column is incorrect.

The Problem:
    person has location_id (foreign key to location.location_id) to identify
    the patient's home address. Joining on other columns (e.g., person_id to
    location_id) is semantically incorrect and produces wrong results.

Violation pattern:
    SELECT * FROM person p
    JOIN location l ON p.person_id = l.location_id
    -- Wrong: should use p.location_id, not p.person_id

Correct pattern:
    SELECT * FROM person p
    JOIN location l ON p.location_id = l.location_id
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

PERSON = "person"
LOCATION = "location"
LOCATION_ID = "location_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: str) -> str:
    """Normalize table name and strip schema if present."""
    return _norm(name.split(".")[-1])


def _is_person(table: Optional[str]) -> bool:
    return _norm(table) == PERSON


def _is_location(table: Optional[str]) -> bool:
    return _norm(table) == LOCATION


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_join_conditions(join: exp.Join) -> List[exp.Expression]:
    """Extract equality conditions from JOIN ON clause."""
    on_clause = join.args.get("on")
    if not on_clause:
        return []
    return list(on_clause.find_all(exp.EQ))


# --- Detection -------------------------------------------------------------

def _check_person_location_join(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """
    Detect incorrect joins between person and location.

    Returns:
        List of (person_table, person_col, location_table, location_col)
    """
    violations: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    for join in tree.find_all(exp.Join):
        conditions = _extract_join_conditions(join)

        has_correct_join = False
        incorrect_pairs: List[Tuple[str, str, str, str]] = []

        for eq in conditions:
            left, right = eq.this, eq.expression

            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue

            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            if not (lt and lc and rt and rc):
                continue

            lt = _normalize_table(lt)
            rt = _normalize_table(rt)

            # Check both directions
            for (t1, c1, t2, c2) in [
                (lt, lc, rt, rc),
                (rt, rc, lt, lc),
            ]:
                if _is_person(t1) and _is_location(t2):
                    if _is_col(c1, LOCATION_ID) and _is_col(c2, LOCATION_ID):
                        has_correct_join = True
                    else:
                        key = (t1, c1, t2, c2)
                        if key not in seen:
                            incorrect_pairs.append(key)
                            seen.add(key)

        # Only report violations if we found incorrect joins and no correct join
        if incorrect_pairs and not has_correct_join:
            violations.extend(incorrect_pairs)

    return violations


# --- Rule ------------------------------------------------------------------

@register
class PersonLocationJoinValidationRule(Rule):
    """Validate that person joins to location via location_id."""

    rule_id = "joins.person_location_join_validation"
    name = "Person to Location Join Validation"

    description = (
        "person must join to location via location_id to get patient address. "
        "Joining on person_id, person_source_value, or other columns is incorrect."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: the join target WITH `person.location_id = location.location_id`. Patient address comes via the location_id FK, not via person_id or person_source_value."
    example_bad = "SELECT * FROM person p JOIN location l ON p.person_id = l.location_id;"
    example_good = "SELECT * FROM person p JOIN location l ON p.location_id = l.location_id;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-filter
        sql_lower = sql.lower()
        if "person" not in sql_lower or "location" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            # Must have both tables
            if not (has_table_reference(tree, PERSON) and has_table_reference(tree, LOCATION)):
                continue

            aliases = extract_aliases(tree)
            bad_joins = _check_person_location_join(tree, aliases)

            for person_table, person_col, location_table, location_col in bad_joins:
                fix_text = (
                    f"REPLACE: `{person_table}.{person_col} = {location_table}.{location_col}` "
                    f"WITH `{person_table}.location_id = {location_table}.location_id`."
                )
                violations.append(
                    self.create_violation(
                        message=(
                            f"Incorrect join between {person_table} and {location_table}. "
                            f"Found {person_table}.{person_col} = {location_table}.{location_col}. "
                            f"Expected location_id = location_id."
                        ),
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=build_join_replace_patch(
                            sql, person_table, person_col,
                            location_table, location_col,
                            LOCATION_ID, LOCATION_ID,
                            fix_text,
                            aliases=aliases,
                        ),
                        details={
                            "person_table": person_table,
                            "person_column": person_col,
                            "location_table": location_table,
                            "location_column": location_col,
                            "expected": f"{person_table}.location_id = {location_table}.location_id",
                        },
                    )
                )

        return violations


__all__ = ["PersonLocationJoinValidationRule"]

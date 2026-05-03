"""Care Site to Location Join Validation Rule.

OMOP semantic rule JOIN_003:
care_site joins to location via care_site.location_id = location.location_id.
Joining on care_site_id, care_site_name, or any other column is incorrect.

The Problem:
    care_site has location_id (foreign key to location.location_id) to identify
    the geographic location of the care site. Joining on other columns (e.g.,
    care_site_id to location_id) is semantically incorrect and produces wrong results.

Violation pattern:
    SELECT * FROM care_site cs
    JOIN location l ON cs.care_site_id = l.location_id
    -- Wrong: should use cs.location_id, not cs.care_site_id

Correct pattern:
    SELECT * FROM care_site cs
    JOIN location l ON cs.location_id = l.location_id
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

CARE_SITE = "care_site"
LOCATION = "location"
LOCATION_ID = "location_id"


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: str) -> str:
    """Normalize table name and strip schema if present."""
    return _norm(name.split(".")[-1])


def _is_care_site(table: Optional[str]) -> bool:
    return _norm(table) == CARE_SITE


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


def _check_care_site_location_join(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """
    Detect incorrect joins between care_site and location.

    Returns:
        List of (care_site_table, care_site_col, location_table, location_col)
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
            for t1, c1, t2, c2 in [
                (lt, lc, rt, rc),
                (rt, rc, lt, lc),
            ]:
                if _is_care_site(t1) and _is_location(t2):
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
class CareSiteLocationJoinValidationRule(Rule):
    """Validate that care_site joins to location via location_id."""

    rule_id = "joins.care_site_location_join_validation"
    name = "Care Site to Location Join Validation"

    description = "care_site must join to location via location_id. Joining on other columns is incorrect."

    severity = Severity.ERROR

    suggested_fix = "REPLACE: the join target WITH `care_site.location_id = location.location_id`. care_site joins to location only via location_id."
    example_bad = "SELECT * FROM care_site cs JOIN location l ON cs.care_site_id = l.location_id;"
    example_good = "SELECT * FROM care_site cs JOIN location l ON cs.location_id = l.location_id;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-filter
        sql_lower = sql.lower()
        if "care_site" not in sql_lower or "location" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            # Ensure both tables exist in query
            if not (has_table_reference(tree, CARE_SITE) and has_table_reference(tree, LOCATION)):
                continue

            aliases = extract_aliases(tree)
            bad_joins = _check_care_site_location_join(tree, aliases)

            for cs_table, cs_col, loc_table, loc_col in bad_joins:
                fix_text = (
                    f"REPLACE: `{cs_table}.{cs_col} = {loc_table}.{loc_col}` "
                    f"WITH `{cs_table}.location_id = {loc_table}.location_id`."
                )
                violations.append(
                    self.create_violation(
                        message=(
                            f"Incorrect join between {cs_table} and {loc_table}. "
                            f"Found {cs_table}.{cs_col} = {loc_table}.{loc_col}. "
                            f"Expected location_id = location_id."
                        ),
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=build_join_replace_patch(
                            sql,
                            cs_table,
                            cs_col,
                            loc_table,
                            loc_col,
                            LOCATION_ID,
                            LOCATION_ID,
                            fix_text,
                            aliases=aliases,
                        ),
                        details={
                            "care_site_table": cs_table,
                            "care_site_column": cs_col,
                            "location_table": loc_table,
                            "location_column": loc_col,
                            "expected": f"{cs_table}.location_id = {loc_table}.location_id",
                        },
                    )
                )

        return violations


__all__ = ["CareSiteLocationJoinValidationRule"]

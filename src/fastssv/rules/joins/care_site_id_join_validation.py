"""Care Site ID Join Validation Rule.

OMOP semantic rule JOIN_002:
Clinical event tables must join to care_site via care_site_id on both sides.
Joining on location_id, provider_id, or any other column is incorrect.

The Problem:
    Clinical tables have care_site_id (foreign key to care_site.care_site_id).
    Joining on other columns (e.g., care_site_id to location_id) is semantically
    incorrect and produces wrong results.

Violation pattern:
    SELECT * FROM visit_occurrence vo
    JOIN care_site cs ON vo.care_site_id = cs.location_id
    -- Wrong: should use cs.care_site_id, not cs.location_id

Correct pattern:
    SELECT * FROM visit_occurrence vo
    JOIN care_site cs ON vo.care_site_id = cs.care_site_id
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

CARE_SITE = "care_site"
CARE_SITE_ID = "care_site_id"

TABLES_WITH_CARE_SITE_ID = {
    "visit_occurrence",
    "visit_detail",
    "condition_occurrence",
    "procedure_occurrence",
    "drug_exposure",
    "device_exposure",
    "measurement",
    "observation",
    "specimen",
    "person",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_care_site(table: Optional[str]) -> bool:
    return _norm(table) == CARE_SITE


def _has_care_site_id(table: Optional[str]) -> bool:
    return _norm(table) in TABLES_WITH_CARE_SITE_ID


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_join_conditions(join: exp.Join) -> List[exp.Expression]:
    """Extract equality conditions from JOIN ON clause."""
    on_clause = join.args.get("on")
    if not on_clause:
        return []
    return list(on_clause.find_all(exp.EQ))


# --- Detection -------------------------------------------------------------

def _check_care_site_join(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """
    Detect incorrect joins between tables and care_site.

    Returns:
        List of (source_table, source_col, care_site_table, care_site_col)
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

            for (t1, c1, t2, c2) in [
                (lt, lc, rt, rc),
                (rt, rc, lt, lc),
            ]:
                if _has_care_site_id(t1) and _is_care_site(t2):
                    if _is_col(c1, CARE_SITE_ID) and _is_col(c2, CARE_SITE_ID):
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
class CareSiteIdJoinValidationRule(Rule):
    """Validate that tables join to care_site via care_site_id."""

    rule_id = "joins.care_site_id_join_validation"
    name = "Care Site ID Join Validation"

    description = (
        "Tables containing care_site_id must join to care_site using care_site_id. "
        "Joining on location_id, provider_id, or other columns is incorrect."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Join using care_site_id on both sides: "
        "table.care_site_id = care_site.care_site_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-filter
        if "care_site" not in sql.lower():
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not uses_table(tree, CARE_SITE):
                continue

            aliases = extract_aliases(tree)
            bad_joins = _check_care_site_join(tree, aliases)

            for source_table, source_col, care_site_table, care_site_col in bad_joins:
                violations.append(
                    self.create_violation(
                        message=(
                            f"Incorrect join between {source_table} and {care_site_table}. "
                            f"Found {source_table}.{source_col} = {care_site_table}.{care_site_col}. "
                            f"Expected care_site_id = care_site_id."
                        ),
                        suggested_fix=self.suggested_fix,
                        details={
                            "source_table": source_table,
                            "source_column": source_col,
                            "care_site_table": care_site_table,
                            "care_site_column": care_site_col,
                            "expected": f"{source_table}.care_site_id = {care_site_table}.care_site_id",
                        },
                    )
                )

        return violations


__all__ = ["CareSiteIdJoinValidationRule"]
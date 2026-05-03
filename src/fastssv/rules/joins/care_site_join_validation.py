"""Care Site Join Path Validation Rule.

OMOP semantic rule OMOP_039:
Joins from clinical tables to location must go through care_site, not directly.
The proper join path is: clinical_table → care_site → location.

The Problem:
    Clinical tables have care_site_id (foreign key to care_site.care_site_id).
    Direct joins to location.location_id bypass the care_site intermediary and are
    semantically incorrect (comparing care_site_id with location_id).

    Exception: person.location_id is valid - it represents the person's home address.

Violation pattern:
    SELECT * FROM visit_occurrence vo
    JOIN location l ON vo.care_site_id = l.location_id
    -- Wrong: care_site_id ≠ location_id (different identifiers!)

Correct pattern:
    SELECT * FROM visit_occurrence vo
    JOIN care_site cs ON vo.care_site_id = cs.care_site_id
    JOIN location l ON cs.location_id = l.location_id
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CARE_SITE = "care_site"
LOCATION = "location"
PERSON = "person"

CARE_SITE_ID = "care_site_id"
LOCATION_ID = "location_id"

CLINICAL_TABLES = {
    "visit_occurrence",
    "visit_detail",
    "condition_occurrence",
    "procedure_occurrence",
    "drug_exposure",
    "device_exposure",
    "measurement",
    "observation",
    "specimen",
}


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_clinical(table: Optional[str]) -> bool:
    return _norm(table) in CLINICAL_TABLES


def _is_person(table: Optional[str]) -> bool:
    return _norm(table) == PERSON


def _is_location(table: Optional[str]) -> bool:
    return _norm(table) == LOCATION


def _is_care_site(table: Optional[str]) -> bool:
    return _norm(table) == CARE_SITE


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


# --- Join detection --------------------------------------------------------


def _extract_conditions(join: exp.Join):
    on = join.args.get("on")
    if not on:
        return []
    return list(on.walk())


def _resolve(col: exp.Column, aliases: Dict[str, str]):
    t, c = resolve_table_col(col, aliases)
    return _norm(t), _norm(c)


# --- Correct path detection -----------------------------------------------


def _has_valid_path(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """
    Detect if ANY valid path exists:
    clinical → care_site → location
    """
    found_clinical_to_cs = False
    found_cs_to_location = False

    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        left, right = eq.this, eq.expression
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = _resolve(left, aliases)
        rt, rc = _resolve(right, aliases)

        # clinical → care_site
        if (_is_clinical(lt) and _is_col(lc, CARE_SITE_ID) and _is_care_site(rt) and _is_col(rc, CARE_SITE_ID)) or (
            _is_clinical(rt) and _is_col(rc, CARE_SITE_ID) and _is_care_site(lt) and _is_col(lc, CARE_SITE_ID)
        ):
            found_clinical_to_cs = True

        # care_site → location
        if (_is_care_site(lt) and _is_col(lc, LOCATION_ID) and _is_location(rt) and _is_col(rc, LOCATION_ID)) or (
            _is_care_site(rt) and _is_col(rc, LOCATION_ID) and _is_location(lt) and _is_col(lc, LOCATION_ID)
        ):
            found_cs_to_location = True

    return found_clinical_to_cs and found_cs_to_location


# --- Invalid join detection ------------------------------------------------


def _is_invalid_direct_join(node, aliases) -> Optional[str]:
    """
    Detect clinical.care_site_id ↔ location.location_id (invalid)
    """
    if not isinstance(node, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return None

    left = node.this
    right = getattr(node, "expression", None)

    if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
        return None

    lt, lc = _resolve(left, aliases)
    rt, rc = _resolve(right, aliases)

    # allow person.location_id
    if (_is_person(lt) and _is_col(lc, LOCATION_ID) and _is_location(rt)) or (
        _is_person(rt) and _is_col(rc, LOCATION_ID) and _is_location(lt)
    ):
        return None

    # invalid direct join
    if (_is_clinical(lt) and _is_col(lc, CARE_SITE_ID) and _is_location(rt) and _is_col(rc, LOCATION_ID)) or (
        _is_clinical(rt) and _is_col(rc, CARE_SITE_ID) and _is_location(lt) and _is_col(lc, LOCATION_ID)
    ):
        return node.sql()

    return None


# --- Core ------------------------------------------------------------------


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues = []
    seen: Set[str] = set()

    valid_path_exists = _has_valid_path(tree, aliases)

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        violation = _is_invalid_direct_join(node, aliases)
        if not violation:
            continue

        key = violation
        if key in seen:
            continue
        seen.add(key)

        # Only flag if NO valid path exists
        if not valid_path_exists:
            issues.append(
                f"Invalid direct join to location detected: {violation}. "
                f"Clinical tables must join via care_site "
                f"(clinical → care_site → location)."
            )

    return issues


# --- Rule ------------------------------------------------------------------


@register
class CareSiteJoinValidationRule(Rule):
    """Robust validation of care_site join path."""

    rule_id = "joins.care_site_join_validation"
    name = "Care Site Join Path Validation"
    description = "Ensures clinical tables join to location via care_site."
    severity = Severity.WARNING
    suggested_fix = "ADD: the full clinical → care_site → location chain: `JOIN care_site cs ON <clinical>.care_site_id = cs.care_site_id JOIN location l ON cs.location_id = l.location_id`. Don't skip care_site to join clinical directly to location."

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, LOCATION):
                continue

            aliases = extract_aliases(tree)

            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["CareSiteJoinValidationRule"]

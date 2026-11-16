"""Care Site Join Path Validation Rule.

In OMOP CDM v5.4 the ``care_site_id`` of PERSON, PROVIDER, VISIT_OCCURRENCE and VISIT_DETAIL is a foreign key to
CARE_SITE.care_site_id; the facility's address is reached through CARE_SITE.location_id. Equating such a
``care_site_id`` with LOCATION.location_id compares the keys of two different tables, so the join returns arbitrary
locations or none.

Operands are resolved by lineage (casts, CTEs, derived tables, set operations, IN-subqueries) in every SELECT. Every
such predicate is reported, even when a correct ... -> care_site -> location chain appears elsewhere in the
statement. ``person.location_id = location.location_id`` (the person's own address) is a different, valid link.

Violation pattern:
    SELECT * FROM visit_occurrence vo
    JOIN location l ON vo.care_site_id = l.location_id

Correct pattern:
    SELECT * FROM visit_occurrence vo
    JOIN care_site cs ON vo.care_site_id = cs.care_site_id
    JOIN location l ON cs.location_id = l.location_id
"""

from typing import List, Set

from fastssv.core.deduplication import predicate_key
from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import cdm_table
from fastssv.core.registry import register

from ._join_scope import hoist_joins, key_mismatches
from .care_site_id_join_validation import care_site_to_location


@register
class CareSiteJoinValidationRule(Rule):
    """A care_site_id must not be joined to location.location_id."""

    rule_id = "joins.care_site_join_validation"
    name = "Care Site Join Path Validation"
    description = (
        "care_site_id (on person, provider, visit_occurrence or visit_detail) must not be joined directly to "
        "location.location_id; the facility address is reached through care_site (... -> care_site -> location)."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the direct join WITH the chain `JOIN care_site cs ON <table>.care_site_id = cs.care_site_id "
        "JOIN location l ON cs.location_id = l.location_id`."
    )
    example_bad = "SELECT * FROM visit_occurrence vo\nJOIN location l ON vo.care_site_id = l.location_id;"
    example_good = (
        "SELECT * FROM visit_occurrence vo\n"
        "JOIN care_site cs ON vo.care_site_id = cs.care_site_id\n"
        "JOIN location l ON cs.location_id = l.location_id;"
    )
    references = (cdm_table("visit_occurrence"), cdm_table("care_site"), cdm_table("location"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "location" not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees:
            if not tree:
                continue
            tree = hoist_joins(tree)
            for eq, a, b in key_mismatches(tree):
                if not care_site_to_location(a, b):
                    continue
                bad = eq.sql()
                if bad in seen:
                    continue
                seen.add(bad)
                violations.append(
                    self.create_violation(
                        message=(
                            f"Invalid direct join to location: {bad}. care_site_id is a key of care_site, not of "
                            f"location; join through care_site (care_site_id -> care_site.location_id -> location)."
                        ),
                        details={"predicate": predicate_key(eq.node)},
                    )
                )
        return violations


__all__ = ["CareSiteJoinValidationRule"]

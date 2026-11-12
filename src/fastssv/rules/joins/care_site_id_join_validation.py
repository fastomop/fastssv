"""Care Site ID Join Validation Rule.

In CDM v5.4 ``care_site_id`` on PERSON, PROVIDER, VISIT_OCCURRENCE and VISIT_DETAIL is a foreign key to
CARE_SITE.care_site_id, and ``location_id`` on PERSON and CARE_SITE is a foreign key to LOCATION.location_id. An
equality between a care-site or location key and the key of another entity (``person.location_id =
care_site.care_site_id``, ``care_site.care_site_id = location.location_id``) matches rows only where unrelated ids
coincide.

Each operand is resolved by lineage (casts, CTEs, derived tables, set operations, IN-subqueries) to the CDM column it
reads, and that column to the entity whose key it holds (keys generated from the CDM v5.4 field-level
specification). Equalities between two columns that reference the same entity (``person.location_id =
care_site.location_id``: patients living at a care site's address) and comparisons with non-key columns are
accepted.

Other rules own the remaining pairs: ``<table>.care_site_id = location.location_id`` is
joins.care_site_join_validation; keys of a person-level event, person or visit_occurrence are
joins.clinical_pk_cross_join_validation, joins.person_id_join_validation and
joins.visit_occurrence_id_join_validation; provider columns are joins.provider_care_site_join_validation.

Violation patterns:
    SELECT * FROM person p JOIN care_site cs ON p.location_id = cs.care_site_id
    SELECT * FROM care_site cs JOIN location l ON cs.care_site_id = l.location_id

Correct patterns:
    SELECT * FROM person p JOIN care_site cs ON p.care_site_id = cs.care_site_id
    SELECT * FROM care_site cs JOIN location l ON cs.location_id = l.location_id
"""

from typing import List, Set, Tuple

from fastssv.core.deduplication import predicate_key
from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import cdm_table
from fastssv.core.registry import register

from ._join_scope import Side, fk_replace_patch, hoist_joins, key_mismatches
from ._person_keys import PERSON_SCOPED_KEYS

CARE_SITE_KEY = ("care_site", "care_site_id")
LOCATION_KEY = ("location", "location_id")
CONCEPT_KEY = ("concept", "concept_id")
CARE_SITE_FK_TABLES = {"person", "provider", "visit_occurrence", "visit_detail"}


def care_site_to_location(a: Side, b: Side) -> bool:
    """``<table>.care_site_id = location.location_id``, reported by joins.care_site_join_validation."""
    for x, y in ((a, b), (b, a)):
        if x.table in CARE_SITE_FK_TABLES and x.name == "care_site_id" and y.origin == LOCATION_KEY:
            return True
    return False


def _reported(a: Side, b: Side) -> bool:
    # An entity key equated with a concept id is reported by anti_patterns.join_key_validation.
    if CONCEPT_KEY in (a.domain, b.domain):
        return False
    if CARE_SITE_KEY not in (a.domain, b.domain) and LOCATION_KEY not in (a.domain, b.domain):
        return False
    if a.domain in PERSON_SCOPED_KEYS or b.domain in PERSON_SCOPED_KEYS:
        return False
    if "provider" in (a.table, b.table):
        return False
    return not care_site_to_location(a, b)


@register
class CareSiteIdJoinValidationRule(Rule):
    """Care-site and location keys must be equated with their own foreign keys."""

    rule_id = "joins.care_site_id_join_validation"
    name = "Care Site and Location Key Join Validation"

    description = (
        "A care_site or location key equated with another entity's key (person.location_id = "
        "care_site.care_site_id, care_site.care_site_id = location.location_id) matches unrelated rows. Keys are "
        "followed by lineage and taken from the CDM v5.4 specification."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "REPLACE: the key equality WITH the foreign key pair: `<table>.care_site_id = care_site.care_site_id` or "
        "`<table>.location_id = location.location_id`."
    )
    example_bad = "SELECT * FROM person p JOIN care_site cs ON p.location_id = cs.care_site_id;"
    example_good = "SELECT * FROM person p JOIN care_site cs ON p.care_site_id = cs.care_site_id;"
    extra_examples = (
        RuleExample(
            sql="SELECT * FROM care_site cs JOIN location l ON cs.care_site_id = l.location_id;",
            severity=Severity.ERROR,
            note="The care site's own key used where its location_id foreign key belongs.",
        ),
    )
    references = (cdm_table("care_site"), cdm_table("person"), cdm_table("location"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if "care_site" not in lowered and "location" not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, str]] = set()
        for tree in trees:
            if not tree:
                continue
            tree = hoist_joins(tree)
            for eq, a, b in key_mismatches(tree):
                if not _reported(a, b):
                    continue
                key = tuple(sorted((f"{a.table}.{a.name}", f"{b.table}.{b.name}")))
                if key in seen:
                    continue
                seen.add(key)
                fix = f"REPLACE: `{eq.sql()}` WITH the foreign key pair that links {a.table} and {b.table}."
                violations.append(
                    self.create_violation(
                        message=(
                            f"Invalid join: {eq.sql()}. {a.table}.{a.name} holds {a.domain[0]} keys and "
                            f"{b.table}.{b.name} holds {b.domain[0]} keys; they never identify the same row."
                        ),
                        suggested_fix=fix,
                        suggested_fix_patch=fk_replace_patch(sql, a, b, fix),
                        details={
                            "left": f"{a.table}.{a.name}",
                            "right": f"{b.table}.{b.name}",
                            "predicate": predicate_key(eq.node),
                        },
                    )
                )
        return violations


__all__ = ["CareSiteIdJoinValidationRule"]

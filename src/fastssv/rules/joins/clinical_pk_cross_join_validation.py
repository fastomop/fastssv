"""Clinical Primary Key Cross-Join Validation Rule.

Each clinical event table has its own primary key ([Event]_ID, Book of OHDSI 4.2.6), and every foreign key in the
CDM v5.4 specification references exactly one table's key. An equality between two columns whose values identify
rows of different entities (``condition_occurrence_id = drug_exposure_id``, ``note_nlp_id = note_id``,
``visit_detail_id = condition_occurrence_id``) matches rows only where unrelated ids happen to coincide.

Each operand is resolved by lineage (casts, CTEs, derived tables, set operations, IN-subqueries) to the CDM column it
reads, and that column to the entity whose key it holds: itself for a primary key, the referenced key for a foreign
key (keys generated from the CDM v5.4 field-level specification). The rule reports an equality when the two entities
differ and one of them is a person-level event (the primary key of a table that carries person_id, other than
person and visit_occurrence). Keys of person and visit_occurrence are checked by joins.person_id_join_validation and
joins.visit_occurrence_id_join_validation.

Not reported: an operand whose source cannot be resolved or is computed (``MIN(condition_occurrence_id)``), a key
equated with a non-key column, and the polymorphic keys (``measurement_event_id``, ``observation_event_id``,
``note_event_id``, ``cost_event_id``, ``fact_relationship.fact_id_1/2``, ``episode_event.event_id``), which hold the
primary key of whichever table their companion column names. Whether that companion names the right table is
``domain_specific.event_field_polymorphic_resolution``'s check.

Violation pattern:
    SELECT * FROM condition_occurrence co
    JOIN drug_exposure de ON co.condition_occurrence_id = de.drug_exposure_id

Correct pattern:
    SELECT * FROM condition_occurrence co
    JOIN drug_exposure de ON co.person_id = de.person_id
"""

from typing import List, Set, Tuple

from fastssv.core.deduplication import predicate_key
from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import BOOK_CDM, cdm_table, section
from fastssv.core.registry import register

from ._join_scope import Side, fk_replace_patch, hoist_joins, key_mismatches
from ._person_keys import PERSON_SCOPED_KEYS

# Keys owned by other rules: person_id_join_validation and visit_occurrence_id_join_validation.
_OTHER_RULES = {("person", "person_id"), ("visit_occurrence", "visit_occurrence_id")}
EVENT_KEYS = PERSON_SCOPED_KEYS - _OTHER_RULES
CONCEPT_KEY = ("concept", "concept_id")


def _reported(a: Side, b: Side) -> bool:
    da, db = a.domain, b.domain
    # An entity key equated with a concept id is reported by anti_patterns.join_key_validation.
    if CONCEPT_KEY in (da, db):
        return False
    if da in _OTHER_RULES or db in _OTHER_RULES:
        return False
    return da in EVENT_KEYS or db in EVENT_KEYS


@register
class ClinicalPkCrossJoinValidationRule(Rule):
    """An event key must not be equated with another entity's key."""

    rule_id = "joins.clinical_pk_cross_join_validation"
    name = "Clinical Primary Key Join Validation"

    description = (
        "An equality between keys of two different entities, one of them a clinical event "
        "(condition_occurrence_id = drug_exposure_id, note_nlp_id = note_id), matches unrelated rows. Keys are "
        "followed by lineage and taken from the CDM v5.4 specification; polymorphic keys are exempt."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "REPLACE: the key equality WITH the foreign key that links the two tables (e.g. `nnlp.note_id = n.note_id`, "
        "`co.visit_detail_id = vd.visit_detail_id`), or `person_id` on both sides to relate a person's events."
    )
    example_bad = (
        "SELECT * FROM condition_occurrence co\n"
        "JOIN drug_exposure de ON co.condition_occurrence_id = de.drug_exposure_id;"
    )
    example_good = "SELECT * FROM condition_occurrence co\nJOIN drug_exposure de ON co.person_id = de.person_id;"
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT n.person_id, nnlp.lexical_variant\n"
                "FROM note_nlp nnlp\n"
                "JOIN note n ON nnlp.note_nlp_id = n.note_id;"
            ),
            severity=Severity.ERROR,
            note="The NLP record's own key used where its note_id foreign key belongs.",
        ),
    )
    references = (
        section(BOOK_CDM, "general-naming-conventions-of-fields", "General naming conventions of fields"),
        cdm_table("condition_occurrence"),
        cdm_table("note_nlp"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "_id" not in sql.lower():
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
                fix = (
                    f"REPLACE: `{eq.sql()}` WITH the foreign key that links {a.table} and {b.table}, or "
                    f"`person_id` on both sides."
                )
                violations.append(
                    self.create_violation(
                        message=(
                            f"Invalid join: {eq.sql()}. {a.table}.{a.name} holds {a.domain[0]} keys and "
                            f"{b.table}.{b.name} holds {b.domain[0]} keys; keys of different entities never identify "
                            f"the same row."
                        ),
                        suggested_fix=fix,
                        suggested_fix_patch=fk_replace_patch(sql, a, b, fix),
                        details={
                            "type": "clinical_pk_misuse",
                            "left": f"{a.table}.{a.name}",
                            "right": f"{b.table}.{b.name}",
                            "predicate": predicate_key(eq.node),
                        },
                    )
                )
        return violations


__all__ = ["ClinicalPkCrossJoinValidationRule"]

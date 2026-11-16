"""Concept Relationship to Relationship Join Validation Rule.

CONCEPT_RELATIONSHIP references RELATIONSHIP through ``concept_relationship.relationship_id`` (CDM v5.4 foreign key).
``relationship.reverse_relationship_id`` also holds relationship ids: every concept relationship exists with its
mirror image, whose relationship_id is the reverse_relationship_id of the original (Data Model Conventions, Concept
Relationships), so joining on it reaches the reverse relationship's row on purpose.

* WARNING: ``concept_relationship.relationship_id`` compared with ``relationship_name``. The join returns correct
  rows wherever the name equals the id, which in Athena v5.0 27-AUG-25 holds for 72 of 722 relationships, and drops
  the others.
* ERROR: ``concept_relationship.relationship_id`` compared with another non-key column of RELATIONSHIP
  (``is_hierarchical``, ``defines_ancestry``), which never holds a relationship id.
* WARNING: any other column pair between the two tables (``cr.concept_id_1 = r.relationship_concept_id``), unless the
  same two sources are already linked in that SELECT.

Operands are resolved by lineage through casts, LOWER / UPPER / TRIM, CTEs, derived tables, set operations and
IN-subqueries, in every SELECT.

Violation patterns:
    SELECT * FROM concept_relationship cr
    JOIN relationship r ON cr.relationship_id = r.is_hierarchical        -- ERROR

    SELECT * FROM concept_relationship cr
    JOIN relationship r ON cr.relationship_id = r.relationship_name      -- WARNING

Correct patterns:
    SELECT * FROM concept_relationship cr
    JOIN relationship r ON cr.relationship_id = r.relationship_id

    SELECT cr.concept_id_1, rev.relationship_id FROM concept_relationship cr
    JOIN relationship rev ON rev.reverse_relationship_id = cr.relationship_id
"""

from typing import List

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from .concept_metadata_join_validation import report


@register
class ConceptRelationshipRelationshipJoinValidationRule(Rule):
    """Validate concept_relationship <-> relationship joins."""

    rule_id = "joins.concept_relationship_relationship_join_validation"
    name = "Concept Relationship to Relationship Join Validation"

    description = (
        "concept_relationship joins relationship on relationship_id (= relationship.relationship_id, or "
        "= relationship.reverse_relationship_id for the reverse type). relationship_id compared with a column that "
        "never holds a relationship id (is_hierarchical) is an ERROR; compared with relationship_name, which equals "
        "the id for some relationships, and other column pairs are WARNINGs."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "REPLACE: the join target WITH `concept_relationship.relationship_id = relationship.relationship_id`. "
        "Use relationship_id, not relationship_name or relationship_concept_id."
    )
    example_bad = (
        "SELECT * FROM concept_relationship cr\nJOIN relationship r ON cr.relationship_id = r.is_hierarchical;"
    )
    example_good = (
        "SELECT * FROM concept_relationship cr\nJOIN relationship r ON cr.relationship_id = r.relationship_id;"
    )
    extra_examples = (
        RuleExample(
            sql="SELECT * FROM concept_relationship cr\nJOIN relationship r ON cr.relationship_id = r.relationship_name;",
            severity=Severity.WARNING,
            note="relationship_id compared with the name: right only for the 72 of 722 relationships named by their id.",
        ),
        RuleExample(
            sql=(
                "SELECT * FROM concept_relationship cr\n"
                "JOIN relationship r ON cr.concept_id_1 = r.relationship_concept_id;"
            ),
            severity=Severity.WARNING,
            note="A related concept matched against the concept that represents the relationship type.",
        ),
    )
    references = (
        cdm_table("concept_relationship"),
        cdm_table("relationship"),
        section(CDM_CONVENTIONS, "concept-relationships", "Concept Relationships"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "relationship" not in sql.lower():
            return []
        return report(self, sql, dialect, {"relationship"})


__all__ = ["ConceptRelationshipRelationshipJoinValidationRule"]

"""Fact Relationship Requires Relationship Concept Filter Rule.

OMOP semantic rules OMOP_250, OMOP_507.

CDM v5.4 ``fact_relationship`` records every kind of link between two facts in the same five columns:
person-to-person, care-site hierarchy, drug indication, measurement parent/child, and so on. Each link is stored
twice, once per direction, and ``relationship_concept_id`` says which kind and which direction a row is. A query
that reads ``fact_id_1`` / ``fact_id_2`` without restricting that column, and without returning it, mixes all
relationship types and both directions. Whether that is wrong depends on the data and the question, so the finding
is a WARNING: the CDM specification defines the column but does not require a filter on it.

Flagged, per ``fact_relationship`` instance in a SELECT:
    * ``relationship_concept_id`` is neither restricted nor shown.

It counts as restricted by any non-negated WHERE / ON / HAVING predicate on it (``=``, ``IN``, ``BETWEEN``, a join
to a concept-set table), by a filter on a ``concept`` row joined to it, or by a restriction downstream on a
projected copy. ``<>``, ``NOT IN`` and ``IS [NOT] NULL`` let every type through and do not count. It counts as
shown when the SELECT projects or groups by it, or projects a column of the joined ``concept`` row. ``SELECT *``
does not count. Pinning ``domain_concept_id_1`` / ``domain_concept_id_2`` does not count either: two domains can be
linked by several relationship types.

Not flagged: a read that analyses nothing (fact_relationship as the only source, no WHERE / GROUP BY, and a
``LIMIT`` peek or a whole-table ``COUNT(*)``), constant-false schema probes (``WHERE 0 = 1``), and DML / DDL
targets. Any other aggregate (links per fact, a count of one fact's links, an EXISTS probe) mixes the types like a
row-level read and is flagged.

Violation:
    SELECT fr.fact_id_2
    FROM fact_relationship fr
    WHERE fr.domain_concept_id_1 = 21 AND fr.fact_id_1 = 100

Correct:
    ... AND fr.relationship_concept_id = 581436   -- Parent to Child Measurement
"""

from typing import List

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.references import cdm_table
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register

from ._type_column import instances_using, unused_instances

FACT_RELATIONSHIP = "fact_relationship"
RELATIONSHIP_CONCEPT_ID = "relationship_concept_id"


@register
class FactRelationshipRequiresRelationshipConceptFilterRule(Rule):
    """Flag fact_relationship reads that mix every relationship type."""

    rule_id = "data_quality.fact_relationship_requires_relationship_concept_filter"
    name = "Fact Relationship Requires Relationship Concept Filter"

    description = (
        "A fact_relationship read that neither restricts nor returns relationship_concept_id mixes every "
        "relationship type, and both directions of each, in one result."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "ADD: `relationship_concept_id = <id>` (or IN (...)) to select one relationship type, or add "
        "relationship_concept_id to the SELECT list / GROUP BY if every type is wanted."
    )
    long_description = (
        "fact_relationship stores every kind of link between two facts (person to person, care-site hierarchy, "
        "drug indication, measurement parent and child) and stores each link once per direction; "
        "relationship_concept_id says which kind and direction a row is. Reading fact_id_1 / fact_id_2 without "
        "restricting or returning that column puts all of them in one result, so 'the facts related to this "
        "measurement' returns its parent, its children and anything else linked to it. The CDM specification "
        "does not require the filter, so this is a warning: restrict relationship_concept_id, or project or "
        "group by it when the mix is intended. A LIMIT peek and a whole-table COUNT(*) are not flagged; a count of "
        "the links of one fact is."
    )
    example_bad = (
        "SELECT fr.fact_id_2 AS child_measurement_id\n"
        "FROM fact_relationship fr\n"
        "WHERE fr.domain_concept_id_1 = 21\n"
        "  AND fr.fact_id_1 = 100;"
    )
    example_good = (
        "SELECT fr.fact_id_2 AS child_measurement_id\n"
        "FROM fact_relationship fr\n"
        "WHERE fr.domain_concept_id_1 = 21\n"
        "  AND fr.fact_id_1 = 100\n"
        "  AND fr.relationship_concept_id = 581436;"
    )
    references = (cdm_table("fact_relationship"),)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if FACT_RELATIONSHIP not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            typed = instances_using(tree, FACT_RELATIONSHIP, RELATIONSHIP_CONCEPT_ID)
            for _, alias in unused_instances(tree, FACT_RELATIONSHIP, typed, {RELATIONSHIP_CONCEPT_ID}):
                violations.append(
                    self.create_violation(
                        message=(
                            f"fact_relationship '{alias}' is read without restricting relationship_concept_id, so "
                            "rows of every relationship type, in both directions, are mixed. Restrict "
                            "relationship_concept_id, or select it if every type is wanted."
                        ),
                        details={"table": FACT_RELATIONSHIP, "alias": alias},
                    )
                )

        return violations


__all__ = ["FactRelationshipRequiresRelationshipConceptFilterRule"]

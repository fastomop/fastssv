"""Maps To Direction Rule.

A 'Maps to' row reads concept_id_1 (any concept, usually a non-standard source concept) → concept_id_2 (the
standard concept it maps to); 'Mapped from' is the same link reversed. Standard concepts also map to themselves, so
joining a standard *_concept_id to concept_id_1 is a harmless self-map. The error is the reverse lookup: a
*_source_concept_id matched against the standard side (concept_id_2 of 'Maps to', concept_id_1 of 'Mapped from').
Non-standard source concepts never appear there, so those rows silently drop out.

Column lineage is followed through CTEs and derived tables, and the relationship restriction is checked on the
concept_relationship instance the column actually comes from.

Violation:
    SELECT cr.concept_id_1
    FROM condition_occurrence co
    JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_2
    WHERE cr.relationship_id = 'Maps to'

Correct:
    SELECT cr.concept_id_2
    FROM condition_occurrence co
    JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_1
    WHERE cr.relationship_id = 'Maps to'
"""

from typing import List, Set, Tuple


from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.references import BOOK_VOCABULARIES, CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register
from fastssv.core.helpers import (
    CONCEPT_RELATIONSHIP,
    comparison_pairs,
    relationship_filters,
    side_reads,
)
from fastssv.schemas import SOURCE_CONCEPT_FIELDS

_SOURCE_FIELDS: Set[Tuple[str, str]] = {(normalize_name(t), normalize_name(c)) for t, c in SOURCE_CONCEPT_FIELDS}

# relationship_id → the concept_relationship column that holds the standard concept.
_STANDARD_SIDE = {"maps to": "concept_id_2", "mapped from": "concept_id_1"}


@register
class MapsToDirectionRule(Rule):
    """Flag a source concept id matched against the standard side of a 'Maps to' / 'Mapped from' link."""

    rule_id = "joins.maps_to_direction"
    name = "Maps To Direction"
    description = (
        "A *_source_concept_id matched against the standard side of a mapping (concept_id_2 of 'Maps to', "
        "concept_id_1 of 'Mapped from') runs the mapping backwards; non-standard source concepts never match."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the concept_relationship column matched to the source concept id WITH `cr.concept_id_1` for "
        "'Maps to' (or `cr.concept_id_2` for 'Mapped from') and read the standard concept from the other column."
    )
    long_description = (
        "In concept_relationship, a 'Maps to' row goes from concept_id_1 (a source or standard concept) to "
        "concept_id_2 (a standard concept); 'Mapped from' is the reverse row. Standard concepts map to themselves, "
        "so only standard concepts sit on the target side. Matching *_source_concept_id to that side keeps only "
        "records whose source code is itself standard and silently loses ICD10CM, NDC, Read and other "
        "non-standard source codes. Join the source concept to the source side and read the target from the other."
    )
    example_bad = (
        "SELECT cr.concept_id_1\n"
        "FROM condition_occurrence co\n"
        "JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_2\n"
        "WHERE cr.relationship_id = 'Maps to';"
    )
    example_good = (
        "SELECT cr.concept_id_2\n"
        "FROM condition_occurrence co\n"
        "JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_1\n"
        "WHERE cr.relationship_id = 'Maps to';"
    )
    extra_examples = (
        RuleExample(
            "SELECT cr.concept_id_2\n"
            "FROM condition_occurrence co\n"
            "JOIN concept_relationship cr ON co.condition_source_concept_id = cr.concept_id_1\n"
            "WHERE cr.relationship_id = 'Mapped from';",
            Severity.WARNING,
            "'Mapped from' keeps the standard concept in concept_id_1; the source concept belongs on concept_id_2.",
        ),
    )
    references = (
        section(BOOK_VOCABULARIES, "conceptMapping", "Mapping relationships"),
        section(CDM_CONVENTIONS, "concept-relationships", "Concept relationships"),
        cdm_table("concept_relationship"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if CONCEPT_RELATIONSHIP not in lowered or "source_concept_id" not in lowered:
            return []
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            filters = relationship_filters(tree)
            seen: Set[Tuple[int, str, str]] = set()
            for node, left, right in comparison_pairs(tree):
                left_reads, right_reads = side_reads(left, relay=False), side_reads(right, relay=False)
                for a, b in ((left_reads, right_reads), (right_reads, left_reads)):
                    sources = sorted(f"{t}.{c}" for _, _, t, c in a if (t, c) in _SOURCE_FIELDS)
                    if not sources:
                        continue
                    for select, alias, table, column in b:
                        if table != CONCEPT_RELATIONSHIP:
                            continue
                        values = filters.get((id(select), alias), set())
                        if len(values) != 1:
                            continue
                        relationship = next(iter(values))
                        if _STANDARD_SIDE.get(relationship) != column:
                            continue
                        key = (id(node), alias, column)
                        if key in seen:
                            continue
                        seen.add(key)
                        source_side = "concept_id_1" if column == "concept_id_2" else "concept_id_2"
                        violations.append(
                            self.create_violation(
                                message=(
                                    f"{sources[0]} (a source concept id) is matched against "
                                    f"concept_relationship.{column}, the standard side of '{relationship.capitalize()}'. "
                                    "Non-standard source concepts never appear there, so their records are "
                                    f"silently dropped; match it to {source_side} instead."
                                ),
                                details={"alias": alias, "relationship_id": relationship, "column": column},
                            )
                        )
        return violations


__all__ = ["MapsToDirectionRule"]

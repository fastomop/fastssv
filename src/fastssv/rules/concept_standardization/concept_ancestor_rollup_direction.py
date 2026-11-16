"""Concept Ancestor Rollup Direction Rule.

The usual hierarchy query joins a record's concept to ``concept_ancestor.descendant_concept_id`` and fixes
``ancestor_concept_id`` to the concept of interest, which returns records coded at that concept or anywhere below it
(Book of OHDSI, SQL chapter: "find all occurrences of angioedema or any of its descendants").

The reverse — the record's concept joined to ``ancestor_concept_id`` while ``descendant_concept_id`` is fixed to a
literal — returns records coded at that concept or at any *broader* concept above it. The CDM allows that query ("all
descendants of a hierarchical concept, and the other way around"), but it is rarely what an analyst wants when asking
for a condition or drug, so it is reported as a WARNING:

    SELECT co.condition_occurrence_id
    FROM condition_occurrence co
    JOIN concept_ancestor ca ON co.condition_concept_id = ca.ancestor_concept_id
    WHERE ca.descendant_concept_id = 201820;   -- Diabetes mellitus and its ancestors, not its subtypes

The record column is any concept foreign key of a CDM data table other than ``*_source_concept_id`` and
``*_type_concept_id`` (CDM v5.4 field-level specification). Only a literal restriction on ``descendant_concept_id``
(directly, or through the ``concept`` row it is joined to) counts; comparing it with another column links two
hierarchies and is not reported. The link and the restriction are followed through CTEs, derived tables and
IN / EXISTS subqueries.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import BOOK_SQL, BOOK_VOCABULARIES, cdm_table, section
from fastssv.core.registry import register

from ._cs_cdm_fields import EVENT_CONCEPT_FIELDS
from ._cs_scope import ColumnFilter, InstanceKey, Link, instance_filters, links

CONCEPT_ANCESTOR = "concept_ancestor"
ANCESTOR_CONCEPT_ID = "ancestor_concept_id"
DESCENDANT_CONCEPT_ID = "descendant_concept_id"

# concept columns that pin one concept row to a value the analyst chose
_CONCEPT_KEY_COLUMNS = ("concept_id", "concept_code", "concept_name")


def _literal_restriction(filters: List[ColumnFilter]) -> Optional[ColumnFilter]:
    for flt in filters:
        if flt.required and flt.complete and flt.op in ("eq", "in"):
            return flt
    return None


def _descendant_restrictions(tree: exp.Expression) -> Dict[InstanceKey, ColumnFilter]:
    """concept_ancestor instance -> a required literal restriction on its descendant_concept_id, written on the column
    itself or on the concept row joined to it."""
    out: Dict[InstanceKey, ColumnFilter] = {}
    for key, flts in instance_filters(tree, CONCEPT_ANCESTOR, DESCENDANT_CONCEPT_ID).items():
        flt = _literal_restriction(flts)
        if flt is not None:
            out[key] = flt
    concept_filters: Dict[InstanceKey, ColumnFilter] = {}
    for column in _CONCEPT_KEY_COLUMNS:
        for key, flts in instance_filters(tree, "concept", column).items():
            flt = _literal_restriction(flts)
            if flt is not None:
                concept_filters.setdefault(key, flt)
    if not concept_filters:
        return out
    for link in links(tree, "concept", "concept_id"):
        if link.origin != (CONCEPT_ANCESTOR, DESCENDANT_CONCEPT_ID) or not link.required:
            continue
        flt = concept_filters.get(link.instance)
        if flt is None:
            continue
        for ca_link in links(tree, CONCEPT_ANCESTOR, DESCENDANT_CONCEPT_ID):
            if ca_link.node is link.node:
                out.setdefault(ca_link.instance, flt)
    return out


@register
class ConceptAncestorRollupDirectionRule(Rule):
    """A record concept joined to ancestor_concept_id while descendant_concept_id is fixed: the hierarchy runs upward."""

    rule_id = "concept_standardization.concept_ancestor_rollup_direction"
    name = "Concept Ancestor Rollup Direction"
    description = (
        "A record's *_concept_id is joined to concept_ancestor.ancestor_concept_id while descendant_concept_id is "
        "fixed to a concept: the query returns records coded at that concept or broader, not its descendants "
        "(WARNING: the upward lookup can be intended)."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the join and filter WITH `<record>.<x>_concept_id = ca.descendant_concept_id AND "
        "ca.ancestor_concept_id = <concept>` to get the concept and its descendants."
    )
    long_description = (
        "concept_ancestor is directional: ancestor_concept_id is the broader concept and descendant_concept_id every "
        "concept below it (including itself). Joining a record's concept to descendant_concept_id and fixing "
        "ancestor_concept_id returns records coded at the concept or any of its descendants, the usual hierarchy "
        "query. With the columns swapped the query returns records coded at the concept or at any broader concept "
        "above it. The CDM allows both directions, so this is a WARNING; it is reported only when the descendant side "
        "is fixed to literal concepts, directly or through the concept row joined to it."
    )
    example_bad = (
        "SELECT co.condition_occurrence_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept_ancestor ca\n"
        "  ON co.condition_concept_id = ca.ancestor_concept_id\n"
        "WHERE ca.descendant_concept_id = 201820;"
    )
    example_good = (
        "SELECT co.condition_occurrence_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept_ancestor ca\n"
        "  ON co.condition_concept_id = ca.descendant_concept_id\n"
        "WHERE ca.ancestor_concept_id = 201820;"
    )
    references = (
        cdm_table("concept_ancestor"),
        section(BOOK_SQL, "using-the-vocabulary-when-querying", "Using the vocabulary when querying"),
        section(BOOK_VOCABULARIES, "conceptAncestor", "Concept ancestor"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if CONCEPT_ANCESTOR not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            record_links: List[Link] = [
                link
                for link in links(tree, CONCEPT_ANCESTOR, ANCESTOR_CONCEPT_ID)
                if link.required and link.origin in EVENT_CONCEPT_FIELDS
            ]
            if not record_links:
                continue
            restrictions = _descendant_restrictions(tree)
            seen: Set[Tuple[InstanceKey, Tuple[str, str]]] = set()
            for link in record_links:
                flt = restrictions.get(link.instance)
                if flt is None or (link.instance, link.origin) in seen:
                    continue
                seen.add((link.instance, link.origin))
                table, column = link.origin
                alias = link.alias
                violations.append(
                    self.create_violation(
                        message=(
                            f"{table}.{column} is joined to {alias}.ancestor_concept_id while the descendant side is "
                            f"fixed by `{flt.sql}`. This returns records coded at that concept or at any broader "
                            "concept above it, not its descendants."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=(
                            f"REPLACE: the join WITH `{table}.{column} = {alias}.descendant_concept_id` and fix "
                            f"`{alias}.ancestor_concept_id` to the concept instead, to get the concept and its "
                            "descendants. Keep the query if broader concepts are what you want."
                        ),
                        details={"record_column": f"{table}.{column}", "restriction": flt.sql},
                    )
                )
        return violations


__all__ = ["ConceptAncestorRollupDirectionRule"]

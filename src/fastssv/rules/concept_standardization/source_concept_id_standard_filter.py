"""Source Concept ID with Standard Filter Rule.

``*_source_concept_id`` holds the concept of the source code (CDM v5.4 field-level specification: "the concept
representing the condition source value and may not necessarily be standard"). It is non-standard when the source
system codes in a source vocabulary (ICD10CM, NDC, Read) and Standard when it codes in a standard one (SNOMED, LOINC,
RxNorm, CVX). Joining the source field to ``concept`` and requiring that concept row to be Standard (``standard_concept
= 'S'``, ``IN ('S', 'C')``, ``IS NOT NULL``) keeps only the records whose source code is itself standard and drops
every record coded in a source vocabulary. That is rarely intended, but whether rows remain depends on the data, so
the rule is a WARNING.

The source fields are every ``*_source_concept_id`` of the CDM v5.4 field-level specification. The link and the filter
are tied to one concept table instance by lineage (CTEs, derived tables, IN / EXISTS subqueries, comma joins, casts);
a filter under OR or NOT, in a CASE or on another concept row is not reported.
"""

import re
from typing import List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import locate, remove as patch_remove
from fastssv.core.references import BOOK_VOCABULARIES, CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from ._cs_cdm_fields import SOURCE_CONCEPT_FIELDS
from ._cs_scope import ColumnFilter, instance_filters, instance_is_null_extended, links

SOURCE_CONCEPT_ID_COLUMNS = frozenset(column for _, column in SOURCE_CONCEPT_FIELDS)


def _remove_patch(sql: str, node: exp.Expression) -> Optional[dict]:
    """REMOVE one predicate from a conjunction together with its AND; None when it is the only predicate."""
    parent = node.parent
    if not isinstance(parent, exp.And):
        return None
    span = locate(sql, node.sql())
    if span is None:
        return None
    start, end = span
    if parent.this is node:
        match = re.match(r"\s+AND\s+", sql[end:], flags=re.IGNORECASE)
        return patch_remove((start, end + match.end())) if match else None
    match = re.search(r"\s+AND\s+\Z", sql[:start], flags=re.IGNORECASE)
    return patch_remove((match.start(), end)) if match else None


def _requires_standard(flt: ColumnFilter) -> bool:
    """A required filter that no non-standard (NULL) concept satisfies."""
    if not flt.required or flt.outer_join_on:
        return False
    if flt.op == "is_not_null":
        return True
    if not flt.complete:
        return False
    values = {v.upper() for v in flt.values}
    if flt.op in ("eq", "in"):
        return bool(values) and values <= {"S", "C"} and "S" in values
    return False


@register
class SourceConceptIdStandardFilterRule(Rule):
    """Detect JOINs on *_source_concept_id with standard_concept = 'S' filter."""

    rule_id = "concept_standardization.source_concept_id_standard_filter"
    name = "Source Concept ID Should Not Filter Standard Concepts"

    description = (
        "A *_source_concept_id is joined to concept and the concept is required to be Standard. Source concepts are "
        "often non-standard, so records coded in source vocabularies (ICD10CM, NDC, ...) are dropped (WARNING: "
        "source fields hold Standard concepts too when the source system codes in a standard vocabulary)."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "REMOVE: the standard_concept filter on the concept joined to *_source_concept_id, OR join the paired "
        "*_concept_id (without `_source`) if Standard concepts are what you want."
    )
    long_description = (
        "Every OMOP clinical table has two concept columns: *_concept_id "
        "(the standard vocabulary mapping, e.g. SNOMED for conditions) and "
        "*_source_concept_id (the code from the originating system, "
        "e.g. ICD10CM). The source field 'may not necessarily be standard' (CDM v5.4 field-level "
        "specification): it is non-standard when the source system codes in a source vocabulary, and Standard "
        "when it codes in a standard one (SNOMED, LOINC, RxNorm, CVX). Requiring the joined concept to be Standard "
        "therefore keeps only the records whose source code happens to be standard and drops the rest. Either join "
        "against *_concept_id (for standard-code analytics) or drop the standard filter (to inspect source codes)."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_source_concept_id = c.concept_id\n"
        "WHERE c.standard_concept = 'S';"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.standard_concept = 'S';"
    )
    references = (
        section(
            CDM_CONVENTIONS,
            "source-values-source-concept-ids-and-standard-concept-ids",
            "Source values, source concept ids and standard concept ids",
        ),
        section(BOOK_VOCABULARIES, "non-standard-concepts", "Non-standard concepts"),
        cdm_table("condition_occurrence"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if "source_concept_id" not in lowered or "standard_concept" not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            filters = instance_filters(tree, "concept", "standard_concept")
            if not filters:
                continue
            seen: Set[int] = set()
            for link in links(tree, "concept", "concept_id"):
                if link.origin not in SOURCE_CONCEPT_FIELDS:
                    continue
                if not link.required and not instance_is_null_extended(link):
                    continue
                for flt in filters.get(link.instance, []):
                    if id(flt.node) in seen or not _requires_standard(flt):
                        continue
                    seen.add(id(flt.node))
                    table, column = link.origin
                    violations.append(
                        self.create_violation(
                            message=(
                                f"JOIN on {table}.{column} to concept (alias: {link.alias}) with `{flt.sql}`. Source "
                                "concepts are often non-standard, so records coded in a source vocabulary are dropped; "
                                "only records whose source code is itself Standard remain."
                            ),
                            severity=self.severity,
                            suggested_fix_patch=_remove_patch(sql, flt.node),
                            details={"source_column": f"{table}.{column}", "filter": flt.sql},
                        )
                    )
        return violations


__all__ = ["SOURCE_CONCEPT_ID_COLUMNS", "SourceConceptIdStandardFilterRule"]

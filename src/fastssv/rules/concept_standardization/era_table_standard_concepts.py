"""Era Table Standard Concepts Rule.

``drug_era.drug_concept_id`` and ``dose_era.drug_concept_id`` hold Ingredient concepts (CDM v5.4: "The drug_concept_id
should conform to the concept class 'ingredient'"), which are Standard. ``condition_era.condition_concept_id`` holds
the concepts of the condition_occurrence records the era is built from, which are Standard or 0 for an unmapped
record. An era concept therefore joins to a ``concept`` row whose ``standard_concept`` is 'S', or, on condition_era
only, to concept 0, whose ``standard_concept`` is NULL.

When the era's concept column is matched to ``concept.concept_id`` and that concept row is *required* to be anything
other than Standard, no era row survives:

    SELECT de.person_id
    FROM drug_era de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.standard_concept = 'C';          -- ERROR: no rows

``standard_concept IS NULL`` on condition_era keeps only the unmapped eras (concept 0) and is reported as a WARNING.

A predicate counts only when every surviving row must satisfy it: it is joined by AND to WHERE, HAVING or an inner
join's ON clause. A predicate under OR or NOT, in a CASE, in an aggregate FILTER or in the ON clause of an outer join
removes no era row and is not reported; neither is a list that admits 'S' (``IN ('S', 'C')``).
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import locate, remove as patch_remove
from fastssv.core.references import BOOK_VOCABULARIES, cdm_table, section
from fastssv.core.registry import register

from ._cs_scope import ColumnFilter, dead_scalar_column, instance_filters, links

# Era table -> its concept column (CDM v5.4 field-level specification).
ERA_CONCEPT_COLUMNS: Dict[str, str] = {
    "condition_era": "condition_concept_id",
    "drug_era": "drug_concept_id",
    "dose_era": "drug_concept_id",
}

# condition_era copies condition_occurrence.condition_concept_id, which is 0 for an unmapped record. Drug eras are
# built per Ingredient, so they have no unmapped rows.
_ALLOWS_UNMAPPED = {"condition_era"}


def _build_remove_predicate_patch(sql: str, node: exp.Expression) -> Optional[dict]:
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


def _is_standard(value: str) -> bool:
    return value.strip().upper() == "S"


def _verdict(flt: ColumnFilter, table: str) -> Optional[Tuple[Severity, str]]:
    """What a required predicate on the era concept's standard_concept does to the era rows."""
    values = [v for v in flt.values]
    if flt.op == "eq" and flt.complete and not _is_standard(values[0]):
        return Severity.ERROR, f"`{flt.sql}` requires a concept that is not Standard"
    if flt.op == "in" and flt.complete and not any(_is_standard(v) for v in values):
        return Severity.ERROR, f"`{flt.sql}` admits no Standard concept"
    if flt.op == "neq" and flt.complete and _is_standard(values[0]):
        return Severity.ERROR, f"`{flt.sql}` excludes every Standard concept"
    if flt.op == "not_in" and flt.complete and any(_is_standard(v) for v in values):
        return Severity.ERROR, f"`{flt.sql}` excludes every Standard concept"
    if flt.op == "is_null":
        if table in _ALLOWS_UNMAPPED:
            return Severity.WARNING, f"`{flt.sql}` keeps only unmapped eras (concept 0)"
        return Severity.ERROR, f"`{flt.sql}` requires a non-standard concept"
    return None


@register
class EraTableStandardConceptsRule(Rule):
    """A required standard_concept filter on an era table's concept that no era row can satisfy."""

    rule_id = "concept_standardization.era_table_standard_concepts"
    name = "Era Tables Use Standard Concepts Only"
    description = (
        "An era table's concept column is matched to concept and the concept row is required to be non-standard "
        "(standard_concept = 'C', <> 'S', IS NULL, ...). Era concepts are Standard, so the query returns no rows "
        "(ERROR); IS NULL on condition_era keeps only unmapped eras (WARNING)."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REMOVE: the standard_concept predicate, OR REPLACE it WITH `standard_concept = 'S'`. drug_era / dose_era "
        "hold Ingredient concepts and condition_era holds the Standard concepts of its condition_occurrence records."
    )
    long_description = (
        "drug_era and dose_era are built per Ingredient and condition_era per condition_concept_id of the underlying "
        "condition_occurrence records, so every era concept is Standard (condition_era can also hold concept 0 for "
        "unmapped records). Requiring the joined concept row to have standard_concept = 'C', <> 'S', NOT IN ('S') or "
        "IS NULL therefore returns no era rows. Only predicates that every surviving row must satisfy are reported: "
        "a predicate under OR or NOT, in a CASE expression, in the ON clause of an outer join, or a list that also "
        "admits 'S' leaves the Standard rows in place."
    )
    example_bad = (
        "SELECT de.person_id\n"
        "FROM drug_era de\n"
        "JOIN concept c ON de.drug_concept_id = c.concept_id\n"
        "WHERE c.standard_concept = 'C';"
    )
    example_good = (
        "SELECT de.person_id\n"
        "FROM drug_era de\n"
        "JOIN concept c ON de.drug_concept_id = c.concept_id\n"
        "WHERE c.standard_concept = 'S';"
    )
    references = (
        cdm_table("drug_era"),
        cdm_table("condition_era"),
        section(BOOK_VOCABULARIES, "standardConcepts", "Standard concepts"),
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT ce.person_id\n"
                "FROM condition_era ce\n"
                "JOIN concept c ON ce.condition_concept_id = c.concept_id\n"
                "WHERE c.standard_concept IS NULL;"
            ),
            severity=Severity.WARNING,
            note="condition_era: IS NULL keeps only eras of unmapped records (concept 0)",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if "standard_concept" not in lowered or "_era" not in lowered:
            return []
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            era_links = [
                link
                for link in links(tree, "concept", "concept_id")
                if link.required and ERA_CONCEPT_COLUMNS.get(link.origin[0]) == link.origin[1]
            ]
            if not era_links:
                continue
            filters = instance_filters(tree, "concept", "standard_concept")
            seen: Set[int] = set()
            for link in era_links:
                table, column = link.origin
                for flt in filters.get(link.instance, []):
                    if not flt.required or id(flt.node) in seen:
                        continue
                    verdict = _verdict(flt, table)
                    if verdict is None:
                        continue
                    seen.add(id(flt.node))
                    severity, what = verdict
                    if severity == Severity.ERROR:
                        dead = dead_scalar_column(flt.node) or dead_scalar_column(link.node)
                        message = (
                            f"{what}, but {table}.{column} only holds Standard concepts"
                            f"{' (or 0 for unmapped records, whose standard_concept is NULL)' if table in _ALLOWS_UNMAPPED else ''}"
                            + (f"; {dead}." if dead else ". The query returns 0 rows.")
                        )
                    else:
                        message = (
                            f"{what}: every mapped {table}.{column} is a Standard concept, so this filter drops all "
                            "mapped eras. If unmapped eras are what you want, `condition_concept_id = 0` says so "
                            "directly."
                        )
                    violations.append(
                        self.create_violation(
                            message=message,
                            severity=severity,
                            suggested_fix_patch=_build_remove_predicate_patch(sql, flt.node),
                        )
                    )
        return violations


__all__ = ["ERA_CONCEPT_COLUMNS", "EraTableStandardConceptsRule"]

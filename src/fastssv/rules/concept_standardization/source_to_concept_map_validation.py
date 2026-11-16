"""Source to Concept Map Validation Rule.

CDM v5.4 conventions, Source to Concept Map: "The source_concept_id, or the combination of the fields source_code and
the source_vocabulary_id uniquely identifies the source information." A source code alone is not unique: the Book of
OHDSI notes that "the same code can exist in different vocabularies with completely different meanings", and a site
that loads several local code lists (lab and pharmacy, say) can hold 'GLU' in both. A lookup that restricts
``source_code`` without restricting ``source_vocabulary_id`` can therefore return mappings from unrelated vocabularies.
Whether it does depends on what the site loaded, so the rule is a WARNING.

The restriction may be a literal, a list, a pattern, a join to another table's column (``USING (source_code)``
included) or an IN-subquery, followed to the source_to_concept_map table through CTEs, derived tables and casts. A
``source_vocabulary_id`` restriction counts in any of the same forms except a negation (``<> 'OLD'`` admits every
other vocabulary).
"""

from typing import Dict, List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import _select_sources, normalize_name, parse_sql
from fastssv.core.patch import add as patch_add, locate
from fastssv.core.references import BOOK_VOCABULARIES, CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from ._cs_scope import ColumnFilter, InstanceKey, instance_filters

SOURCE_TO_CONCEPT_MAP = "source_to_concept_map"
SOURCE_CODE = "source_code"
SOURCE_VOCABULARY_ID = "source_vocabulary_id"
_NOT_A_RESTRICTION = {"neq", "not_in", "is_not_null", "is_null"}


def _restricting(flts: List[ColumnFilter]) -> List[ColumnFilter]:
    return [f for f in flts if f.op not in _NOT_A_RESTRICTION]


def _using_code_instances(tree: exp.Expression) -> Dict[InstanceKey, exp.Join]:
    """source_to_concept_map instances joined ``USING (source_code)``."""
    out: Dict[InstanceKey, exp.Join] = {}
    for join in tree.find_all(exp.Join):
        if not any(normalize_name(u.name) == SOURCE_CODE for u in join.args.get("using") or []):
            continue
        select = join.find_ancestor(exp.Select)
        if select is None:
            continue
        for src in _select_sources(select):
            if isinstance(src, exp.Table) and normalize_name(src.name) == SOURCE_TO_CONCEPT_MAP:
                out[(id(select), normalize_name(src.alias_or_name))] = join
    return out


@register
class SourceToConceptMapValidationRule(Rule):
    """Ensures proper filtering of source_to_concept_map."""

    rule_id = "concept_standardization.source_to_concept_map_validation"
    name = "Source to Concept Map Validation"
    description = "Requires source_vocabulary_id when filtering source_code to avoid ambiguity across vocabularies."
    severity = Severity.WARNING
    suggested_fix = "ADD: `AND source_vocabulary_id = '<vocab>'` alongside any source_code filter. source_code is unique only within a source_vocabulary_id."
    long_description = (
        "source_to_concept_map is the per-site translation table from "
        "local source codes to OMOP standard concepts. Source codes are not "
        "unique across vocabularies: a site that loads local lab and pharmacy "
        "code lists can hold 'GLU' in both. Filtering on source_code "
        "alone can pick up matches from unrelated vocabularies, returning "
        "incorrect target_concept_ids. Always pair source_code with "
        "source_vocabulary_id so the match is unambiguous."
    )
    example_bad = "SELECT target_concept_id\nFROM source_to_concept_map\nWHERE source_code = 'GLU';"
    example_good = (
        "SELECT target_concept_id\n"
        "FROM source_to_concept_map\n"
        "WHERE source_code = 'GLU'\n"
        "  AND source_vocabulary_id = 'HOSP_LAB';"
    )
    references = (
        section(CDM_CONVENTIONS, "source-to-concept-map", "Source to concept map"),
        cdm_table("source_to_concept_map"),
        section(BOOK_VOCABULARIES, "concept-codes", "Concept codes"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if SOURCE_TO_CONCEPT_MAP not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            codes = {k: _restricting(v) for k, v in instance_filters(tree, SOURCE_TO_CONCEPT_MAP, SOURCE_CODE).items()}
            for key in _using_code_instances(tree):
                codes.setdefault(key, [])
            vocabularies = {
                k
                for k, v in instance_filters(tree, SOURCE_TO_CONCEPT_MAP, SOURCE_VOCABULARY_ID).items()
                if _restricting(v)
            }
            using = _using_code_instances(tree)
            reported: Set[InstanceKey] = set()
            for key, flts in codes.items():
                if key in vocabularies or key in reported or not (flts or key in using):
                    continue
                reported.add(key)
                patch = None
                if flts:
                    span = locate(sql, flts[0].sql)
                    if span is not None:
                        qualifier = f"{flts[0].column.table}." if flts[0].column.table else ""
                        patch = patch_add(span[1], f" AND {qualifier}source_vocabulary_id = '<vocab>'")
                violations.append(
                    self.create_violation(
                        message=(
                            f"Filtering source_to_concept_map.{SOURCE_CODE} without {SOURCE_VOCABULARY_ID}. "
                            "Source codes are not unique across vocabularies: the lookup can return mappings of the "
                            "same code from other source vocabularies. Add source_vocabulary_id to disambiguate."
                        ),
                        suggested_fix_patch=patch,
                    )
                )
        return violations


__all__ = ["SourceToConceptMapValidationRule"]

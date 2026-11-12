"""Concept Primary Key Join Validation Rule.

Every ``*_concept_id`` column of the CDM v5.4 is a foreign key to CONCEPT.concept_id (Data Model Conventions,
Fields). An equality between such a column and any other CONCEPT column (concept_name, concept_code, vocabulary_id,
...) compares a concept id with an unrelated attribute: PostgreSQL rejects the type mismatch, DuckDB fails on the first
value that does not convert, and with a cast to text the join runs and matches nothing.

Operands are resolved by lineage (casts, CTEs, derived tables, set operations, IN-subqueries) in every SELECT, so an
alias that names a different source in each scope, or a concept-id column renamed in a CTE, is judged by the column it
reads. Columns of VOCABULARY, DOMAIN, CONCEPT_CLASS and RELATIONSHIP are checked by
joins.concept_metadata_join_validation and joins.concept_relationship_relationship_join_validation, columns of
CONCEPT_SYNONYM by joins.concept_synonym_join_validation. Joins on concept.concept_code without a vocabulary_id
constraint are checked by anti_patterns.concept_code_requires_vocabulary_id.

Violation pattern:
    SELECT * FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_name

Correct pattern:
    SELECT * FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
"""

from typing import List, Optional, Set, Tuple

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import build_join_replace_patch
from fastssv.core.references import BOOK_SQL, CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from ._j1_reference_joins import REFERENCE_TABLES
from ._join_scope import Side, hoist_joins, tree_equalities

CONCEPT_KEY = ("concept", "concept_id")
_OWNED_ELSEWHERE = REFERENCE_TABLES | {"concept_synonym"}


def _mismatch(a: Side, b: Side) -> Optional[Tuple[Side, Side]]:
    """(concept-id operand, concept attribute operand) when one holds concept ids and the other another CONCEPT column."""
    for ids, attr in ((a, b), (b, a)):
        if ids.domain != CONCEPT_KEY or ids.table in _OWNED_ELSEWHERE:
            continue
        if attr.table == "concept" and attr.name != "concept_id":
            return ids, attr
    return None


@register
class ConceptJoinValidationRule(Rule):
    """A concept id must be compared with concept.concept_id."""

    rule_id = "joins.concept_join_validation"
    name = "Concept Join Validation"

    description = (
        "A *_concept_id column (a foreign key to concept.concept_id) must be compared with concept.concept_id, not "
        "with concept_name, concept_code or another concept column."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: any `*_concept_id` join target other than `concept.concept_id` WITH `concept.concept_id`."

    example_bad = (
        "SELECT co.person_id FROM condition_occurrence co\nJOIN concept c ON co.condition_concept_id = c.concept_name;"
    )
    example_good = (
        "SELECT co.person_id FROM condition_occurrence co\nJOIN concept c ON co.condition_concept_id = c.concept_id;"
    )
    references = (
        section(CDM_CONVENTIONS, "fields", "Fields"),
        cdm_table("concept"),
        section(BOOK_SQL, "using-the-vocabulary-when-querying", "Using the vocabulary when querying"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "concept" not in sql.lower():
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, str]] = set()
        for tree in trees:
            if not tree:
                continue
            for eq in tree_equalities(hoist_joins(tree)):
                if eq.left.origin is None or eq.right.origin is None:
                    continue
                found = _mismatch(eq.left, eq.right)
                if found is None:
                    continue
                ids, attr = found
                key = (f"{ids.table}.{ids.name}", attr.name)
                if key in seen:
                    continue
                seen.add(key)
                ids_text = ids.column.sql() if ids.column is not None else f"{ids.table}.{ids.name}"
                attr_text = attr.column.sql() if attr.column is not None else f"concept.{attr.name}"
                fix = f"REPLACE: `{eq.sql()}` WITH a comparison of {ids_text} with concept.concept_id."
                patch = None
                if ids.column is not None and attr.column is not None and ids.column.table and attr.column.table:
                    patch = build_join_replace_patch(
                        sql,
                        ids.column.table,
                        ids.column.name,
                        attr.column.table,
                        attr.column.name,
                        ids.column.name,
                        "concept_id",
                        fix,
                    )
                violations.append(
                    self.create_violation(
                        message=(
                            f"Invalid join: {eq.sql()}. {ids.table}.{ids.name} holds concept ids and must be compared "
                            f"with concept.concept_id, not concept.{attr.name} ({attr_text})."
                        ),
                        suggested_fix=fix,
                        suggested_fix_patch=patch,
                        details={
                            "type": "invalid_concept_id_join",
                            "other_table": ids.table,
                            "other_column": ids.name,
                            "concept_column": attr.name,
                        },
                    )
                )
        return violations


__all__ = ["ConceptJoinValidationRule"]

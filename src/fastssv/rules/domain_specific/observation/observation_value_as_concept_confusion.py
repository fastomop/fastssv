"""Observation Value As Concept Confusion Rule.

CDM v5.4: when a source code carries two clinical ideas, "the Vocabulary breaks these two clinical ideas into two
codes; one becomes the OBSERVATION_CONCEPT_ID and the other becomes the VALUE_AS_CONCEPT_ID" ('Maps to' and 'Maps
to value'). ``observation_concept_id`` is the question and ``value_as_concept_id`` the answer.

A filter that requires both columns of one row to be the same concept is usually a concept put in the wrong
column: it matches only rows where question and answer coincide. The CDM does not forbid such rows, and for the
few concepts whose 'Maps to' and 'Maps to value' targets are identical (903657 'Cigarette smoker') a conformant ETL
writes them, so this is a WARNING and those concepts are not reported.

Fires when, for one observation row (followed through casts, CTEs and derived tables), row filters

* restrict ``observation_concept_id`` and ``value_as_concept_id`` to the same set of literal concept ids
  (``= 134057 AND = 134057``, the same ``IN`` list on both), or
* equate the two columns (``observation_concept_id = value_as_concept_id``).

Only conjuncts of WHERE, HAVING or an inner join's ON count, outside NOT. Lists that merely overlap, conditions on
two different rows (a self-join, a subquery), 0 (unmapped) and OR branches are not reported.

Violation:
    SELECT * FROM observation WHERE observation_concept_id = 134057 AND value_as_concept_id = 134057

Correct:
    SELECT * FROM observation WHERE observation_concept_id = 4167217 AND value_as_concept_id = 134057
"""

from typing import Dict, FrozenSet, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import BOOK_VOCABULARIES, cdm_table, section
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import (
    RowPath,
    group_by_row,
    in_negated_scope,
    int_literal,
    read_of,
    restricts_rows,
    same_row,
)

from ._same_question_and_answer_concepts import SAME_QUESTION_AND_ANSWER_CONCEPTS

TABLE_NAME = "observation"
OBSERVATION_CONCEPT_ID = "observation_concept_id"
VALUE_AS_CONCEPT_ID = "value_as_concept_id"
_COLUMNS = (OBSERVATION_CONCEPT_ID, VALUE_AS_CONCEPT_ID)


def _concept_read(node: Optional[exp.Expression]) -> Optional[Tuple[RowPath, str]]:
    read = read_of(node)
    if read is None or read[1] != TABLE_NAME or read[2] not in _COLUMNS:
        return None
    return read[0], read[2]


def _find_violations(tree: exp.Expression) -> List[str]:
    found: List[str] = []
    # (row, (column, admitted ids)) for each literal restriction
    restrictions: List[Tuple[RowPath, Tuple[str, FrozenSet[int]]]] = []

    for node in tree.find_all(exp.EQ, exp.In):
        if not restricts_rows(node) or in_negated_scope(node):
            continue
        if isinstance(node, exp.In):
            col = _concept_read(node.this)
            if col is None or isinstance(node.parent, exp.Not) or node.args.get("query") is not None:
                continue
            values = [int_literal(v) for v in node.expressions or []]
            if values and all(v is not None for v in values):
                restrictions.append((col[0], (col[1], frozenset(values))))
            continue
        left, right = _concept_read(node.this), _concept_read(node.expression)
        if left is not None and right is not None:
            if left[1] != right[1] and same_row(left[0], right[0]):
                text = f"`{node.sql()}` requires the two columns of one observation row to hold the same concept"
                if text not in found:
                    found.append(text)
            continue
        col, other = (left, node.expression) if left is not None else (right, node.this)
        value = int_literal(other) if col is not None else None
        if value is not None:
            restrictions.append((col[0], (col[1], frozenset({value}))))

    for _, row_restrictions in group_by_row(restrictions):
        admitted: Dict[str, FrozenSet[int]] = {}
        for column, ids in row_restrictions:
            admitted[column] = admitted[column] & ids if column in admitted else ids
        question, answer = admitted.get(OBSERVATION_CONCEPT_ID), admitted.get(VALUE_AS_CONCEPT_ID)
        if not question or question != answer:
            continue
        # 0 is "no matching concept" on both columns; some concepts are legitimately question and answer.
        if question - {0} <= SAME_QUESTION_AND_ANSWER_CONCEPTS:
            continue
        listed = ", ".join(str(i) for i in sorted(question))
        text = (
            f"observation_concept_id and value_as_concept_id of the same row are both restricted to {listed}, so the "
            "filter requires one row to hold the same concept in both columns"
        )
        if text not in found:
            found.append(text)
    return found


@register
class ObservationValueAsConceptConfusionRule(Rule):
    """The same concept required as observation_concept_id and as value_as_concept_id of one row."""

    rule_id = "domain_specific.observation_value_as_concept_confusion"
    name = "Observation Value As Concept Confusion"
    description = (
        "Flags a filter that requires observation_concept_id (the question) and value_as_concept_id (the answer) "
        "of the same row to be the same concept. Few rows are recorded that way, so the concept is probably in the "
        "wrong column."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: one of the two concept ids, so that observation_concept_id holds what was observed (the 'Maps to' "
        "target) and value_as_concept_id holds its value (the 'Maps to value' target); keep the filter only when "
        "auditing rows where the two coincide."
    )
    long_description = (
        "In CDM v5.4 a source code that carries two clinical ideas is split: its 'Maps to' target becomes "
        "observation_concept_id and its 'Maps to value' target becomes value_as_concept_id (ICD10 Z82.4 gives 4167217 "
        "'Family history of clinical finding' with value 134057 'Disorder of cardiovascular system'). Requiring the "
        "same concept in both columns of a row matches only records where question and answer coincide. The CDM "
        "allows such rows, and concepts whose two mapping targets are identical in the vocabulary are not reported, "
        "so the finding is a warning about a likely misplaced concept, not an error."
    )
    example_bad = (
        "SELECT person_id FROM observation\nWHERE observation_concept_id = 134057\n  AND value_as_concept_id = 134057;"
    )
    example_good = (
        "SELECT person_id FROM observation\nWHERE observation_concept_id = 4167217\n  AND value_as_concept_id = 134057;"
    )
    extra_examples = (
        RuleExample(
            sql="SELECT person_id FROM observation\nWHERE observation_concept_id = value_as_concept_id;",
            severity=Severity.WARNING,
            note="the two columns of one row equated directly",
        ),
    )
    references = (
        cdm_table("observation"),
        section(BOOK_VOCABULARIES, "conceptMapping", "Mapping relationships ('Maps to value')"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql or VALUE_AS_CONCEPT_ID not in sql.lower():
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            for text in _find_violations(tree):
                violations.append(
                    self.create_violation(
                        message=(
                            f"{text[0].upper()}{text[1:]}. observation_concept_id is the question and "
                            "value_as_concept_id the answer; rows where they coincide are rare, so check which "
                            "column the concept belongs in."
                        ),
                    )
                )
        return violations


__all__ = ["ObservationValueAsConceptConfusionRule"]

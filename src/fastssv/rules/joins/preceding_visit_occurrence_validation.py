"""Preceding Visit Occurrence Validation Rule.

``visit_occurrence.preceding_visit_occurrence_id`` is a foreign key to ``visit_occurrence.visit_occurrence_id``:
"Use this field to find the visit that occurred for the person prior to the given visit" (OMOP CDM v5.4 field-level
specification). It holds a visit id, so equating it with the key of another entity (``person_id``,
``visit_detail_id``, ``condition_occurrence_id``, ...) compares unrelated id sequences and matches unrelated rows on
every conformant CDM.

The foreign key alone identifies the prior visit: ``vo.preceding_visit_occurrence_id = prev.visit_occurrence_id`` is
the complete join, with no further person or date condition required. Any column that holds a visit id is a valid
partner, including ``visit_detail.visit_occurrence_id`` and another ``preceding_visit_occurrence_id``.

Each operand is followed through casts, CTEs, derived tables and ``IN (SELECT …)`` to the CDM column it reads; ON,
WHERE and HAVING are all read.

Violation pattern:
    SELECT vo.visit_occurrence_id
    FROM visit_occurrence vo
    JOIN visit_occurrence prev ON vo.preceding_visit_occurrence_id = prev.person_id

Correct pattern:
    SELECT vo.visit_occurrence_id, prev.visit_end_date
    FROM visit_occurrence vo
    JOIN visit_occurrence prev ON vo.preceding_visit_occurrence_id = prev.visit_occurrence_id
"""

from typing import List

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import CDM_FIELD_LEVEL, cdm_table
from fastssv.core.registry import register

from ._j2_key_pairs import key_pair_violations
from ._lineage import VISIT_KEY


@register
class PrecedingVisitOccurrenceValidationRule(Rule):
    """preceding_visit_occurrence_id equated with the key of an entity other than a visit."""

    rule_id = "joins.preceding_visit_occurrence_validation"
    name = "Preceding Visit Occurrence Validation"
    description = (
        "preceding_visit_occurrence_id holds a visit_occurrence_id; equating it with the key of another entity "
        "(person_id, visit_detail_id, ...) matches unrelated rows."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the equality WITH `vo.preceding_visit_occurrence_id = prev.visit_occurrence_id`. The foreign key "
        "alone identifies the person's prior visit."
    )
    example_bad = (
        "SELECT vo.visit_occurrence_id FROM visit_occurrence vo\n"
        "JOIN visit_occurrence prev ON vo.preceding_visit_occurrence_id = prev.person_id;"
    )
    example_good = (
        "SELECT vo.visit_occurrence_id, prev.visit_end_date\n"
        "FROM visit_occurrence vo\n"
        "JOIN visit_occurrence prev ON vo.preceding_visit_occurrence_id = prev.visit_occurrence_id;"
    )
    extra_examples = (
        RuleExample(
            "SELECT vo.visit_occurrence_id\n"
            "FROM visit_occurrence vo\n"
            "JOIN visit_detail vd ON vo.preceding_visit_occurrence_id = vd.visit_detail_id;",
            Severity.ERROR,
            "A visit detail id is not a visit id; the prior visit is visit_occurrence.visit_occurrence_id.",
        ),
    )
    references = (cdm_table("visit_occurrence"), CDM_FIELD_LEVEL)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        return key_pair_violations(
            self,
            sql,
            dialect,
            keep_entity=VISIT_KEY,
            explain=lambda m: "Join preceding_visit_occurrence_id to a visit_occurrence_id.",
            prefilter="preceding_visit_occurrence_id",
        )


__all__ = ["PrecedingVisitOccurrenceValidationRule"]

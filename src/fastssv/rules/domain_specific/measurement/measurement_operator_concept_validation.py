"""Measurement Operator Concept Validation Rule.

CDM v5.4 defines ``measurement.operator_concept_id`` as a foreign key to CONCEPT restricted to the
'Meas Value Operator' domain: "Operators are <, <=, =, >=, > and these concepts belong to the 'Meas Value
Operator' domain." An equality or ``IN`` test of the column against concept ids that are all outside that domain
can therefore never be true on a conformant CDM.

Judged: ``operator_concept_id = <id>`` and ``operator_concept_id IN (<ids>)``, with the column followed through
casts, CTEs and derived tables to ``measurement``.

* ERROR when the test must hold for every returned row (a conjunct of WHERE, HAVING or an inner join's ON): the
  query returns no rows.
* WARNING anywhere else (under OR, in CASE, in an outer join's ON): that branch is dead, the rest of the query
  still runs.

Not judged, because they are satisfiable or filter nothing:

* 0, the "no matching concept" value an unmapped source operator is stored as;
* an ``IN`` list with at least one value of the domain (or 0);
* exclusions and ordering tests (``<>``, ``NOT IN``, ``> 0``).

Violation:
    SELECT * FROM measurement WHERE operator_concept_id = 8554        -- 8554 is the unit 'percent'

Correct:
    SELECT * FROM measurement WHERE operator_concept_id = 4172704     -- >
    SELECT * FROM measurement WHERE operator_concept_id IS NULL OR operator_concept_id IN (0, 4172703)
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import DQD_FIELD_THRESHOLDS, cdm_table
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import in_negated_scope, int_literal, read_of, restricts_rows

from ._operator_concept_ids import COMPARISON_OPERATOR_CONCEPTS, MEAS_VALUE_OPERATOR_CONCEPTS

TABLE_NAME = "measurement"
COLUMN_NAME = "operator_concept_id"

# 0 is CDM's "no matching concept": the value of a source operator that could not be mapped.
_SATISFIABLE = frozenset(MEAS_VALUE_OPERATOR_CONCEPTS) | {0}
_OPERATORS_TEXT = ", ".join(f"{cid} ({name})" for cid, name in COMPARISON_OPERATOR_CONCEPTS.items())


def _is_operator_column(node: Optional[exp.Expression]) -> bool:
    read = read_of(node)
    return read is not None and read[1:] == (TABLE_NAME, COLUMN_NAME)


def _find_violations(tree: exp.Expression) -> List[Tuple[List[int], str, Severity]]:
    """``(ids, predicate text, severity)`` for each equality / IN test that no conformant row satisfies."""
    found: List[Tuple[List[int], str, Severity]] = []
    seen: Set[str] = set()
    for node in tree.find_all(exp.EQ, exp.In):
        if isinstance(node, exp.EQ):
            if _is_operator_column(node.this):
                values = [int_literal(node.expression)]
            elif _is_operator_column(node.expression):
                values = [int_literal(node.this)]
            else:
                continue
        else:
            # NOT IN is an exclusion; a subquery is not a literal list.
            if isinstance(node.parent, exp.Not) or node.args.get("query") is not None:
                continue
            if not _is_operator_column(node.this) or not node.expressions:
                continue
            values = [int_literal(v) for v in node.expressions]
        if any(v is None or v in _SATISFIABLE for v in values):
            continue
        text = node.sql()
        if text in seen:
            continue
        seen.add(text)
        certain = restricts_rows(node) and not in_negated_scope(node)
        found.append((values, text, Severity.ERROR if certain else Severity.WARNING))
    return found


@register
class MeasurementOperatorConceptValidationRule(Rule):
    """measurement.operator_concept_id tested against concept ids outside the 'Meas Value Operator' domain."""

    rule_id = "domain_specific.measurement_operator_concept_validation"
    name = "Measurement Operator Concept Validation"
    description = (
        "measurement.operator_concept_id holds concepts of the 'Meas Value Operator' domain (<, <=, =, >=, >). "
        "An equality or IN test against ids outside that domain never matches on a conformant CDM."
    )
    severity = Severity.ERROR
    suggested_fix = (
        f"REPLACE: the concept id WITH an operator concept: {_OPERATORS_TEXT}. Use `operator_concept_id IS NULL OR "
        "operator_concept_id IN (0, 4172703)` for results recorded as an exact value."
    )
    long_description = (
        "CDM v5.4 restricts measurement.operator_concept_id to the 'Meas Value Operator' domain, whose comparison "
        "operators are <, <=, =, >= and >. A test for any other concept id cannot be true on a conformant CDM: as a "
        "row filter it empties the result (error), and in a CASE or OR branch it is dead code (warning). 0 (unmapped), "
        "lists that contain a valid operator, and exclusions such as `<>` or NOT IN are not reported."
    )
    example_bad = (
        "SELECT person_id, value_as_number FROM measurement\n"
        "WHERE measurement_concept_id = 3004410\n"
        "  AND operator_concept_id = 8554;"
    )
    example_good = (
        "SELECT person_id, value_as_number FROM measurement\n"
        "WHERE measurement_concept_id = 3004410\n"
        "  AND operator_concept_id = 4171756;"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT SUM(CASE WHEN operator_concept_id = 8554 THEN 1 ELSE 0 END) AS n_below_limit\nFROM measurement;"
            ),
            severity=Severity.WARNING,
            note="the test is not a row filter: the CASE branch can never be taken",
        ),
    )
    references = (
        cdm_table("measurement"),
        DQD_FIELD_THRESHOLDS,
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql or COLUMN_NAME not in sql.lower():
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            for values, text, severity in _find_violations(tree):
                listed = ", ".join(str(v) for v in values)
                effect = (
                    "so the query returns no rows on a conformant CDM"
                    if severity == Severity.ERROR
                    else "so this branch is never taken on a conformant CDM"
                )
                violations.append(
                    self.create_violation(
                        message=(
                            f"`{text}` can never match: {listed} "
                            f"{'is' if len(values) == 1 else 'are'} not in the 'Meas Value Operator' domain that "
                            f"measurement.operator_concept_id is restricted to, {effect}. "
                            f"The valid operator concepts are {_OPERATORS_TEXT}."
                        ),
                        severity=severity,
                        details={"invalid_values": values, "valid_operators": sorted(MEAS_VALUE_OPERATOR_CONCEPTS)},
                    )
                )
        return violations


__all__ = ["MeasurementOperatorConceptValidationRule"]

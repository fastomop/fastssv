"""Visit Detail / Visit Occurrence linkage rule.

OMOP semantic rule CLIN_044 (narrowed).

When a query references *both* ``visit_detail`` and ``visit_occurrence``,
the relational link between them must be on
``visit_detail.visit_occurrence_id = visit_occurrence.visit_occurrence_id``.
Linking on any other key (e.g. ``person_id`` alone) produces a
within-person cartesian fan-out and is a real correctness bug.

Earlier this rule also fired whenever ``visit_detail`` was used *without*
``visit_occurrence``, on the assumption that visit-level context is
always needed. That was an over-reach: legitimate Achilles-style
analyses count distinct ``visit_detail_concept_id`` per person, or
distribute LOS by detail concept, without ever needing visit-level
columns. Column-on-wrong-table mistakes (e.g. ``vd.visit_concept_id``)
are already caught by ``data_quality.schema_validation`` from the CDM
column catalogue, so this rule no longer duplicates that check.

Violation pattern:
    SELECT vd.*, vo.*
    FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.person_id = vo.person_id  -- WRONG key

Correct pattern:
    SELECT vd.*, vo.*
    FROM visit_detail vd
    JOIN visit_occurrence vo
      ON vd.visit_occurrence_id = vo.visit_occurrence_id
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    tables_co_used,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


VISIT_DETAIL = "visit_detail"
VISIT_OCCURRENCE = "visit_occurrence"
VISIT_OCCURRENCE_ID = "visit_occurrence_id"


def _norm(x: str) -> str:
    return normalize_name(x) if x else ""


def _check_visit_occurrence_id_linkage(node: exp.Expression, aliases: dict) -> bool:
    """True iff ``node`` is an EQ linking visit_detail and visit_occurrence on visit_occurrence_id."""
    if not isinstance(node, exp.EQ):
        return False

    left, right = node.this, node.expression
    if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
        return False

    lt, lc = resolve_table_col(left, aliases)
    rt, rc = resolve_table_col(right, aliases)
    if not (lt and lc and rt and rc):
        return False
    if _norm(lc) != VISIT_OCCURRENCE_ID or _norm(rc) != VISIT_OCCURRENCE_ID:
        return False

    return (_norm(lt) == VISIT_DETAIL and _norm(rt) == VISIT_OCCURRENCE) or (
        _norm(rt) == VISIT_DETAIL and _norm(lt) == VISIT_OCCURRENCE
    )


def _has_valid_join(tree: exp.Expression, aliases: dict) -> bool:
    """Check for a visit_occurrence_id linkage in any JOIN ON / WHERE / subquery."""
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue
        for eq in on_clause.find_all(exp.EQ):
            if _check_visit_occurrence_id_linkage(eq, aliases):
                return True

    for where in tree.find_all(exp.Where):
        for eq in where.find_all(exp.EQ):
            if _check_visit_occurrence_id_linkage(eq, aliases):
                return True

    # Subquery scope: a correlated reference to vd.visit_occurrence_id is enough.
    for subquery in tree.find_all(exp.Subquery):
        subquery_aliases = extract_aliases(subquery)
        has_vo = any(_norm(t) == VISIT_OCCURRENCE for t in subquery_aliases.values())
        if not has_vo:
            continue
        for col in subquery.find_all(exp.Column):
            _, col_name = resolve_table_col(col, subquery_aliases)
            if _norm(col_name) == VISIT_OCCURRENCE_ID:
                return True

    return False


@register
class VisitDetailVisitOccurrenceReferenceRule(Rule):
    rule_id = "domain_specific.visit_detail_visit_occurrence_reference"
    name = "Visit Detail Visit Occurrence Linkage"

    description = (
        "When both visit_detail and visit_occurrence are referenced, they must be "
        "linked on visit_occurrence_id = visit_occurrence_id."
    )

    severity = Severity.ERROR
    suggested_fix = (
        "JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id. "
        "Any other join key (e.g. person_id alone) silently fans rows out within a person."
    )
    example_bad = "SELECT vd.*, vo.*\nFROM visit_detail vd\nJOIN visit_occurrence vo ON vd.person_id = vo.person_id;"
    example_good = (
        "SELECT vd.*, vo.*\n"
        "FROM visit_detail vd\n"
        "JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue
            if not tables_co_used(tree, VISIT_DETAIL, VISIT_OCCURRENCE):
                continue

            aliases = extract_aliases(tree)
            if _has_valid_join(tree, aliases):
                continue

            violations.append(
                self.create_violation(
                    message=(
                        "visit_detail and visit_occurrence are both referenced but not "
                        "linked via visit_occurrence_id. Join on "
                        "visit_detail.visit_occurrence_id = visit_occurrence.visit_occurrence_id; "
                        "any other key will fan rows out within a person."
                    ),
                    severity=self.severity,
                    details={
                        "visit_detail_table": VISIT_DETAIL,
                        "visit_occurrence_table": VISIT_OCCURRENCE,
                        "expected_join_column": VISIT_OCCURRENCE_ID,
                    },
                )
            )

        return violations


__all__ = ["VisitDetailVisitOccurrenceReferenceRule"]

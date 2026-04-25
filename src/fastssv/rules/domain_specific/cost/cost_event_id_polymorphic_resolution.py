"""Cost Event ID Polymorphic Resolution Rule.

The OMOP CDM v5.4 ``cost.cost_event_id`` column is a *polymorphic* foreign
key — it points at a row in a different clinical event table depending
on ``cost.cost_domain_id``. For example,

    cost_domain_id = 'Drug'      → cost_event_id → drug_exposure.drug_exposure_id
    cost_domain_id = 'Procedure' → cost_event_id → procedure_occurrence.procedure_occurrence_id
    cost_domain_id = 'Visit'     → cost_event_id → visit_occurrence.visit_occurrence_id
    cost_domain_id = 'Device'    → cost_event_id → device_exposure.device_exposure_id
    cost_domain_id = 'Observation' → cost_event_id → observation.observation_id

Joining or filtering on ``cost_event_id`` without restricting
``cost_domain_id`` mixes IDs from disjoint sequences (a drug_exposure_id
of 1234 and a visit_occurrence_id of 1234 are unrelated), producing
either zero matches or — worse — coincidental matches that look
plausible but are semantically nonsense.

This is the same anti-pattern as ``location_history.entity_id``
requiring a ``domain_id`` filter; the two rules cover analogous
polymorphic-FK structures.
"""

from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    has_table_reference,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


COST = "cost"
COST_EVENT_ID = "cost_event_id"
COST_DOMAIN_ID = "cost_domain_id"


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_cost_column(col: exp.Column, aliases: dict, target_col: str) -> bool:
    table, col_name = resolve_table_col(col, aliases)
    if _norm(col_name) != target_col:
        return False
    if table:
        return _norm(table) == COST
    real_tables = {_norm(t) for t in aliases.values()}
    return real_tables == {COST} or COST in real_tables and len(real_tables) == 1


def _references_cost_event_id_in_join_or_where(
    tree: exp.Expression, aliases: dict
) -> List[str]:
    """Return SQL fragments where cost.cost_event_id is used in a JOIN or
    WHERE clause. Empty list means the column isn't being used to link or
    filter (and the rule shouldn't fire).
    """
    found: List[str] = []
    for col in tree.find_all(exp.Column):
        if not _is_cost_column(col, aliases, COST_EVENT_ID):
            continue
        if not is_in_where_or_join_clause(col):
            continue
        found.append(col.sql())
    return found


def _has_cost_domain_id_filter(tree: exp.Expression, aliases: dict) -> bool:
    """True if the query restricts ``cost.cost_domain_id`` via ``=`` or
    ``IN`` in WHERE / JOIN ON.
    """
    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.In)):
            continue
        if not is_in_where_or_join_clause(node):
            continue
        left = node.this
        if not isinstance(left, exp.Column):
            continue
        if not _is_cost_column(left, aliases, COST_DOMAIN_ID):
            continue
        if isinstance(node, exp.EQ):
            right = node.expression
            if isinstance(right, exp.Literal) and right.is_string:
                return True
        else:  # exp.In
            if any(isinstance(v, exp.Literal) and v.is_string
                   for v in (node.expressions or [])):
                return True
    return False


@register
class CostEventIdPolymorphicResolutionRule(Rule):
    """Require cost_domain_id when cost_event_id is joined or filtered."""

    rule_id = "domain_specific.cost_event_id_polymorphic_resolution"
    name = "Cost Event ID Polymorphic Resolution"

    description = (
        "cost.cost_event_id is a polymorphic FK whose target table is identified "
        "by cost.cost_domain_id. Queries that join or filter on cost_event_id "
        "without a cost_domain_id restriction mix IDs from disjoint sequences."
    )

    severity = Severity.ERROR

    suggested_fix = "ADD: `WHERE c.cost_domain_id = '<Domain>'` (e.g. 'Drug', 'Visit', 'Procedure') matching the table joined to cost_event_id. Without the filter the polymorphic FK matches IDs across disjoint sequences."
    long_description = (
        "cost.cost_event_id has no single foreign-key target; it points into "
        "a different table depending on cost.cost_domain_id. With "
        "cost_domain_id = 'Drug' it points into drug_exposure; with 'Visit' "
        "it points into visit_occurrence; and so on. Joining cost_event_id "
        "without filtering cost_domain_id either matches nothing (the "
        "value lives in a different sequence than the joined table's PK) "
        "or matches by coincidence (two unrelated tables happen to use "
        "overlapping integer ranges). Either outcome is a silent bug. "
        "The mirror rule for location_history.entity_id requires the same "
        "discipline."
    )

    example_bad = (
        "SELECT c.cost_id, de.drug_concept_id\n"
        "FROM cost c\n"
        "JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id;"
    )
    example_good = (
        "SELECT c.cost_id, de.drug_concept_id\n"
        "FROM cost c\n"
        "JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id\n"
        "WHERE c.cost_domain_id = 'Drug';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if COST not in sql.lower() or COST_EVENT_ID not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: set = set()

        for tree in trees:
            if not tree:
                continue
            if not has_table_reference(tree, COST):
                continue

            aliases = extract_aliases(tree)
            references = _references_cost_event_id_in_join_or_where(tree, aliases)
            if not references:
                continue
            if _has_cost_domain_id_filter(tree, aliases):
                continue

            for ref in references:
                if ref in seen:
                    continue
                seen.add(ref)
                violations.append(
                    self.create_violation(
                        message=(
                            f"`{ref}` used in a JOIN/WHERE without a "
                            f"cost_domain_id filter. cost_event_id is a "
                            f"polymorphic FK; without restricting "
                            f"cost_domain_id, the join either matches "
                            f"nothing or matches by coincidence."
                        ),
                        details={
                            "table": COST,
                            "column": COST_EVENT_ID,
                        },
                    )
                )

        return violations


__all__ = ["CostEventIdPolymorphicResolutionRule"]

"""Observation Period Unlinked Rule.

OBSERVATION_PERIOD.person_id is "the Person ID of the PERSON record for which the Observation Period is recorded"
(OMOP CDM v5.4). When a SELECT reads observation_period together with person or a person-level table as sources of
the same FROM list, only a predicate that equates ``observation_period.person_id`` with a value determined by the
other row ties each period to its own person. Without it the join is a cross product (PostgreSQL: "For every possible
combination of rows from T1 and T2 … the joined table will contain a row"), so a date-containment test checks one
patient's event against every patient's observation windows and rows multiply.

Linkage counts when it appears in that SELECT's JOIN ON or WHERE in either direction: an equality with a column of
another source under any name (``lhs.subject_id = op.person_id``), through casts, as ``op.person_id IN
(co.person_id)``, as ``USING (person_id)`` or ``NATURAL JOIN`` between observation_period and the other source, or
both sides fixed to the same patient by one literal (``co.person_id = 42 AND op.person_id = 42``). Tables wrapped in a
CTE or derived table that passes their rows through are judged as the tables. A SELECT where observation_period is
the only source (a UNION branch, a correlated ``EXISTS`` subquery) is not checked here.
"""

from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql, unwrap_cast

from ._cdm_keys import PERSON_ID_TABLES
from ._lineage import join_chains, owner_alias, scope_tables
from fastssv.core.references import BOOK_SQL, cdm_table, section
from fastssv.core.registry import register

OBSERVATION_PERIOD = "observation_period"

# Person-level tables: CDM v5.4 tables with a person_id column, other than observation_period itself.
PERSON_LEVEL_TABLES = frozenset(PERSON_ID_TABLES) - {OBSERVATION_PERIOD}


def _own_predicates(select: exp.Select) -> List[exp.Expression]:
    preds: List[exp.Expression] = []
    where = select.args.get("where")
    if where is not None:
        preds.append(where.this)
    for join in select.args.get("joins") or []:
        if join.args.get("on") is not None:
            preds.append(join.args["on"])
    return preds


def _shares_person_id(join: exp.Join) -> bool:
    if normalize_name(join.args.get("method") or "") == "natural":
        return True
    return any(normalize_name(getattr(u, "name", "") or str(u)) == "person_id" for u in join.args.get("using") or [])


def _linked_by_using(select: exp.Select, op_aliases: set) -> bool:
    """A ``USING (person_id)`` or NATURAL join pairs its right side with the sources before it in the same FROM item;
    it links observation_period only when observation_period and another source are among them."""
    for chain in join_chains(select):
        earlier: List[str] = []
        for item in chain:
            alias = (
                normalize_name(item.this.alias_or_name) if item.this is not None and item.this.alias_or_name else None
            )
            if isinstance(item, exp.Join) and _shares_person_id(item) and alias is not None:
                group = [*earlier, alias]
                if any(a in op_aliases for a in group) and any(a not in op_aliases for a in group):
                    return True
            if alias is not None:
                earlier.append(alias)
    return False


def _owner(node: exp.Expression, select: exp.Select) -> Optional[str]:
    """Alias of the source of ``select`` that supplies column ``node`` (an unqualified name is settled by which
    source can supply it); None for anything else."""
    return owner_alias(node, select) if isinstance(node, exp.Column) else None


def _op_person_id(node: exp.Expression, select: exp.Select, op_aliases: set) -> bool:
    return (
        isinstance(node, exp.Column) and normalize_name(node.name) == "person_id" and _owner(node, select) in op_aliases
    )


def _other_column(node: exp.Expression, select: exp.Select, op_aliases: set) -> bool:
    """A column of a source other than observation_period (an outer row, for a correlated reference)."""
    if not isinstance(node, exp.Column):
        return False
    owner = _owner(node, select)
    return owner not in op_aliases if owner is not None else bool(node.table)


def _links_op(select: exp.Select, op_aliases: set) -> bool:
    if _linked_by_using(select, op_aliases):
        return True
    op_pins: set = set()
    other_pins: set = set()
    for pred in _own_predicates(select):
        for node in pred.find_all(exp.In):
            # `op.person_id IN (co.person_id, …)` equates op.person_id with a column of another source.
            if node.find_ancestor(exp.Select) is not select or node.args.get("query") is not None:
                continue
            values = [unwrap_cast(v) for v in node.expressions]
            if _op_person_id(unwrap_cast(node.this), select, op_aliases) and any(
                _other_column(v, select, op_aliases) for v in values
            ):
                return True
        for eq in pred.find_all(exp.EQ):
            if eq.find_ancestor(exp.Select) is not select:
                continue
            sides = [unwrap_cast(eq.this), unwrap_cast(eq.expression)]
            for a, b in (sides, sides[::-1]):
                if not _op_person_id(a, select, op_aliases):
                    continue
                if _other_column(b, select, op_aliases):
                    return True
                if isinstance(b, exp.Literal):
                    op_pins.add(b.this)
            # Both sides pinned to the same patient by literals (`co.person_id = 42 AND op.person_id = 42`).
            for a, b in (sides, sides[::-1]):
                if (
                    isinstance(a, exp.Column)
                    and normalize_name(a.name) == "person_id"
                    and _owner(a, select) not in op_aliases
                    and isinstance(b, exp.Literal)
                ):
                    other_pins.add(b.this)
    return bool(op_pins & other_pins)


def _unlinked_partner(select: exp.Select) -> Optional[str]:
    local = scope_tables(select)
    op_aliases = {alias for alias, table in local.items() if table == OBSERVATION_PERIOD}
    if not op_aliases:
        return None
    partners = sorted({table for alias, table in local.items() if table in PERSON_LEVEL_TABLES})
    if not partners or _links_op(select, op_aliases):
        return None
    return partners[0]


@register
class ObservationPeriodUnlinkedRule(Rule):
    """Flag observation_period read alongside a person-level table without a person_id link."""

    rule_id = "joins.observation_period_unlinked"
    name = "Observation Period Not Linked on person_id"
    description = (
        "Flags a SELECT that reads observation_period alongside a person-level table without equating "
        "observation_period.person_id to the other side, which pairs every event with every person's "
        "observation periods."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: `op.person_id = <clinical>.person_id` to the JOIN ON (or WHERE) that brings in observation_period."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co, observation_period op\n"
        "WHERE co.condition_start_date BETWEEN op.observation_period_start_date\n"
        "  AND op.observation_period_end_date;"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN observation_period op ON co.person_id = op.person_id\n"
        "WHERE co.condition_start_date BETWEEN op.observation_period_start_date\n"
        "  AND op.observation_period_end_date;"
    )
    extra_examples = (
        RuleExample(
            "SELECT de.person_id\n"
            "FROM drug_exposure de\n"
            "JOIN observation_period op\n"
            "  ON de.drug_exposure_start_date >= op.observation_period_start_date\n"
            "  AND de.drug_exposure_start_date <= op.observation_period_end_date;",
            Severity.WARNING,
            "An explicit JOIN ... ON that tests only dates still matches other patients' observation periods.",
        ),
    )
    references = (
        cdm_table("observation_period"),
        section(BOOK_SQL, "exposure-cohort", "Exposure cohort"),
        Reference(
            "PostgreSQL documentation: Joined Tables",
            "https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if OBSERVATION_PERIOD not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            for select in tree.find_all(exp.Select):
                partner = _unlinked_partner(select)
                if partner is None:
                    continue
                violations.append(
                    self.create_violation(
                        message=(
                            f"observation_period is read together with {partner} but observation_period.person_id "
                            "is never equated to the other side, so each row is paired with every person's "
                            "observation periods."
                        ),
                        details={"partner_table": partner},
                    )
                )
        return violations


__all__ = ["ObservationPeriodUnlinkedRule"]

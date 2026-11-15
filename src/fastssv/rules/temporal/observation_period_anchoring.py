"""Observation Period Anchoring Rule.

The CDM v5.4 OBSERVATION_PERIOD user guide states what observation periods are needed for:

    Events may exist outside all of the time spans of the OBSERVATION_PERIOD records for a patient, however,
    absence of an Event outside these time spans cannot be construed as evidence of absence of an Event.
    Incidence or prevalence rates should only be calculated for the time of active OBSERVATION_PERIOD records.

and that selecting recorded events does not need them: "When constructing cohorts, outside Events can be used for
inclusion criteria definition".

So the rule reports the two uses the passage names, when the query never reads observation_period for them:

* an absence test on a clinical event table, made per person: ``NOT EXISTS (SELECT ... FROM drug_exposure de WHERE
  de.person_id = p.person_id ...)``, ``person_id NOT IN (SELECT person_id FROM condition_occurrence ...)``,
  ``LEFT JOIN measurement m ON m.person_id = ... WHERE m.<column> IS NULL``, or ``EXCEPT SELECT person_id FROM
  <event table>``. A test that is not tied to the event table's person_id (a foreign-key check on
  visit_occurrence_id, an uncorrelated NOT EXISTS) is not about a person's events and is not reported;
* a rate: a count over a clinical event table divided by a count of persons.

A query is anchored when observation_period is a FROM / JOIN source of the SELECT that makes the test, of a SELECT
enclosing it, or of a CTE or derived table those read. An observation_period read only in an unrelated scalar
subquery does not anchor anything. Plain selections and counts of recorded events, with or without a calendar
window, are not reported. Whether the omission matters depends on how the site built its observation periods and on
the study question, so the finding is a WARNING.

A query that reads observation_period but never links it to the clinical rows on person_id is the separate rule
``joins.observation_period_unlinked``.
"""

from typing import Iterator, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import _select_sources, _source_query, display_sql, normalize_name, parse_sql
from fastssv.core.references import BOOK_CDM, cdm_table, section
from fastssv.core.registry import register
from fastssv.rules.temporal._cdm_dates import PERSON_EVENT_TABLES
from fastssv.rules.temporal._typing import base_column, conjuncts, terminals, unwrap

OBSERVATION_PERIOD = "observation_period"
_MAX_DEPTH = 12


def _queries(node: Optional[exp.Expression]) -> List[exp.Select]:
    """The SELECTs of a query expression (every branch of a set operation)."""
    node = unwrap(node)
    if isinstance(node, exp.Subquery):
        return _queries(node.this)
    if isinstance(node, exp.SetOperation):
        return _queries(node.this) + _queries(node.expression)
    return [node] if isinstance(node, exp.Select) else []


def _base_tables(select: exp.Select, depth: int = 0) -> Iterator[str]:
    """Base CDM tables a SELECT reads through its FROM / JOIN sources, following CTEs and derived tables."""
    if depth > _MAX_DEPTH:
        return
    for src in _select_sources(select):
        query = _source_query(select, src)
        if query is not None:
            for branch in _queries(query):
                yield from _base_tables(branch, depth + 1)
        elif isinstance(src, exp.Table):
            yield normalize_name(src.name)


def _event_tables(select: exp.Select) -> Set[str]:
    return {t for t in _base_tables(select) if t in PERSON_EVENT_TABLES}


def _anchored(select: exp.Select) -> bool:
    """observation_period is a source of ``select``, of an enclosing SELECT, or of a CTE / derived table they read."""
    scope: Optional[exp.Select] = select
    while scope is not None:
        if OBSERVATION_PERIOD in set(_base_tables(scope)):
            return True
        parent = scope.parent
        scope = parent.find_ancestor(exp.Select) if parent is not None else None
    return False


def _is_event_person_id(node: Optional[exp.Expression]) -> bool:
    """A column that reads person_id of a clinical event table, through renames, CTEs and derived tables."""
    node = unwrap(node)
    while isinstance(node, (exp.Cast, exp.TryCast)):
        node = unwrap(node.this)
    if not isinstance(node, exp.Column):
        return False
    origin = base_column(node)
    return origin is not None and origin[1] == "person_id" and origin[0] in PERSON_EVENT_TABLES


def _links_person(predicates: List[Optional[exp.Expression]]) -> bool:
    """An AND-ed equality of an event table's person_id with another column: the test is about each person."""
    for predicate in predicates:
        for leaf in conjuncts(predicate):
            if (
                isinstance(leaf, exp.EQ)
                and isinstance(unwrap(leaf.this), exp.Column)
                and isinstance(unwrap(leaf.expression), exp.Column)
            ):
                if _is_event_person_id(leaf.this) or _is_event_person_id(leaf.expression):
                    return True
    return False


def _where_and_on(select: exp.Select) -> List[Optional[exp.Expression]]:
    where = select.args.get("where")
    return [where.this if where is not None else None, *(j.args.get("on") for j in select.args.get("joins") or [])]


def _left_join_absence(select: exp.Select) -> Iterator[Tuple[exp.Expression, str, List[exp.Select]]]:
    """``LEFT JOIN <event source> e ON e.person_id = ... WHERE e.<col> IS NULL`` in one SELECT, the IS NULL being an
    AND-ed conjunct of the WHERE clause."""
    where = select.args.get("where")
    if where is None:
        return
    for join in select.args.get("joins") or []:
        if normalize_name(join.args.get("side") or "") != "left":
            continue
        src = join.this
        alias = normalize_name(src.alias_or_name)
        query = _source_query(select, src)
        if query is not None:
            tables = {t for branch in _queries(query) for t in _base_tables(branch)}
        elif isinstance(src, exp.Table):
            tables = {normalize_name(src.name)}
        else:
            continue
        events = tables & PERSON_EVENT_TABLES
        using = {normalize_name(u.name) for u in join.args.get("using") or []}
        if not events or not ("person_id" in using or _links_person([join.args.get("on")])):
            continue
        for test in conjuncts(where.this):
            target = unwrap(test.this) if isinstance(test, exp.Is) else None
            if (
                isinstance(target, exp.Column)
                and isinstance(test.expression, exp.Null)
                and normalize_name(target.table) == alias
            ):
                yield test, sorted(events)[0], [select, *(_queries(query) if query is not None else [])]
                break


def _absence_tests(tree: exp.Expression) -> Iterator[Tuple[exp.Expression, str, List[exp.Select]]]:
    """(predicate, event table, SELECTs that make the test) for each absence test: the SELECT whose population is
    filtered and the event query it tests against."""
    for node in tree.find_all(exp.Not):
        inner = unwrap(node.this)
        if isinstance(inner, exp.Exists):
            subqueries = _queries(inner.this)
            if not any(_links_person(_where_and_on(sub)) for sub in subqueries):
                continue
        elif isinstance(inner, exp.In) and inner.args.get("query") is not None:
            subqueries = _queries(inner.args["query"])
            if not all(sub.expressions and _is_event_person_id(sub.expressions[0]) for sub in subqueries):
                continue
        else:
            continue
        population = node.find_ancestor(exp.Select)
        events = {t for sub in subqueries for t in _event_tables(sub)}
        if events and population is not None:
            yield node, sorted(events)[0], [population, *subqueries]
    for op in tree.find_all(exp.Except):
        right = _queries(op.expression)
        if not all(len(b.expressions) == 1 and _is_event_person_id(b.expressions[0]) for b in right):
            continue
        events = {t for branch in right for t in _event_tables(branch)}
        left = _queries(op.this)
        if events and left:
            yield op.expression, sorted(events)[0], [*left, *right]
    for select in tree.find_all(exp.Select):
        yield from _left_join_absence(select)


def _aggregate_sources(node: exp.Expression) -> Iterator[Tuple[exp.AggFunc, exp.Select]]:
    """(COUNT / SUM, the SELECT it aggregates over) for every aggregate a value is computed from, following columns
    through the CTEs and derived tables that project them."""
    stack: List[Tuple[exp.Expression, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > _MAX_DEPTH:
            continue
        for agg in current.find_all(exp.Count, exp.Sum):
            owner = agg.find_ancestor(exp.Select)
            if owner is not None:
                yield agg, owner
        for sub in current.find_all(exp.Subquery):
            for branch in _queries(sub):
                for proj in branch.expressions:
                    stack.append((proj, depth + 1))
        for col in current.find_all(exp.Column):
            if col.find_ancestor(exp.AggFunc) is not None:
                continue
            for terminal in terminals(col) or []:
                if terminal.expr is not None:
                    stack.append((terminal.expr, depth + 1))


def _rates(tree: exp.Expression) -> Iterator[Tuple[exp.Div, str, exp.Select]]:
    """(division, event table, SELECT holding the event count) for a count of events divided by a count of persons."""
    for div in tree.find_all(exp.Div):
        numerator = [(a, s) for a, s in _aggregate_sources(div.this)]
        denominator = [(a, s) for a, s in _aggregate_sources(div.expression)]
        persons = any({"person", OBSERVATION_PERIOD} & set(_base_tables(s)) for _, s in denominator)
        if not persons:
            continue
        for _, owner in numerator:
            events = _event_tables(owner)
            if events:
                yield div, sorted(events)[0], owner
                break


_ABSENCE_FIX = (
    "ADD: `JOIN observation_period op ON op.person_id = <population>.person_id AND "
    "op.observation_period_start_date <= <window_start> AND op.observation_period_end_date >= <window_end>`, so that "
    "only persons observed over the whole window of the absence test are counted."
)
_RATE_FIX = (
    "ADD: `JOIN observation_period op ON op.person_id = <event>.person_id AND <event_date> BETWEEN "
    "op.observation_period_start_date AND op.observation_period_end_date` to the numerator, and take the denominator "
    "from persons (or person-time) under observation in the same period."
)


@register
class ObservationPeriodAnchoringRule(Rule):
    """Absence tests and rates over clinical events that never consult observation_period."""

    rule_id = "temporal.observation_period_anchoring"
    name = "Observation Period Anchoring"
    description = (
        "Flags absence tests on clinical event tables (NOT EXISTS, NOT IN, LEFT JOIN ... IS NULL, EXCEPT) and "
        "event-count-per-person rates in queries that never read observation_period for them."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: a join to observation_period that restricts the population (for an absence test) or the events and "
        "the denominator (for a rate) to time under observation."
    )
    long_description = (
        "The CDM v5.4 OBSERVATION_PERIOD user guide says that events may exist outside every observation period, "
        "that the absence of an event outside these spans cannot be read as evidence that it did not happen, and "
        "that incidence or prevalence rates should only be calculated over active observation periods. The rule "
        "reports exactly those two uses when observation_period is not a source of the SELECT that makes them, of "
        "an enclosing SELECT, or of a CTE or derived table they read: an absence test on a clinical event table "
        "(NOT EXISTS / NOT IN over an event subquery, LEFT JOIN to an event source filtered on IS NULL, EXCEPT "
        "with an event query), and a count of events divided by a count of persons. The same guide allows outside "
        "events to be used for inclusion criteria, so selecting or counting recorded events, with or without a "
        "calendar window, is not reported. The finding is a WARNING: whether the omission changes the answer depends "
        "on how the site derived its observation periods and on the study question."
    )
    example_bad = (
        "SELECT p.person_id\n"
        "FROM person p\n"
        "WHERE NOT EXISTS (\n"
        "  SELECT 1 FROM drug_exposure de\n"
        "  WHERE de.person_id = p.person_id\n"
        "    AND de.drug_exposure_start_date >= DATE '2020-01-01'\n"
        "    AND de.drug_exposure_start_date < DATE '2021-01-01');"
    )
    example_good = (
        "SELECT p.person_id\n"
        "FROM person p\n"
        "JOIN observation_period op ON op.person_id = p.person_id\n"
        " AND op.observation_period_start_date <= DATE '2020-01-01'\n"
        " AND op.observation_period_end_date >= DATE '2020-12-31'\n"
        "WHERE NOT EXISTS (\n"
        "  SELECT 1 FROM drug_exposure de\n"
        "  WHERE de.person_id = p.person_id\n"
        "    AND de.drug_exposure_start_date >= DATE '2020-01-01'\n"
        "    AND de.drug_exposure_start_date < DATE '2021-01-01');"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT COUNT(DISTINCT co.person_id) * 1.0 / (SELECT COUNT(*) FROM person) AS prevalence\n"
                "FROM condition_occurrence co\n"
                "WHERE co.condition_concept_id = 201826\n"
                "  AND co.condition_start_date >= DATE '2020-01-01'\n"
                "  AND co.condition_start_date < DATE '2021-01-01';"
            ),
            severity=Severity.WARNING,
            note="Prevalence computed over all persons instead of persons under observation",
        ),
    )
    references = (
        cdm_table("observation_period"),
        section(BOOK_CDM, "observationPeriod", "OBSERVATION_PERIOD table"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []
        violations = []
        for tree in trees:
            if tree is None:
                continue
            seen = set()
            for predicate, table, scopes in _absence_tests(tree):
                if any(_anchored(s) for s in scopes) or ("absence", table) in seen:
                    continue
                seen.add(("absence", table))
                violations.append(
                    self.create_violation(
                        message=(
                            f"Absence of {table} records is tested (`{display_sql(predicate)[:120]}`) but "
                            "observation_period is never read for this population. The CDM v5.4 OBSERVATION_PERIOD "
                            "guide: absence of an event outside the observation periods cannot be construed as "
                            "evidence that it did not happen. Whether this matters depends on how the site built "
                            "its observation periods."
                        ),
                        suggested_fix=_ABSENCE_FIX,
                        details={"kind": "absence", "table": table},
                    )
                )
            for div, table, _owner in _rates(tree):
                if _anchored(div.find_ancestor(exp.Select)) or any(_anchored(s) for _, s in _aggregate_sources(div)):
                    continue
                if ("rate", table) in seen:
                    continue
                seen.add(("rate", table))
                violations.append(
                    self.create_violation(
                        message=(
                            f"A count of {table} records is divided by a count of persons "
                            f"(`{display_sql(div)[:120]}`) without observation_period. The CDM v5.4 "
                            "OBSERVATION_PERIOD guide: incidence or prevalence rates should only be calculated for "
                            "the time of active observation periods."
                        ),
                        suggested_fix=_RATE_FIX,
                        details={"kind": "rate", "table": table},
                    )
                )
        return violations


__all__ = ["ObservationPeriodAnchoringRule"]

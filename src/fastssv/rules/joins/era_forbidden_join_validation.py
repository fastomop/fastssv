"""Era to Visit Join Without Date Relation Rule.

``condition_era``, ``drug_era`` and ``dose_era`` hold one row per span of time and carry no visit key (OMOP CDM
v5.4: their only foreign keys are ``person_id`` and concept ids). A person usually has several eras and several
visits, so joining an era table to ``visit_occurrence`` or ``visit_detail`` on ``person_id`` alone pairs every
era of a person with every visit of that person. Rows repeat (eras × visits) and ``COUNT(*)``, ``SUM`` and
similar aggregates over the join are inflated.

The rule looks at one SELECT at a time and reports an era table and a visit table whose ``person_id`` values
that SELECT equates (directly, through ``person`` or another column, or with ``USING (person_id)``) when nothing
else relates the two. It stays silent when:

    - any other comparison relates the era and the visit, directly or through a third table: a date condition
      (``vo.visit_start_date BETWEEN de.drug_era_start_date AND de.drug_era_end_date``, also inside a
      ``CASE`` in the select list) or another key;
    - the join is not on ``person_id`` (a wrong key is reported by ``joins.person_id_join_validation`` and
      ``joins.visit_occurrence_id_join_validation``);
    - one side is pinned to a single row by its primary key;
    - row duplication cannot change the result: ``SELECT DISTINCT``, a ``GROUP BY`` without aggregates,
      aggregates that are all ``COUNT(DISTINCT …)``, ``MIN`` or ``MAX``, or the query of an ``IN (…)`` /
      ``EXISTS (…)``;
    - the SELECT feeds a later step (CTE, derived table, INSERT, CREATE TABLE AS) and passes on columns of both
      tables, and every SELECT of the statement that reads it compares or combines an era value with a visit value or
      cannot be changed by duplicated rows (an INSERT or CREATE TABLE AS leaves the statement, so a later statement
      may still relate them).

A comparison includes ``OVERLAPS``, and an expression that combines an era date with a visit date (``LEAST(...) -
GREATEST(...)``, a date difference) relates the two as well. Tables wrapped in a CTE or derived table that passes
their rows through are judged as the tables themselves.

Joins from an era through ``person`` to ``provider``, ``care_site`` or ``location`` are valid and not reported.

Violation pattern:
    SELECT de.person_id, COUNT(*) FROM drug_era de
    JOIN visit_occurrence vo ON de.person_id = vo.person_id
    GROUP BY de.person_id

Correct pattern:
    SELECT de.person_id, COUNT(*) FROM drug_era de
    JOIN visit_occurrence vo
      ON de.person_id = vo.person_id
     AND vo.visit_start_date BETWEEN de.drug_era_start_date AND de.drug_era_end_date
    GROUP BY de.person_id
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    _select_sources,
    column_owner,
    normalize_name,
    parse_sql,
    scope_nodes,
    scope_predicates,
    unwrap_cast,
)
from fastssv.core.references import cdm_table
from fastssv.core.registry import register
from fastssv.rules.joins.period_join_requires_date_overlap import _in_semi_join

from ._lineage import origin, scope_tables


ERA_TABLES: Dict[str, Tuple[str, str]] = {
    "condition_era": ("condition_era_id", "condition_era_start_date"),
    "drug_era": ("drug_era_id", "drug_era_start_date"),
    "dose_era": ("dose_era_id", "dose_era_start_date"),
}
VISIT_TABLES: Dict[str, Tuple[str, str]] = {
    "visit_occurrence": ("visit_occurrence_id", "visit_start_date"),
    "visit_detail": ("visit_detail_id", "visit_detail_start_date"),
}
PERSON = "person"
PERSON_ID = "person_id"
PERSON_KEYS = {PERSON_ID, "subject_id"}

_COMPARISONS = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Between,
    exp.In,
    exp.Like,
    exp.ILike,
    exp.Overlaps,
)
# Value expressions that combine an era date with a visit date (an overlap length, a gap in days): computing them
# per pair is the point of pairing every era with every visit.
_DATE_COMBINATIONS = (exp.Sub, exp.Add, exp.DateDiff, exp.Greatest, exp.Least)
_DUP_INSENSITIVE_AGGS = (exp.Min, exp.Max)


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[object, object] = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> None:
        self.parent[self.find(a)] = self.find(b)

    def joined(self, a, b) -> bool:
        return a in self.parent and b in self.parent and self.find(a) == self.find(b)


def _is_person_key_equality(node: exp.Expression) -> bool:
    if not isinstance(node, exp.EQ):
        return False
    left, right = unwrap_cast(node.this), unwrap_cast(node.expression)
    return (
        isinstance(left, exp.Column)
        and isinstance(right, exp.Column)
        and normalize_name(left.name) in PERSON_KEYS
        and normalize_name(right.name) in PERSON_KEYS
    )


def _person_linked_pairs(select: exp.Select, local: Dict[str, str]) -> List[Tuple[str, str]]:
    """(era_alias, visit_alias) pairs whose person_id values ``select`` equates, directly or through other columns
    (``de.person_id = p.person_id AND vo.person_id = p.person_id``), in ON/WHERE or ``USING (person_id)``."""
    columns = _UnionFind()
    for pred in scope_predicates(select):
        for node in scope_nodes(pred):
            if not isinstance(node, exp.EQ):
                continue
            left, right = unwrap_cast(node.this), unwrap_cast(node.expression)
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                lo, ro = column_owner(left, select), column_owner(right, select)
                if lo and ro:
                    columns.union((lo, normalize_name(left.name)), (ro, normalize_name(right.name)))

    earlier: List[str] = []
    for src in [select.args.get("from_") or select.args.get("from"), *(select.args.get("joins") or [])]:
        if src is None or src.this is None or not src.this.alias_or_name:
            continue
        alias = normalize_name(src.this.alias_or_name)
        using = [normalize_name(u.name) for u in (src.args.get("using") or [])] if isinstance(src, exp.Join) else []
        for col in using:
            for prior in earlier:
                columns.union((alias, col), (prior, col))
        earlier.append(alias)

    return sorted(
        (era, visit)
        for era, era_table in local.items()
        if era_table in ERA_TABLES
        for visit, visit_table in local.items()
        if visit_table in VISIT_TABLES and columns.joined((era, PERSON_ID), (visit, PERSON_ID))
    )


def _related_beyond_person(select: exp.Select, local: Dict[str, str]) -> _UnionFind:
    """Aliases of ``select`` that some comparison relates other than by equating person keys. The whole SELECT is
    read (select list, HAVING, correlated subqueries), since ``SUM(CASE WHEN vo.visit_start_date BETWEEN … END)``
    relates the rows as much as a join condition does. ``person`` is left out: it has one row per person, so a
    comparison with it relates nothing more than person_id does."""
    related = _UnionFind()
    for node in select.find_all(*_COMPARISONS, *_DATE_COMBINATIONS):
        if _is_person_key_equality(node):
            continue
        owners = set()
        for col in node.find_all(exp.Column):
            if col.find_ancestor(exp.Select) is select or col.table:
                owner = column_owner(col, select)
                if owner and local.get(owner) != PERSON:
                    owners.add(owner)
        owners = sorted(owners)
        for other in owners[1:]:
            related.union(owners[0], other)

    earlier: List[str] = []
    for src in [select.args.get("from_") or select.args.get("from"), *(select.args.get("joins") or [])]:
        if src is None or src.this is None or not src.this.alias_or_name:
            continue
        alias = normalize_name(src.this.alias_or_name)
        using = {normalize_name(u.name) for u in (src.args.get("using") or [])} if isinstance(src, exp.Join) else set()
        if using - PERSON_KEYS:
            for prior in earlier:
                related.union(alias, prior)
        earlier.append(alias)
    return related


def _pinned_to_one_row(select: exp.Select, alias: str, key: str) -> bool:
    """``alias``'s primary key is equated to a single value, so the other side cannot fan it out."""
    for pred in scope_predicates(select):
        for node in scope_nodes(pred):
            if not isinstance(node, exp.EQ):
                continue
            for side, other in ((node.this, node.expression), (node.expression, node.this)):
                side = unwrap_cast(side)
                if (
                    isinstance(side, exp.Column)
                    and normalize_name(side.name) == key
                    and column_owner(side, select) == alias
                    and other.find(exp.Column) is None
                ):
                    return True
    return False


def _is_duplicate_insensitive(select: exp.Select) -> bool:
    """The SELECT's result is unchanged if its input rows are duplicated."""
    outputs = [n for e in [*select.expressions, select.args.get("having")] if e is not None for n in scope_nodes(e)]
    if any(isinstance(n, exp.Window) for n in outputs):
        return False
    if select.args.get("distinct") is not None:
        return True
    aggs = [n for n in outputs if isinstance(n, exp.AggFunc)]
    if not aggs:
        return select.args.get("group") is not None
    return all(
        isinstance(a, _DUP_INSENSITIVE_AGGS) or (isinstance(a, exp.Count) and isinstance(a.this, exp.Distinct))
        for a in aggs
    )


def _exposes(select: exp.Select, alias: str) -> bool:
    """``select`` projects a column of ``alias`` other than its person key (``*`` and ``alias.*`` included)."""
    for projection in select.expressions:
        if isinstance(projection, exp.Star):
            return True
        if isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
            if normalize_name(projection.table) == alias:
                return True
            continue
        for node in scope_nodes(projection):
            if (
                isinstance(node, exp.Column)
                and normalize_name(node.name) not in PERSON_KEYS
                and column_owner(node, select) == alias
            ):
                return True
    return False


def _feeds_later_step(select: exp.Select) -> bool:
    """``select`` is not the statement's final result: a CTE, a derived table, or the source of an INSERT / CTAS."""
    node: exp.Expression = select
    while isinstance(node.parent, (exp.SetOperation, exp.Paren)):
        node = node.parent
    return node.parent is not None


def _consumer_selects(select: exp.Select) -> Optional[List[exp.Select]]:
    """The SELECTs of the same statement that read ``select``'s output (through the CTE or derived table it
    defines); None when the output leaves the statement (INSERT, CREATE TABLE AS), where a later statement may still
    relate the two tables."""
    node: exp.Expression = select
    while isinstance(node.parent, (exp.SetOperation, exp.Paren)):
        node = node.parent
    parent = node.parent
    if isinstance(parent, exp.Subquery):
        outer = parent.find_ancestor(exp.Select)
        return [outer] if outer is not None else None
    if isinstance(parent, exp.CTE):
        name = normalize_name(parent.alias)
        scope = parent.parent.parent if parent.parent is not None else None
        if scope is None:
            return None
        return [
            s
            for s in scope.find_all(exp.Select)
            if any(
                isinstance(src, exp.Table) and normalize_name(src.name) == name and not src.args.get("db")
                for src in _select_sources(s)
            )
        ]
    return None


def _consumers_relate(select: exp.Select, era_table: str, visit_table: str) -> bool:
    """Every consumer of ``select``'s output either compares or combines a value that comes from the era table with
    one that comes from the visit table, or cannot be changed by duplicated rows."""
    consumers = _consumer_selects(select)
    if consumers is None:
        return True
    if not consumers:
        return False
    for consumer in consumers:
        if _is_duplicate_insensitive(consumer) or _in_semi_join(consumer):
            continue
        related = False
        for node in consumer.find_all(*_COMPARISONS, *_DATE_COMBINATIONS):
            if node.find_ancestor(exp.Select) is not consumer or _is_person_key_equality(node):
                continue
            tables = {o[0] for col in node.find_all(exp.Column) if (o := origin(col)) is not None}
            if era_table in tables and visit_table in tables:
                related = True
                break
        if not related:
            return False
    return True


def _find_person_only_joins(select: exp.Select) -> List[Tuple[str, str, str, str]]:
    """(era_alias, era_table, visit_alias, visit_table) joined in ``select`` on person_id and nothing else."""
    local = scope_tables(select)
    pairs = _person_linked_pairs(select, local)
    if not pairs:
        return []
    if _in_semi_join(select) or _is_duplicate_insensitive(select):
        return []
    related = _related_beyond_person(select, local)
    later = _feeds_later_step(select)

    found = []
    for era, visit in pairs:
        era_table, visit_table = local[era], local[visit]
        if related.joined(era, visit):
            continue
        if _pinned_to_one_row(select, era, ERA_TABLES[era_table][0]) or _pinned_to_one_row(
            select, visit, VISIT_TABLES[visit_table][0]
        ):
            continue
        if (
            later
            and _exposes(select, era)
            and _exposes(select, visit)
            and _consumers_relate(select, era_table, visit_table)
        ):
            continue
        found.append((era, era_table, visit, visit_table))
    return found


@register
class EraForbiddenJoinValidationRule(Rule):
    """Era tables joined to a visit table on person_id need a condition relating the era to the visit."""

    rule_id = "joins.era_forbidden_join_validation"
    name = "Era to Visit Join Without Date Relation"
    description = (
        "Flags condition_era, drug_era or dose_era joined to visit_occurrence or visit_detail on person_id alone. "
        "Era tables have no visit key, so without a date condition every era of a person is paired with every "
        "visit of that person and counts or sums over the join are inflated."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: a condition relating the visit to the era, e.g. `AND <visit>.visit_start_date BETWEEN "
        "<era>.<era>_start_date AND <era>.<era>_end_date`, in addition to person_id. If only co-occurrence within "
        "a person is wanted, use `EXISTS`, `SELECT DISTINCT` or `COUNT(DISTINCT …)`."
    )
    example_bad = (
        "SELECT de.person_id, COUNT(*) AS visits_on_metformin\n"
        "FROM drug_era de\n"
        "JOIN visit_occurrence vo ON de.person_id = vo.person_id\n"
        "WHERE de.drug_concept_id = 1503297\n"
        "GROUP BY de.person_id;"
    )
    example_good = (
        "SELECT de.person_id, COUNT(*) AS visits_on_metformin\n"
        "FROM drug_era de\n"
        "JOIN visit_occurrence vo\n"
        "  ON de.person_id = vo.person_id\n"
        "  AND vo.visit_start_date BETWEEN de.drug_era_start_date AND de.drug_era_end_date\n"
        "WHERE de.drug_concept_id = 1503297\n"
        "GROUP BY de.person_id;"
    )
    extra_examples = (
        RuleExample(
            "SELECT ce.condition_concept_id, SUM(vd.visit_detail_end_date - vd.visit_detail_start_date) AS days\n"
            "FROM condition_era ce\n"
            "JOIN person p ON ce.person_id = p.person_id\n"
            "JOIN visit_detail vd ON vd.person_id = p.person_id\n"
            "GROUP BY ce.condition_concept_id;",
            Severity.WARNING,
            "The person_id link runs through person; the era and the visit detail are still unrelated in time.",
        ),
    )
    references = (
        cdm_table("drug_era"),
        cdm_table("condition_era"),
        cdm_table("visit_occurrence"),
        Reference(
            "PostgreSQL documentation: Joined Tables",
            "https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if not any(t in lowered for t in ERA_TABLES) or not any(t in lowered for t in VISIT_TABLES):
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, str]] = set()
        for tree in trees:
            if tree is None:
                continue
            for select in tree.find_all(exp.Select):
                for era, era_table, visit, visit_table in _find_person_only_joins(select):
                    if (era_table, visit_table) in seen:
                        continue
                    seen.add((era_table, visit_table))
                    visit_date = VISIT_TABLES[visit_table][1]
                    era_start = ERA_TABLES[era_table][1]
                    era_end = era_start.replace("_start_date", "_end_date")
                    violations.append(
                        self.create_violation(
                            message=(
                                f"{era_table} is joined to {visit_table} on person_id with no condition relating "
                                f"the era to the visit. {era_table} has no visit key, so every era of a person is "
                                f"paired with every {visit_table} row of that person: rows repeat and counts or "
                                f"sums over the join are inflated. Relate them in time, e.g. "
                                f"`{visit}.{visit_date} BETWEEN {era}.{era_start} AND {era}.{era_end}`."
                            ),
                            details={
                                "type": "era_visit_person_only_join",
                                "era_table": era_table,
                                "visit_table": visit_table,
                            },
                        )
                    )
        return violations


__all__ = ["EraForbiddenJoinValidationRule"]

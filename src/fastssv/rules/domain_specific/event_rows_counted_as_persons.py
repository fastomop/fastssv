"""Event Rows Counted as Persons Rule.

The CDM is person-centric: every clinical event table links to PERSON, and a person can have many rows in it (CDM
v5.4: one or more OBSERVATION_PERIOD records per person, "multiple, overlapping" PAYER_PLAN_PERIOD records, and
"multiple, discrete records" per subject in COHORT). The tables with a person key that is not their primary key are
generated from the CDM v5.4 field-level specification. ``COUNT(*)`` or ``COUNT(col)`` over such rows counts rows; as
a count of people it overstates the number of people whenever someone has several rows.

A SELECT is reported when

1. it counts rows: ``COUNT(*)``, ``COUNT(col)``, ``SUM(1)``, a windowed count not partitioned by a person key, or a
   ``COUNT(DISTINCT ...)`` of anything but a person key;
2. the count is presented as a count of people: the head noun of its alias is a person noun (``num_persons``,
   ``patients_with_drug``, ``overall_patients``), or the count takes the person key itself (``COUNT(person_id)``)
   and the alias does not name something else (``n_visits``);
3. its rows are event rows: its FROM / JOIN list reads one of those tables directly, or through a CTE or derived
   table that neither aggregates, groups, deduplicates nor limits its rows; and
4. it does not group by a person key.

An alias's head noun is its last word once counter words (``n``, ``num``, ``count``, ``total``) are dropped and the
phrase is cut at its first preposition, with trailing participles dropped: ``patients_with_drug`` and
``patients_exposed`` name patients, ``patient_visits`` names visits and ``person_days`` names days. The word lists are English, not taken from any query collection.

Two reads do not raise the grain. An observation_period confined to the one period containing a date (start ``<=``
the date ``<=`` end, ``BETWEEN``, or the same expressed as non-negative date differences) is one row per person,
because the specification says a person's observation periods "will not overlap". A table filtered to
``<primary key> IN (SELECT MIN/MAX(<primary key>) ... GROUP BY person_id)`` keeps one row per person.
"""

import re
from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    _explicit_projection,
    _pick_source,
    _query_branches,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    normalize_name,
    parse_sql,
    period_is_pinned,
    scope_predicates,
    unwrap_cast,
)
from fastssv.core.references import BOOK_CDM, cdm_table, section
from fastssv.core.registry import register

from ._ds1_cdm_facts import MANY_ROWS_PER_PERSON_TABLES
from ._ds1_scope import column_origins_with_paths, own_nodes

PERSON_KEYS = {"person_id", "subject_id"}
PERSON_NOUNS = {"person", "persons", "people", "patient", "patients", "subject", "subjects", "member", "members"}
COUNTER_WORDS = {"n", "num", "number", "nb", "no", "count", "counts", "cnt", "total", "tot", "distinct", "unique"}
PREPOSITIONS = {
    "with", "without", "on", "per", "in", "by", "from", "having", "at", "for", "during", "after", "before", "of",
    "to", "under", "over", "within",
}  # fmt: skip
_MAX_DEPTH = 8


def _head_noun(alias: str) -> Optional[str]:
    words = [w for w in re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", alias).lower()) if w]
    words = [w for w in words if w not in COUNTER_WORDS]
    while words and words[0] in PREPOSITIONS:
        words = words[1:]
    for i, w in enumerate(words):
        if w in PREPOSITIONS:
            words = words[:i]
            break
    # A trailing participle modifies the noun before it: patients_exposed, subjects_enrolled.
    while len(words) > 1 and words[-1].endswith("ed"):
        words = words[:-1]
    return words[-1] if words else None


def _is_person_key(node: exp.Expression) -> bool:
    node = unwrap_cast(node)
    if not isinstance(node, exp.Column):
        return False
    if normalize_name(node.name) in PERSON_KEYS:
        return True
    return any(c in PERSON_KEYS for _, _, c in column_origins_with_paths(node))


def _groups_by_person(select: exp.Select) -> bool:
    group = select.args.get("group")
    return group is not None and any(_is_person_key(e) for e in group.expressions)


def _one_row_per_person_by_key(select: exp.Select, alias: str, table: str) -> bool:
    """``<alias>.<pk> IN (SELECT MIN/MAX(<pk>) ... GROUP BY <person key>)`` keeps one row per person."""
    for pred in scope_predicates(select):
        for node in own_nodes(pred):
            if not isinstance(node, exp.In) or node.args.get("query") is None:
                continue
            col = unwrap_cast(node.this)
            if not isinstance(col, exp.Column):
                continue
            if not any(p[0][1] == alias and t == table for p, t, _ in column_origins_with_paths(col) if len(p) == 1):
                continue
            query = node.args["query"]
            branches = _query_branches(query.this if isinstance(query, exp.Subquery) else query)
            if len(branches) != 1:
                continue
            body = branches[0]
            group = body.args.get("group")
            first = body.expressions[0] if body.expressions else None
            first = first.this if isinstance(first, exp.Alias) else first
            if (
                group is not None
                and group.expressions
                and all(_is_person_key(e) for e in group.expressions)
                and isinstance(first, (exp.Min, exp.Max))
            ):
                return True
    return False


def _keeps_every_row(body: exp.Select) -> bool:
    """A derived table body that passes its source rows through: no grouping, deduplication, aggregate or limit."""
    if body.args.get("group") or body.args.get("distinct") or body.args.get("limit"):
        return False
    for proj in body.expressions:
        if any(isinstance(n, (exp.AggFunc, exp.Window)) for n in own_nodes(proj)):
            return False
    return True


def _many_row_tables(select: exp.Select, depth: int = 0) -> Set[str]:
    out: Set[str] = set()
    if depth > _MAX_DEPTH:
        return out
    for src in _select_sources(select):
        query = _source_query(select, src)
        if query is None:
            if not isinstance(src, exp.Table) or not src.name:
                continue
            table, alias = normalize_name(src.name), normalize_name(src.alias_or_name)
            if table not in MANY_ROWS_PER_PERSON_TABLES:
                continue
            if table == "observation_period" and period_is_pinned(select, alias, table, date_differences=True):
                continue
            if _one_row_per_person_by_key(select, alias, table):
                continue
            out.add(table)
            continue
        branches = _query_branches(query)
        if len(branches) == 1 and _keeps_every_row(branches[0]) and not _groups_by_person(branches[0]):
            out |= _many_row_tables(branches[0], depth + 1)
    return out


def _row_counts(expr: exp.Expression) -> List[Tuple[exp.Expression, bool]]:
    """(count node, takes the person key) for every row count in ``expr``'s own scope."""
    out: List[Tuple[exp.Expression, bool]] = []
    for node in own_nodes(expr):
        if isinstance(node, exp.Sum):
            arg = unwrap_cast(node.this)
            if isinstance(arg, exp.Literal) and not arg.is_string and str(arg.this) == "1":
                out.append((node, False))
            continue
        if not isinstance(node, exp.Count):
            continue
        window = node.parent if isinstance(node.parent, exp.Window) else None
        if window is not None and any(_is_person_key(p) for p in window.args.get("partition_by") or []):
            continue
        arg = node.this
        if isinstance(arg, exp.Distinct):
            if any(_is_person_key(e) for e in arg.expressions):
                continue
            out.append((node, False))
            continue
        out.append((node, arg is not None and _is_person_key(arg)))
    return out


def _derived_projection(col: exp.Column) -> Optional[Tuple[exp.Select, exp.Expression]]:
    """The SELECT and expression behind a column read from a CTE / derived table (one branch only)."""
    select = col.find_ancestor(exp.Select)
    if select is None:
        return None
    name = normalize_name(col.name)
    if col.table:
        scope, src = _source_by_qualifier(select, normalize_name(col.table))
    else:
        scope, src = select, _pick_source(select, _select_sources(select), name, 0)
    if scope is None or src is None:
        return None
    query = _source_query(scope, src)
    branches = _query_branches(query) if query is not None else []
    if len(branches) != 1:
        return None
    proj = _explicit_projection(branches[0], name)
    if proj is None:
        return None
    return branches[0], proj.this if isinstance(proj, exp.Alias) else proj


@register
class EventRowsCountedAsPersonsRule(Rule):
    """A person count taken over event-grain rows overstates the number of people."""

    rule_id = "domain_specific.event_rows_counted_as_persons"
    name = "Event Rows Counted as Persons"
    description = (
        "COUNT(*) or COUNT(col) over rows of a table with several rows per person (event, era, period and cohort "
        "tables), presented as a count of people, counts rows: a person with several records is counted several "
        "times."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: `COUNT(*)` / `COUNT(person_id)` WITH `COUNT(DISTINCT person_id)` (`subject_id` for cohort), or first "
        "reduce the rows to one per person (GROUP BY person_id in a subquery) before counting."
    )
    example_bad = (
        "SELECT drug_concept_id, COUNT(person_id) AS num_persons\nFROM drug_exposure\nGROUP BY drug_concept_id;"
    )
    example_good = "SELECT drug_concept_id, COUNT(DISTINCT person_id) AS num_persons\nFROM drug_exposure\nGROUP BY drug_concept_id;"
    references = (
        Reference(
            "PostgreSQL: Value Expressions, aggregate expressions (count(*) vs count(distinct ...))",
            "https://www.postgresql.org/docs/current/sql-expressions.html#SYNTAX-AGGREGATES",
        ),
        section(BOOK_CDM, "model-Conv", "General conventions of the model (person-centric event tables)"),
        cdm_table("observation_period"),
        cdm_table("payer_plan_period"),
        cdm_table("cohort"),
    )

    def _check_select(
        self, select: exp.Select, label: Optional[str], counts: List[Tuple[exp.Expression, bool]], depth: int = 0
    ) -> Optional[str]:
        if not counts or _groups_by_person(select):
            return None
        head = _head_noun(label) if label else None
        if head in PERSON_NOUNS:
            pass
        elif head is None and any(person_arg for _, person_arg in counts):
            pass
        else:
            return None
        tables = _many_row_tables(select)
        if not tables:
            return None
        listed = ", ".join(sorted(tables))
        shown = f"`{label}`" if label else f"`{counts[0][0].sql()}`"
        return (
            f"{shown} counts rows of {listed}, which can hold several rows per person, without COUNT(DISTINCT "
            f"person_id); a person with several records is counted several times."
        )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = (sql or "").lower()
        if "count" not in lowered and "sum" not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees:
            if tree is None:
                continue
            for select in tree.find_all(exp.Select):
                for proj in select.expressions:
                    label = proj.alias if isinstance(proj, exp.Alias) and proj.alias else None
                    expr = proj.this if isinstance(proj, exp.Alias) else proj
                    target_select, counts = select, _row_counts(expr)
                    inner = unwrap_cast(expr)
                    if not counts and isinstance(inner, exp.Column) and label:
                        behind = _derived_projection(inner)
                        if behind is not None:
                            target_select, counts = behind[0], _row_counts(behind[1])
                    message = self._check_select(target_select, label, counts)
                    if message and message not in seen:
                        seen.add(message)
                        violations.append(self.create_violation(message=message, details={"alias": label}))
        return violations


__all__ = ["EventRowsCountedAsPersonsRule"]

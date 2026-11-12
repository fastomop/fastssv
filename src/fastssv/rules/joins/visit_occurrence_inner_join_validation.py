"""Visit Occurrence INNER JOIN Validation Rule.

``visit_occurrence_id`` is optional on the OMOP CDM v5.4 event tables (field-level specification: isRequired = No on
condition_occurrence, drug_exposure, procedure_occurrence, measurement, observation, device_exposure and note; "While
not required, an attempt should be made to locate the VISIT_OCCURRENCE_ID"). An inner join from an event table to
``visit_occurrence`` on ``visit_occurrence_id`` therefore drops every event without a visit. ``visit_detail`` is not
covered: its ``visit_occurrence_id`` is required, so an inner join from it drops nothing.

The rule looks at one SELECT at a time. It fires when that SELECT links an event table to ``visit_occurrence`` on
``visit_occurrence_id`` with an inner join: ``JOIN … ON``, ``JOIN … USING (visit_occurrence_id)``, or a comma join
linked in WHERE, through casts and through CTEs or derived tables that pass the tables' rows through.

It stays silent when the SELECT shows that only visit-linked events are wanted, because the LEFT JOIN advice would
then change nothing or contradict the query:

- a predicate in ON, WHERE or HAVING that can only be true for a matched visit row (``visit_concept_id = 9201``,
  visit dates, ``vo.visit_occurrence_id IS NOT NULL``): it is never true for NULL-extended rows, so a LEFT JOIN
  returns the same rows (PostgreSQL: a WHERE row is kept only when the condition is true);
- ``<event>.visit_occurrence_id IS NOT NULL``, which removes the events without a visit explicitly;
- a GROUP BY on a visit column, which makes the result per visit.

``vo.<col> IS NULL`` is not such a predicate: it is true for NULL-extended rows.

Violation pattern:
    SELECT co.person_id
    FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id

Correct patterns:
    -- keep events without a visit
    SELECT co.person_id
    FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id

    -- deliberately restrict to inpatient visits
    SELECT co.person_id
    FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_concept_id = 9201
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.patch import freeform
from fastssv.core.patch import replace as patch_replace
from fastssv.core.references import CDM_FIELD_LEVEL, cdm_table
from fastssv.core.registry import register
from fastssv.core.helpers import (
    column_owner,
    scope_nodes,
    unwrap_cast,
)

from ._cdm_keys import OPTIONAL_VISIT_OCCURRENCE_ID_TABLES
from ._lineage import column_ref, scope_tables

VISIT_OCCURRENCE = "visit_occurrence"
VISIT_OCCURRENCE_ID = "visit_occurrence_id"
# Columns that link rather than filter: equating them restricts nothing about the visit itself.
_LINK_COLUMNS = {VISIT_OCCURRENCE_ID, "person_id"}

# Tables whose visit_occurrence_id is optional in CDM v5.4 (generated from the field-level specification).
CLINICAL_EVENT_TABLES = frozenset(OPTIONAL_VISIT_OCCURRENCE_ID_TABLES)

_FILTER_NODES = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.In, exp.Is, exp.Like, exp.ILike)

_JOIN_VO_RE = re.compile(
    r"(?P<kw>(?:\bINNER\s+)?\bJOIN)\s+(?:[\w\"]+\.)*\"?visit_occurrence\"?"
    r"(?:\s+(?:AS\s+)?(?P<alias>[A-Za-z_]\w*))?",
    re.IGNORECASE,
)
_OUTER_PREFIX_RE = re.compile(r"\b(?:LEFT|RIGHT|FULL|OUTER|CROSS|NATURAL)\s*$", re.IGNORECASE)
_NOT_ALIASES = {"on", "using", "where", "join", "inner", "left", "right", "full", "cross", "group", "order", "limit"}


def _build_left_join_patch(sql: str, join: exp.Join, vo_alias: str) -> dict:
    """REPLACE the ``JOIN`` keyword of the flagged inner join to ``visit_occurrence`` with ``LEFT JOIN``.

    Only offered when the join's target is visit_occurrence itself (making the event table's join LEFT would keep
    visits, not events) and the keyword can be located unambiguously; otherwise a FREEFORM instruction is returned.
    """
    instruction = freeform(
        f"REWRITE: make visit_occurrence `{vo_alias}` the optional side of the join "
        f"(`FROM <event> LEFT JOIN visit_occurrence {vo_alias} ON …`) so events without a visit are kept."
    )
    target = join.this
    if not (isinstance(target, exp.Table) and normalize_name(target.name) == VISIT_OCCURRENCE):
        return instruction
    spans = []
    for m in _JOIN_VO_RE.finditer(sql):
        if _OUTER_PREFIX_RE.search(sql[: m.start("kw")]):
            continue
        alias = (m.group("alias") or "").lower()
        alias = VISIT_OCCURRENCE if alias in ("", *_NOT_ALIASES) else alias
        if alias == vo_alias:
            spans.append(m.span("kw"))
    if len(spans) != 1:
        return instruction
    return patch_replace(spans[0], "LEFT JOIN")


def _attachments(select: exp.Select) -> Dict[str, Optional[exp.Join]]:
    """alias → the Join node that brings it into ``select`` (None for the FROM source)."""
    out: Dict[str, Optional[exp.Join]] = {}
    from_node = select.args.get("from_") or select.args.get("from")
    if from_node is not None and from_node.this is not None:
        out[normalize_name(from_node.this.alias_or_name)] = None
    for join in select.args.get("joins") or []:
        if join.this is not None:
            out[normalize_name(join.this.alias_or_name)] = join
    return out


def _is_outer(join: Optional[exp.Join]) -> bool:
    return join is not None and bool(join.args.get("side"))


def _vo_links(select: exp.Select, local: Dict[str, str]) -> List[Tuple[exp.Expression, Optional[exp.Join], str, str]]:
    """Inner event-to-visit links on visit_occurrence_id: (predicate or USING join, join holding it or None, vo alias,
    event alias)."""
    attach = _attachments(select)
    links = []

    def add(node: exp.Expression, join: Optional[exp.Join], aliases: List[Optional[str]]) -> None:
        tables = [local.get(a) if a else None for a in aliases]
        if VISIT_OCCURRENCE not in tables:
            return
        vo_alias = aliases[tables.index(VISIT_OCCURRENCE)]
        others = [a for a, t in zip(aliases, tables) if t in CLINICAL_EVENT_TABLES]
        if not others:
            return
        event_alias = others[0]
        if join is not None:
            if _is_outer(join):
                return
        elif _is_outer(attach.get(vo_alias)) or _is_outer(attach.get(event_alias)):
            return
        links.append((node, join, vo_alias, event_alias))

    holders: List[Tuple[Optional[exp.Join], exp.Expression]] = [
        (j, j.args["on"]) for j in select.args.get("joins") or [] if j.args.get("on") is not None
    ]
    where = select.args.get("where")
    if where is not None:
        holders.append((None, where.this))
    for join, pred in holders:
        for node in scope_nodes(pred):
            if not isinstance(node, exp.EQ):
                continue
            left, right = unwrap_cast(node.this), unwrap_cast(node.expression)
            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue
            refs = [column_ref(left, select, local), column_ref(right, select, local)]
            if any(r is None or r[1] != VISIT_OCCURRENCE_ID for r in refs):
                continue
            add(node, join, [refs[0][0], refs[1][0]])

    # `JOIN visit_occurrence vo USING (visit_occurrence_id)` links the joined source with the earlier sources.
    earlier: List[str] = []
    from_node = select.args.get("from_") or select.args.get("from")
    for src in [from_node, *(select.args.get("joins") or [])]:
        if src is None or src.this is None or not src.this.alias_or_name:
            continue
        alias = normalize_name(src.this.alias_or_name)
        using = [normalize_name(u.name) for u in src.args.get("using") or []] if isinstance(src, exp.Join) else []
        if VISIT_OCCURRENCE_ID in using:
            for prior in earlier:
                add(src, src, [alias, prior])
        earlier.append(alias)
    return links


def _is_not_null_test(node: exp.Expression) -> bool:
    return isinstance(node, exp.Is) and isinstance(node.parent, exp.Not)


def _restricted_to_visits(select: exp.Select, vo_alias: str, event_alias: str, link_nodes: Set[int]) -> bool:
    """True when ``select`` shows that only visit-linked events are wanted (see the module docstring)."""
    preds = [j.args["on"] for j in select.args.get("joins") or [] if j.args.get("on") is not None]
    for key in ("where", "having"):
        if select.args.get(key) is not None:
            preds.append(select.args[key].this)
    for pred in preds:
        for node in scope_nodes(pred):
            if not isinstance(node, _FILTER_NODES) or id(node) in link_nodes:
                continue
            if isinstance(node, exp.Is) and not _is_not_null_test(node):
                continue
            for col in node.find_all(exp.Column):
                owner = column_owner(col, select)
                name = normalize_name(col.name)
                if owner == vo_alias and (name not in _LINK_COLUMNS or isinstance(node, exp.Is)):
                    return True
                if owner == event_alias and name == VISIT_OCCURRENCE_ID and isinstance(node, exp.Is):
                    return True
    group = select.args.get("group")
    if group is not None and any(column_owner(col, select) == vo_alias for col in group.find_all(exp.Column)):
        return True
    return False


@register
class VisitOccurrenceInnerJoinValidationRule(Rule):
    """Detects inner joins from event tables to visit_occurrence that drop events without a visit."""

    rule_id = "joins.visit_occurrence_inner_join_validation"
    name = "Visit Occurrence INNER JOIN Validation"
    description = (
        "Detects inner joins from event tables to visit_occurrence on visit_occurrence_id that silently drop "
        "events whose (optional) visit_occurrence_id is NULL, when no visit attribute is filtered."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: `JOIN visit_occurrence` WITH `LEFT JOIN visit_occurrence` if events without a recorded visit "
        "should be kept (visit_occurrence_id is optional on event tables); keep the inner join only when the query "
        "is meant to be restricted to visit-linked events."
    )
    example_bad = (
        "SELECT co.person_id FROM condition_occurrence co\n"
        "JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id;"
    )
    example_good = (
        "SELECT co.person_id FROM condition_occurrence co\n"
        "LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id;"
    )
    extra_examples = (
        RuleExample(
            "SELECT de.drug_concept_id, COUNT(*)\n"
            "FROM drug_exposure de, visit_occurrence vo\n"
            "WHERE de.visit_occurrence_id = vo.visit_occurrence_id\n"
            "GROUP BY de.drug_concept_id;",
            Severity.WARNING,
            "A comma join linked in WHERE is an inner join too, and drops exposures without a visit.",
        ),
    )
    references = (cdm_table("condition_occurrence"), CDM_FIELD_LEVEL)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if VISIT_OCCURRENCE not in sql.lower():
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
                local = scope_tables(select)
                if VISIT_OCCURRENCE not in local.values():
                    continue
                links = _vo_links(select, local)
                link_ids = {id(node) for node, _, _, _ in links}
                for node, join, vo_alias, event_alias in links:
                    if _restricted_to_visits(select, vo_alias, event_alias, link_ids):
                        continue
                    key = node.sql()
                    if key in seen:
                        continue
                    seen.add(key)
                    event_table = local[event_alias]
                    via = "an INNER JOIN" if join is not None else "a comma join linked in WHERE"
                    violations.append(
                        self.create_violation(
                            message=(
                                f"{event_table} is linked to visit_occurrence on visit_occurrence_id by {via}; "
                                f"{event_table} rows with NULL visit_occurrence_id are dropped. Use LEFT JOIN unless "
                                f"the query should only cover visit-linked events."
                            ),
                            suggested_fix_patch=_build_left_join_patch(sql, join, vo_alias)
                            if join is not None
                            else None,
                            details={"event_table": event_table},
                        )
                    )
        return violations


__all__ = ["VisitOccurrenceInnerJoinValidationRule"]

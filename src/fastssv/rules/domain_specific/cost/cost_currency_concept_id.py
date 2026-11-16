"""Cost Currency Concept ID Rule.

CDM v5.4, COST.currency_concept_id: "A foreign key identifier to the concept representing the 3-letter code used to
delineate international currencies, such as USD for US Dollar." Every amount on a cost row is in that row's currency,
so an aggregate that combines amounts of several rows (a sum, a mean, a minimum, a variance, a percentile) mixes
currencies whenever the rows hold more than one. Whether they do depends on the data, so the finding is a WARNING.

The amounts are the float fields of COST in the CDM v5.4 field-level specification. An aggregate over one of them,
or over a value computed from one in a CTE or derived table, in the projection, HAVING or ORDER BY, is reported
unless the cost row it reads has its currency fixed or grouped in the aggregating SELECT or in a CTE / derived table
it reads from:

- ``currency_concept_id`` equal to one literal, parameter or scalar subquery, or ``IN`` a one-element list, as a
  condition that holds for every row (not under OR or NOT);
- ``currency_concept_id`` in GROUP BY (also by position) or in the window's PARTITION BY;
- the same, applied to a column of the concept row equi-joined with ``currency_concept_id`` (``cur.concept_code =
  'USD'``, ``GROUP BY cur.concept_name``).

COUNT, ARRAY_AGG and STRING_AGG do not combine amounts and are not reported. Aggregates fed by the same cost read are
reported once.
"""

from typing import Dict, Iterator, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    DERIVED,
    _query_branches,
    _select_sources,
    _source_query,
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_column_source,
    scope_nodes,
    scope_predicates,
    side_reads,
    unwrap_cast,
)
from fastssv.core.references import cdm_table
from fastssv.core.registry import register

from .._ds1_cdm_facts import COST_AMOUNT_COLUMNS
from .._ds1_scope import or_branches

TABLE_NAME = "cost"
CURRENCY_COLUMN = "currency_concept_id"
AMOUNT_COLUMNS: Set[str] = set(COST_AMOUNT_COLUMNS)

# Aggregates that keep the values apart instead of combining them.
NON_COMBINING = (exp.Count, exp.ArrayAgg, exp.GroupConcat, exp.AnyValue)

# A base-table read: (id of the SELECT that reads it, alias of the table in it).
ReadKey = Tuple[int, str]


def _is_amount(col: exp.Column, aliases: Dict[str, str]) -> bool:
    table, name = resolve_column_source(col, aliases)
    return normalize_name(table or "") == TABLE_NAME and normalize_name(name) in AMOUNT_COLUMNS


def _in_predicate(node: exp.Expression, stop: exp.Expression) -> bool:
    """``node`` is only tested on its way up to ``stop`` (``CASE WHEN total_paid > 0 THEN 1``), not carried."""
    parent = node.parent
    while parent is not None and parent is not stop:
        if isinstance(parent, exp.Predicate):
            return True
        parent = parent.parent
    return False


def _scope_selects(select: exp.Select, depth: int = 0) -> Iterator[exp.Select]:
    """``select`` and every SELECT behind the derived tables / CTEs it reads from."""
    yield select
    if depth > 8:
        return
    for src in _select_sources(select):
        query = _source_query(select, src)
        if query is None:
            continue
        for branch in _query_branches(query):
            yield from _scope_selects(branch, depth + 1)


def _single_value(node: Optional[exp.Expression]) -> bool:
    node = unwrap_cast(node) if node is not None else None
    if isinstance(node, exp.Subquery):
        return True  # a scalar subquery yields at most one value
    return isinstance(node, (exp.Literal, exp.Neg, exp.Placeholder, exp.Parameter))


def _reads(col: exp.Expression, table: str, column_names: Set[str]) -> Set[ReadKey]:
    """Base reads of ``table`` whose ``column_names`` columns flow into ``col``."""
    return {(id(r[0]), r[1]) for r in side_reads(col, relay=False) if r[2] == table and r[3] in column_names}


def _currency_concepts(select: exp.Select) -> Dict[ReadKey, Set[ReadKey]]:
    """concept read → the cost reads whose currency_concept_id it is equi-joined with, in ``select``'s conditions."""
    out: Dict[ReadKey, Set[ReadKey]] = {}
    for predicate in scope_predicates(select):
        for node in scope_nodes(predicate):
            if not isinstance(node, exp.EQ) or or_branches(node):
                continue
            for mine, other in ((node.this, node.expression), (node.expression, node.this)):
                costs = _reads(unwrap_cast(mine), TABLE_NAME, {CURRENCY_COLUMN})
                concepts = _reads(unwrap_cast(other), "concept", {"concept_id"})
                for concept in concepts:
                    out.setdefault(concept, set()).update(costs)
    return out


def _fixed_or_grouped_columns(select: exp.Select) -> List[exp.Expression]:
    """Columns ``select`` fixes to one value for every row, groups on or partitions on."""
    out: List[exp.Expression] = []
    for predicate in scope_predicates(select):
        for node in scope_nodes(predicate):
            if isinstance(node.parent, exp.Not) or or_branches(node):
                continue
            if isinstance(node, exp.EQ):
                for side, other in ((node.this, node.expression), (node.expression, node.this)):
                    side = unwrap_cast(side)
                    if isinstance(side, exp.Column) and _single_value(other):
                        out.append(side)
            elif isinstance(node, exp.In) and node.args.get("query") is None:
                values = node.expressions or []
                col = unwrap_cast(node.this)
                if isinstance(col, exp.Column) and len(values) == 1 and _single_value(values[0]):
                    out.append(col)
    group = select.args.get("group")
    if group is not None:
        for item in group.expressions:
            item = unwrap_cast(item)
            if isinstance(item, exp.Literal) and not item.is_string and str(item.this).isdigit():
                position = int(item.this) - 1
                if 0 <= position < len(select.expressions):
                    projected = select.expressions[position]
                    item = projected.this if isinstance(projected, exp.Alias) else projected
            out += [c for c in scope_nodes(item) if isinstance(c, exp.Column)]
    for proj in select.expressions:
        for window in scope_nodes(proj):
            if isinstance(window, exp.Window):
                for part in window.args.get("partition_by") or []:
                    out += [c for c in scope_nodes(part) if isinstance(c, exp.Column)]
    return out


def _pinned_reads(select: exp.Select) -> Set[ReadKey]:
    """Cost reads whose currency is fixed to one value, grouped or partitioned on in ``select``."""
    linked = _currency_concepts(select)
    pinned: Set[ReadKey] = set()
    for col in _fixed_or_grouped_columns(select):
        pinned |= _reads(col, TABLE_NAME, {CURRENCY_COLUMN})
        for concept in _reads(col, "concept", {"concept_id", "concept_code", "concept_name"}):
            pinned |= linked.get(concept, set())
    return pinned


def _computed_from_amount(select: exp.Select, name: str, aliases: Dict[str, str]) -> bool:
    """Whether output ``name`` of a derived table / CTE behind ``select`` carries a cost amount value
    (``total_paid / quantity``), as opposed to only testing one (``CASE WHEN total_paid > 0 THEN 1``)."""
    for inner in _scope_selects(select):
        if inner is select:
            continue
        for proj in inner.expressions:
            if normalize_name(proj.alias_or_name) != name:
                continue
            for col in scope_nodes(proj):
                if isinstance(col, exp.Column) and _is_amount(col, aliases) and not _in_predicate(col, proj):
                    return True
    return False


def _aggregating_parts(select: exp.Select) -> List[exp.Expression]:
    parts: List[exp.Expression] = list(select.expressions)
    if select.args.get("having") is not None:
        parts.append(select.args["having"])
    order = select.args.get("order")
    if order is not None:
        parts.extend(order.expressions)
    return parts


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    """One finding per unpinned cost read instance: every aggregate fed by the same ``cost`` read shares one fix."""
    reported: Set[ReadKey] = set()
    issues: List[str] = []
    # Innermost SELECTs first, so the finding names the aggregate closest to the cost read.
    for select in reversed(list(tree.find_all(exp.Select))):
        pinned: Optional[Set[ReadKey]] = None
        for part in _aggregating_parts(select):
            for agg in scope_nodes(part):
                if not isinstance(agg, exp.AggFunc) or isinstance(agg, NON_COMBINING):
                    continue
                # PERCENTILE_CONT(...) WITHIN GROUP (ORDER BY amount) keeps its argument outside the function node.
                scanned = agg.parent if isinstance(agg.parent, exp.WithinGroup) else agg
                for col in scope_nodes(scanned):
                    if not isinstance(col, exp.Column) or _in_predicate(col, scanned):
                        continue
                    if _is_amount(col, aliases):
                        target = f"cost.{normalize_name(resolve_column_source(col, aliases)[1])}"
                    elif resolve_column_source(col, aliases)[0] == DERIVED and _computed_from_amount(
                        select, normalize_name(col.name), aliases
                    ):
                        target = "cost-derived column"
                    else:
                        continue
                    reads = _reads(col, TABLE_NAME, AMOUNT_COLUMNS) - reported
                    if not reads:
                        continue
                    if pinned is None:
                        pinned = set()
                        for scoped in _scope_selects(select):
                            pinned |= _pinned_reads(scoped)
                    unpinned = reads - pinned
                    if not unpinned:
                        continue
                    reported |= unpinned
                    issues.append(
                        f"{agg.sql_name()} over {target} without fixing or grouping currency_concept_id. "
                        f"Each cost row is in its own currency; where the rows hold more than one, the result "
                        f"combines amounts in different currencies (e.g. USD with EUR)."
                    )
    return issues


@register
class CostCurrencyConceptIdRule(Rule):
    """An aggregate over cost amounts that does not fix or group the currency of the rows it combines."""

    rule_id = "domain_specific.cost_currency_concept_id"
    name = "Cost Currency Concept ID For Multi-Currency"
    description = (
        "Aggregating cost amount columns (total_paid, total_charge, paid_ingredient_cost, etc.) without fixing or "
        "grouping currency_concept_id combines amounts in different currencies wherever the cost rows hold more "
        "than one."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: `GROUP BY c.currency_concept_id` (each result row is then in one currency; rows without a currency "
        "form their own group) OR a filter to one currency, `c.currency_concept_id = <currency concept_id>` "
        "(44818668 is the US dollar), on the cost rows being aggregated."
    )
    long_description = (
        "Every amount on a cost row is denominated in that row's currency_concept_id. Summing or averaging "
        "total_paid across rows in several currencies produces a meaningless figure (GBP + USD + EUR). Either "
        "restrict the aggregate to one currency, or aggregate per currency so the figures can be converted before "
        "they are combined. currency_concept_id is optional in the CDM, so a filter on one currency drops rows that "
        "leave it empty; grouping keeps them as their own group."
    )
    example_bad = "SELECT SUM(total_paid) AS paid_total\nFROM cost;"
    example_good = "SELECT currency_concept_id, SUM(total_paid) AS paid_total\nFROM cost\nGROUP BY currency_concept_id;"
    extra_examples = (
        RuleExample(
            sql=(
                "WITH drug_costs AS (\n"
                "  SELECT c.cost_event_id, c.total_paid\n"
                "  FROM cost c\n"
                "  WHERE c.cost_domain_id = 'Drug'\n"
                ")\n"
                "SELECT AVG(total_paid) AS mean_paid\n"
                "FROM drug_costs;"
            ),
            severity=Severity.WARNING,
            note="the amount is aggregated outside the CTE that reads cost, and no scope fixes the currency",
        ),
    )
    references = (cdm_table("cost"),)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []
        sql_lower = sql.lower()
        if TABLE_NAME not in sql_lower or not any(col in sql_lower for col in AMOUNT_COLUMNS):
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            aliases = extract_aliases(tree)
            for msg in _find_violations(tree, aliases):
                violations.append(self.create_violation(message=msg, severity=self.severity))
        return violations


__all__ = ["CostCurrencyConceptIdRule"]

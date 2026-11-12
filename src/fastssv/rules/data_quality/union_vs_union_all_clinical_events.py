"""UNION vs UNION ALL for Clinical Events Rule.

UNION removes duplicate rows ("The result of UNION does not contain any duplicate rows unless the ALL option is
specified"). Rows taken from clinical event records can be identical on the selected columns and still be
distinct events (two visits of one person on the same day), so a UNION over them drops events and undercounts.

Flagged, as a WARNING (whether duplicates exist depends on the data):
    * a UNION (without ALL) where some arm returns rows taken one-to-one from a person-level CDM table (every CDM
      table that carries person_id), directly or through CTEs and derived tables;
    * a UNION whose arms are each a single aggregate row or a grouped result over that data but carry no label
      column that tells the arms apart: two arms with equal values (equal counts) collapse into one row.

Not flagged, because de-duplication cannot change the result or is what the query asks for:
    * every arm projects only foreign-key columns (person_id, visit_occurrence_id, *_concept_id, ...): the rows name
      other entities, so duplicates are the same entity (a cohort or concept set union);
    * the UNION feeds only set-style consumers: an enclosing SELECT DISTINCT, IN (...) / EXISTS (...), or a SELECT
      whose outputs are GROUP BY keys and COUNT(DISTINCT ...) / MIN / MAX aggregates;
    * every arm is SELECT DISTINCT (set semantics are explicit);
    * the arms carry literal label columns whose values differ between arms, so no row of one arm can equal a row of
      another, and every arm that returns event-level rows is itself unique (GROUP BY over its outputs, DISTINCT, or
      a single aggregate row);
    * no arm reads person-level CDM data (vocabulary unions).
"""

import re
from typing import Iterator, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import cdm_table
from fastssv.core.helpers import (
    _find_cte,
    _query_branches,
    _select_sources,
    _source_query,
    column_origins,
    normalize_name,
    parse_sql,
)
from fastssv.core.patch import replace as patch_replace
from fastssv.core.registry import register

from ._dq_foreign_keys import FOREIGN_KEY_COLUMNS, PERSON_LEVEL_TABLES


# Match `UNION` as a standalone keyword that is *not* immediately followed
# by `ALL` (so we don't false-match the already-correct form). Word
# boundaries on either side prevent matching e.g. `UNIONS`.
_UNION_KEYWORD_RE = re.compile(
    r"\bUNION\b(?!\s+ALL\b)",
    re.IGNORECASE,
)


def _find_unique_union_keyword_span(sql: str) -> Optional[Tuple[int, int]]:
    """If the source contains exactly one `UNION` (not followed by ALL),
    return its [start, end) byte span. Otherwise return None.
    """
    matches = list(_UNION_KEYWORD_RE.finditer(sql))
    if len(matches) != 1:
        return None
    m = matches[0]
    return (m.start(), m.end())


# --- Constants -------------------------------------------------------------

CLINICAL_EVENT_TABLES: Set[str] = set(PERSON_LEVEL_TABLES)


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_clinical_event_table(table: Optional[str]) -> bool:
    return _norm(table) in CLINICAL_EVENT_TABLES if table else False


def _is_union_without_all(union: exp.Union) -> bool:
    """
    Detect UNION (deduplicating) vs UNION ALL.

    sqlglot uses:
    - distinct=True  -> UNION (removes duplicates)
    - distinct=False -> UNION ALL (keeps all rows)
    """
    return union.args.get("distinct", False) is True


def _is_safely_deduplicated(union: exp.Union) -> bool:
    """
    Suppress warning if parent SELECT explicitly uses DISTINCT,
    indicating intentional deduplication.
    """
    parent = union.parent

    while parent:
        if isinstance(parent, exp.Select):
            if parent.args.get("distinct"):
                return True
            return False
        parent = parent.parent

    return False


def _iter_union_arms(union: exp.Union) -> Iterator[exp.Expression]:
    """Yield every SELECT arm in a (possibly chained) UNION tree."""
    for side in (union.this, union.expression):
        if side is None:
            continue
        if isinstance(side, exp.Union):
            yield from _iter_union_arms(side)
        else:
            yield side


def _projects_only_foreign_keys(node: exp.Expression) -> bool:
    """Every output of ``node`` is a CDM foreign-key column by lineage (person_id, visit_occurrence_id,
    *_concept_id, ...): its rows name other entities, so equal rows are the same entity."""
    if isinstance(node, exp.Subquery):
        node = node.this
    if not isinstance(node, exp.Select) or not node.expressions:
        return False
    for proj in node.expressions:
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        while isinstance(expr, (exp.Cast, exp.Paren)):
            expr = expr.this
        if not isinstance(expr, exp.Column) or isinstance(expr.this, exp.Star):
            return False
        origins = column_origins(expr)
        if not origins or not all(o in FOREIGN_KEY_COLUMNS for o in origins):
            return False
    return True


def _is_key_only_union(union: exp.Union) -> bool:
    arms = list(_iter_union_arms(union))
    return bool(arms) and all(_projects_only_foreign_keys(arm) for arm in arms)


def _as_select(node: exp.Expression) -> Optional[exp.Select]:
    if isinstance(node, exp.Subquery):
        node = node.this
    return node if isinstance(node, exp.Select) else None


def _is_single_aggregate_row(select: exp.Select) -> bool:
    if select.args.get("group") is not None or not select.expressions:
        return False
    return all(e.find(exp.AggFunc) is not None or e.find(exp.Column) is None for e in select.expressions) and any(
        e.find(exp.AggFunc) is not None for e in select.expressions
    )


def _arm_rows_unique(select: exp.Select) -> bool:
    """True when ``select`` cannot return two identical rows."""
    if select.args.get("distinct") is not None or _is_single_aggregate_row(select):
        return True
    group = select.args.get("group")
    if group is None:
        return False
    outputs = {normalize_name(p.alias_or_name) for p in select.expressions if p.alias_or_name}
    outputs |= {(p.this if isinstance(p, exp.Alias) else p).sql() for p in select.expressions}
    for key in group.expressions:
        name = normalize_name(key.name) if isinstance(key, exp.Column) else None
        if key.sql() not in outputs and name not in outputs:
            return False
    return True


def _is_aggregated(select: exp.Select) -> bool:
    return select.args.get("group") is not None or _is_single_aggregate_row(select)


def _exposes_event_rows(select: exp.Select, depth: int = 0) -> bool:
    """True when ``select`` returns rows that come one-to-one from clinical event records."""
    if depth > 12 or _is_aggregated(select):
        return False
    for src in _select_sources(select):
        query = _source_query(select, src)
        if query is None:
            if isinstance(src, exp.Table) and _is_clinical_event_table(src.name):
                return True
            continue
        if any(_exposes_event_rows(branch, depth + 1) for branch in _query_branches(query)):
            return True
    return False


def _reads_event_data(select: exp.Select, depth: int = 0) -> bool:
    """``select`` reads a person-level CDM table, directly or through CTEs and derived tables."""
    if depth > 12:
        return False
    for src in _select_sources(select):
        query = _source_query(select, src)
        if query is None:
            if isinstance(src, exp.Table) and _is_clinical_event_table(src.name):
                return True
            continue
        if any(_reads_event_data(branch, depth + 1) for branch in _query_branches(query)):
            return True
    return any(
        _reads_event_data(inner, depth + 1)
        for inner in select.find_all(exp.Select)
        if inner is not select and inner.find_ancestor(exp.Select) is select
    )


def _literal_label(select: exp.Select) -> dict:
    """Projection position -> literal value, for literal (label) outputs."""
    out = {}
    for i, proj in enumerate(select.expressions):
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        while isinstance(expr, (exp.Cast, exp.Paren)):
            expr = expr.this
        if isinstance(expr, exp.Literal):
            out[i] = (expr.is_string, expr.this)
    return out


def _arms_labelled_apart(arms: List[exp.Select]) -> bool:
    labels = [_literal_label(a) for a in arms]
    positions = set.intersection(*(set(lbl) for lbl in labels)) if labels else set()
    if not positions:
        return False
    keys = [tuple(lbl[p] for p in sorted(positions)) for lbl in labels]
    return len(set(keys)) == len(keys)


_DEDUP_INSENSITIVE_AGGS = (exp.Min, exp.Max)


def _is_dedup_insensitive_reader(select: exp.Select) -> bool:
    """A SELECT whose result is the same whether or not its input rows were de-duplicated."""
    if select.args.get("distinct") is not None:
        return True
    parent = select.parent
    while isinstance(parent, (exp.Subquery, exp.Paren)):
        parent = parent.parent
    if isinstance(parent, (exp.In, exp.Exists)):
        return True
    group = select.args.get("group")
    group_sql = {k.sql() for k in group.expressions} if group is not None else set()
    saw_agg = False
    for proj in select.expressions:
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        aggs = list(expr.find_all(exp.AggFunc))
        if not aggs:
            if expr.find(exp.Column) is not None and expr.sql() not in group_sql:
                return False
            continue
        saw_agg = True
        for agg in aggs:
            if isinstance(agg, _DEDUP_INSENSITIVE_AGGS):
                continue
            if isinstance(agg, exp.Count) and isinstance(agg.this, exp.Distinct):
                continue
            return False
    return saw_agg or group is not None


def _readers(union: exp.Expression) -> Optional[List[exp.Select]]:
    """SELECTs that consume the UNION's rows directly, or None when the UNION is the statement's result."""
    node = union
    parent = node.parent
    while isinstance(parent, (exp.Subquery, exp.Paren)):
        node, parent = parent, parent.parent
    if isinstance(parent, (exp.In, exp.Exists)):
        return []
    if isinstance(parent, exp.CTE):
        name = normalize_name(parent.alias)
        scope = parent.find_ancestor(exp.Select, exp.SetOperation, exp.Insert, exp.Create)
        root = scope if scope is not None else parent.root()
        out = []
        for table in root.find_all(exp.Table):
            if normalize_name(table.name) != name or _find_cte(table, name) is not parent:
                continue
            reader = table.find_ancestor(exp.Select)
            if reader is None:
                return None
            out.append(reader)
        return out or None
    if isinstance(node, exp.Subquery) and isinstance(parent, (exp.From, exp.Join)):
        reader = parent.find_ancestor(exp.Select)
        return [reader] if reader is not None else None
    return None


def _feeds_only_set_consumers(union: exp.Expression) -> bool:
    readers = _readers(union)
    if readers is None:
        return False
    return all(_is_dedup_insensitive_reader(r) for r in readers)


# --- Rule ------------------------------------------------------------------


@register
class UnionVsUnionAllClinicalEventsRule(Rule):
    """Detect UNION without ALL for clinical event tables."""

    rule_id = "data_quality.union_vs_union_all_clinical_events"
    name = "UNION vs UNION ALL for Clinical Events"

    description = (
        "Detects UNION (without ALL) when combining clinical event data and the de-duplication "
        "can change the result: identical-looking rows may be distinct clinical events, and "
        "unlabelled aggregate arms with equal values collapse into one row. Use UNION ALL."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `UNION` WITH `UNION ALL` when combining clinical event rows. Identical-looking rows can represent distinct events; UNION silently dedupes them."
    long_description = (
        "UNION de-duplicates rows; UNION ALL preserves them. Two distinct "
        "clinical events can share identical values on the selected "
        "columns (same person, same date, different visit) — "
        "de-duplicating them via UNION silently drops legitimate events "
        "and under-counts the cohort. Use UNION ALL for clinical-event "
        "combinations; use UNION only when you genuinely want to "
        "collapse duplicates and can justify why."
    )
    example_bad = (
        "SELECT person_id, condition_start_date AS event_date\n"
        "FROM condition_occurrence\n"
        "UNION\n"
        "SELECT person_id, drug_exposure_start_date\n"
        "FROM drug_exposure;"
    )
    example_good = (
        "SELECT person_id, condition_start_date AS event_date\n"
        "FROM condition_occurrence\n"
        "UNION ALL\n"
        "SELECT person_id, drug_exposure_start_date\n"
        "FROM drug_exposure;"
    )
    extra_examples = (
        RuleExample(
            sql="SELECT COUNT(*) AS n FROM condition_occurrence\nUNION\nSELECT COUNT(*) FROM drug_exposure;",
            severity=Severity.WARNING,
            note="Unlabelled aggregate arms: equal counts collapse into one row; add a label column per arm.",
        ),
    )
    references = (
        Reference(
            "PostgreSQL documentation: SELECT, UNION clause",
            "https://www.postgresql.org/docs/current/sql-select.html#SQL-UNION",
        ),
        cdm_table("condition_occurrence"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        # --- Fast pre-check ---
        if "union" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            for union in tree.find_all(exp.Union):
                if not _is_union_without_all(union):
                    continue
                # A distinct UNION de-duplicates its whole chain; report once at the top.
                if isinstance(union.parent, exp.Union) and _is_union_without_all(union.parent):
                    continue
                if _is_safely_deduplicated(union) or _is_key_only_union(union):
                    continue

                arm_nodes = list(_iter_union_arms(union))
                arms = [_as_select(a) for a in arm_nodes]
                if any(a is None for a in arms):
                    continue
                if not any(_reads_event_data(a) for a in arms):
                    continue
                if _feeds_only_set_consumers(union):
                    continue
                if all(a.args.get("distinct") is not None for a in arms):
                    continue  # every arm already de-duplicates: set semantics are explicit
                all_unique = all(_arm_rows_unique(a) for a in arms)
                event_arms = [a for a in arms if _exposes_event_rows(a)]
                if not event_arms and not all_unique:
                    continue  # rows come from vocabulary/other tables, not clinical event records
                if _arms_labelled_apart(arms) and all(_arm_rows_unique(a) for a in event_arms):
                    continue

                key = union.sql()
                if key in seen:
                    continue
                seen.add(key)

                # Structured patch: REPLACE the bare `UNION` keyword with
                # `UNION ALL`. Only applied when the source contains a
                # single `UNION` (without ALL) — otherwise the violation
                # falls back to FREEFORM auto-default.
                patch = None
                span = _find_unique_union_keyword_span(sql)
                if span is not None:
                    keyword_text = sql[span[0] : span[1]]
                    patch = patch_replace(span, f"{keyword_text} ALL")

                if all_unique:
                    issue = "union_without_all_unlabelled_arms"
                    message = (
                        "UNION (without ALL) over clinical event data whose arms carry no label column "
                        "that distinguishes them. "
                        "UNION removes duplicate rows across arms, so two arms that return identical rows "
                        "(e.g. equal counts) collapse into one row, and the output does not say which arm "
                        "a row came from. Use UNION ALL and add a literal label column per arm."
                    )
                else:
                    issue = "union_without_all_clinical_events"
                    message = (
                        "UNION (without ALL) detected when combining clinical event data. "
                        "This removes duplicate rows, but identical rows may represent "
                        "distinct clinical events (e.g., multiple visits on the same day)."
                    )

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=patch,
                        details={
                            "issue": issue,
                            "union_sql": union.sql(),
                        },
                    )
                )

        return violations


__all__ = ["UnionVsUnionAllClinicalEventsRule"]

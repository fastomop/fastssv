"""Comma-Separated Cross Join Rule.

OMOP semantic rule GAP_035:
Listing tables in FROM with commas and no WHERE join condition produces a
Cartesian cross join. In OMOP with tables containing millions of rows, this
generates billions of rows and crashes queries.

The Problem:
    Comma-separated FROM clauses without proper join conditions create
    Cartesian products (cross joins):

    SELECT * FROM condition_occurrence, drug_exposure
    WHERE condition_concept_id = 201826
    -- WRONG: No join condition! Creates 10M × 50M = 500 BILLION rows!

    Each clinical table in OMOP has millions of rows:
    - condition_occurrence: ~10M rows
    - drug_exposure: ~50M rows
    - measurement: ~100M rows
    - observation: ~50M rows

    Without a join condition (co.person_id = de.person_id), the query
    creates every possible combination of rows from both tables.

    This causes:
    - Out of memory errors
    - Database crashes
    - Production system locks
    - Hours of wasted compute time

Common mistakes:
    1. Forgot to add WHERE join condition
    2. Should have used JOIN...ON instead of comma syntax
    3. Accidentally omitted join predicate in WHERE clause
    4. Mixed old comma syntax with modern JOIN syntax

Violation pattern:
    SELECT *
    FROM condition_occurrence, drug_exposure
    WHERE condition_concept_id = 201826
    -- WRONG: Filters condition, but no join between tables!

    SELECT co.person_id, de.drug_concept_id
    FROM condition_occurrence co, drug_exposure de, measurement m
    WHERE co.condition_concept_id = 201826
      AND de.drug_concept_id = 1545999
    -- WRONG: Multiple clinical tables with no join conditions!

Correct pattern:
    SELECT *
    FROM condition_occurrence co
    JOIN drug_exposure de ON co.person_id = de.person_id
    WHERE co.condition_concept_id = 201826
    -- CORRECT: Explicit JOIN...ON

    SELECT *
    FROM condition_occurrence co, drug_exposure de
    WHERE co.person_id = de.person_id
      AND co.condition_concept_id = 201826
    -- CORRECT: Comma with WHERE join condition
"""

from typing import Dict, List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register
from fastssv.schemas import CDM_COLUMN_TYPES


# --- Constants -------------------------------------------------------------

# Large clinical tables where cross joins are catastrophic
LARGE_CLINICAL_TABLES = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "device_exposure",
    "visit_occurrence",
    "visit_detail",
    "specimen",
    "note",
    "episode",
    "person",
    "death",
}


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_large_clinical_table(table: Optional[str]) -> bool:
    return _norm(table) in LARGE_CLINICAL_TABLES if table else False


def _get_comma_separated_tables(tree: exp.Expression) -> List[tuple]:
    """Find FROM clauses with comma-separated tables.

    Sqlglot represents comma-separated tables as Join nodes with kind=None.
    Returns list of (select_node, table_list) tuples for proper scope handling.
    """
    comma_groups: List[tuple] = []

    # Look for Join nodes with kind=None (these are comma joins)
    for select in tree.find_all(exp.Select):
        tables: List[str] = []

        # Get the FROM table
        from_node = select.find(exp.From)
        if from_node:
            # Get the first table from FROM clause
            from_table = from_node.this
            if isinstance(from_table, exp.Table):
                tables.append(from_table.name)

        # Find comma joins in THIS SELECT only (not nested subqueries)
        # Use args.get("joins") instead of find_all() to avoid recursion
        for join in select.args.get("joins", []):
            kind = join.args.get("kind")
            on_clause = join.args.get("on")

            # Comma joins have kind=None and no ON clause
            if kind is None and on_clause is None:
                join_table = join.this
                if isinstance(join_table, exp.Table):
                    tables.append(join_table.name)

        # If we have 2+ tables, this is a comma-separated FROM
        if len(tables) >= 2:
            comma_groups.append((select, tables))

    return comma_groups


# Predicate-shaped nodes that can semantically connect two tables.
# Equi-join is the common case but range / interval-overlap joins (theta
# joins) are normal in temporal OMOP queries — joining events to time
# windows, observation periods, or eras — and used to false-positive the
# rule because the original walk only looked at ``exp.EQ`` with bare
# ``Column`` operands. ``In`` is included for the rare ``a.x IN (b.y, b.z)``
# semi-join shape; subqueries inside ``In`` have their own scope so the
# outer-scope table extraction doesn't pick them up as joins.
_JOIN_PREDICATE_TYPES = (
    exp.EQ,
    exp.NEQ,
    exp.LT,
    exp.LTE,
    exp.GT,
    exp.GTE,
    exp.Between,
    exp.In,
)


def _predicate_join_tables(
    predicate: exp.Expression,
    aliases: Dict[str, str],
    scope_tables: set,
) -> set:
    """Real table names referenced anywhere inside ``predicate``.

    Uses ``find_all(exp.Column)`` rather than checking direct operands so
    function-wrapped columns are seen — ``EXTRACT(YEAR FROM op1.x) <=
    t1.y`` references both ``op1`` and ``t1`` and counts as a join. Only
    columns whose enclosing ``Select`` is the predicate's own scope are
    included so a correlated subquery's inner columns don't blur the
    outer-scope join check.

    Unqualified columns are attributed against the schema catalogue so
    Achilles-style queries (``WHERE ppp.start_date <= obs_month_start``,
    where ``obs_month_start`` lives on a comma-joined scratch table) are
    recognised as theta-joins instead of false-firing as Cartesians:

    * Exactly one scope table owns the column in ``CDM_COLUMN_TYPES`` →
      attribute to that table.
    * No OMOP scope table owns it, and exactly one scope table is
      non-OMOP (a CTE, scratch / temp table, or derived view) →
      attribute to that one. SQL's name-resolution discipline guarantees
      the unqualified column must come from *some* scope table, and the
      catalogue rules out every OMOP candidate, so the lone non-OMOP
      table is the only resolution.
    * Otherwise leave it unattributed (ambiguous resolutions stay loud
      rather than silently picking a side).
    """
    outer_select = predicate.find_ancestor(exp.Select)
    local_alias_names: set = set()
    if outer_select is not None:
        from_node = outer_select.args.get("from_") or outer_select.args.get("from")
        for scope_node in [from_node, *(outer_select.args.get("joins") or [])]:
            src = getattr(scope_node, "this", None) if scope_node is not None else None
            if isinstance(src, exp.Table):
                local_alias_names.add(_norm(src.alias_or_name))
                local_alias_names.add(_norm(src.name))
            elif isinstance(src, exp.Subquery) and src.alias:
                local_alias_names.add(_norm(src.alias))
    omop_scope = {t for t in scope_tables if t in CDM_COLUMN_TYPES}
    non_omop_scope = scope_tables - omop_scope

    tables: set = set()
    for col in predicate.find_all(exp.Column):
        if col.find_ancestor(exp.Select) is not outer_select:
            continue
        # A column bound in an ENCLOSING scope (correlated subquery: `d1.person_id = p.person_id` inside EXISTS)
        # anchors the comma-joined table to the outer row — that is a join, not a Cartesian product
        # (17/17 agent-corpus findings of this rule were this shape, Study 1).
        if col.table and _norm(col.table) not in local_alias_names:
            tables.add("<outer>")
            continue
        t, c = resolve_table_col(col, aliases)
        if t:
            tables.add(_norm(t))
            continue
        cname = _norm(c)
        if not cname:
            continue
        owners = {ot for ot in omop_scope if cname in CDM_COLUMN_TYPES[ot]}
        if len(owners) == 1:
            tables.add(next(iter(owners)))
        elif not owners and len(non_omop_scope) == 1:
            tables.add(next(iter(non_omop_scope)))
    return tables


def _scope_tables(select: exp.Select) -> List[str]:
    """Real table names of every FROM / JOIN target in this Select scope
    (regardless of join kind). Includes ``INNER JOIN`` / ``LEFT JOIN`` /
    explicit ``CROSS JOIN`` targets — anything an alias might resolve to."""
    out: List[str] = []
    from_node = select.find(exp.From)
    if from_node and isinstance(from_node.this, exp.Table):
        out.append(from_node.this.name)
    for j in select.args.get("joins", []) or []:
        if isinstance(j.this, exp.Table):
            out.append(j.this.name)
    return out


def _comma_only_tables(select: exp.Select) -> List[str]:
    """The subset of join targets with no explicit ``ON`` clause (i.e. comma
    joins). These are the ones at risk of producing a Cartesian product
    when no predicate elsewhere connects them to the rest of the scope.
    The FROM table is never considered "comma-only" — it's the anchor
    other tables join TO."""
    out: List[str] = []
    for j in select.args.get("joins", []) or []:
        if j.args.get("kind") is None and j.args.get("on") is None:
            if isinstance(j.this, exp.Table):
                out.append(j.this.name)
    return out


def _all_predicate_clauses(select: exp.Select) -> List[exp.Expression]:
    """Boolean clauses where a join predicate can live: WHERE plus every
    explicit JOIN's ON clause. A comma-joined table is connected if any
    of these mention it alongside another scope table — the original
    rule only inspected WHERE, which missed setups where an explicit
    INNER JOIN's ON clause linked the chain together."""
    clauses: List[exp.Expression] = []
    where = select.args.get("where")
    if where:
        clauses.append(where)
    for j in select.args.get("joins", []) or []:
        on = j.args.get("on")
        if on:
            clauses.append(on)
    return clauses


def _unjoined_comma_tables(
    select: exp.Select,
    comma_tables: List[str],
    aliases: Dict[str, str],
) -> List[str]:
    """Return the subset of ``comma_tables`` that lack any predicate
    connecting them to another table in the SELECT's scope.

    A "predicate" here is any binary comparison (``=``, ``!=``, ``<``,
    ``<=``, ``>``, ``>=``), ``BETWEEN``, or ``IN`` whose column
    references span at least two distinct scope tables, one of which is
    the comma-joined table under test. Function-wrapped columns
    (``EXTRACT(YEAR FROM x)``, ``CAST(x AS …)``) count — the test runs
    over every column in the predicate via ``find_all``.

    Returns ``[]`` when every comma-joined table is properly anchored
    (the common case post-fix). Returns the offenders by name when at
    least one is genuinely unjoined.
    """
    if not comma_tables:
        return []
    scope_set = {_norm(t) for t in _scope_tables(select)}
    connected: set = set()
    for clause in _all_predicate_clauses(select):
        for pred in clause.find_all(_JOIN_PREDICATE_TYPES):
            all_tables = _predicate_join_tables(pred, aliases, scope_set)
            pred_tables = all_tables & scope_set
            if len(pred_tables) < 2 and not (pred_tables and "<outer>" in all_tables):
                continue
            for c in comma_tables:
                if _norm(c) in pred_tables:
                    connected.add(_norm(c))
    return [c for c in comma_tables if _norm(c) not in connected]


def _has_join_condition_in_where(
    select: exp.Select,
    tables: List[str],
    aliases: Dict[str, str],
) -> bool:
    """Back-compat wrapper retained so external callers (and tests) that
    import this name still work. ``True`` means "no Cartesian risk" —
    every comma-joined table in this Select scope is connected to
    something else.
    """
    comma_only = _comma_only_tables(select)
    return not _unjoined_comma_tables(select, comma_only, aliases)


# --- Rule ------------------------------------------------------------------


@register
class CommaSeparatedCrossJoinRule(Rule):
    """Detect accidental Cartesian products from comma-separated tables."""

    rule_id = "anti_patterns.comma_separated_cross_join"
    name = "Comma-Separated Cross Join"

    description = "Comma-separated FROM between clinical tables with no join predicate, produces a Cartesian product."

    severity = Severity.ERROR

    suggested_fix = (
        "ADD: a predicate connecting these tables (typically on `person_id`, "
        "sometimes `visit_occurrence_id` when both sides sit inside the same encounter), "
        "or write `CROSS JOIN` explicitly if the Cartesian product is intentional. "
        "Range / interval-overlap predicates (e.g. `start_date <= window_end "
        "AND end_date >= window_start`) count as joins too — the rule recognises them."
    )
    long_description = (
        "Comma-join syntax (FROM a, b) predates the explicit JOIN...ON form "
        "introduced in SQL-92 and is still common in analysts who come from "
        "SAS, SPSS, or older SQL dialects where it was idiomatic. In OMOP the "
        "mistake is rarely about performance first: even on a small dataset "
        "the cross-joined query returns row combinations that don't correspond "
        "to any real clinical event, every condition paired with every drug "
        "for every patient, so the results are semantically wrong before the "
        "query size becomes catastrophic. The natural join column between two "
        "clinical tables is almost always person_id; occasionally "
        "visit_occurrence_id when both sides sit inside the same encounter. "
        "If you genuinely want a Cartesian product (test-matrix generation, "
        "sparse-grid filling), write CROSS JOIN explicitly; this rule fires "
        "only on the implicit comma form, so an explicit CROSS JOIN documents "
        "the intent and stays silent."
    )
    example_bad = (
        "SELECT co.condition_occurrence_id, de.drug_exposure_id\n"
        "FROM condition_occurrence co, drug_exposure de\n"
        "WHERE co.condition_concept_id = 201820;"
    )
    # Two equivalent fixes: explicit JOIN for readability, or the minimal
    # WHERE-predicate patch when you're editing legacy comma-style SQL and
    # want to preserve its shape.
    example_good = (
        "-- Fix A: explicit JOIN ... ON (preferred for readability)\n"
        "SELECT co.condition_occurrence_id, de.drug_exposure_id\n"
        "FROM condition_occurrence co\n"
        "JOIN drug_exposure de\n"
        "  ON co.person_id = de.person_id\n"
        "WHERE co.condition_concept_id = 201820;\n"
        "\n"
        "-- Fix B: keep the comma syntax, add the join predicate to WHERE\n"
        "SELECT co.condition_occurrence_id, de.drug_exposure_id\n"
        "FROM condition_occurrence co, drug_exposure de\n"
        "WHERE co.person_id = de.person_id\n"
        "  AND co.condition_concept_id = 201820;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            # Find comma-separated table groups (returns list of (select_node, tables) tuples)
            comma_groups = _get_comma_separated_tables(tree)

            for select, tables in comma_groups:
                # Check if any are large clinical tables — small reference
                # / vocabulary cross joins aren't query-killers and aren't
                # worth flagging.
                has_large_table = any(_is_large_clinical_table(t) for t in tables)
                if not has_large_table:
                    continue

                # Per-table connectivity: which comma-joined tables lack a
                # predicate linking them to something else in scope? The
                # check inspects both WHERE *and* every explicit JOIN's ON
                # clause, and accepts theta-joins / function-wrapped
                # columns — so `op1.start_date <= t1.window_end` is a join.
                comma_only = _comma_only_tables(select)
                unjoined = _unjoined_comma_tables(select, comma_only, aliases)
                if not unjoined:
                    continue

                table_list = ", ".join(tables)
                unjoined_list = ", ".join(unjoined)
                violations.append(
                    self.create_violation(
                        message=(
                            f"Comma-separated FROM clause with large clinical tables "
                            f"({table_list}); no predicate connects "
                            f"{unjoined_list} to the rest of the FROM/JOIN scope. "
                            f"This creates a Cartesian product that can generate billions "
                            f"of rows and crash the query."
                        ),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details={
                            "issue": "comma_separated_cross_join",
                            "tables": tables,
                            "unjoined_tables": unjoined,
                        },
                    )
                )

        return violations


__all__ = ["CommaSeparatedCrossJoinRule"]

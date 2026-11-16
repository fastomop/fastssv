"""Ambiguous Column Reference Rule.

An unqualified column name must resolve to exactly one FROM/JOIN source of the
SELECT it appears in. When two or more OMOP CDM tables of that same SELECT
define the name, the database cannot resolve it and rejects the query. Only
CDM tables are counted: knowing which CDM tables share a column name (person_id,
visit_occurrence_id, ...) needs the CDM specification, while a clash between
sources the query defines itself (CTEs, derived tables) is ordinary SQL scoping
that the database reports on its own. Only names that are ambiguous under every
engine's scoping are reported, so the verdict is the same on every dialect.

    SELECT person_id
    FROM condition_occurrence co
    JOIN person p ON co.person_id = p.person_id
    -- ERROR: both co and p expose person_id

Resolution is scope-correct:

- Only the sources of the SELECT that owns the reference are counted. Tables
  read inside CTE bodies, derived tables or subqueries belong to their own
  scope; a derived table or CTE contributes only the columns it projects.
- CDM tables are resolved through the OMOP CDM v5.4 catalogue. CTEs and derived
  tables are resolved through their projection lists (including ``*`` expansion
  and ``AS t(col, ...)`` column lists) only to know which names they expose; they
  are never counted as providers. A source whose columns cannot be determined
  is never counted, so every finding is a certain ambiguity.
- ``JOIN ... USING (col)`` and ``NATURAL JOIN`` merge the joined columns into
  one. A name merged by any visible USING / NATURAL join is never flagged: some
  engines (DuckDB) bind it to the merged column even when a further source
  exposes it too.
- An ``ON`` clause only sees the sources joined up to and including its own
  JOIN, and so does a correlated subquery inside that ON clause or a LATERAL
  source.
- A name that matches an output column (including one produced by ``*`` /
  ``t.*``) outside the ON clauses is not flagged: ORDER BY / DISTINCT ON resolve
  it to the output column first, and engines such as BigQuery, DuckDB and
  Snowflake also accept select-list aliases in GROUP BY / HAVING / WHERE. Inside
  a projection, only an alias defined earlier in the same select list counts.
- ``* EXCLUDE / EXCEPT / REPLACE / RENAME`` and ``COLUMNS(...)`` hide which
  names a source exposes; those sources are treated as unknown.
"""

from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    _find_cte,
    _has_modified_star,
    _provides,
    _source_query,
    is_schema_qualified,
    normalize_name,
    parse_sql,
)
from fastssv.core.registry import register
from fastssv.schemas.cdm_column_types import get_table_columns


def _alias_columns(alias: Optional[exp.Expression]) -> Optional[List[str]]:
    if isinstance(alias, exp.TableAlias) and alias.columns:
        return [normalize_name(c.name) for c in alias.columns]
    return None


def _renamed_columns(select: exp.Select, src: exp.Expression) -> Optional[List[str]]:
    """Column names fixed by an ``AS t(a, b)`` list on the source or on the CTE it reads, if any."""
    own = _alias_columns(src.args.get("alias"))
    if own is not None:
        return own
    if isinstance(src, exp.Table) and not is_schema_qualified(src):
        cte = _find_cte(select, normalize_name(src.name))
        if cte is not None:
            return _alias_columns(cte.args.get("alias"))
    return None


def _source_exposes(select: exp.Select, src: exp.Expression, name: str) -> Optional[bool]:
    renamed = _renamed_columns(select, src)
    if renamed is not None:
        # A shorter list leaves the trailing columns under their own names, so absence proves nothing.
        return True if name in renamed else None
    return _provides(select, src, name, 0)


def _is_cdm_table(select: exp.Select, src: exp.Expression) -> bool:
    if not isinstance(src, exp.Table) or _renamed_columns(select, src) is not None:
        return False
    return _source_query(select, src) is None and bool(get_table_columns(normalize_name(src.name)))


def _merges_name(join: exp.Join, name: str) -> bool:
    if (join.args.get("method") or "").upper() == "NATURAL":
        return True
    return any(normalize_name(u.name) == name for u in join.args.get("using") or [])


def _providers(select: exp.Select, sources: List[exp.Expression], joins: List[exp.Join], name: str) -> int:
    """Number of distinct visible CDM-table columns called ``name`` among ``sources`` (USING / NATURAL merges
    counted once)."""
    exposes = [_is_cdm_table(select, s) and _source_exposes(select, s, name) is True for s in sources]
    count = sum(exposes)
    for i, join in enumerate(joins, start=1):
        if i < len(exposes) and exposes[i] and any(exposes[:i]) and _merges_name(join, name):
            count -= 1
    return count


def _owning_select(col: exp.Column) -> Optional[exp.Expression]:
    return col.find_ancestor(exp.Select, exp.SetOperation)


def _is_star(proj: exp.Expression) -> bool:
    return isinstance(proj, (exp.Star, exp.Columns)) or (
        isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)
    )


def _scope(select: exp.Select) -> Tuple[List[exp.Expression], List[exp.Join]]:
    """FROM/JOIN sources of ``select`` and the joins that bring them in, aligned (``joins[i]`` brings
    ``sources[i + 1]``). A LATERAL subquery is taken as the derived table it is, under the LATERAL's alias, and a
    parenthesised join tree (``JOIN (a JOIN b ON ...) ON ...``) contributes each of its tables."""
    from_node = select.args.get("from_") or select.args.get("from")
    sources: List[exp.Expression] = []
    joins: List[exp.Join] = []

    def add(node: exp.Expression, join: Optional[exp.Join]) -> None:
        if isinstance(node, exp.Lateral) and isinstance(node.this, exp.Subquery):
            sub = node.this.copy()
            if node.args.get("alias") is not None:
                sub.set("alias", node.args["alias"].copy())
            node = sub
        elif isinstance(node, exp.Subquery) and isinstance(node.this, exp.Table) and node.this.args.get("joins"):
            inner = node.this
            add(inner, join)
            for inner_join in inner.args.get("joins") or []:
                add(inner_join.this, inner_join)
            return
        sources.append(node)
        if join is not None:
            joins.append(join)

    if from_node is not None and from_node.this is not None:
        add(from_node.this, None)
    for join in select.args.get("joins") or []:
        if join.this is not None:
            add(join.this, join)
    return sources, joins


def _output_count(select: exp.Select, name: str) -> Optional[int]:
    """How many output columns of ``select`` are called ``name`` (``*`` / ``t.*`` expanded); None if unknown."""
    if _has_modified_star(select):
        return None
    sources, _ = _scope(select)
    count = 0
    for proj in select.expressions:
        if not _is_star(proj):
            count += normalize_name(proj.alias_or_name) == name
            continue
        if isinstance(proj, exp.Columns):
            return None
        qualifier = normalize_name(proj.table) if isinstance(proj, exp.Column) else None
        for src in sources:
            if qualifier is not None and normalize_name(src.alias_or_name) != qualifier:
                continue
            exposed = _source_exposes(select, src, name)
            if exposed is None:
                return None
            count += exposed
    return count


def _top_projection(col: exp.Column, select: exp.Select) -> Optional[exp.Expression]:
    node: exp.Expression = col
    while node.parent is not None and node.parent is not select:
        node = node.parent
    return node if node.parent is select and node in select.expressions else None


def _may_name_output_column(col: exp.Column, select: exp.Select) -> bool:
    """True when some engine can resolve ``col`` to an output column of ``select`` instead of a FROM source.

    ORDER BY / DISTINCT ON match output columns first everywhere; BigQuery, DuckDB, Snowflake, Redshift ... also
    accept select-list aliases in GROUP BY / HAVING / QUALIFY / WHERE or later projections."""
    name = normalize_name(col.name)
    if _own_join(col, select) is not None:
        return False
    proj = _top_projection(col, select)
    if proj is not None:
        return any(
            isinstance(p, exp.Alias) and normalize_name(p.alias) == name
            for p in select.expressions[: select.expressions.index(proj)]
        )
    return _output_count(select, name) != 0


def _own_join(col: exp.Column, select: exp.Select) -> Optional[exp.Join]:
    """The JOIN of ``select`` whose ON clause or LATERAL source holds ``col``, possibly inside a nested subquery.

    The nearest Join ancestor may belong to a subquery (a correlated ``EXISTS (... JOIN ... ON ...)`` inside an
    ON clause); visibility is set by the join of the SELECT being judged."""
    node = col.parent
    while node is not None and node is not select:
        if isinstance(node, exp.Join) and node.parent is select:
            return node
        node = node.parent
    return None


def _is_comma_join(join: exp.Join) -> bool:
    return not any(join.args.get(k) for k in ("on", "using", "kind", "side", "method"))


def _visible_sources(col: exp.Column, select: exp.Select, sources: List[exp.Expression], joins: List[exp.Join]):
    """Sources visible to ``col``: all of them, or for an ON clause only those joined so far.

    A comma binds looser than JOIN (``a, b JOIN c ON ...`` is ``a, (b JOIN c ON ...)``), so an ON clause
    cannot see sources listed before the nearest preceding comma."""
    join = _own_join(col, select)
    if join is None or join not in joins:
        return sources, joins
    idx = joins.index(join)
    start = 0
    for k in range(idx):
        if _is_comma_join(joins[k]):
            start = k + 1
    return sources[start : idx + 2], joins[start : idx + 1]


def _resolves_outward(col: exp.Column, inner: exp.Select) -> bool:
    """True when no source of ``inner`` (the SELECT that owns ``col``) can supply its name, so the database looks
    for it in the enclosing SELECT."""
    sources, _ = _scope(inner)
    name = normalize_name(col.name)
    return bool(sources) and all(_source_exposes(inner, s, name) is False for s in sources)


def _sees_enclosing_sources(inner: exp.Select, select: exp.Select) -> bool:
    """A subquery in the SELECT list, WHERE, HAVING or ON of ``select`` sees its FROM sources; a CTE body or a
    (non-LATERAL) derived table in its FROM list does not."""
    node: exp.Expression = inner
    while node.parent is not None and node.parent is not select:
        if isinstance(node, (exp.CTE, exp.With)):
            return False
        if isinstance(node, exp.Subquery) and isinstance(node.parent, (exp.From, exp.Join)):
            return False
        node = node.parent
    return not isinstance(node, (exp.With, exp.From))


def _scope_columns(select: exp.Select) -> List[exp.Column]:
    """Columns ``select`` resolves: its own, plus unqualified columns of nested subqueries that no source of their
    own SELECT can supply (correlated references)."""
    out: List[exp.Column] = []
    for col in select.find_all(exp.Column):
        owner = _owning_select(col)
        if owner is select:
            out.append(col)
        elif (
            isinstance(owner, exp.Select)
            and not col.table
            and owner.find_ancestor(exp.Select) is select
            and _sees_enclosing_sources(owner, select)
            and not isinstance(col.this, exp.Star)
            and _resolves_outward(col, owner)
        ):
            out.append(col)
    return out


def find_ambiguous_columns(select: exp.Select) -> Dict[str, int]:
    """Unqualified names in ``select``'s own scope that two or more of its CDM tables expose → provider count."""
    sources, joins = _scope(select)
    if len(sources) < 2 or len(joins) != len(sources) - 1:
        return {}
    found: Dict[str, int] = {}
    for col in _scope_columns(select):
        if col.table or isinstance(col.this, exp.Star):
            continue
        name = normalize_name(col.name)
        if not name or name in found or _may_name_output_column(col, select):
            continue
        visible, visible_joins = _visible_sources(col, select, sources, joins)
        # DuckDB binds an unqualified name to a USING/NATURAL merged column even when a later source also
        # exposes it, so that is not ambiguous everywhere.
        if any(_merges_name(j, name) for j in visible_joins):
            continue
        n = _providers(select, visible, visible_joins, name)
        if n >= 2:
            found[name] = n
    return found


@register
class AmbiguousColumnReferenceRule(Rule):
    """Unqualified column defined by two or more CDM tables of the same SELECT."""

    rule_id = "anti_patterns.ambiguous_column_reference"
    name = "Ambiguous Column Reference"

    description = (
        "Detects an unqualified column (e.g. person_id instead of co.person_id) that two or more "
        "OMOP CDM tables of the same SELECT define. The database cannot resolve it and rejects "
        "the query. Clashes between CTEs or derived tables the query defines are left to the "
        "database. Only names ambiguous under every engine's scoping are reported."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "QUALIFY: prefix the column with the alias of the source you mean (e.g. co.person_id), "
        "or join with USING (col) so the joined columns merge into one."
    )
    long_description = (
        "Many CDM tables share column names (person_id, visit_occurrence_id, provider_id). When two "
        "or more CDM tables of one SELECT define the same column name, an unqualified reference to "
        "it cannot be resolved and the database refuses to run the query. Only CDM tables, resolved "
        "through the CDM v5.4 column catalogue, are counted; a clash between CTEs or derived tables "
        "the query defines itself needs no CDM knowledge and is left to the database. The check is "
        "scope-aware: CTE bodies, derived tables and subqueries are separate scopes, and USING / "
        "NATURAL joins merge the joined column. A "
        "name some engine resolves anyway (one merged by USING / NATURAL, or one matching a "
        "select-list alias) is not reported, so the verdict is the same on every dialect."
    )
    example_bad = "SELECT person_id\nFROM condition_occurrence co\nJOIN person p ON co.person_id = p.person_id;"
    example_good = "SELECT co.person_id\nFROM condition_occurrence co\nJOIN person p ON co.person_id = p.person_id;"
    references = (
        Reference(
            "PostgreSQL documentation: Value Expressions, column references",
            "https://www.postgresql.org/docs/current/sql-expressions.html#SQL-EXPRESSIONS-COLUMN-REFS",
        ),
        Reference(
            "PostgreSQL documentation: Table Expressions, joined tables",
            "https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees or []:
            if tree is None:
                continue
            for select_idx, select in enumerate(tree.find_all(exp.Select)):
                for name, count in find_ambiguous_columns(select).items():
                    violations.append(
                        self.create_violation(
                            message=(
                                f"Ambiguous column reference '{name}': {count} sources of the same SELECT "
                                f"expose it, so the database cannot resolve it and rejects the query. "
                                f"Qualify it (e.g. alias.{name})."
                            ),
                            severity=self.severity,
                            suggested_fix=self.suggested_fix,
                            details={
                                "issue": "ambiguous_column_reference",
                                "column": name,
                                "source_count": count,
                                "select_index": select_idx,
                            },
                        )
                    )
        return violations


__all__ = ["AmbiguousColumnReferenceRule", "find_ambiguous_columns"]

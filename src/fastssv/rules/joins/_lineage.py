"""Lineage and scope helpers shared by the joins rules.

A join is judged on what its operands read, not on how they are spelled: a column is followed through CTEs,
derived tables, casts and renames to the base column it carries (``column_origins``), and a FROM/JOIN source that
only passes a table's rows through is treated as that table.
"""

from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple

from sqlglot import exp

from fastssv.core.helpers import (
    DERIVED,
    _pick_source,
    _query_branches,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    column_origins,
    normalize_name,
    unwrap_cast,
)
from fastssv.core.patch import locate

from ._cdm_keys import ENTITY

Origin = Tuple[str, str]
Instance = Tuple[int, str]

_MAX_WRAPPER_DEPTH = 12


def origin(col: exp.Column) -> Optional[Origin]:
    """The one base ``(table, column)`` whose value ``col`` carries unchanged, or None when it is computed, merged
    from several sources, or cannot be resolved."""
    origins = column_origins(col)
    if not origins or len(origins) != 1:
        return None
    table, name = next(iter(origins))
    return None if table == DERIVED else (table, name)


def entity(col: exp.Column) -> Optional[Origin]:
    """The CDM key ``col`` holds: the primary key its base column is, or the one its foreign key points at."""
    source = origin(col)
    return ENTITY.get(source) if source else None


def source_of(col: exp.Column) -> Tuple[Optional[exp.Select], Optional[exp.Expression]]:
    """The SELECT and the FROM/JOIN source of it that ``col`` is read through (enclosing SELECTs are searched for a
    correlated reference)."""
    select = col.find_ancestor(exp.Select)
    if select is None:
        return None, None
    if col.table:
        return _source_by_qualifier(select, normalize_name(col.table))
    src = _pick_source(select, _select_sources(select), normalize_name(col.name), 0)
    return (select, src) if src is not None else (None, None)


def instance(col: exp.Column) -> Optional[Instance]:
    """Identity of the row source ``col`` is read through: two columns with the same instance are on the same row."""
    scope, src = source_of(col)
    if scope is None or src is None or not src.alias_or_name:
        return None
    return id(scope), normalize_name(src.alias_or_name)


def reads_base_table_directly(col: exp.Column) -> bool:
    """``col`` is read from a base table of its SELECT, not through a CTE or derived table."""
    scope, src = source_of(col)
    return scope is not None and isinstance(src, exp.Table) and _source_query(scope, src) is None


def in_filter_position(node: exp.Expression) -> bool:
    """``node`` sits in a WHERE, ON or HAVING clause of its SELECT."""
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, (exp.Where, exp.Join, exp.Having)):
            return True
        parent = parent.parent
    return False


class ColumnPair(NamedTuple):
    """Two columns a predicate requires to be equal. ``node`` is the ``=`` or the ``IN``."""

    node: exp.Expression
    left: exp.Column
    right: exp.Column


def _single_projected_column(query: exp.Expression) -> Optional[exp.Column]:
    branches = _query_branches(query)
    if len(branches) != 1 or len(branches[0].expressions) != 1:
        return None
    projected = branches[0].expressions[0]
    projected = unwrap_cast(projected.this if isinstance(projected, exp.Alias) else projected)
    return projected if isinstance(projected, exp.Column) and not isinstance(projected.this, exp.Star) else None


def equated_columns(tree: exp.Expression) -> Iterator[ColumnPair]:
    """Every pair of columns the statement equates in a WHERE, ON or HAVING clause: ``a.x = b.y`` (through CAST and
    parentheses) and ``a.x IN (SELECT b.y …)``, whose subquery is a semi-join on that column."""
    for node in tree.find_all(exp.EQ):
        left, right = unwrap_cast(node.this), unwrap_cast(node.expression)
        if isinstance(left, exp.Column) and isinstance(right, exp.Column) and in_filter_position(node):
            yield ColumnPair(node, left, right)
    for node in tree.find_all(exp.In):
        query = node.args.get("query")
        left = unwrap_cast(node.this)
        if query is None or not isinstance(left, exp.Column) or not in_filter_position(node):
            continue
        projected = _single_projected_column(query)
        if projected is not None:
            yield ColumnPair(node, left, projected)


class KeyMismatch(NamedTuple):
    """A predicate that equates the keys of two different CDM entities."""

    node: exp.Expression
    left: exp.Column
    right: exp.Column
    left_origin: Origin
    right_origin: Origin
    left_entity: Origin
    right_entity: Origin


def key_mismatches(tree: exp.Expression) -> Iterator[KeyMismatch]:
    """Predicates that equate two columns holding keys of different CDM entities (``person_id`` with
    ``visit_occurrence_id``, ``provider_id`` with ``care_site_id``). Columns without a declared target (polymorphic
    keys such as ``fact_id_1`` or ``cohort.subject_id``, columns of tables outside the CDM, computed values) are not
    judged, and neither is a comparison of two columns of the same row."""
    for pair in equated_columns(tree):
        left_origin, right_origin = origin(pair.left), origin(pair.right)
        if left_origin is None or right_origin is None:
            continue
        left_entity, right_entity = ENTITY.get(left_origin), ENTITY.get(right_origin)
        if left_entity is None or right_entity is None or left_entity == right_entity:
            continue
        left_instance, right_instance = instance(pair.left), instance(pair.right)
        if left_instance is not None and left_instance == right_instance:
            continue
        yield KeyMismatch(pair.node, pair.left, pair.right, left_origin, right_origin, left_entity, right_entity)


PERSON_KEY: Origin = ("person", "person_id")
VISIT_KEY: Origin = ("visit_occurrence", "visit_occurrence_id")
PROVIDER_KEY: Origin = ("provider", "provider_id")
PRECEDING_VISIT: Origin = ("visit_occurrence", "preceding_visit_occurrence_id")
CONCEPT_KEY: Origin = ("concept", "concept_id")

RULE_PRECEDING_VISIT = "joins.preceding_visit_occurrence_validation"
RULE_PERSON_ID = "joins.person_id_join_validation"
RULE_VISIT_OCCURRENCE_ID = "joins.visit_occurrence_id_join_validation"
RULE_PROVIDER_CARE_SITE = "joins.provider_care_site_join_validation"
RULE_PROVIDER = "joins.provider_join_validation"


def reporting_rule(mismatch: KeyMismatch) -> Optional[str]:
    """The one joins rule that reports ``mismatch``, so a single wrong predicate yields a single finding: the most
    specific key involved decides."""
    origins = {mismatch.left_origin, mismatch.right_origin}
    entities = {mismatch.left_entity, mismatch.right_entity}
    if CONCEPT_KEY in entities:
        return None  # an entity key equated with a concept id is reported by anti_patterns.join_key_validation
    if PRECEDING_VISIT in origins:
        return RULE_PRECEDING_VISIT
    if PERSON_KEY in entities:
        return RULE_PERSON_ID
    if VISIT_KEY in entities:
        return RULE_VISIT_OCCURRENCE_ID
    if {table for table, _ in origins} == {"provider", "care_site"}:
        return RULE_PROVIDER_CARE_SITE
    if PROVIDER_KEY in entities:
        return RULE_PROVIDER
    return None


def written(col: exp.Column) -> str:
    """``col`` as written in the query: ``alias.column`` or the bare column."""
    return f"{col.table}.{col.name}" if col.table else col.name


def described(col: exp.Column, source: Origin) -> str:
    """``col`` for a message: as written, with the base column it carries when the two differ."""
    text = written(col)
    base = f"{source[0]}.{source[1]}"
    return text if text.lower() == base else f"{text} ({base})"


def locate_equality(sql: str, left: exp.Column, right: exp.Column) -> Optional[Tuple[Tuple[int, int], bool]]:
    """Span of ``left = right`` in ``sql`` as written, in either operand order; the flag is True when the operands
    are found swapped. None when the text is not found exactly once (casts, reformatting, repeats)."""
    span = locate(sql, f"{written(left)} = {written(right)}")
    if span is not None:
        return span, False
    span = locate(sql, f"{written(right)} = {written(left)}")
    if span is not None:
        return span, True
    return None


def with_column(col: exp.Column, name: str) -> str:
    """Column ``name`` of the row source ``col`` reads, qualified so it cannot be ambiguous."""
    if col.table:
        return f"{col.table}.{name}"
    _, src = source_of(col)
    return f"{src.alias_or_name}.{name}" if src is not None and src.alias_or_name else name


# --- FROM/JOIN sources that stand for a base table ---


def _passes_rows_through(query: exp.Expression) -> Optional[exp.Select]:
    """``query`` when it returns the rows of its single source one for one (it may filter or rename, but not join,
    group, de-duplicate, limit, aggregate or number them)."""
    if isinstance(query, exp.Subquery):
        query = query.this
    if not isinstance(query, exp.Select):
        return None
    if query.args.get("joins") or len(_select_sources(query)) != 1:
        return None
    if any(query.args.get(key) is not None for key in ("group", "distinct", "having", "limit", "qualify")):
        return None
    for projection in query.expressions:
        if projection.find(exp.AggFunc, exp.Window) is not None:
            return None
    return query


def base_table_of(select: exp.Select, src: exp.Expression, depth: int = 0) -> Optional[str]:
    """The base table whose rows FROM/JOIN source ``src`` of ``select`` supplies: the table itself, or the single
    table behind a CTE or derived table that passes its rows through."""
    if depth > _MAX_WRAPPER_DEPTH:
        return None
    query = _source_query(select, src)
    if query is None:
        return normalize_name(src.name) if isinstance(src, exp.Table) and src.name else None
    inner = _passes_rows_through(query)
    if inner is None:
        return None
    return base_table_of(inner, _select_sources(inner)[0], depth + 1)


def wrapper_selects(select: exp.Select, src: exp.Expression) -> List[exp.Select]:
    """The SELECTs of the CTE / derived-table chain through which ``src`` supplies a base table's rows (empty for a
    base table read directly)."""
    out: List[exp.Select] = []
    for _ in range(_MAX_WRAPPER_DEPTH):
        query = _source_query(select, src)
        if query is None:
            return out
        inner = _passes_rows_through(query)
        if inner is None:
            return out
        out.append(inner)
        select, src = inner, _select_sources(inner)[0]
    return out


def scope_tables(select: exp.Select) -> Dict[str, str]:
    """alias -> base table for every FROM/JOIN source of ``select`` that supplies a base table's rows."""
    out: Dict[str, str] = {}
    for src in _select_sources(select):
        if src is None or not src.alias_or_name:
            continue
        table = base_table_of(select, src)
        if table:
            out[normalize_name(src.alias_or_name)] = table
    return out


def owner_alias(col: exp.Column, select: exp.Select) -> Optional[str]:
    """Alias of the FROM/JOIN source of ``select`` that supplies ``col`` (a correlated reference from a nested query
    included); None when it belongs to another scope or cannot be settled."""
    scope, src = source_of(col)
    if scope is not select or src is None or not src.alias_or_name:
        return None
    return normalize_name(src.alias_or_name)


def column_ref(col: exp.Column, select: exp.Select, tables: Dict[str, str]) -> Optional[Tuple[str, str]]:
    """``(alias, base column)`` for a column supplied by a source of ``select``. For a source that stands for a base
    table the column name is the base table's (a rename in a wrapper is undone); a value computed inside the wrapper
    is reported under its own name prefixed with ``<derived>``."""
    alias = owner_alias(col, select)
    if alias is None:
        return None
    name = normalize_name(col.name)
    table = tables.get(alias)
    if table is None:
        return alias, name
    source = origin(col)
    if source is None:
        return alias, f"{DERIVED}{name}"
    return alias, source[1]


def join_chains(select: exp.Select) -> List[List[exp.Expression]]:
    """The FROM list of ``select`` as its comma-separated items, each a list of ``[first source, join, join, …]``.
    A ``USING`` or ``NATURAL`` join pairs its right side with the sources earlier in the same item only."""
    from_node = select.args.get("from_") or select.args.get("from")
    chains: List[List[exp.Expression]] = []
    if from_node is not None and from_node.this is not None:
        chains.append([from_node])
    for join in select.args.get("joins") or []:
        is_comma = not any(join.args.get(key) for key in ("on", "using", "kind", "side", "method"))
        if is_comma or not chains:
            chains.append([join])
        else:
            chains[-1].append(join)
    return chains


__all__ = [
    "ColumnPair",
    "KeyMismatch",
    "PERSON_KEY",
    "PRECEDING_VISIT",
    "PROVIDER_KEY",
    "VISIT_KEY",
    "base_table_of",
    "column_ref",
    "described",
    "entity",
    "equated_columns",
    "in_filter_position",
    "instance",
    "join_chains",
    "key_mismatches",
    "locate_equality",
    "origin",
    "owner_alias",
    "reads_base_table_directly",
    "reporting_rule",
    "scope_tables",
    "wrapper_selects",
    "source_of",
    "with_column",
    "written",
]

"""Clinical concept literal filtered without hierarchy expansion.

Filtering a record's own concept on a literal concept id — ``condition_concept_id = 201826`` or
``drug_concept_id IN (1127433, 19078106)`` — matches only records coded at exactly that concept. OMOP standard
vocabularies are hierarchical: descendants (subtypes, clinical drugs of an ingredient, branded forms) carry their own
concept ids, so records coded below the literal are not matched. The Book of OHDSI expands the concept through
``concept_ancestor`` ("find all occurrences of angioedema or any of its descendants"), and concept sets select a
concept "and all of its descendants".

The rule is a WARNING: a leaf concept has no descendants and a query may deliberately target one exact concept,
neither of which can be known without the vocabulary. The record concept columns are the concept field named after
each CDM table (condition_occurrence.condition_concept_id, visit_occurrence.visit_concept_id, ...; CDM v5.4
field-level specification), followed through CTEs and derived tables by lineage. The literal may be written as
``=``, ``IN``, ``= ANY(ARRAY[...])``, ``BETWEEN n AND n``, a quoted or cast number, a literal-only subquery, a VALUES
list, or on the ``concept.concept_id`` the column is joined to. It stays silent on:

* concept id 0 (the CDM value for an unmapped record) and ids above 2,000,000,000, which the CDM conventions reserve
  for local concepts outside the standard hierarchy;
* ``drug_era.drug_concept_id``, which "should conform to the concept class 'ingredient'": an era is already rolled up
  to its ingredient;
* a column that the same SELECT also compares with concept_ancestor ids (the literal is part of that expansion);
* a CTE the statement never reads (its filter cannot affect the result);
* exclusions (``NOT IN``, ``<>``), CASE labels and aggregates, which do not restrict the rows being counted.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    DERIVED,
    _select_sources,
    column_origins,
    normalize_name,
    parse_sql,
    select_scope_tables,
)
from fastssv.core.references import BOOK_COHORTS, BOOK_SQL, BOOK_VOCABULARIES, section
from fastssv.core.registry import register

from ._cs_cdm_fields import PRIMARY_CONCEPT_FIELDS
from ._cs_scope import instance_filters, int_value, links, peel_cast

# drug_era holds Ingredient concepts only (CDM v5.4), so its literal already covers every drug of that ingredient.
_ALREADY_ROLLED_UP = {("drug_era", "drug_concept_id")}
RECORD_CONCEPT_FIELDS = frozenset(PRIMARY_CONCEPT_FIELDS - _ALREADY_ROLLED_UP)
_FIELD_BY_TABLE: Dict[str, str] = {table: column for table, column in RECORD_CONCEPT_FIELDS}

# CDM v5.4 conventions: concept ids 0..2,000,000,000 are reserved for the Standardized Vocabularies.
_LOCAL_CONCEPT_FLOOR = 2_000_000_000


def _hierarchical(value: int) -> bool:
    return 0 < value < _LOCAL_CONCEPT_FLOOR


def _ints(node: Optional[exp.Expression]) -> List[int]:
    node = peel_cast(node)
    if isinstance(node, exp.Any):
        node = node.this
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, (exp.Tuple, exp.Array)):
        return [v for e in node.expressions for v in _ints(e)]
    value = int_value(node)
    return [value] if value is not None else []


def _literal_only_query(query: Optional[exp.Expression]) -> List[int]:
    """Values of ``(SELECT 1125315)`` / ``(SELECT 1 UNION SELECT 2)``: no FROM, literal projections."""
    if isinstance(query, exp.Subquery):
        query = query.this
    if isinstance(query, exp.SetOperation):
        return _literal_only_query(query.this) + _literal_only_query(query.expression)
    if not isinstance(query, exp.Select) or query.args.get("from_") or query.args.get("from"):
        return []
    values: List[int] = []
    for e in query.expressions:
        found = _ints(e.this if isinstance(e, exp.Alias) else e)
        if not found:
            return []
        values += found
    return values


def _values_column(col: exp.Column) -> List[int]:
    """Literal values behind a column of a ``(VALUES ...) v(x)`` source."""
    select = col.find_ancestor(exp.Select)
    if select is None or not col.table:
        return []
    for src in _select_sources(select):
        values = src.this if isinstance(src, exp.Subquery) else src
        if not isinstance(values, exp.Values) or normalize_name(src.alias_or_name) != normalize_name(col.table):
            continue
        alias = src.args.get("alias")
        names = [normalize_name(c.name) for c in (alias.columns if alias is not None else [])]
        if normalize_name(col.name) not in names:
            return []
        index = names.index(normalize_name(col.name))
        out: List[int] = []
        for row in values.expressions:
            items = row.expressions if isinstance(row, exp.Tuple) else [row]
            if index < len(items):
                out += _ints(items[index])
        return out
    return []


def _in_filter_position(node: exp.Expression) -> bool:
    """WHERE / JOIN ON / HAVING, not under NOT, CASE or an aggregate."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, (exp.Not, exp.Case, exp.If, exp.AggFunc)):
            return False
        if isinstance(parent, (exp.Where, exp.Join, exp.Having)):
            return True
        if isinstance(parent, exp.Select):
            return False
        parent = parent.parent
    return False


def _record_field(col: exp.Column) -> Optional[Tuple[str, str]]:
    """The record concept field ``col`` reads, by lineage; for an unqualified column with several possible sources,
    the field of the one table in its SELECT that has a column of that name."""
    origins = column_origins(col)
    if origins is not None:
        real = {o for o in origins if o[0] != DERIVED}
        if len(real) == 1 and next(iter(real)) in RECORD_CONCEPT_FIELDS:
            return next(iter(real))
        return None
    select = col.find_ancestor(exp.Select)
    if select is None:
        return None
    name = normalize_name(col.name)
    owners = [t for t in select_scope_tables(select) if _FIELD_BY_TABLE.get(t) == name]
    return (owners[0], name) if len(owners) == 1 else None


def _literal_filters(tree: exp.Expression) -> List[Tuple[exp.Expression, exp.Column, List[int]]]:
    """(predicate, record column, literal ids) for each literal restriction of a column."""
    found = []
    for node in tree.find_all(exp.EQ, exp.In, exp.Between):
        if isinstance(node, exp.EQ):
            for col, other in ((node.this, node.expression), (node.expression, node.this)):
                col = peel_cast(col)
                if not isinstance(col, exp.Column):
                    continue
                other_peeled = peel_cast(other)
                values = (
                    _values_column(other_peeled) if isinstance(other_peeled, exp.Column) else _ints(other)
                ) or _literal_only_query(other_peeled)
                if values:
                    found.append((node, col, values))
                    break
        elif isinstance(node, exp.In):
            col = peel_cast(node.this)
            if not isinstance(col, exp.Column):
                continue
            values = [v for e in node.expressions for v in _ints(e)] or _literal_only_query(node.args.get("query"))
            if values:
                found.append((node, col, values))
        else:
            col = peel_cast(node.this)
            low, high = _ints(node.args.get("low")), _ints(node.args.get("high"))
            if isinstance(col, exp.Column) and low and low == high:
                found.append((node, col, low))
    return found


def _dead_ctes(tree: exp.Expression) -> Set[int]:
    """ids of CTE nodes that the statement never reads, directly or through other CTEs."""
    dead: Set[int] = set()
    for with_node in tree.find_all(exp.With):
        owner = with_node.parent
        if owner is None:
            continue
        ctes = {normalize_name(c.alias): c for c in with_node.expressions if c.alias}

        def refs(node: exp.Expression) -> Set[str]:
            return {normalize_name(t.name) for t in node.find_all(exp.Table)} & set(ctes)

        body_refs: Set[str] = set()
        for key, child in owner.args.items():
            if key in ("with", "with_") or child is None:
                continue
            for part in child if isinstance(child, list) else [child]:
                if isinstance(part, exp.Expression):
                    body_refs |= refs(part)
        live, stack = set(), list(body_refs)
        while stack:
            name = stack.pop()
            if name in live:
                continue
            live.add(name)
            stack.extend(refs(ctes[name].this) - live)
        dead |= {id(c) for name, c in ctes.items() if name not in live}
    return dead


def _expanded_fields(tree: exp.Expression) -> Set[Tuple[int, Tuple[str, str]]]:
    """(id of SELECT, record field) for every record field that a SELECT compares with concept_ancestor ids."""
    out: Set[Tuple[int, Tuple[str, str]]] = set()
    for column in ("descendant_concept_id", "ancestor_concept_id"):
        for link in links(tree, "concept_ancestor", column):
            select = link.node.find_ancestor(exp.Select)
            if select is not None:
                out.add((id(select), link.origin))
    return out


@register
class ConceptLiteralWithoutHierarchyRule(Rule):
    rule_id = "concept_standardization.concept_literal_without_hierarchy"
    name = "Concept literal without hierarchy expansion"
    description = (
        "A record's concept column is filtered on literal concept ids without a concept_ancestor expansion of that "
        "column; records coded at descendant concepts (subtypes, clinical drugs of an ingredient) are missed if the "
        "concept has descendants."
    )
    severity = Severity.WARNING
    long_description = (
        "Filtering a record's own concept column (condition_concept_id, drug_concept_id, visit_concept_id, ...) on "
        "literal concept ids matches only records coded at exactly those concepts. Standard vocabularies are "
        "hierarchical, so records coded at descendants (subtypes, the clinical and branded drugs of an ingredient) "
        "are missed; the Book of OHDSI expands the concept through concept_ancestor instead. It is a WARNING because "
        "a leaf concept has no descendants and an exact-concept query can be intended. Concept 0 (unmapped), local "
        "ids above 2,000,000,000, drug_era (already rolled up to ingredients), columns the same SELECT expands "
        "through concept_ancestor, unread CTEs, exclusions, CASE labels and aggregates are not reported."
    )
    suggested_fix = (
        "REPLACE: `e.<x>_concept_id = <id>` WITH a hierarchy expansion: `JOIN concept_ancestor ca ON "
        "e.<x>_concept_id = ca.descendant_concept_id WHERE ca.ancestor_concept_id = <id>` (or a codeset built the "
        "same way), unless exactly that one concept is intended."
    )
    example_bad = (
        "-- acetaminophen ingredient: its clinical and branded drugs are descendants\n"
        "SELECT COUNT(DISTINCT person_id) FROM drug_exposure\n"
        "WHERE drug_concept_id = 1125315;"
    )
    example_good = (
        "SELECT COUNT(DISTINCT de.person_id) FROM drug_exposure de\n"
        "JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id\n"
        "WHERE ca.ancestor_concept_id = 1125315;"
    )
    references = (
        section(BOOK_SQL, "using-the-vocabulary-when-querying", "Using the vocabulary when querying"),
        section(BOOK_COHORTS, "conceptSets", "Concept sets"),
        section(BOOK_VOCABULARIES, "conceptAncestor", "Concept ancestor"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            dead = _dead_ctes(tree)
            expanded = _expanded_fields(tree)
            cols: Set[str] = set()
            ids: Set[int] = set()

            def consider(node: exp.Expression, field: Optional[Tuple[str, str]], values: List[int]) -> None:
                values = [v for v in values if _hierarchical(v)]
                if field is None or not values or not _in_filter_position(node):
                    return
                select = node.find_ancestor(exp.Select)
                if select is not None and (id(select), field) in expanded:
                    return
                cte = node.find_ancestor(exp.CTE)
                if cte is not None and id(cte) in dead:
                    return
                cols.add(field[1])
                ids.update(values)

            for node, col, values in _literal_filters(tree):
                consider(node, _record_field(col), values)

            # `JOIN concept c ON c.concept_id = e.x_concept_id WHERE c.concept_id = <id>` restricts the record the same way.
            concept_ids = instance_filters(tree, "concept", "concept_id")
            for link in links(tree, "concept", "concept_id"):
                if not link.required or link.origin not in RECORD_CONCEPT_FIELDS:
                    continue
                for flt in concept_ids.get(link.instance, []):
                    if flt.op in ("eq", "in") and flt.complete:
                        consider(flt.node, link.origin, [int(v) for v in flt.values if v.lstrip("-").isdigit()])

            if not cols:
                continue
            shown = sorted(ids)[:6]
            violations.append(
                self.create_violation(
                    message=(
                        f"{', '.join(sorted(cols))} filtered on literal concept id(s) {', '.join(map(str, shown))} "
                        "without concept_ancestor expansion; records coded at descendant concepts may be missed, if "
                        "these concepts have descendants."
                    ),
                    severity=self.severity,
                )
            )
        return violations


__all__ = ["ConceptLiteralWithoutHierarchyRule", "RECORD_CONCEPT_FIELDS"]

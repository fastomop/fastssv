"""Fact Relationship Polymorphic Join Validation Rule.

FACT_RELATIONSHIP relates "facts stored as records in any table of the CDM" (OMOP CDM v5.4). ``fact_id_1`` and
``fact_id_2`` carry no foreign key; ``domain_concept_id_1`` and ``domain_concept_id_2`` (foreign keys to CONCEPT)
say which table each id belongs to. A join of ``fact_id_N`` to one table's primary key therefore needs a
restriction on ``domain_concept_id_N`` of the same fact_relationship row. Without it, rows that describe facts of
other tables are matched wherever the ids coincide. Whether such rows exist depends on what the table holds, so the
finding is a WARNING.

The specification does not say which concept stands for which table. Two conventions exist in the vocabulary: the
Domain concepts recorded in DOMAIN.domain_concept_id (21 = Measurement) and the concepts of vocabulary ``CDM``,
class ``Table`` (1147330 = measurement). Both are accepted, and so is any value the rule cannot read (a parameter,
a subquery, a join to DOMAIN or CONCEPT) or does not recognise. A second WARNING is given when the restriction
admits a literal concept that the vocabulary assigns to a different table or domain (``= 19``, ``IN (21, 27)`` for a
measurement join).

The check is made per fact_relationship row source: a restriction counts when it sits in the ON clause that holds
the join, in a WHERE clause over the same rows, in the ON clause of an inner join, or inside the CTE or derived
table the rows come from. Resolving one polymorphic key against several tables, each in its own LEFT JOIN with its
own restriction, is therefore accepted.

Violation pattern:
    SELECT m.measurement_id, fr.fact_id_2
    FROM fact_relationship fr
    JOIN measurement m ON fr.fact_id_1 = m.measurement_id

Correct pattern:
    SELECT m.measurement_id, fr.fact_id_2
    FROM fact_relationship fr
    JOIN measurement m ON fr.fact_id_1 = m.measurement_id
    WHERE fr.domain_concept_id_1 = 21
"""

from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    _NON_RESTRICTING_ATOMS,
    _peel_value_wrappers,
    _predicate_atom,
    _reads,
    parse_sql,
)
from fastssv.core.references import CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from ._cdm_keys import DOMAIN_CONCEPT_ID, ENTITY, PRIMARY_KEY, TABLE_CONCEPT_IDS, TABLE_DOMAIN
from ._lineage import equated_columns, in_filter_position, origin, written

FACT_RELATIONSHIP = "fact_relationship"
DOMAIN_COLUMN = {"fact_id_1": "domain_concept_id_1", "fact_id_2": "domain_concept_id_2"}

# concept id -> what the vocabulary says it stands for.
_CONCEPT_MEANING: Dict[int, str] = {
    **{cid: f"Domain '{domain}'" for domain, cid in DOMAIN_CONCEPT_ID.items()},
    **{cid: f"CDM table '{table}'" for table, cids in TABLE_CONCEPT_IDS.items() for cid in cids},
}

RowSource = Tuple[int, str]


def _accepted_concepts(table: str) -> Set[int]:
    accepted = set(TABLE_CONCEPT_IDS.get(table, ()))
    domain = TABLE_DOMAIN.get(table)
    if domain is not None:
        accepted.add(DOMAIN_CONCEPT_ID[domain])
    return accepted


def _concept_hint(table: str) -> str:
    options = []
    domain = TABLE_DOMAIN.get(table)
    if domain is not None:
        options.append(f"{DOMAIN_CONCEPT_ID[domain]} (Domain '{domain}')")
    options.extend(f"{cid} (CDM table '{table}')" for cid in TABLE_CONCEPT_IDS.get(table, ()))
    return " or ".join(options)


def _row_sources(col: exp.Column, column: str) -> Set[RowSource]:
    """fact_relationship row sources whose ``column`` flows into ``col``, through CTEs and derived tables."""
    return {
        (id(select), alias)
        for select, alias, table, name in _reads(col, relay=False)
        if table == FACT_RELATIONSHIP and name == column
    }


def _clause_of(node: exp.Expression) -> Tuple[Optional[exp.Expression], bool]:
    """The WHERE / HAVING / JOIN that holds ``node``, and whether it is the ON clause of an outer join."""
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, (exp.Where, exp.Having)):
            return parent, False
        if isinstance(parent, exp.Join):
            return parent, bool(parent.args.get("side"))
        parent = parent.parent
    return None, False


class _Restriction(NamedTuple):
    clause: Optional[exp.Expression]
    outer_join_only: bool
    values: Optional[Set[int]]  # None: the value cannot be read from the SQL text


def _literal_int(node: Optional[exp.Expression]) -> Optional[int]:
    node = _peel_value_wrappers(node)
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return int(node.this)
        except ValueError:
            return None
    return None


def _restricted_values(atom: exp.Expression, col: exp.Column) -> Optional[Set[int]]:
    if isinstance(atom, exp.EQ):
        other = atom.expression if _peel_value_wrappers(atom.this) is col else atom.this
        value = _literal_int(other)
        return {value} if value is not None else None
    if isinstance(atom, exp.In) and atom.expressions and _peel_value_wrappers(atom.this) is col:
        values = [_literal_int(v) for v in atom.expressions]
        return set(values) if all(v is not None for v in values) else None
    return None


def _domain_restrictions(tree: exp.Expression) -> Dict[Tuple[RowSource, str], List[_Restriction]]:
    """(fact_relationship row source, domain column) -> the predicates that restrict it."""
    out: Dict[Tuple[RowSource, str], List[_Restriction]] = {}
    for col in tree.find_all(exp.Column):
        if not in_filter_position(col):
            continue
        hits = [(source, column) for column in DOMAIN_COLUMN.values() for source in _row_sources(col, column)]
        if not hits:
            continue
        atom, negated = _predicate_atom(col)
        if negated or atom is col or isinstance(atom, _NON_RESTRICTING_ATOMS):
            continue
        clause, outer_join_only = _clause_of(col)
        restriction = _Restriction(clause, outer_join_only, _restricted_values(atom, col))
        for key in hits:
            out.setdefault(key, []).append(restriction)
    return out


class _Link(NamedTuple):
    node: exp.Expression
    fact_col: exp.Column
    fact_id: str
    table: str
    key: str
    sources: Set[RowSource]


def _fact_links(tree: exp.Expression) -> List[_Link]:
    """Predicates that equate ``fact_id_1`` / ``fact_id_2`` with the primary key of a CDM table."""
    links = []
    for pair in equated_columns(tree):
        for fact_col, other in ((pair.left, pair.right), (pair.right, pair.left)):
            fact_origin = origin(fact_col)
            if fact_origin is None or fact_origin[0] != FACT_RELATIONSHIP or fact_origin[1] not in DOMAIN_COLUMN:
                continue
            other_origin = origin(other)
            target = ENTITY.get(other_origin) if other_origin else None
            if target is None or target[0] == "concept" or PRIMARY_KEY.get(target[0]) != target[1]:
                continue
            sources = _row_sources(fact_col, fact_origin[1])
            if sources:
                links.append(_Link(pair.node, fact_col, fact_origin[1], target[0], target[1], sources))
    return links


def _applicable(link: _Link, restrictions: List[_Restriction]) -> List[_Restriction]:
    """Restrictions that hold for the rows ``link`` joins: an outer join's ON clause filters only its own join."""
    link_clause, _ = _clause_of(link.node)
    return [r for r in restrictions if not r.outer_join_only or r.clause is link_clause]


@register
class FactRelationshipJoinValidationRule(Rule):
    """A join on fact_relationship.fact_id_N needs a restriction on domain_concept_id_N of the same row."""

    rule_id = "joins.fact_relationship_join_validation"
    name = "Fact Relationship Polymorphic Join Validation"
    description = (
        "fact_relationship.fact_id_1 / fact_id_2 can hold a key of any CDM table; domain_concept_id_1 / "
        "domain_concept_id_2 say which. Warns when a fact id is joined to a table's primary key and the matching "
        "domain_concept_id of the same row is not restricted, or is restricted to a concept the vocabulary assigns "
        "to another table."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: a filter on the matching domain column of the same fact_relationship row, e.g. "
        "`AND fr.domain_concept_id_1 = <concept your CDM uses for the joined table>`. The CDM does not fix the "
        "value: ETLs use either the Domain concept (21 = Measurement) or the CDM table concept (1147330 = "
        "measurement); check which one your fact_relationship holds."
    )
    example_bad = (
        "SELECT m.measurement_id, fr.fact_id_2\n"
        "FROM fact_relationship fr\n"
        "JOIN measurement m ON fr.fact_id_1 = m.measurement_id;"
    )
    example_good = (
        "SELECT m.measurement_id, fr.fact_id_2\n"
        "FROM fact_relationship fr\n"
        "JOIN measurement m ON fr.fact_id_1 = m.measurement_id\n"
        "WHERE fr.domain_concept_id_1 = 21;"
    )
    extra_examples = (
        RuleExample(
            "SELECT m.measurement_id, fr.fact_id_2\n"
            "FROM fact_relationship fr\n"
            "JOIN measurement m ON fr.fact_id_1 = m.measurement_id\n"
            "WHERE fr.domain_concept_id_1 = 19;",
            Severity.WARNING,
            "The domain filter names another domain (19 is the Domain concept 'Condition', not 'Measurement').",
        ),
    )
    references = (
        cdm_table("fact_relationship"),
        cdm_table("domain"),
        section(CDM_CONVENTIONS, "domains", "Domains"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if FACT_RELATIONSHIP not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            links = _fact_links(tree)
            if not links:
                continue
            restrictions = _domain_restrictions(tree)
            seen: Set[Tuple[int, str]] = set()
            for link in links:
                if (id(link.node), link.fact_id) in seen:
                    continue
                seen.add((id(link.node), link.fact_id))
                domain_col = DOMAIN_COLUMN[link.fact_id]
                found = [
                    r for source in link.sources for r in _applicable(link, restrictions.get((source, domain_col), []))
                ]
                fact_text, hint = written(link.fact_col), _concept_hint(link.table)
                if not found:
                    kind = "missing_domain_filter"
                    message = (
                        f"{fact_text} is joined to {link.table}.{link.key} with no filter on {domain_col} of the "
                        f"same fact_relationship row. {link.fact_id} can hold a key of any CDM table, so rows about "
                        f"other tables' facts are matched wherever the ids coincide. Restrict {domain_col} to the "
                        f"concept your CDM uses for {link.table}" + (f": {hint}." if hint else ".")
                    )
                else:
                    # Restrictions on one row hold together, so a concept admitted by every readable restriction
                    # is admitted by the join.
                    accepted = _accepted_concepts(link.table)
                    admitted = [
                        {v for v in r.values if v in _CONCEPT_MEANING and v not in accepted}
                        for r in found
                        if r.values is not None
                    ]
                    foreign = sorted(set.intersection(*admitted)) if admitted else []
                    if not foreign:
                        continue
                    kind = "other_table_domain_filter"
                    named = ", ".join(f"{v} ({_CONCEPT_MEANING[v]})" for v in foreign)
                    message = (
                        f"{fact_text} is joined to {link.table}.{link.key}, but {domain_col} admits {named}, "
                        f"which the vocabulary assigns elsewhere, so the join also matches ids of that table. Expected the concept your CDM uses for {link.table}"
                        + (f": {hint}." if hint else ".")
                    )
                violations.append(
                    self.create_violation(
                        message=message,
                        details={
                            "type": kind,
                            "clinical_table": link.table,
                            "fact_id_column": link.fact_id,
                            "domain_column": domain_col,
                        },
                    )
                )
        return violations


__all__ = ["FactRelationshipJoinValidationRule"]

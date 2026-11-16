"""Joins between a vocabulary reference table (VOCABULARY, DOMAIN, CONCEPT_CLASS, RELATIONSHIP) and a table that
references it.

The CDM v5.4 specification links them only through foreign keys: ``concept.vocabulary_id`` -> VOCABULARY,
``concept_relationship.relationship_id`` -> RELATIONSHIP, ``vocabulary.vocabulary_concept_id`` -> CONCEPT, and so on
(keys in ``_person_keys``). An equality between columns of two such tables is classified as

* a link: one column is a foreign key to the other, or both hold the key of one of the two tables
  (``relationship.reverse_relationship_id = concept_relationship.relationship_id``);
* a key misuse (ERROR): a foreign key compared with a non-key column of the table it references
  (``concept.vocabulary_id = vocabulary.vocabulary_name``): the comparison runs, but the name is not the key, so it
  silently drops rows;
* suspicious (WARNING): any other pair, unless the same two sources are already linked in that SELECT, in which
  case the extra predicate is a filter.

Operands are resolved by lineage, through casts, case folding and trimming, CTEs, derived tables, set operations and
IN-subqueries.
"""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Set, Tuple

from sqlglot import exp

from ._join_scope import Equality, Side, scope_qualifier, tree_equalities
from ._person_keys import FOREIGN_KEYS, PRIMARY_KEYS, key_domain

REFERENCE_TABLES = frozenset({"vocabulary", "domain", "concept_class", "relationship"})


@dataclass(frozen=True)
class Finding:
    eq: Equality
    partner: Side
    reference: Side
    error: bool


def _related(t1: str, t2: str) -> bool:
    return any(t == t1 and ref[0] == t2 for (t, _), ref in FOREIGN_KEYS.items()) or any(
        t == t2 and ref[0] == t1 for (t, _), ref in FOREIGN_KEYS.items()
    )


def _is_link(a: Side, b: Side) -> bool:
    if FOREIGN_KEYS.get(a.origin) == b.origin or FOREIGN_KEYS.get(b.origin) == a.origin:
        return True
    da, db = a.domain, b.domain
    own = {(a.table, PRIMARY_KEYS.get(a.table)), (b.table, PRIMARY_KEYS.get(b.table))}
    return da is not None and da == db and da in own


def _is_misuse(a: Side, b: Side) -> bool:
    """``a`` is a foreign key into ``b``'s table, and ``b`` is not a key column."""
    target = FOREIGN_KEYS.get(a.origin)
    return target is not None and target[0] == b.table and key_domain(*b.origin) is None


def reference_findings(tree: exp.Expression, references: Set[str]) -> Iterator[Finding]:
    """Findings for joins whose reference-table side is one of ``references``."""
    by_select: Dict[int, List[Tuple[Equality, Side, Side]]] = {}
    for eq in tree_equalities(tree, text=True):
        a, b = eq.left, eq.right
        if a.origin is None or b.origin is None or a.table == b.table:
            continue
        for partner, ref in ((a, b), (b, a)):
            if ref.table in references and _related(partner.table, ref.table):
                by_select.setdefault(id(eq.select), []).append((eq, partner, ref))
                break

    for items in by_select.values():
        linked: Set[Tuple[Optional[str], Optional[str]]] = set()
        for eq, partner, ref in items:
            if _is_link(partner, ref) and eq.conjunct:
                linked.add(_pair(eq, partner, ref))
        for eq, partner, ref in items:
            if _is_link(partner, ref):
                continue
            if _is_misuse(partner, ref) or _is_misuse(ref, partner):
                yield Finding(eq, partner, ref, True)
            elif _pair(eq, partner, ref) not in linked:
                yield Finding(eq, partner, ref, False)


def _pair(eq: Equality, partner: Side, ref: Side) -> Tuple[Optional[str], Optional[str]]:
    return (
        scope_qualifier(partner, eq.select) or partner.table,
        scope_qualifier(ref, eq.select) or ref.table,
    )


def expected_link(partner: Side, ref: Side) -> Optional[str]:
    """The foreign-key pair between the two tables, as SQL text with table names."""
    for (t, c), target in sorted(FOREIGN_KEYS.items()):
        if t == partner.table and target[0] == ref.table:
            return f"{t}.{c} = {target[0]}.{target[1]}"
    return None


__all__ = ["REFERENCE_TABLES", "Finding", "expected_link", "reference_findings"]

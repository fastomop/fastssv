"""Concept Relationship Transitive Misuse Rule.

concept_relationship stores first-degree hierarchy only: each 'Is a' / 'Subsumes' row links a concept to its direct
parent / child. Chaining two or more such lookups (``cr1.concept_id_2 = cr2.concept_id_1``, written as self-joins or
across CTEs) returns only the concepts reached in exactly N steps: it skips the levels in between and the deeper
ones, and the result depends on the chain length. concept_ancestor already stores every transitive path with its
levels of separation for standard and classification concepts; non-standard vocabularies (ICD10CM, Read, ...) are
not in it, and a recursive CTE walks their hierarchy instead.

Flagged: a chain of two or more concept_relationship instances, each restricted to a single hierarchical
relationship, that walks one direction of the hierarchy: 'Subsumes' read from concept_id_1 to concept_id_2, or 'Is a'
read from concept_id_2 to concept_id_1, goes down; the reverse goes up. Hops joined directly or through a concept row
(``concept.concept_id`` carries the id unchanged) both count. Not flagged: mixed chains ('Is a' then 'Subsumes'
finds siblings; 'Maps to' then 'Is a' finds the parent of a mapped concept), and any hop inside a recursive CTE, which
repeats until the closure is complete.

Violation:
    SELECT cr2.concept_id_2
    FROM concept_relationship cr1
    JOIN concept_relationship cr2 ON cr1.concept_id_2 = cr2.concept_id_1
    WHERE cr1.concept_id_1 = 201820
      AND cr1.relationship_id = 'Subsumes'
      AND cr2.relationship_id = 'Subsumes'

Correct:
    SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
      AND min_levels_of_separation BETWEEN 1 AND 2
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.references import BOOK_VOCABULARIES, CDM_CONVENTIONS, section
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register
from fastssv.core.helpers import (
    CONCEPT_RELATIONSHIP,
    comparison_pairs,
    relationship_filters,
    side_reads,
)

HIERARCHICAL_RELATIONSHIPS = {"is a", "subsumes"}

Instance = Tuple[int, str]


def _in_recursive_cte(select: exp.Select) -> bool:
    """True inside a CTE body that reads the CTE itself: such a walk repeats until no new rows, so it is not
    fixed-depth."""
    cte = select.find_ancestor(exp.CTE)
    while cte is not None:
        name = normalize_name(cte.alias_or_name)
        if any(normalize_name(t.name) == name and not t.args.get("db") for t in cte.this.find_all(exp.Table)):
            return True
        cte = cte.find_ancestor(exp.CTE)
    return False


# Columns a hop reads from and leads to when walking down the hierarchy (parent -> child): a 'Subsumes' row goes
# from concept_id_1 (parent) to concept_id_2 (child), an 'Is a' row from concept_id_2 (parent) to concept_id_1.
_DOWN = {"subsumes": ("concept_id_1", "concept_id_2"), "is a": ("concept_id_2", "concept_id_1")}


def _equalities(tree: exp.Expression) -> List[Tuple[Set, Set]]:
    """Pairs of read sets equated by the statement; reads equated to the same concept row are paired too, because
    joining through concept.concept_id carries the id unchanged."""
    pairs = []
    via_concept: Dict[Tuple[int, str], Set] = {}
    for _, left, right in comparison_pairs(tree):
        left_reads, right_reads = side_reads(left, relay=False), side_reads(right, relay=False)
        pairs.append((left_reads, right_reads))
        for a, b in ((left_reads, right_reads), (right_reads, left_reads)):
            for s1, a1, t1, c1 in a:
                if t1 == "concept" and c1 == "concept_id":
                    via_concept.setdefault((id(s1), a1), set()).update(r for r in b if r[2] == CONCEPT_RELATIONSHIP)
    for reads in via_concept.values():
        pairs.append((reads, reads))
    return pairs


def _chain_edges(tree: exp.Expression, relationship_of: Dict[Instance, str], direction: str):
    """Edges X -> Y where the row X leads to is the row Y starts from, walking ``direction`` ("down" or "up")."""
    edges: Dict[Instance, Set[Instance]] = {}

    def ends(instance: Instance) -> Tuple[str, str]:
        start, end = _DOWN[relationship_of[instance]]
        return (start, end) if direction == "down" else (end, start)

    for a_reads, b_reads in _equalities(tree):
        for a, b in ((a_reads, b_reads), (b_reads, a_reads)):
            for s1, a1, t1, c1 in a:
                x = (id(s1), a1)
                if t1 != CONCEPT_RELATIONSHIP or x not in relationship_of or c1 != ends(x)[1]:
                    continue
                for s2, a2, t2, c2 in b:
                    y = (id(s2), a2)
                    if t2 != CONCEPT_RELATIONSHIP or y not in relationship_of or c2 != ends(y)[0]:
                        continue
                    if x != y and not _in_recursive_cte(s1) and not _in_recursive_cte(s2):
                        edges.setdefault(x, set()).add(y)
    return edges


def _anchored(tree: exp.Expression, instance: Instance, column: str) -> bool:
    """Whether ``column`` of ``instance`` is compared with something other than another concept_relationship row:
    that is where the walk starts."""
    for node in tree.find_all(exp.EQ, exp.In):
        sides = [node.this, node.expression] if isinstance(node, exp.EQ) else [node.this]
        others = [node.expression, node.this] if isinstance(node, exp.EQ) else [node.args.get("query")]
        for side, other in zip(sides, others):
            reads = side_reads(side, relay=False) if side is not None else set()
            if not any((id(r[0]), r[1]) == instance and r[3] == column for r in reads):
                continue
            if other is None:
                return True  # IN (literal list)
            if not any(r[2] == CONCEPT_RELATIONSHIP for r in side_reads(other, relay=False)):
                return True
    return False


def _longest_chain(edges: Dict[Instance, Set[Instance]], nodes: Set[Instance]) -> List[Instance]:
    best: List[Instance] = []

    def walk(path: List[Instance]) -> None:
        nonlocal best
        if len(path) > len(best):
            best = list(path)
        for nxt in sorted(edges.get(path[-1], ()), key=str):
            if nxt in nodes and nxt not in path:
                walk(path + [nxt])

    for start in sorted(nodes, key=str):
        walk([start])
    return best


@register
class ConceptRelationshipTransitiveMisuseRule(Rule):
    """Detect chained 'Is a' / 'Subsumes' lookups that emulate hierarchy traversal."""

    rule_id = "anti_patterns.concept_relationship_transitive_misuse"
    name = "Concept Relationship Transitive Misuse"
    description = (
        "Chaining two or more 'Is a' (or 'Subsumes') concept_relationship lookups returns only the concepts "
        "reached in exactly N steps and misses the rest of the hierarchy. Use concept_ancestor (standard and "
        "classification concepts) or a recursive CTE."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the chained concept_relationship lookups WITH concept_ancestor "
        "(`descendant_concept_id` for 'Subsumes', `ancestor_concept_id` for 'Is a'), filtering "
        "`min_levels_of_separation` when a depth limit is intended; for a non-standard vocabulary, which "
        "concept_ancestor does not cover, use a recursive CTE."
    )
    long_description = (
        "concept_relationship holds only first-degree hierarchy: one 'Is a' / 'Subsumes' row per direct "
        "parent-child pair. A chain of such lookups returns the concepts reached in exactly that many steps, "
        "skipping the intermediate levels and everything deeper, and concepts with several parents can be reached "
        "at different depths. concept_ancestor is built from these relationships and stores every transitive path "
        "with min/max levels of separation, so one join answers 'all descendants' or 'descendants within N levels'. "
        "It covers standard and classification concepts only; a non-standard vocabulary's hierarchy needs a "
        "recursive CTE."
    )
    example_bad = (
        "SELECT cr2.concept_id_2\n"
        "FROM concept_relationship cr1\n"
        "JOIN concept_relationship cr2 ON cr1.concept_id_2 = cr2.concept_id_1\n"
        "WHERE cr1.concept_id_1 = 201820\n"
        "  AND cr1.relationship_id = 'Subsumes'\n"
        "  AND cr2.relationship_id = 'Subsumes';"
    )
    example_good = (
        "SELECT descendant_concept_id\n"
        "FROM concept_ancestor\n"
        "WHERE ancestor_concept_id = 201820\n"
        "  AND min_levels_of_separation BETWEEN 1 AND 2;"
    )
    references = (
        section(CDM_CONVENTIONS, "concept-relationships", "Concept relationships"),
        section(BOOK_VOCABULARIES, "conceptAncestor", "Hierarchy"),
        section(CDM_CONVENTIONS, "concept-ancestor", "Concept ancestor"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if CONCEPT_RELATIONSHIP not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            relationship_of: Dict[Instance, str] = {}
            for instance, values in relationship_filters(tree).items():
                if len(values) == 1 and next(iter(values)) in HIERARCHICAL_RELATIONSHIPS:
                    relationship_of[instance] = next(iter(values))
            if len(relationship_of) < 2:
                continue
            chains = {}
            for direction in ("down", "up"):
                chain = _longest_chain(_chain_edges(tree, relationship_of, direction), set(relationship_of))
                if len(chain) >= 2:
                    chains[direction] = chain
            if len(chains) == 2 and set(chains["down"]) == set(chains["up"]):
                # The same hops read in reverse: the walk goes the way whose first hop is anchored to an outside
                # value; without an anchor, the way most hops are written ('Subsumes' down, 'Is a' up).
                down_head, up_head = chains["down"][0], chains["up"][0]
                down_anchor = _anchored(tree, down_head, _DOWN[relationship_of[down_head]][0])
                up_anchor = _anchored(tree, up_head, _DOWN[relationship_of[up_head]][1])
                if down_anchor != up_anchor:
                    keep = "down" if down_anchor else "up"
                else:
                    subsumes = sum(relationship_of[i] == "subsumes" for i in chains["down"])
                    keep = "down" if subsumes * 2 >= len(chains["down"]) else "up"
                chains = {keep: chains[keep]}
            for direction, chain in chains.items():
                hops = len(chain)
                aliases = " → ".join(alias for _, alias in chain)
                used = " / ".join(sorted({f"'{relationship_of[i].capitalize()}'" for i in chain}))
                target, anchor = (
                    ("descendant_concept_id", "ancestor")
                    if direction == "down"
                    else ("ancestor_concept_id", "descendant")
                )
                violations.append(
                    self.create_violation(
                        message=(
                            f"Query chains {hops} {used} concept_relationship lookups ({aliases}) {direction} the "
                            f"hierarchy, which returns only concepts reached in exactly {hops} steps and skips the levels "
                            "in between and beyond. concept_ancestor stores every level of the standard and "
                            "classification hierarchy; for a non-standard vocabulary (e.g. ICD10CM), which "
                            "concept_ancestor does not cover, walk all levels with a recursive CTE."
                        ),
                        suggested_fix=(
                            f"REWRITE: the {hops}-step chain WITH `SELECT {target} FROM concept_ancestor WHERE "
                            f"{anchor}_concept_id = <start> AND min_levels_of_separation BETWEEN 1 AND {hops}` for "
                            "standard / classification concepts, or a recursive CTE over the same relationship for a "
                            "non-standard vocabulary."
                        ),
                        details={"chain_length": hops, "direction": direction, "chain": aliases},
                    )
                )
        return violations


__all__ = ["ConceptRelationshipTransitiveMisuseRule"]

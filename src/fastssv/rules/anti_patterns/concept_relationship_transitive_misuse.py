"""Concept Relationship Transitive Misuse Rule.

OMOP semantic rule VOCAB_034:
concept_relationship stores only direct (one-hop) relationships. To find all
descendants across multiple levels, use concept_ancestor (which is pre-computed
for all transitive hierarchical paths). Chaining multiple concept_relationship
self-joins to simulate transitivity is fragile and incomplete.

The Problem:
    concept_relationship contains direct relationships only:
    - Concept A "Subsumes" Concept B (one hop)
    - Concept B "Subsumes" Concept C (one hop)

    To find all descendants of A (both B and C), users sometimes chain joins:
    cr1 → cr2 → cr3 (each hop requires another join)

    Issues with manual chaining:
    1. Incomplete: Only gets descendants at specific depth (e.g., exactly 3 hops)
    2. Fragile: Misses concepts with multiple inheritance paths
    3. Performance: Multiple self-joins are slow
    4. Complexity: Hard to maintain and understand

    The concept_ancestor table pre-computes ALL transitive hierarchical paths
    and is optimized for hierarchy traversal queries.

Violation pattern:
    SELECT cr3.concept_id_2
    FROM concept_relationship cr1
    JOIN concept_relationship cr2
      ON cr1.concept_id_2 = cr2.concept_id_1
    JOIN concept_relationship cr3
      ON cr2.concept_id_2 = cr3.concept_id_1
    WHERE cr1.concept_id_1 = 201820
      AND cr1.relationship_id = 'Subsumes'
      AND cr2.relationship_id = 'Subsumes'
      AND cr3.relationship_id = 'Subsumes'
    -- Only gets descendants exactly 3 hops away!

Correct patterns:
    -- Get all descendants at any depth
    SELECT ca.descendant_concept_id
    FROM concept_ancestor ca
    WHERE ca.ancestor_concept_id = 201820
      AND ca.min_levels_of_separation >= 1

    -- Get descendants within depth 3
    SELECT ca.descendant_concept_id
    FROM concept_ancestor ca
    WHERE ca.ancestor_concept_id = 201820
      AND ca.min_levels_of_separation BETWEEN 1 AND 3
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    normalize_name,
    parse_sql,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_RELATIONSHIP = "concept_relationship"

HIERARCHICAL_RELATIONSHIPS = {
    "Subsumes",
    "Is a",
    "Has ancestor",
    "Has descendant",
}

MIN_CHAIN_LENGTH = 3

HIERARCHICAL_RELATIONSHIPS_NORM = {
    normalize_name(r) for r in HIERARCHICAL_RELATIONSHIPS
}


# --- Helpers ---------------------------------------------------------------

def _norm(val: Optional[str]) -> Optional[str]:
    return normalize_name(val) if val else None


def _get_table_alias(table: exp.Table) -> str:
    alias_expr = table.args.get("alias")
    return alias_expr.name if alias_expr else table.name


def _is_concept_relationship_table(table_name: str) -> bool:
    return _norm(table_name) == _norm(CONCEPT_RELATIONSHIP)


def _extract_concept_relationship_aliases(tree: exp.Expression) -> Set[str]:
    aliases = set()

    for table in tree.find_all(exp.Table):
        if _is_concept_relationship_table(table.name):
            alias = _get_table_alias(table)
            aliases.add(_norm(alias))

    return aliases


# --- Graph construction ----------------------------------------------------

def _build_join_graph(
    tree: exp.Expression,
    cr_aliases: Set[str]
) -> Dict[str, Set[str]]:
    graph: Dict[str, Set[str]] = {alias: set() for alias in cr_aliases}

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left, right = eq.this, eq.expression

            if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                continue

            left_table = _norm(left.table)
            right_table = _norm(right.table)

            if left_table not in cr_aliases or right_table not in cr_aliases:
                continue

            left_col = _norm(left.name)
            right_col = _norm(right.name)

            # Forward direction
            if left_col == "concept_id_2" and right_col == "concept_id_1":
                if left_table != right_table:
                    graph[left_table].add(right_table)

            # Reverse direction
            elif left_col == "concept_id_1" and right_col == "concept_id_2":
                if left_table != right_table:
                    graph[right_table].add(left_table)

    return graph


# --- Chain detection -------------------------------------------------------

def _find_chains(
    graph: Dict[str, Set[str]],
    max_depth: int = 10
) -> List[List[str]]:
    all_chains: List[List[str]] = []

    def dfs(path: List[str], visited: Set[str]):
        # Record intermediate chains (FIX)
        if len(path) >= 2:
            all_chains.append(path[:])

        if len(path) >= max_depth:
            return

        current = path[-1]

        for neighbor in graph.get(current, set()):
            if neighbor in visited:
                continue
            dfs(path + [neighbor], visited | {neighbor})

    for start in graph:
        dfs([start], {start})

    return all_chains


# --- Relationship filtering ------------------------------------------------

def _has_hierarchical_relationship_filter(
    tree: exp.Expression,
    alias: str
) -> bool:
    alias_norm = _norm(alias)

    # EQ filters
    for eq in tree.find_all(exp.EQ):
        left, right = eq.this, eq.expression

        for col_node, val_node in [(left, right), (right, left)]:
            if not isinstance(col_node, exp.Column):
                continue

            if _norm(col_node.table) != alias_norm:
                continue

            if _norm(col_node.name) != "relationship_id":
                continue

            if isinstance(val_node, exp.Literal) and val_node.is_string:
                val = _norm(str(val_node.this))
                if val in HIERARCHICAL_RELATIONSHIPS_NORM:
                    return True

    # IN filters
    for in_expr in tree.find_all(exp.In):
        col = in_expr.this
        if not isinstance(col, exp.Column):
            continue

        if _norm(col.table) != alias_norm:
            continue

        if _norm(col.name) != "relationship_id":
            continue

        for val in in_expr.expressions or []:
            if isinstance(val, exp.Literal) and val.is_string:
                norm_val = _norm(str(val.this))
                if norm_val in HIERARCHICAL_RELATIONSHIPS_NORM:
                    return True

    return False


def _chain_uses_hierarchical_relationships(
    tree: exp.Expression,
    chain: List[str]
) -> bool:
    """Check if any alias in the chain uses hierarchical relationships."""
    for alias in chain:
        if _has_hierarchical_relationship_filter(tree, alias):
            return True
    return False


# --- Rule ------------------------------------------------------------------

@register
class ConceptRelationshipTransitiveMisuseRule(Rule):
    """Detect chained concept_relationship joins attempting transitive closure."""

    rule_id = "anti_patterns.concept_relationship_transitive_misuse"
    name = "Concept Relationship Transitive Misuse"

    description = (
        "concept_relationship stores only direct relationships. "
        "Chaining multiple self-joins attempts transitive closure and is fragile. "
        "Use concept_ancestor instead."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use concept_ancestor table instead for transitive hierarchy traversal."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "concept_relationship" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            cr_aliases = _extract_concept_relationship_aliases(tree)

            if len(cr_aliases) < MIN_CHAIN_LENGTH:
                continue

            graph = _build_join_graph(tree, cr_aliases)
            chains = _find_chains(graph)

            valid_chains = [
                chain for chain in chains
                if len(chain) >= MIN_CHAIN_LENGTH
                and _chain_uses_hierarchical_relationships(tree, chain)
            ]

            if not valid_chains:
                continue

            longest_chain = max(valid_chains, key=len)
            chain_key = "->".join(longest_chain)

            if chain_key in seen:
                continue
            seen.add(chain_key)

            violations.append(
                self.create_violation(
                    message=(
                        f"Query chains {len(longest_chain)} concept_relationship self-joins "
                        f"({' → '.join(longest_chain)}) to traverse hierarchy. "
                        f"This is fragile and incomplete - only finds descendants at specific depth. "
                        f"Use concept_ancestor table instead, which pre-computes all transitive paths."
                    ),
                    severity=Severity.WARNING,
                    suggested_fix=(
                        f"Replace the {len(longest_chain)}-join chain with concept_ancestor. "
                        f"Example: SELECT descendant_concept_id FROM concept_ancestor "
                        f"WHERE ancestor_concept_id = <start_concept> "
                        f"AND min_levels_of_separation BETWEEN 1 AND {len(longest_chain)-1}"
                    ),
                    details={
                        "chain_length": len(longest_chain),
                        "chain": longest_chain,
                    },
                )
            )

        return violations


__all__ = ["ConceptRelationshipTransitiveMisuseRule"]
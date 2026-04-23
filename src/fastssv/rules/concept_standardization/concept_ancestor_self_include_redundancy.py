"""Concept Ancestor Self-Include Redundancy Validation Rule.

OMOP semantic rule VOCAB_017:
concept_ancestor always includes a self-referencing row (ancestor = descendant,
min_levels_of_separation = 0). When building concept sets via concept_ancestor,
this is typically desired (include the anchor concept itself). But queries that
separately add the anchor AND use concept_ancestor will double-count it.

The Problem:
    The concept_ancestor table includes self-referencing rows where:
    - ancestor_concept_id = descendant_concept_id
    - min_levels_of_separation = 0
    - max_levels_of_separation = 0

    This means every concept is its own ancestor at distance 0.

    When building concept sets, the anchor concept is AUTOMATICALLY included
    in concept_ancestor results. Queries that explicitly add the anchor concept
    AND query concept_ancestor will duplicate the anchor.

    Common mistakes:
    1. UNION with explicit anchor: Include concept_id = X and also query
       concept_ancestor with ancestor_concept_id = X
    2. OR condition mixing direct concept_id and concept_ancestor
    3. IN clause with explicit IDs and concept_ancestor subquery

Violation patterns:
    -- WRONG: UNION with explicit anchor (duplicates 201820)
    SELECT concept_id FROM concept WHERE concept_id = 201820
    UNION
    SELECT descendant_concept_id FROM concept_ancestor
    WHERE ancestor_concept_id = 201820

    -- WRONG: OR with explicit anchor and concept_ancestor
    SELECT DISTINCT c.concept_id
    FROM concept c
    LEFT JOIN concept_ancestor ca
      ON c.concept_id = ca.descendant_concept_id
      AND ca.ancestor_concept_id = 201820
    WHERE c.concept_id = 201820 OR ca.ancestor_concept_id IS NOT NULL

    -- WRONG: IN clause mixing explicit ID and subquery
    SELECT * FROM condition_occurrence
    WHERE condition_concept_id IN (
      201820,
      SELECT descendant_concept_id FROM concept_ancestor
      WHERE ancestor_concept_id = 201820
    )

Correct patterns:
    -- CORRECT: Just use concept_ancestor (includes anchor via min_levels = 0)
    SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820

    -- CORRECT: Exclude anchor if you don't want it
    SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
      AND min_levels_of_separation > 0

    -- CORRECT: UNION ALL with explicit tracking (intentional duplication)
    SELECT concept_id, 'anchor' AS source
    FROM concept WHERE concept_id = 201820
    UNION ALL
    SELECT descendant_concept_id, 'hierarchy' AS source
    FROM concept_ancestor WHERE ancestor_concept_id = 201820
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_ANCESTOR = "concept_ancestor"
CONCEPT_TABLE = "concept"

ANCESTOR_CONCEPT_ID = "ancestor_concept_id"
DESCENDANT_CONCEPT_ID = "descendant_concept_id"
CONCEPT_ID = "concept_id"
MIN_LEVELS = "min_levels_of_separation"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _get_aliases_by_table(aliases: Dict[str, str], table_name: str) -> Set[str]:
    return {
        alias for alias, table in aliases.items()
        if _norm(table) == table_name
    }


def _extract_literal_int(node: exp.Expression) -> Optional[int]:
    if isinstance(node, exp.Literal) and node.is_int:
        try:
            return int(node.this)
        except Exception:
            return None
    return None


# --- Column Checks (STRICT) ------------------------------------------------

def _is_column(
    col: exp.Column,
    aliases: Dict[str, str],
    target_table: str,
    target_column: str,
    valid_aliases: Set[str],
) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != target_column:
        return False

    if table:
        return _norm(table) == target_table

    # Unqualified column → only allow if unambiguous
    return len(valid_aliases) == 1


# --- min_levels semantics --------------------------------------------------

def _excludes_self(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """
    Returns True ONLY if query guarantees exclusion of self (min_levels > 0).
    """

    for node in tree.find_all((exp.GT, exp.GTE)):
        if not isinstance(node, (exp.GT, exp.GTE)):
            continue

        pairs = [(node.this, node.expression), (node.expression, node.this)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            table, col_name = resolve_table_col(col_node, aliases)

            # min_levels_of_separation is unique to concept_ancestor, so table check is optional
            if _norm(table) and _norm(table) != CONCEPT_ANCESTOR:
                continue
            if _norm(col_name) != MIN_LEVELS:
                continue

            val = _extract_literal_int(val_node)
            if val is None:
                continue

            # min_levels > 0 OR >= 1 excludes self
            if isinstance(node, exp.GT) and val >= 0:
                return True
            if isinstance(node, exp.GTE) and val >= 1:
                return True

    return False


# --- ID Extraction (Scoped) ------------------------------------------------

def _extract_ids_from_branch(
    node: exp.Expression,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
    ancestor_aliases: Set[str],
) -> Tuple[Set[int], Set[int]]:
    """
    Extract (concept_ids, ancestor_ids) within a scoped node.
    """

    concept_ids: Set[int] = set()
    ancestor_ids: Set[int] = set()

    # EQ
    for eq in node.find_all(exp.EQ):
        pairs = [(eq.this, eq.expression), (eq.expression, eq.this)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            val = _extract_literal_int(val_node)
            if val is None:
                continue

            if _is_column(col_node, aliases, CONCEPT_TABLE, CONCEPT_ID, concept_aliases):
                concept_ids.add(val)

            elif _is_column(col_node, aliases, CONCEPT_ANCESTOR, ANCESTOR_CONCEPT_ID, ancestor_aliases):
                ancestor_ids.add(val)

            elif _is_column(col_node, aliases, CONCEPT_ANCESTOR, DESCENDANT_CONCEPT_ID, ancestor_aliases):
                ancestor_ids.add(val)

    # IN
    for in_node in node.find_all(exp.In):
        exprs = in_node.args.get("expressions") or []

        if isinstance(in_node.this, exp.Column):
            col = in_node.this

            for expr in exprs:
                val = _extract_literal_int(expr)
                if val is None:
                    continue

                if _is_column(col, aliases, CONCEPT_TABLE, CONCEPT_ID, concept_aliases):
                    concept_ids.add(val)

                elif _is_column(col, aliases, CONCEPT_ANCESTOR, ANCESTOR_CONCEPT_ID, ancestor_aliases):
                    ancestor_ids.add(val)

                elif _is_column(col, aliases, CONCEPT_ANCESTOR, DESCENDANT_CONCEPT_ID, ancestor_aliases):
                    ancestor_ids.add(val)

    return concept_ids, ancestor_ids


# --- Detection -------------------------------------------------------------

def _detect_union(tree: exp.Expression) -> List[Dict]:
    violations = []

    for union in tree.find_all(exp.Union):
        left = union.this
        right = union.expression

        if not left or not right:
            continue

        left_aliases = extract_aliases(left)
        right_aliases = extract_aliases(right)

        if _excludes_self(left, left_aliases) or _excludes_self(right, right_aliases):
            continue

        left_concept_aliases = _get_aliases_by_table(left_aliases, CONCEPT_TABLE)
        left_ancestor_aliases = _get_aliases_by_table(left_aliases, CONCEPT_ANCESTOR)

        right_concept_aliases = _get_aliases_by_table(right_aliases, CONCEPT_TABLE)
        right_ancestor_aliases = _get_aliases_by_table(right_aliases, CONCEPT_ANCESTOR)

        if not ((left_concept_aliases and right_ancestor_aliases) or
                (right_concept_aliases and left_ancestor_aliases)):
            continue

        l_c, l_a = _extract_ids_from_branch(left, left_aliases, left_concept_aliases, left_ancestor_aliases)
        r_c, r_a = _extract_ids_from_branch(right, right_aliases, right_concept_aliases, right_ancestor_aliases)

        overlap = (l_c | r_c) & (l_a | r_a)

        for cid in overlap:
            violations.append({
                "type": "union",
                "concept_id": cid,
                "context": union.sql(),
                "union_type": "UNION ALL" if union.args.get("distinct") is False else "UNION",
            })

    return violations


def _detect_or(tree: exp.Expression) -> List[Dict]:
    violations = []

    aliases = extract_aliases(tree)

    if _excludes_self(tree, aliases):
        return violations

    concept_aliases = _get_aliases_by_table(aliases, CONCEPT_TABLE)
    ancestor_aliases = _get_aliases_by_table(aliases, CONCEPT_ANCESTOR)

    if not (concept_aliases and ancestor_aliases):
        return violations

    for or_node in tree.find_all(exp.Or):
        if not is_in_where_or_join_clause(or_node):
            continue

        c_ids, a_ids = _extract_ids_from_branch(or_node, aliases, concept_aliases, ancestor_aliases)

        overlap = c_ids & a_ids

        for cid in overlap:
            violations.append({
                "type": "or",
                "concept_id": cid,
                "context": or_node.sql(),
            })

    return violations


def _detect_in_subquery(tree: exp.Expression) -> List[Dict]:
    violations = []

    for in_node in tree.find_all(exp.In):
        exprs = in_node.args.get("expressions") or []

        literals = {_extract_literal_int(e) for e in exprs if not isinstance(e, exp.Subquery)}
        literals.discard(None)

        subqueries = [e for e in exprs if isinstance(e, exp.Subquery)]

        if not literals or not subqueries:
            continue

        for sub in subqueries:
            sub_tree = sub.this
            if not sub_tree or not has_table_reference(sub_tree, CONCEPT_ANCESTOR):
                continue

            sub_aliases = extract_aliases(sub_tree)

            if _excludes_self(sub_tree, sub_aliases):
                continue

            ancestor_aliases = _get_aliases_by_table(sub_aliases, CONCEPT_ANCESTOR)

            _, ancestor_ids = _extract_ids_from_branch(
                sub_tree,
                sub_aliases,
                set(),
                ancestor_aliases,
            )

            overlap = literals & ancestor_ids

            for cid in overlap:
                violations.append({
                    "type": "in_subquery",
                    "concept_id": cid,
                    "context": in_node.sql(),
                })

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ConceptAncestorSelfIncludeRedundancyRule(Rule):
    """Detect redundant inclusion of anchor concepts with concept_ancestor."""

    rule_id = "concept_standardization.concept_ancestor_self_include_redundancy"
    name = "Concept Ancestor Self-Include Redundancy"

    description = (
        "concept_ancestor includes self (min_levels_of_separation = 0). "
        "Explicit inclusion of anchor concepts causes duplication."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use concept_ancestor alone, or filter with min_levels_of_separation > 0."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "concept_ancestor" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        seen: Set[str] = set()
        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree or not has_table_reference(tree, CONCEPT_ANCESTOR):
                continue

            detected = (
                _detect_union(tree) +
                _detect_or(tree) +
                _detect_in_subquery(tree)
            )

            for v in detected:
                key = f"{v['type']}_{v['concept_id']}_{v['context']}"
                if key in seen:
                    continue
                seen.add(key)

                cid = v["concept_id"]

                message = (
                    f"Concept {cid} is explicitly included but also returned by "
                    f"concept_ancestor (self via min_levels_of_separation = 0)."
                )

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details=v,
                    )
                )

        return violations


__all__ = ["ConceptAncestorSelfIncludeRedundancyRule"]

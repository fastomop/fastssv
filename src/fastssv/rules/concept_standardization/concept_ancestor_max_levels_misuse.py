"""Concept Ancestor Max Levels Misuse Validation Rule.

OMOP semantic rule VOCAB_008:
concept_ancestor.max_levels_of_separation represents the longest path in the hierarchy,
not a unique distance metric. Filtering max_levels_of_separation = 1 does NOT reliably
return only direct children.

The Problem:
    The concept_ancestor table tracks hierarchical relationships with two distance columns:
    - min_levels_of_separation: The shortest path from ancestor to descendant
    - max_levels_of_separation: The LONGEST path from ancestor to descendant

    Due to multiple inheritance (a concept can have multiple parents), there can be
    multiple paths between an ancestor and descendant. The max_levels_of_separation
    represents the longest of these paths.

    Common misconception:
    - Users think max_levels_of_separation = 1 returns "direct children"
    - This is WRONG because a concept with multiple paths might have:
      * min_levels_of_separation = 1 (direct child via one path)
      * max_levels_of_separation = 3 (via a longer alternate path)

    Using max_levels_of_separation = 1 will MISS concepts that have direct
    relationships but also have longer alternate paths.

Correct usage:
    - Use min_levels_of_separation = 1 for direct parent-child relationships
    - Use max_levels_of_separation <= N to limit maximum depth to explore
    - Use max_levels_of_separation >= N to find distant relationships only

Violation pattern:
    SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
      AND max_levels_of_separation = 1  -- WRONG: misses multi-path children

Correct patterns:
    -- Find direct children (immediate descendants)
    SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
      AND min_levels_of_separation = 1

    -- Limit hierarchy depth to at most 2 levels
    SELECT descendant_concept_id
    FROM concept_ancestor
    WHERE ancestor_concept_id = 201820
      AND max_levels_of_separation <= 2
"""

from typing import Dict, List, Optional, Set

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
MAX_LEVELS = "max_levels_of_separation"
MIN_LEVELS = "min_levels_of_separation"

# Flag suspicious usage up to this level
SUSPICIOUS_MAX_LEVEL = 3


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_max_levels_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    """Check if column is max_levels_of_separation."""
    table, col_name = resolve_table_col(col, aliases)
    # max_levels_of_separation is unique to concept_ancestor, so table check is optional
    if _norm(table) and _norm(table) != CONCEPT_ANCESTOR:
        return False
    return _norm(col_name) == MAX_LEVELS


def _is_min_levels_present(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if query uses min_levels_of_separation anywhere."""
    for col in tree.find_all(exp.Column):
        table, col_name = resolve_table_col(col, aliases)
        # min_levels_of_separation is unique to concept_ancestor
        if _norm(table) and _norm(table) != CONCEPT_ANCESTOR:
            continue
        if _norm(col_name) == MIN_LEVELS:
            return True
    return False


def _extract_literal_int(node: exp.Expression) -> Optional[int]:
    """Extract integer literal value from expression."""
    if isinstance(node, exp.Literal) and node.is_int:
        try:
            return int(node.this)
        except (ValueError, TypeError):
            return None
    return None


# --- Detection -------------------------------------------------------------

def _detect_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Dict[str, object]]:
    """Detect misuse patterns of max_levels_of_separation."""
    violations: List[Dict[str, object]] = []
    seen: Set[str] = set()

    has_min_levels = _is_min_levels_present(tree, aliases)

    # --- Equality / Inequality (problematic patterns) ---
    for node in list(tree.find_all(exp.EQ)) + list(tree.find_all(exp.NEQ)):
        if not is_in_where_or_join_clause(node):
            continue

        pairs = [(node.this, node.expression), (node.expression, node.this)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_max_levels_column(col_node, aliases):
                continue

            value = _extract_literal_int(val_node)
            if value is None or value > SUSPICIOUS_MAX_LEVEL:
                continue

            key = f"{type(node).__name__}_{value}_{node.sql()}"
            if key in seen:
                continue
            seen.add(key)

            violations.append({
                "operator": type(node).__name__,
                "value": value,
                "context": node.sql(),
                "has_min_levels": has_min_levels,
            })

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ConceptAncestorMaxLevelsMisuseRule(Rule):
    """Detect misuse of max_levels_of_separation for hierarchy traversal."""

    rule_id = "concept_standardization.concept_ancestor_max_levels_misuse"
    name = "Concept Ancestor Max Levels Misuse"

    description = (
        "Detects incorrect usage of max_levels_of_separation for identifying "
        "direct relationships. Due to multiple hierarchy paths, exact equality "
        "on max_levels_of_separation is unreliable."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use min_levels_of_separation = 1 for direct relationships, "
        "or max_levels_of_separation <= N to limit hierarchy depth."
    )
    long_description = (
        "A concept pair can appear multiple times in concept_ancestor via "
        "different hierarchy paths, each with its own "
        "max_levels_of_separation. Exact equality on that column only "
        "matches pairs whose longest path is exactly that value and misses "
        "pairs where a shorter path also exists. Use "
        "max_levels_of_separation <= N for depth limits, or "
        "min_levels_of_separation = 1 when you specifically want direct "
        "parent-child relationships."
    )
    example_bad = (
        "SELECT descendant_concept_id\n"
        "FROM concept_ancestor\n"
        "WHERE ancestor_concept_id = 201820\n"
        "  AND max_levels_of_separation = 1;"
    )
    example_good = (
        "SELECT descendant_concept_id\n"
        "FROM concept_ancestor\n"
        "WHERE ancestor_concept_id = 201820\n"
        "  AND max_levels_of_separation <= 1;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "concept_ancestor" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, CONCEPT_ANCESTOR):
                continue

            aliases = extract_aliases(tree)
            detected = _detect_violations(tree, aliases)

            for v in detected:
                op = v["operator"]
                value = v["value"]

                # --- Message logic ---
                if op == "EQ":
                    if value == 1:
                        message = (
                            "Using max_levels_of_separation = 1 does not reliably return "
                            "direct children due to multiple hierarchy paths. "
                            "Use min_levels_of_separation = 1 instead."
                        )
                        fix = "Replace with min_levels_of_separation = 1"
                    else:
                        message = (
                            f"Using max_levels_of_separation = {value} may exclude valid descendants "
                            f"due to multiple hierarchy paths."
                        )
                        fix = f"Use max_levels_of_separation <= {value}"

                elif op == "NEQ":
                    message = (
                        f"Using max_levels_of_separation != {value} is unreliable due to multiple "
                        f"hierarchy paths."
                    )
                    fix = "Use min_levels_of_separation or range filters (<= / >=)"

                else:
                    continue

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        suggested_fix=fix,
                        details={
                            "operator": op,
                            "value": value,
                            "context": v["context"],
                            "has_min_levels_filter": v["has_min_levels"],
                        },
                    )
                )

        return violations


__all__ = ["ConceptAncestorMaxLevelsMisuseRule"]

"""Maps To Chain Follow To Terminal Rule.

OMOP semantic rule OMOP_600:
When using concept_relationship with relationship_id = 'Maps to' to resolve
source-to-standard mappings, the mapping may form a chain (A maps to B maps to C).
Always follow the chain to the terminal standard concept. A single-hop 'Maps to'
lookup may land on a deprecated or intermediate concept.

The Problem:
    OMOP vocabulary mappings can form chains where a concept maps to another concept
    which maps to yet another concept. Queries that perform only a single-hop lookup
    may land on a deprecated or intermediate concept rather than the final standard concept.

    Example chain:
    - Source concept 44820004 (ICD-10 code)
    - Maps to → Intermediate concept 12345 (deprecated)
    - Maps to → Standard concept 201826 (current standard)

    A single-hop query would incorrectly return 12345 instead of 201826.

Why this is wrong:
    Using deprecated or intermediate concepts in analysis:
    - Causes incorrect results (analyzing deprecated concepts)
    - Breaks vocabulary consistency (mixing old and new concepts)
    - Leads to incomplete cohorts (missing patients with updated mappings)
    - Violates OMOP best practices (should use current standard concepts)

Violation patterns:
    -- Missing standard_concept verification
    SELECT cr.concept_id_2
    FROM concept_relationship cr
    WHERE cr.concept_id_1 = 44820004
    AND cr.relationship_id = 'Maps to'
    AND cr.invalid_reason IS NULL

    -- Missing invalid_reason check on target
    SELECT cr.concept_id_2
    FROM concept_relationship cr
    JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
    WHERE cr.concept_id_1 = 44820004
    AND cr.relationship_id = 'Maps to'
    AND c2.standard_concept = 'S'
    -- Missing: AND c2.invalid_reason IS NULL

Correct patterns:
    -- Verify target is valid standard concept
    SELECT cr.concept_id_2
    FROM concept_relationship cr
    JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
    WHERE cr.concept_id_1 = 44820004
    AND cr.relationship_id = 'Maps to'
    AND cr.invalid_reason IS NULL
    AND c2.standard_concept = 'S'
    AND c2.invalid_reason IS NULL

    -- Recursive approach to follow chain
    WITH RECURSIVE mapping AS (
        SELECT concept_id_1, concept_id_2
        FROM concept_relationship
        WHERE concept_id_1 = 44820004
        AND relationship_id = 'Maps to'
        AND invalid_reason IS NULL
        UNION
        SELECT m.concept_id_1, cr.concept_id_2
        FROM mapping m
        JOIN concept_relationship cr ON m.concept_id_2 = cr.concept_id_1
        WHERE cr.relationship_id = 'Maps to'
        AND cr.invalid_reason IS NULL
    )
    SELECT m.concept_id_2
    FROM mapping m
    JOIN concept c ON m.concept_id_2 = c.concept_id
    WHERE c.standard_concept = 'S'
    AND c.invalid_reason IS NULL
"""

import logging
from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants -------------------------------------------------------------

CONCEPT_RELATIONSHIP_TABLE = "concept_relationship"
CONCEPT_TABLE = "concept"

RELATIONSHIP_ID_COL = "relationship_id"
CONCEPT_ID_COL = "concept_id"
CONCEPT_ID_2_COL = "concept_id_2"

STANDARD_CONCEPT_COL = "standard_concept"
INVALID_REASON_COL = "invalid_reason"

MAPS_TO_VALUE = "Maps to"
STANDARD_VALUE = "S"

# Normalized
CR_NORM = normalize_name(CONCEPT_RELATIONSHIP_TABLE)
CONCEPT_NORM = normalize_name(CONCEPT_TABLE)

REL_ID_NORM = normalize_name(RELATIONSHIP_ID_COL)
CONCEPT_ID_NORM = normalize_name(CONCEPT_ID_COL)
CONCEPT_ID_2_NORM = normalize_name(CONCEPT_ID_2_COL)

STANDARD_CONCEPT_NORM = normalize_name(STANDARD_CONCEPT_COL)
INVALID_REASON_NORM = normalize_name(INVALID_REASON_COL)

MAPS_TO_NORM = normalize_name(MAPS_TO_VALUE)
STANDARD_VALUE_NORM = normalize_name(STANDARD_VALUE)


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    return {
        _norm(cte.alias_or_name)
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _is_maps_to_literal(expr: exp.Expression) -> bool:
    return is_string_literal(expr) and _norm(str(expr.this).strip("'\"")) == MAPS_TO_NORM


def _has_maps_to_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Detect relationship_id = 'Maps to' OR IN ('Maps to')."""

    # EQ
    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        left, right = eq.left, eq.right

        if isinstance(left, exp.Column):
            table, col = resolve_table_col(left, aliases)
            if _norm(col) == REL_ID_NORM and _is_maps_to_literal(right):
                return True

        if isinstance(right, exp.Column):
            table, col = resolve_table_col(right, aliases)
            if _norm(col) == REL_ID_NORM and _is_maps_to_literal(left):
                return True

    # IN
    for in_node in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_node):
            continue

        if not isinstance(in_node.this, exp.Column):
            continue

        table, col = resolve_table_col(in_node.this, aliases)
        if _norm(col) != REL_ID_NORM:
            continue

        for expr in in_node.expressions or []:
            if _is_maps_to_literal(expr):
                return True

    return False


def _get_concept_alias_for_concept_id_2(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Optional[str]:
    """Find concept alias joined via concept_id_2."""
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if not on:
            continue

        for eq in on.find_all(exp.EQ):
            if not is_in_where_or_join_clause(eq):
                continue

            left, right = eq.left, eq.right

            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue

            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            lt, lc = _norm(lt), _norm(lc)
            rt, rc = _norm(rt), _norm(rc)

            left_alias = _norm(left.table) if left.table else None
            right_alias = _norm(right.table) if right.table else None

            # concept_relationship.concept_id_2 → concept.concept_id
            if (
                lc == CONCEPT_ID_2_NORM
                and lt == CR_NORM
                and rc == CONCEPT_ID_NORM
                and rt == CONCEPT_NORM
                and right_alias
            ):
                return right_alias

            if (
                rc == CONCEPT_ID_2_NORM
                and rt == CR_NORM
                and lc == CONCEPT_ID_NORM
                and lt == CONCEPT_NORM
                and left_alias
            ):
                return left_alias

    return None


def _extract_conditions(
    tree: exp.Expression,
    aliases: Dict[str, str],
    concept_alias: str,
) -> Dict[str, Set[str]]:
    """
    Extract conditions for:
      - standard_concept
      - invalid_reason
    Only for the correct concept alias.
    """
    results = {
        "standard": set(),
        "invalid_null": False,
    }

    concept_alias_norm = _norm(concept_alias)

    # EQ
    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        left, right = eq.left, eq.right

        def handle(col_expr, val_expr):
            if not isinstance(col_expr, exp.Column):
                return

            # Use the column's direct table reference (alias), not resolved table name
            col_alias = _norm(col_expr.table) if col_expr.table else None
            _, col = resolve_table_col(col_expr, aliases)
            col = _norm(col)

            # STRICT: must match the concept alias we're checking
            if col_alias != concept_alias_norm:
                return

            if col == STANDARD_CONCEPT_NORM and is_string_literal(val_expr):
                results["standard"].add(_norm(str(val_expr.this).strip("'\"")))

        handle(left, right)
        handle(right, left)

    # IN
    for in_node in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_node):
            continue

        if not isinstance(in_node.this, exp.Column):
            continue

        col_alias = _norm(in_node.this.table) if in_node.this.table else None
        _, col = resolve_table_col(in_node.this, aliases)
        col = _norm(col)

        if col_alias != concept_alias_norm:
            continue

        if col == STANDARD_CONCEPT_NORM:
            for expr in in_node.expressions or []:
                if is_string_literal(expr):
                    results["standard"].add(_norm(str(expr.this).strip("'\"")))

    # IS NULL
    for is_null in tree.find_all(exp.Is):
        if not is_in_where_or_join_clause(is_null):
            continue

        if not isinstance(is_null.expression, exp.Null):
            continue

        col_expr = is_null.this
        if not isinstance(col_expr, exp.Column):
            continue

        col_alias = _norm(col_expr.table) if col_expr.table else None
        _, col = resolve_table_col(col_expr, aliases)
        col = _norm(col)

        if col_alias != concept_alias_norm:
            continue

        if col == INVALID_REASON_NORM:
            results["invalid_null"] = True

    return results


# --- Rule ------------------------------------------------------------------

@register
class MapsToChainFollowToTerminalRule(Rule):
    """
    Ensure 'Maps to' relationships resolve to valid standard concepts.
    """

    rule_id = "concept_standardization.maps_to_chain_follow_to_terminal"
    name = "Maps To Chain Follow To Terminal"

    description = (
        "When using 'Maps to' relationships, ensure the target concept is a valid "
        "standard concept (standard_concept = 'S' AND invalid_reason IS NULL)."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Join concept on concept_id_2 and enforce: "
        "standard_concept = 'S' AND invalid_reason IS NULL."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if CONCEPT_RELATIONSHIP_TABLE not in sql_lower:
            return []

        if MAPS_TO_VALUE.lower() not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            logger.warning(f"[{self.rule_id}] SQL parse error: {err}")
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            cte_names = _extract_cte_names(tree)

            # Must use concept_relationship (not CTE shadow)
            if CR_NORM not in {_norm(t) for t in aliases.values()}:
                continue

            if CR_NORM in cte_names:
                continue

            if not _has_maps_to_filter(tree, aliases):
                continue

            concept_alias = _get_concept_alias_for_concept_id_2(tree, aliases)

            # MUST join concept
            if not concept_alias:
                violations.append(
                    self.create_violation(
                        message=(
                            "Query uses 'Maps to' but does not join concept table via concept_id_2."
                        ),
                        severity=self.severity,
                    )
                )
                continue

            conditions = _extract_conditions(tree, aliases, concept_alias)

            has_standard = STANDARD_VALUE_NORM in conditions["standard"]
            has_invalid = conditions["invalid_null"]

            if has_standard and has_invalid:
                continue

            missing = []
            if not has_standard:
                missing.append("standard_concept = 'S'")
            if not has_invalid:
                missing.append("invalid_reason IS NULL")

            violations.append(
                self.create_violation(
                    message=(
                        f"'Maps to' used but missing {' and '.join(missing)} "
                        f"on concept '{concept_alias}'."
                    ),
                    severity=self.severity,
                )
            )

        return violations


__all__ = ["MapsToChainFollowToTerminalRule"]
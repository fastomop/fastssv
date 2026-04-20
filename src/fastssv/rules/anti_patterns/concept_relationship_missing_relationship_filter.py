"""Concept Relationship Missing Relationship Filter Rule.

OMOP semantic rule VOCAB_018:
A single pair of concepts can have multiple relationship types in concept_relationship
(e.g., 'Maps to', 'Is a', 'Has finding site'). Joining concept_relationship without
filtering relationship_id multiplies rows by the number of relationship types.

The Problem:
    The concept_relationship table stores multiple types of relationships between
    concepts. A single (concept_id_1, concept_id_2) pair can have multiple rows
    with different relationship_id values:
    - 'Maps to'
    - 'Is a'
    - 'Subsumes'
    - 'Has finding site'
    - 'RxNorm has dose form'
    - etc.

    When you JOIN concept_relationship WITHOUT filtering relationship_id, you get
    one row per relationship type. This causes:
    1. Row multiplication: Each concept pair appears multiple times
    2. Incorrect aggregations: Counts and sums are inflated
    3. Duplicate results: Same data with different relationship types

    Common mistakes:
    1. JOIN without relationship_id filter in ON or WHERE clause
    2. Assuming only one relationship exists per concept pair
    3. Not using DISTINCT when multiple relationships are intended

Violation patterns:
    -- WRONG: JOIN without relationship_id filter (row multiplication)
    SELECT c2.concept_name
    FROM concept c1
    JOIN concept_relationship cr
      ON c1.concept_id = cr.concept_id_1
    JOIN concept c2
      ON cr.concept_id_2 = c2.concept_id
    WHERE c1.concept_id = 201826
    -- Returns multiple rows per relationship type!

    -- WRONG: COUNT inflated by unfiltered relationships
    SELECT c1.concept_name, COUNT(*) as related_count
    FROM concept c1
    JOIN concept_relationship cr
      ON c1.concept_id = cr.concept_id_1
    WHERE c1.vocabulary_id = 'SNOMED'
    GROUP BY c1.concept_name
    -- Counts are multiplied by number of relationship types!

    -- WRONG: Data duplication
    SELECT de.drug_exposure_id, c.concept_name
    FROM drug_exposure de
    JOIN concept_relationship cr
      ON de.drug_concept_id = cr.concept_id_1
    JOIN concept c
      ON cr.concept_id_2 = c.concept_id
    -- Each drug_exposure appears multiple times!

Correct patterns:
    -- CORRECT: Filter in JOIN ON clause
    SELECT c2.concept_name
    FROM concept c1
    JOIN concept_relationship cr
      ON c1.concept_id = cr.concept_id_1
      AND cr.relationship_id = 'Maps to'
    JOIN concept c2
      ON cr.concept_id_2 = c2.concept_id
    WHERE c1.concept_id = 201826

    -- CORRECT: Filter in WHERE clause
    SELECT c2.concept_name
    FROM concept c1
    JOIN concept_relationship cr
      ON c1.concept_id = cr.concept_id_1
    JOIN concept c2
      ON cr.concept_id_2 = c2.concept_id
    WHERE c1.concept_id = 201826
      AND cr.relationship_id = 'Is a'

    -- CORRECT: Explicitly enumerate relationships (SELECT relationship_id)
    SELECT c1.concept_name, cr.relationship_id, c2.concept_name
    FROM concept c1
    JOIN concept_relationship cr
      ON c1.concept_id = cr.concept_id_1
    JOIN concept c2
      ON cr.concept_id_2 = c2.concept_id
    WHERE c1.concept_id = 201826

    -- CORRECT: Use DISTINCT to handle duplicates
    SELECT DISTINCT de.drug_exposure_id, c.concept_name
    FROM drug_exposure de
    JOIN concept_relationship cr
      ON de.drug_concept_id = cr.concept_id_1
    JOIN concept c
      ON cr.concept_id_2 = c.concept_id
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
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_RELATIONSHIP = "concept_relationship"
RELATIONSHIP_ID = "relationship_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _get_cr_aliases(aliases: Dict[str, str]) -> Set[str]:
    return {
        alias for alias, table in aliases.items()
        if _norm(table) == CONCEPT_RELATIONSHIP
    }


def _is_relationship_id_column(
    col: exp.Column,
    aliases: Dict[str, str],
    cr_aliases: Set[str],
) -> bool:
    """Strict check for concept_relationship.relationship_id."""
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != RELATIONSHIP_ID:
        return False

    if table:
        return _norm(table) == CONCEPT_RELATIONSHIP

    # Unqualified column allowed only if unambiguous
    return len(cr_aliases) == 1


def _extract_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal):
        return _norm(node.this)
    return None


def _has_relationship_filter(
    node: exp.Expression,
    aliases: Dict[str, str],
    cr_aliases: Set[str],
) -> bool:
    """Check if relationship_id is filtered in a subtree."""

    # EQ / NEQ
    for comp in node.find_all((exp.EQ, exp.NEQ)):
        pairs = [(comp.this, comp.expression), (comp.expression, comp.this)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            if _is_relationship_id_column(col_node, aliases, cr_aliases):
                if _extract_literal(val_node) is not None:
                    return True

    # IN
    for in_node in node.find_all(exp.In):
        if isinstance(in_node.this, exp.Column):
            if _is_relationship_id_column(in_node.this, aliases, cr_aliases):
                exprs = in_node.args.get("expressions") or []
                if any(_extract_literal(e) for e in exprs):
                    return True

    # LIKE
    for like in node.find_all(exp.Like):
        if isinstance(like.this, exp.Column):
            if _is_relationship_id_column(like.this, aliases, cr_aliases):
                return True

    return False


def _selects_relationship_id(
    select: exp.Select,
    aliases: Dict[str, str],
    cr_aliases: Set[str],
) -> bool:
    """Check if relationship_id is explicitly selected."""

    for expr in select.expressions or []:
        if isinstance(expr, exp.Column):
            if _is_relationship_id_column(expr, aliases, cr_aliases):
                return True

        if isinstance(expr, exp.Alias) and isinstance(expr.this, exp.Column):
            if _is_relationship_id_column(expr.this, aliases, cr_aliases):
                return True

    return False


def _groups_by_relationship_id(
    select: exp.Select,
    aliases: Dict[str, str],
    cr_aliases: Set[str],
) -> bool:
    group = select.args.get("group")
    if not group:
        return False

    for expr in group.expressions or []:
        if isinstance(expr, exp.Column):
            if _is_relationship_id_column(expr, aliases, cr_aliases):
                return True

    return False


def _has_having_filter(
    select: exp.Select,
    aliases: Dict[str, str],
    cr_aliases: Set[str],
) -> bool:
    having = select.args.get("having")
    if not having:
        return False

    return _has_relationship_filter(having, aliases, cr_aliases)


# --- Detection -------------------------------------------------------------

def _detect_unfiltered_joins(tree: exp.Expression) -> List[Dict[str, object]]:
    violations: List[Dict[str, object]] = []
    seen: Set[str] = set()

    for select in tree.find_all(exp.Select):
        aliases = extract_aliases(select)
        cr_aliases = _get_cr_aliases(aliases)

        if not cr_aliases:
            continue

        # Check filters scoped to this SELECT
        if (
            _has_relationship_filter(select, aliases, cr_aliases)
            or _has_having_filter(select, aliases, cr_aliases)
        ):
            continue

        # Check if user explicitly handles relationship_id
        if _selects_relationship_id(select, aliases, cr_aliases):
            continue

        if _groups_by_relationship_id(select, aliases, cr_aliases):
            continue

        # Check for DISTINCT
        if select.args.get("distinct"):
            continue

        # Check for SELECT * (includes relationship_id if concept_relationship is in query)
        expressions = select.expressions or []
        has_star = any(isinstance(expr, exp.Star) for expr in expressions)
        if has_star and has_table_reference(select, CONCEPT_RELATIONSHIP):
            continue

        # Detect JOINs
        for join in select.find_all(exp.Join):
            table = join.this

            if not isinstance(table, exp.Table):
                continue

            table_name = _norm(str(table.name))
            table_alias = _norm(str(table.alias)) if table.alias else table_name

            if table_alias not in cr_aliases:
                continue

            key = f"{table_alias}_{id(select)}"
            if key in seen:
                continue
            seen.add(key)

            violations.append({
                "type": "unfiltered_join",
                "alias": table_alias,
                "context": join.sql(),
            })

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ConceptRelationshipMissingRelationshipFilterRule(Rule):
    """Detect JOINs to concept_relationship without relationship_id filter."""

    rule_id = "anti_patterns.concept_relationship_missing_relationship_filter"
    name = "Concept Relationship Missing Relationship Filter"

    description = (
        "Joining concept_relationship without filtering relationship_id causes "
        "row multiplication and semantic ambiguity."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Add a relationship_id filter (e.g., WHERE relationship_id = 'Maps to'), "
        "or explicitly handle multiple relationships via GROUP BY or aggregation."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if CONCEPT_RELATIONSHIP not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree or not has_table_reference(tree, CONCEPT_RELATIONSHIP):
                continue

            detected = _detect_unfiltered_joins(tree)

            for v in detected:
                violations.append(
                    self.create_violation(
                        message=(
                            f"JOIN to concept_relationship (alias: {v['alias']}) "
                            f"without relationship_id filter may cause row multiplication."
                        ),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details=v,
                    )
                )

        return violations


__all__ = ["ConceptRelationshipMissingRelationshipFilterRule"]
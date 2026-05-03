"""Fact Relationship Requires Relationship Concept Filter Rule.

OMOP semantic rules OMOP_250, OMOP_507:
Fact relationship queries should filter by relationship_concept_id to ensure semantic
clarity and optimal query performance. Fact relationship records must specify the
relationship_concept_id describing the relationship between two facts.

The Problem:
    The fact_relationship table links facts across different domain tables using
    relationship types defined by relationship_concept_id. Querying fact_relationship
    without filtering by relationship_concept_id can lead to:
    - Poor query performance (scanning all relationship types)
    - Semantic ambiguity (unclear query intent)
    - Logical errors (mixing incompatible relationship types)

Common relationship types include:
    - "Has temporal context" (concept_id 44818790)
    - "Preceded by" (concept_id 44818783)
    - "Followed by" (concept_id 44818784)
    - "Causally related to" (concept_id 44818888)

Violation patterns:
    -- WRONG: No relationship_concept_id filter (OMOP_507)
    SELECT * FROM fact_relationship
    WHERE fact_id_1 = 100;

    -- WRONG: Filtering on other columns but not relationship_concept_id
    SELECT *
    FROM fact_relationship
    WHERE domain_concept_id_1 = 19;

Correct patterns:
    -- CORRECT: Filter by specific relationship type (OMOP_507)
    SELECT * FROM fact_relationship
    WHERE relationship_concept_id = 44818790  -- "Has temporal context"
      AND fact_id_1 = 100;

    -- CORRECT: Filter by multiple relationship types
    SELECT *
    FROM fact_relationship
    WHERE relationship_concept_id IN (44818783, 44818784)
      AND fact_id_1 = 100;

    -- CORRECT: Using concept table for dynamic filtering
    SELECT fr.*
    FROM fact_relationship fr
    JOIN concept c ON fr.relationship_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Relationship'
      AND c.standard_concept = 'S';
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

FACT_RELATIONSHIP = "fact_relationship"
CONCEPT = "concept"

RELATIONSHIP_CONCEPT_ID = "relationship_concept_id"
CONCEPT_ID = "concept_id"

NORM_FACT_RELATIONSHIP = normalize_name(FACT_RELATIONSHIP)
NORM_RELATIONSHIP_CONCEPT_ID = normalize_name(RELATIONSHIP_CONCEPT_ID)
NORM_CONCEPT = normalize_name(CONCEPT)
NORM_CONCEPT_ID = normalize_name(CONCEPT_ID)


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_relationship_concept_column(
    col: exp.Column,
    aliases: Dict[str, str],
) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != NORM_RELATIONSHIP_CONCEPT_ID:
        return False

    if table:
        return _norm(table) == NORM_FACT_RELATIONSHIP

    # Unqualified → only valid if fact_relationship exists
    return any(_norm(t) == NORM_FACT_RELATIONSHIP for t in aliases.values())


def _has_direct_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Detect direct filters on relationship_concept_id."""

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if isinstance(node, (exp.EQ, exp.NEQ)):
            for side in [node.this, node.expression]:
                if isinstance(side, exp.Column) and _is_relationship_concept_column(side, aliases):
                    return True

        if isinstance(node, exp.In):
            if isinstance(node.this, exp.Column) and _is_relationship_concept_column(node.this, aliases):
                return True

        if isinstance(node, exp.Is):
            if isinstance(node.this, exp.Column) and _is_relationship_concept_column(node.this, aliases):
                return True

    return False


def _is_valid_concept_filter(
    node: exp.Expression,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> bool:
    """Ensure concept filtering is meaningful (not self-comparison)."""

    if not isinstance(node, (exp.EQ, exp.In)):
        return False

    columns = list(node.find_all(exp.Column))

    for col in columns:
        table, _ = resolve_table_col(col, aliases)

        if table and _norm(table) in concept_aliases:
            # Reject self comparisons like c.concept_id = c.concept_id
            col_tables = {resolve_table_col(c, aliases)[0] for c in columns if isinstance(c, exp.Column)}

            if len(col_tables) == 1:
                continue

            return True

    return False


def _has_concept_filtered_join(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check for valid concept join + filter."""

    has_join = False
    concept_aliases: Set[str] = set()

    for join in tree.find_all(exp.Join):
        if not isinstance(join.this, exp.Table):
            continue

        if _norm(join.this.name) != NORM_CONCEPT:
            continue

        concept_alias = join.this.alias_or_name
        if concept_alias:
            concept_aliases.add(_norm(concept_alias))

        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            cols = [c for c in (eq.this, eq.expression) if isinstance(c, exp.Column)]
            if len(cols) != 2:
                continue

            _, left_col = resolve_table_col(cols[0], aliases)
            _, right_col = resolve_table_col(cols[1], aliases)

            if (_is_relationship_concept_column(cols[0], aliases) and _norm(right_col) == NORM_CONCEPT_ID) or (
                _is_relationship_concept_column(cols[1], aliases) and _norm(left_col) == NORM_CONCEPT_ID
            ):
                has_join = True

    if not has_join:
        return False

    # Require meaningful filter on concept table
    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_valid_concept_filter(node, aliases, concept_aliases):
            return True

    return False


def _has_subquery_filter(tree: exp.Expression) -> bool:
    """Check subqueries but only if relevant to fact_relationship."""
    for sub in tree.find_all(exp.Subquery):
        inner = sub.this
        if isinstance(inner, exp.Expression):
            if not has_table_reference(inner, FACT_RELATIONSHIP):
                continue

            aliases = extract_aliases(inner)

            if (
                _has_direct_filter(inner, aliases)
                or _has_concept_filtered_join(inner, aliases)
                or _has_subquery_filter(inner)
            ):
                return True

    return False


def _has_relationship_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    return _has_direct_filter(tree, aliases) or _has_concept_filtered_join(tree, aliases) or _has_subquery_filter(tree)


# --- Rule ------------------------------------------------------------------


@register
class FactRelationshipRequiresRelationshipConceptFilterRule(Rule):
    rule_id = "data_quality.fact_relationship_requires_relationship_concept_filter"
    name = "Fact Relationship Requires Relationship Concept Filter"

    description = (
        "Fact relationship queries should filter by relationship_concept_id to ensure "
        "semantic clarity and optimal query performance."
    )

    severity = Severity.ERROR  # Upgraded for production

    suggested_fix = "ADD: `WHERE relationship_concept_id = <id>` (or IN(...)) when querying fact_relationship. Without it the join fans out across every relationship type."
    long_description = (
        "fact_relationship encodes every kind of linkage between OMOP "
        "facts — visits to procedures, measurements to their ordering "
        "visits, parent-child condition episodes, etc. — each identified "
        "by relationship_concept_id. Querying fact_relationship without "
        "that filter combines unrelated link types into one result set. "
        "Always restrict to the specific relationship you need."
    )
    example_bad = "SELECT fact_id_1, fact_id_2\nFROM fact_relationship;"
    example_good = "SELECT fact_id_1, fact_id_2\nFROM fact_relationship\nWHERE relationship_concept_id = 44818859;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if "fact_relationship" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            if not has_table_reference(tree, FACT_RELATIONSHIP):
                continue

            has_filter = _has_relationship_filter(tree, aliases)

            if not has_filter:
                key = "fact_relationship_no_filter"
                if key not in seen:
                    seen.add(key)
                    violations.append(
                        self.create_violation(
                            message=("fact_relationship table used without filtering by relationship_concept_id."),
                            suggested_fix=self.suggested_fix,
                            details={"table": "fact_relationship"},
                        )
                    )

        return violations


__all__ = ["FactRelationshipRequiresRelationshipConceptFilterRule"]

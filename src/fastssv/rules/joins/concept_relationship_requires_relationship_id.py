from typing import Dict, List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


def _find_concept_relationship_aliases(
    tree: exp.Expression,
) -> Set[str]:
    """Find all aliases of concept_relationship table."""
    cr_aliases: Set[str] = set()

    for table in tree.find_all(exp.Table):
        table_name = normalize_name(table.name)
        if table_name == "concept_relationship":
            alias = table.alias_or_name
            cr_aliases.add(alias)

    return cr_aliases


def _collect_relationship_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cr_aliases: Set[str],
) -> Dict[str, bool]:
    """
    Track which concept_relationship aliases have relationship_id filters.
    Only accepts filters that specify relationship types (EQ, IN).
    Rejects NEQ and IS NULL/IS NOT NULL as they don't prevent cross-products.
    Returns: {alias: has_filter}
    """
    alias_filter_map = {alias: False for alias in cr_aliases}

    for node in tree.walk():
        # Only accept operators that specify relationship types
        if not isinstance(node, (exp.EQ, exp.In)):
            continue

        left = node.this
        right = node.expression

        # Check both sides (handles reversed conditions)
        for col_node, val_node in [(left, right), (right, left)]:
            if not isinstance(col_node, exp.Column):
                continue

            table, col = resolve_table_col(col_node, aliases)
            table = normalize_name(table) if table else ""
            col = normalize_name(col)

            if col != "relationship_id":
                continue

            # Ensure it's from concept_relationship
            if col_node.table not in cr_aliases:
                continue

            alias_filter_map[col_node.table] = True

    return alias_filter_map


def _check_missing_filters(alias_filter_map: Dict[str, bool]) -> List[str]:
    """Generate issues for aliases missing filters."""
    issues = []

    for alias, has_filter in alias_filter_map.items():
        if not has_filter:
            issues.append(
                f"concept_relationship alias '{alias}' is used without filtering on relationship_id. "
                "This will produce a cross-product of all relationship types and likely incorrect results."
            )

    return issues


@register
class ConceptRelationshipRequiresRelationshipIdRule(Rule):
    """Robust validation for concept_relationship usage."""

    rule_id = "joins.concept_relationship_requires_relationship_id"
    name = "Concept Relationship Requires Relationship ID Filter"
    description = (
        "Each use of concept_relationship must filter on relationship_id "
        "to avoid cross-product joins across multiple relationship types."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Add a filter on relationship_id for each concept_relationship alias. "
        "Example: cr.relationship_id = 'Maps to'"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            cr_aliases = _find_concept_relationship_aliases(tree)

            if not cr_aliases:
                continue

            alias_filter_map = _collect_relationship_filters(tree, aliases, cr_aliases)

            issues = _check_missing_filters(alias_filter_map)

            for issue in issues:
                violations.append(self.create_violation(message=issue))

        return violations


__all__ = ["ConceptRelationshipRequiresRelationshipIdRule"]
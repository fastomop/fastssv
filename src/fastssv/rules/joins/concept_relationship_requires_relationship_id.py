from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.patch import add as patch_add, locate
from fastssv.core.registry import register


def _find_concept_relationship_aliases(
    tree: exp.Expression,
) -> Set[str]:
    """Find all aliases of concept_relationship table."""
    cr_aliases: Set[str] = set()

    for table in tree.find_all(exp.Table):
        table_name = normalize_name(table.name)
        if table_name == "concept_relationship":
            alias = normalize_name(table.alias_or_name)
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
            # Normalize the table reference for comparison
            col_table_ref = normalize_name(col_node.table) if col_node.table else ""

            # If unqualified, check if 'relationship_id' could be from a CR table in scope
            if not col_table_ref:
                # relationship_id is unique to concept_relationship table in OMOP CDM
                # If we have CR tables in the query and an unqualified relationship_id,
                # assume it belongs to one of them
                if cr_aliases:
                    # Mark all CR aliases as having a filter (conservative assumption)
                    for cr_alias in cr_aliases:
                        alias_filter_map[cr_alias] = True
                continue

            if col_table_ref not in cr_aliases:
                continue

            alias_filter_map[col_table_ref] = True

    return alias_filter_map


def _is_grouping_by_relationship_id(tree: exp.Expression, cr_aliases: Set[str]) -> bool:
    """Check if query groups by relationship_id from concept_relationship.

    This indicates exploratory analysis, which is valid even without filtering.
    """
    for select in tree.find_all(exp.Select):
        group_by = select.args.get("group")
        if not group_by or not isinstance(group_by, exp.Group):
            continue

        for group_expr in group_by.expressions:
            if isinstance(group_expr, exp.Column):
                col_name = normalize_name(group_expr.name)
                if col_name == "relationship_id":
                    # Check if it's from concept_relationship
                    table_ref = normalize_name(str(group_expr.table)) if group_expr.table else ""
                    if not table_ref or table_ref in cr_aliases:
                        return True

    return False


def _find_join_on_sql_for_alias(
    tree: exp.Expression, target_alias: str
) -> Optional[str]:
    """Find the ON expression SQL for the JOIN that introduces ``target_alias``.

    Returns None if the alias isn't introduced by an explicit JOIN with an ON
    clause (e.g. it's in the FROM clause, or uses USING). The caller falls
    back to FREEFORM in that case.
    """
    target = normalize_name(target_alias)
    for join in tree.find_all(exp.Join):
        table = join.this
        if not isinstance(table, exp.Table):
            continue
        if normalize_name(table.alias_or_name) != target:
            continue
        on = join.args.get("on")
        if on is not None:
            return on.sql()
    return None


def _check_missing_filters(
    alias_filter_map: Dict[str, bool],
    is_exploratory: bool
) -> List[tuple]:
    """Generate issues for aliases missing filters.

    Returns list of (message, severity, alias) tuples. ``alias`` is the
    concept_relationship alias missing a filter, used to build a structured
    ADD patch.

    If the query groups by relationship_id (exploratory analysis), the
    user has explicitly opted in to seeing all relationships — no warning.
    """
    if is_exploratory:
        return []

    issues = []
    for alias, has_filter in alias_filter_map.items():
        if not has_filter:
            issues.append((
                f"concept_relationship alias '{alias}' is used without filtering on relationship_id. "
                f"This may produce a cross-product of all relationship types. "
                f"For analytical queries exploring all relationships, this may be intentional. "
                f"For cohort definitions, add a filter on relationship_id.",
                Severity.WARNING,
                alias,
            ))

    return issues


@register
class ConceptRelationshipRequiresRelationshipIdRule(Rule):
    """Robust validation for concept_relationship usage."""

    rule_id = "joins.concept_relationship_requires_relationship_id"
    name = "Concept Relationship Requires Relationship ID Filter"
    description = (
        "Queries using concept_relationship should typically filter on relationship_id "
        "to avoid cross-product joins. Exploratory/analytical queries may intentionally "
        "omit this filter to analyze all relationships."
    )
    severity = Severity.WARNING  # Changed from ERROR to support analytical queries
    suggested_fix = "ADD: `WHERE cr.relationship_id = '<relationship>'` (or IN(...)) to every concept_relationship query. Common values: 'Maps to', 'Subsumes', 'Is a', 'Has_RxNorm'."
    long_description = (
        "concept_relationship is a many-to-many table: a single source "
        "concept typically has dozens of relationships "
        "('Maps to', 'Is a', 'Subsumes', 'Has brand name', …). Joining to "
        "concept_relationship without filtering on relationship_id pulls "
        "them all, blowing up the row count and mixing semantically "
        "different relationships into one result set. For cohort "
        "definitions, always restrict to a single relationship type "
        "(typically relationship_id = 'Maps to'); exploratory queries "
        "should at least GROUP BY it so the direction of each match is "
        "visible in the output."
    )
    example_bad = (
        "SELECT c1.concept_name, c2.concept_name\n"
        "FROM concept c1\n"
        "JOIN concept_relationship cr\n"
        "  ON c1.concept_id = cr.concept_id_1\n"
        "JOIN concept c2\n"
        "  ON cr.concept_id_2 = c2.concept_id\n"
        "WHERE c1.concept_id = 201820;"
    )
    example_good = (
        "SELECT c1.concept_name, c2.concept_name\n"
        "FROM concept c1\n"
        "JOIN concept_relationship cr\n"
        "  ON c1.concept_id = cr.concept_id_1\n"
        "  AND cr.relationship_id = 'Maps to'\n"
        "JOIN concept c2\n"
        "  ON cr.concept_id_2 = c2.concept_id\n"
        "WHERE c1.concept_id = 201820;"
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

            # Check if this is exploratory analysis (GROUP BY relationship_id)
            is_exploratory = _is_grouping_by_relationship_id(tree, cr_aliases)

            issues = _check_missing_filters(alias_filter_map, is_exploratory)

            # Strict-mode escalation: promote the base WARNING to ERROR when
            # strict mode is on. Listed in validation_context.strict_escalation_rules.
            from fastssv.core.validation_context import get_validation_context
            ctx = get_validation_context()
            escalate = ctx.should_escalate_rule(self.rule_id)

            for issue, severity, alias in issues:
                final_severity = (
                    Severity.ERROR
                    if escalate and severity == Severity.WARNING
                    else severity
                )

                # Structured patch: ADD `AND <alias>.relationship_id =
                # '<relationship>'` to the end of the JOIN ON clause that
                # introduces the offending concept_relationship alias. The
                # relationship value is left as a `<relationship>`
                # placeholder for the outer correction loop. If the alias
                # isn't introduced by an ON-bearing JOIN, fall back to
                # FREEFORM via auto-default.
                patch = None
                on_sql = _find_join_on_sql_for_alias(tree, alias)
                if on_sql is not None:
                    span = locate(sql, on_sql)
                    if span is not None:
                        patch = patch_add(
                            span[1],
                            f" AND {alias}.relationship_id = '<relationship>'",
                        )

                violations.append(self.create_violation(
                    message=issue,
                    severity=final_severity,
                    details={"strict_mode_escalated": final_severity != severity},
                    suggested_fix_patch=patch,
                ))

        return violations


__all__ = ["ConceptRelationshipRequiresRelationshipIdRule"]

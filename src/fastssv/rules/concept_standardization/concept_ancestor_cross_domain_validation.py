"""Concept Ancestor Cross-Domain Validation Rule.

OMOP semantic rule VOCAB_014:
concept_ancestor hierarchies are domain-specific. An ancestor_concept_id from the
Condition domain will only have Condition descendants. Queries should not expect
cross-domain results.

The Problem:
    The concept_ancestor table represents hierarchical relationships within domains:
    - A Condition ancestor has only Condition descendants
    - A Drug ancestor has only Drug descendants
    - A Procedure ancestor has only Procedure descendants

    Cross-domain relationships exist in concept_relationship (e.g., 'Has indication'),
    NOT in concept_ancestor.

    Filtering descendant concepts by a different domain_id than the ancestor's
    domain will always return zero results.

Common mistake scenarios:
    1. Trying to find drugs to treat a condition via concept_ancestor
       (should use concept_relationship with 'Has indication')

    2. Mixing domains when expanding hierarchies
       (e.g., drug ancestor with procedure descendants)

    3. Misunderstanding OMOP's domain architecture

Violation pattern:
    SELECT ca.descendant_concept_id
    FROM concept_ancestor ca
    JOIN concept c ON ca.descendant_concept_id = c.concept_id
    WHERE ca.ancestor_concept_id = 201820  -- Condition: Diabetes
      AND c.domain_id = 'Drug'             -- ERROR: No Drug descendants!

Correct pattern:
    -- Use concept_relationship for cross-domain relationships
    SELECT cr.concept_id_2 AS drug_concept_id
    FROM concept_relationship cr
    WHERE cr.concept_id_1 = 201820
      AND cr.relationship_id = 'Has indication'
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
CONCEPT = "concept"
ANCESTOR_CONCEPT_ID = "ancestor_concept_id"
DESCENDANT_CONCEPT_ID = "descendant_concept_id"
DOMAIN_ID = "domain_id"

# Known ancestor domains (safe subset only)
KNOWN_ANCESTOR_DOMAINS: Dict[int, str] = {
    201820: "Condition",
    192671: "Condition",
    316866: "Condition",
    321052: "Condition",
    313217: "Condition",
    21600001: "Drug",
    21601664: "Drug",
    21604254: "Drug",
    1503297: "Drug",
    1545958: "Drug",
    4203722: "Procedure",
    4301351: "Procedure",
    3000963: "Measurement",
    3004410: "Measurement",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _extract_literal_int(node: exp.Expression) -> Optional[int]:
    if isinstance(node, exp.Literal) and node.is_int:
        try:
            return int(node.this)
        except Exception:
            return None
    return None


def _extract_literal_str(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


def _is_column(node: exp.Expression, col_name: str, aliases: Dict[str, str]) -> bool:
    if not isinstance(node, exp.Column):
        return False
    _, column = resolve_table_col(node, aliases)
    return _norm(column) == _norm(col_name)


def _resolve_table_name(alias: Optional[str], aliases: Dict[str, str]) -> Optional[str]:
    if not alias:
        return None
    return _norm(aliases.get(alias))


# --- Extraction ------------------------------------------------------------

def _find_ancestor_filters(tree: exp.Expression, aliases: Dict[str, str]) -> Set[int]:
    ancestor_ids: Set[int] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if isinstance(node, exp.EQ):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if _is_column(col_node, ANCESTOR_CONCEPT_ID, aliases):
                    cid = _extract_literal_int(val_node)
                    if cid:
                        ancestor_ids.add(cid)

        elif isinstance(node, exp.In):
            if _is_column(node.this, ANCESTOR_CONCEPT_ID, aliases):
                for expr in node.expressions or []:
                    cid = _extract_literal_int(expr)
                    if cid:
                        ancestor_ids.add(cid)

    return ancestor_ids


def _find_descendant_concept_aliases(
    tree: exp.Expression,
    aliases: Dict[str, str],
    ca_aliases: Set[str],
) -> Set[str]:
    descendant_aliases: Set[str] = set()

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            if not isinstance(eq.this, exp.Column) or not isinstance(eq.expression, exp.Column):
                continue

            lt_alias, lt_col = resolve_table_col(eq.this, aliases)
            rt_alias, rt_col = resolve_table_col(eq.expression, aliases)

            lt_table = _resolve_table_name(lt_alias, aliases)
            rt_table = _resolve_table_name(rt_alias, aliases)

            # concept_ancestor.descendant_concept_id -> concept.concept_id
            if (
                lt_alias in ca_aliases
                and _norm(lt_col) == DESCENDANT_CONCEPT_ID
                and rt_table == CONCEPT
                and _norm(rt_col) == "concept_id"
            ):
                descendant_aliases.add(_norm(rt_alias))

            elif (
                rt_alias in ca_aliases
                and _norm(rt_col) == DESCENDANT_CONCEPT_ID
                and lt_table == CONCEPT
                and _norm(lt_col) == "concept_id"
            ):
                descendant_aliases.add(_norm(lt_alias))

    return descendant_aliases


def _find_domain_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
    descendant_aliases: Set[str],
) -> List[Tuple[str, str]]:
    filters: List[Tuple[str, str]] = []

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if isinstance(node, exp.EQ):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                table, column = resolve_table_col(col_node, aliases)
                alias_norm = _norm(table or col_node.table)

                if _norm(column) == DOMAIN_ID and alias_norm in descendant_aliases:
                    value = _extract_literal_str(val_node)
                    if value:
                        filters.append((value, node.sql()))

        elif isinstance(node, exp.In):
            if isinstance(node.this, exp.Column):
                table, column = resolve_table_col(node.this, aliases)
                alias_norm = _norm(table or node.this.table)

                if _norm(column) == DOMAIN_ID and alias_norm in descendant_aliases:
                    for expr in node.expressions or []:
                        value = _extract_literal_str(expr)
                        if value:
                            filters.append((value, node.sql()))

    return filters


# --- Core Validation -------------------------------------------------------

def _validate_domain_compatibility(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Dict[str, object]]:
    violations: List[Dict[str, object]] = []

    ca_aliases = {
        _norm(alias)
        for alias, table in aliases.items()
        if _norm(table) == CONCEPT_ANCESTOR
    }

    if not ca_aliases:
        return []

    descendant_aliases = _find_descendant_concept_aliases(tree, aliases, ca_aliases)
    if not descendant_aliases:
        return []

    ancestor_ids = _find_ancestor_filters(tree, aliases)
    if not ancestor_ids:
        return []

    domain_filters = _find_domain_filters(tree, aliases, descendant_aliases)
    if not domain_filters:
        return []

    expected_domains = {
        KNOWN_ANCESTOR_DOMAINS[a]
        for a in ancestor_ids
        if a in KNOWN_ANCESTOR_DOMAINS
    }

    if not expected_domains:
        return []  # no strong signal → don't fire

    for domain_value, context in domain_filters:
        if all(_norm(domain_value) != _norm(d) for d in expected_domains):
            violations.append({
                "expected_domains": list(expected_domains),
                "filtered_domain": domain_value,
                "ancestor_ids": list(ancestor_ids),
                "context": context,
            })

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ConceptAncestorCrossDomainValidation(Rule):
    """Validate concept_ancestor is not misused across domains."""

    rule_id = "concept_standardization.concept_ancestor_cross_domain"
    name = "Concept Ancestor Cross-Domain Validation"

    description = (
        "Ensures concept_ancestor hierarchies are used within the correct domain. "
        "Hierarchies are domain-specific and do not cross domains."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: `JOIN concept_ancestor` WITH a same-domain ancestor join, OR use `concept_relationship` (with relationship_id filter) for cross-domain relationships. Hierarchies don't span domains in OMOP."
    long_description = (
        "concept_ancestor hierarchies are built within a single domain: a "
        "Condition ancestor has only Condition descendants, a Drug ancestor "
        "has only Drug descendants, and so on. Filtering descendants by a "
        "different domain_id always yields zero rows, which is almost never "
        "what the author intended. Cross-domain lookups belong in "
        "concept_relationship (e.g. 'Has indication' from Drug to Condition), "
        "not in concept_ancestor."
    )
    example_bad = (
        "SELECT ca.descendant_concept_id\n"
        "FROM concept_ancestor ca\n"
        "JOIN concept c ON ca.descendant_concept_id = c.concept_id\n"
        "WHERE ca.ancestor_concept_id = 201820\n"
        "  AND c.domain_id = 'Drug';"
    )
    example_good = (
        "SELECT ca.descendant_concept_id\n"
        "FROM concept_ancestor ca\n"
        "JOIN concept c ON ca.descendant_concept_id = c.concept_id\n"
        "WHERE ca.ancestor_concept_id = 201820\n"
        "  AND c.domain_id = 'Condition';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "concept_ancestor" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, CONCEPT_ANCESTOR):
                continue

            aliases = extract_aliases(tree)
            issues = _validate_domain_compatibility(tree, aliases)

            for issue in issues:
                key = f"{issue['filtered_domain']}|{issue['context']}"
                if key in seen:
                    continue
                seen.add(key)

                violations.append(
                    self.create_violation(
                        message=(
                            f"Domain mismatch in concept_ancestor: filtering descendants by "
                            f"domain_id = '{issue['filtered_domain']}' but ancestor concepts "
                            f"{issue['ancestor_ids']} belong to domains {issue['expected_domains']}. "
                            f"Hierarchies are domain-specific."
                        ),
                        severity=self.severity,
                        suggested_fix=(
                            f"REPLACE: the descendant `domain_id` filter WITH one matching "
                            f"the ancestor's domain ({', '.join(repr(d) for d in issue['expected_domains'])}), "
                            f"OR REWRITE using `concept_relationship` for cross-domain mappings."
                        ),
                        details=issue,
                    )
                )

        return violations


__all__ = ["ConceptAncestorCrossDomainValidation"]

"""Concept Ancestor to Concept Name Resolution Validation Rule.

OMOP semantic rule JOIN_016:
When joining concept_ancestor to concept for name resolution, the join column
must match the intended semantic direction.

The Problem:
    concept_ancestor has two concept_id columns:
    - ancestor_concept_id: The parent/higher-level concept
    - descendant_concept_id: The child/lower-level concept

    When joining to the concept table to retrieve concept_name, the join column
    determines WHICH concept's name you get. Common mistakes:

    1. Aliasing as "descendant_name" but joining on ancestor_concept_id
       - Returns the parent concept's name, not the descendant's
    2. Aliasing as "ancestor_name" but joining on descendant_concept_id
       - Returns the child concept's name, not the ancestor's
    3. Filtering on descendant_concept_id but joining concept on ancestor_concept_id
       - Intent is to get descendant info, but join retrieves ancestor info

Violation pattern:
    SELECT c.concept_name AS descendant_name
    FROM concept_ancestor ca
    JOIN concept c ON ca.ancestor_concept_id = c.concept_id
    -- WRONG: alias says "descendant" but join uses ancestor_concept_id
    WHERE ca.descendant_concept_id = 201826

Correct pattern:
    SELECT c.concept_name AS descendant_name
    FROM concept_ancestor ca
    JOIN concept c ON ca.descendant_concept_id = c.concept_id
    WHERE ca.ancestor_concept_id = 201826
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_ANCESTOR = "concept_ancestor"
CONCEPT = "concept"

ANCESTOR_CONCEPT_ID = "ancestor_concept_id"
DESCENDANT_CONCEPT_ID = "descendant_concept_id"
CONCEPT_ID = "concept_id"

DESCENDANT_KEYWORDS = {"descendant", "child", "specific", "lower", "detail", "narrow"}
ANCESTOR_KEYWORDS = {"ancestor", "parent", "higher", "class", "broad", "category"}

TARGET_COLUMNS = {"concept_name", "concept_code", "vocabulary_id", "domain_id"}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _tokenize(name: str) -> List[str]:
    return name.lower().replace("-", "_").split("_")


def _infer_intent(alias: Optional[str]) -> Optional[str]:
    if not alias:
        return None

    tokens = _tokenize(alias)

    if any(t in DESCENDANT_KEYWORDS for t in tokens):
        return "descendant"

    if any(t in ANCESTOR_KEYWORDS for t in tokens):
        return "ancestor"

    return None


def _extract_all_column_refs(select: exp.Select) -> List[Tuple[str, str, Optional[str]]]:
    """
    Extract (table, column, alias_name) for all columns in SELECT subtree.
    """
    results = []

    for node in select.walk():
        if isinstance(node, exp.Alias):
            alias = node.alias_or_name
            target = node.this

            if isinstance(target, exp.Column):
                table = target.table
                col = target.name
                results.append((table, col, alias))

        elif isinstance(node, exp.Column):
            # Skip columns that are inside an Alias node (already processed above)
            if isinstance(node.parent, exp.Alias):
                continue
            table = node.table
            col = node.name
            results.append((table, col, None))

    return results


def _extract_ca_concept_joins(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str]]:
    """
    Returns list of (concept_alias, ca_column_used)
    """
    results = []

    # JOIN + WHERE conditions
    eqs = list(tree.find_all(exp.EQ))

    for eq in eqs:
        if not (isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)):
            continue

        lt, lc = resolve_table_col(eq.this, aliases)
        rt, rc = resolve_table_col(eq.expression, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt_norm = _normalize_table(lt)
        rt_norm = _normalize_table(rt)

        # CA -> concept
        if lt_norm == CONCEPT_ANCESTOR and rt_norm == CONCEPT:
            if _norm(rc) == CONCEPT_ID:
                if _norm(lc) in {ANCESTOR_CONCEPT_ID, DESCENDANT_CONCEPT_ID}:
                    # Use the alias from the Column node, not the resolved table name
                    concept_alias = eq.expression.table if eq.expression.table else rt
                    results.append((concept_alias, lc))

        elif rt_norm == CONCEPT_ANCESTOR and lt_norm == CONCEPT:
            if _norm(lc) == CONCEPT_ID:
                if _norm(rc) in {ANCESTOR_CONCEPT_ID, DESCENDANT_CONCEPT_ID}:
                    # Use the alias from the Column node, not the resolved table name
                    concept_alias = eq.this.table if eq.this.table else lt
                    results.append((concept_alias, rc))

    return results


# --- Detection -------------------------------------------------------------

def _detect(
    tree: exp.Expression,
    aliases: Dict[str, str],
):
    errors = []
    warnings = []

    seen_e = set()
    seen_w = set()

    joins = _extract_ca_concept_joins(tree, aliases)

    for concept_table_ref, ca_col in joins:
        concept_alias = _norm(concept_table_ref)

        # Find relevant SELECT
        for select in tree.find_all(exp.Select):

            column_refs = _extract_all_column_refs(select)

            for table, col, alias in column_refs:
                table_norm = _norm(table)

                if table_norm != concept_alias:
                    continue

                col_norm = _norm(col)
                if col_norm not in TARGET_COLUMNS:
                    continue

                intent = _infer_intent(alias)

                # --- mismatch logic ---
                if intent == "descendant" and ca_col == ANCESTOR_CONCEPT_ID:
                    key = (concept_alias, col_norm, ca_col)
                    if key not in seen_e:
                        errors.append(key)
                        seen_e.add(key)

                elif intent == "ancestor" and ca_col == DESCENDANT_CONCEPT_ID:
                    key = (concept_alias, col_norm, ca_col)
                    if key not in seen_e:
                        errors.append(key)
                        seen_e.add(key)

                # Note: When intent is unclear (None), we don't flag it as a violation.
                # Only flag clear semantic mismatches.

    return errors, warnings


# --- Rule ------------------------------------------------------------------

@register
class ConceptAncestorNameResolutionValidationRule(Rule):
    """Validate concept_ancestor joins match semantic intent."""

    rule_id = "joins.concept_ancestor_name_resolution"
    name = "Concept Ancestor Name Resolution Validation"

    description = (
        "Ensures concept_ancestor joins align with semantic intent "
        "(ancestor vs descendant) inferred from column aliases."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use descendant_concept_id for descendant values and "
        "ancestor_concept_id for ancestor values."
    )

    def validate(self, sql: str, dialect: str = "postgres"):
        violations = []

        sql_lower = sql.lower()
        if "concept_ancestor" not in sql_lower or "concept" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            if not (uses_table(tree, CONCEPT_ANCESTOR) and uses_table(tree, CONCEPT)):
                continue

            aliases = extract_aliases(tree)

            errors, _ = _detect(tree, aliases)

            for concept_alias, col, join_col in errors:
                expected = (
                    DESCENDANT_CONCEPT_ID
                    if join_col == ANCESTOR_CONCEPT_ID
                    else ANCESTOR_CONCEPT_ID
                )

                violations.append(
                    self.create_violation(
                        message=(
                            f"Semantic mismatch: alias implies opposite hierarchy direction. "
                            f"Using {join_col} but intent suggests {expected}."
                        ),
                        suggested_fix=(
                            f"Use concept_ancestor.{expected} = concept.concept_id"
                        ),
                        details={
                            "type": "semantic_mismatch",
                            "column": col,
                            "used_join": join_col,
                            "expected_join": expected,
                        },
                    )
                )

        return violations


__all__ = ["ConceptAncestorNameResolutionValidationRule"]
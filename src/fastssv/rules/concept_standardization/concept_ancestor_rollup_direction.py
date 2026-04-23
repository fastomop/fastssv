"""Concept Ancestor Rollup Direction Validation Rule.

OMOP semantic rule VOCAB_002:
When rolling up clinical events to a parent concept using concept_ancestor,
the join direction must be correct:
- Clinical table's *_concept_id → concept_ancestor.descendant_concept_id
- Filter on concept_ancestor.ancestor_concept_id

The Problem:
    concept_ancestor represents hierarchical relationships:
    - descendant_concept_id: The more specific child concept (e.g., "Type 2 Diabetes")
    - ancestor_concept_id: The more general parent concept (e.g., "Diabetes Mellitus")

    Patient records contain specific diagnoses (descendants), not parent concepts.
    To roll up to parent concepts, you must:
    1. Join clinical table's concept_id to descendant_concept_id
    2. Filter on ancestor_concept_id

    Swapping this reverses the hierarchy, causing:
    - Missed child concepts
    - Incorrect aggregations
    - Potentially zero results

Violation pattern:
    SELECT ca.descendant_concept_id, COUNT(*)
    FROM condition_occurrence co
    JOIN concept_ancestor ca
      ON co.condition_concept_id = ca.ancestor_concept_id  -- WRONG!
    WHERE ca.descendant_concept_id = 201820
    GROUP BY ca.descendant_concept_id

Correct pattern:
    SELECT ca.ancestor_concept_id, COUNT(*)
    FROM condition_occurrence co
    JOIN concept_ancestor ca
      ON co.condition_concept_id = ca.descendant_concept_id  -- Correct
    WHERE ca.ancestor_concept_id = 201820
    GROUP BY ca.ancestor_concept_id
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

CONCEPT_ANCESTOR = "concept_ancestor"
ANCESTOR_CONCEPT_ID = "ancestor_concept_id"
DESCENDANT_CONCEPT_ID = "descendant_concept_id"

CLINICAL_TABLES: Set[str] = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "device_exposure",
    "specimen",
}

CONCEPT_ID_SUFFIX = "_concept_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _resolve_alias(
    table_or_alias: Optional[str],
    aliases: Dict[str, str],
) -> Optional[str]:
    """Resolve table or alias to canonical alias used in query."""
    if not table_or_alias:
        return None

    for alias, table in aliases.items():
        if _norm(alias) == _norm(table_or_alias) or _norm(table) == _norm(table_or_alias):
            return alias
    return None


def _resolve_table(
    table_or_alias: Optional[str],
    aliases: Dict[str, str],
) -> Optional[str]:
    """Resolve alias → actual table name."""
    if not table_or_alias:
        return None

    for alias, table in aliases.items():
        if _norm(alias) == _norm(table_or_alias) or _norm(table) == _norm(table_or_alias):
            return _norm(table)
    return None


def _is_clinical_table(table: Optional[str]) -> bool:
    return _norm(table) in CLINICAL_TABLES


def _is_concept_id_column(col: Optional[str]) -> bool:
    return _norm(col).endswith(CONCEPT_ID_SUFFIX) if col else False


def _is_predicate(node: exp.Expression) -> bool:
    """Check if node is a filtering predicate."""
    return isinstance(
        node,
        (
            exp.EQ, exp.NEQ,
            exp.GT, exp.GTE,
            exp.LT, exp.LTE,
            exp.In, exp.Between,
        ),
    )


# --- Detection -------------------------------------------------------------

def _analyze_concept_ancestor_usage(
    tree: exp.Expression,
    aliases: Dict[str, str],
):
    """
    Track usage per concept_ancestor alias.

    Returns:
        ca_alias → {
            'joined_to': set(...),
            'clinical_table': str | None,
            'clinical_column': str | None,
            'filters': set(...)
        }
    """
    usage: Dict[str, Dict[str, object]] = {}

    # Initialize concept_ancestor aliases
    for alias, table in aliases.items():
        if _norm(table) == CONCEPT_ANCESTOR:
            usage[alias] = {
                "joined_to": set(),
                "clinical_table": None,
                "clinical_column": None,
                "filters": set(),
            }

    if not usage:
        return usage

    def _process_join_equality(eq: exp.EQ):
        if not (isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)):
            return

        lt, lc = resolve_table_col(eq.this, aliases)
        rt, rc = resolve_table_col(eq.expression, aliases)

        lt_table = _resolve_table(lt, aliases)
        rt_table = _resolve_table(rt, aliases)

        lt_alias = _resolve_alias(lt, aliases)
        rt_alias = _resolve_alias(rt, aliases)

        # Clinical → concept_ancestor
        if _is_clinical_table(lt_table) and rt_table == CONCEPT_ANCESTOR:
            if _is_concept_id_column(lc):
                ca_col = _norm(rc)
                if rt_alias in usage and ca_col in {ANCESTOR_CONCEPT_ID, DESCENDANT_CONCEPT_ID}:
                    usage[rt_alias]["joined_to"].add(ca_col)
                    usage[rt_alias]["clinical_table"] = lt_table
                    usage[rt_alias]["clinical_column"] = _norm(lc)

        # concept_ancestor → clinical
        elif lt_table == CONCEPT_ANCESTOR and _is_clinical_table(rt_table):
            if _is_concept_id_column(rc):
                ca_col = _norm(lc)
                if lt_alias in usage and ca_col in {ANCESTOR_CONCEPT_ID, DESCENDANT_CONCEPT_ID}:
                    usage[lt_alias]["joined_to"].add(ca_col)
                    usage[lt_alias]["clinical_table"] = rt_table
                    usage[lt_alias]["clinical_column"] = _norm(rc)

    # --- JOIN ON ---
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            _process_join_equality(eq)

    # --- WHERE ---
    for where in tree.find_all(exp.Where):
        for node in where.walk():
            # implicit joins
            if isinstance(node, exp.EQ):
                _process_join_equality(node)

            # filters
            if _is_predicate(node):
                for col in node.find_all(exp.Column):
                    table, column = resolve_table_col(col, aliases)
                    table_resolved = _resolve_table(table, aliases)
                    alias_resolved = _resolve_alias(table, aliases)

                    if table_resolved == CONCEPT_ANCESTOR:
                        col_norm = _norm(column)
                        if col_norm in {ANCESTOR_CONCEPT_ID, DESCENDANT_CONCEPT_ID}:
                            if alias_resolved in usage:
                                usage[alias_resolved]["filters"].add(col_norm)

    return usage


def _detect_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Dict[str, str]]:
    violations = []

    usage = _analyze_concept_ancestor_usage(tree, aliases)

    for ca_alias, info in usage.items():
        joined_to: Set[str] = info["joined_to"]
        filters: Set[str] = info["filters"]
        clinical_table = info["clinical_table"]
        clinical_column = info["clinical_column"]

        # --- Error: reversed hierarchy ---
        # Only flag when joined to ancestor_concept_id AND filtering on descendant_concept_id
        # This is the classic reversed join pattern
        if ANCESTOR_CONCEPT_ID in joined_to and DESCENDANT_CONCEPT_ID in filters:
            violations.append({
                "alias": ca_alias,
                "joined_to": ANCESTOR_CONCEPT_ID,
                "expected": DESCENDANT_CONCEPT_ID,
                "clinical_table": clinical_table,
                "clinical_column": clinical_column,
                "type": "reversed_hierarchy",
            })

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ConceptAncestorRollupDirectionRule(Rule):
    """Ensure correct concept_ancestor rollup direction."""

    rule_id = "concept_standardization.concept_ancestor_rollup_direction"
    name = "Concept Ancestor Rollup Direction"

    description = (
        "Ensures correct usage of concept_ancestor for hierarchical rollups: "
        "join clinical concept_id to descendant_concept_id and filter on ancestor_concept_id."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Join clinical concept_id to concept_ancestor.descendant_concept_id "
        "and filter on concept_ancestor.ancestor_concept_id."
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
                message = (
                    f"Incorrect concept_ancestor usage in {v['clinical_table']}. "
                    f"{v['clinical_column']} is joined to {v['joined_to']}, "
                    f"but expected {v['expected']} for proper hierarchy traversal."
                )

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        suggested_fix=(
                            f"Use: {v['clinical_table']}.{v['clinical_column']} = "
                            f"concept_ancestor.{v['expected']} and filter on "
                            f"concept_ancestor.{v['joined_to']}"
                        ),
                        details=v,
                    )
                )

        return violations


__all__ = ["ConceptAncestorRollupDirectionRule"]

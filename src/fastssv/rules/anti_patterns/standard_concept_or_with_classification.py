"""Standard Concept OR with Classification Anti-Pattern Rule.

OMOP semantic rule VOCAB_016:
Filtering standard_concept with OR logic to include both 'S' (Standard) and 'C'
(Classification) is an anti-pattern. Classification concepts should not be used
in clinical table lookups.

The Problem:
    The standard_concept column has three values:
    - 'S': Standard concept (for use in clinical data)
    - 'C': Classification concept (hierarchical grouping only)
    - NULL: Non-standard concept (deprecated, source-specific)

    Classification concepts ('C') are high-level groupings and should NOT be used
    in clinical table *_concept_id fields. They're meant for vocabulary hierarchy
    and navigation, not patient data.

    Using OR logic to include both 'S' and 'C' dilutes data quality by mixing
    clinical concepts with non-clinical hierarchy concepts.

Common mistake scenarios:
    1. Concept set building with overly permissive filters
       (standard_concept = 'S' OR standard_concept = 'C')

    2. Using IN clause with both values
       standard_concept IN ('S', 'C')

    3. Misunderstanding that 'C' concepts are not for clinical use

Violation pattern:
    SELECT concept_id, concept_name
    FROM concept
    WHERE concept_name LIKE '%diabetes%'
      AND (standard_concept = 'S' OR standard_concept = 'C')
    -- ERROR: Includes classification concepts!

Correct pattern:
    -- For clinical data (most common):
    SELECT concept_id, concept_name
    FROM concept
    WHERE concept_name LIKE '%diabetes%'
      AND standard_concept = 'S'

    -- For hierarchy analysis (explicit intent):
    SELECT concept_id, concept_name
    FROM concept
    WHERE concept_class_id = 'Clinical Finding'
      AND standard_concept = 'C'
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    has_table_reference,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_TABLE = "concept"
STANDARD_CONCEPT = "standard_concept"
STANDARD_VALUES = {"s"}
CLASSIFICATION_VALUES = {"c"}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _get_concept_aliases(aliases: Dict[str, str]) -> Set[str]:
    return {
        alias for alias, table in aliases.items()
        if _norm(table) == CONCEPT_TABLE
    }


def _is_standard_concept_column(
    column: exp.Column,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> bool:
    """Strict check for concept.standard_concept."""
    if _norm(column.name) != STANDARD_CONCEPT:
        return False

    if column.table:
        alias = _norm(str(column.table))
        return alias in concept_aliases

    # Only allow unqualified if exactly one concept table
    return len(concept_aliases) == 1


def _extract_literal_value(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        return _norm(node.this)
    return None


def _collect_eq_values(
    node: exp.Expression,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> Set[str]:
    """Collect standard_concept values from direct children."""
    values: Set[str] = set()

    for eq in node.find_all(exp.EQ):
        if not (isinstance(eq.this, exp.Column) or isinstance(eq.expression, exp.Column)):
            continue

        pairs = [(eq.this, eq.expression), (eq.expression, eq.this)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            if _is_standard_concept_column(col_node, aliases, concept_aliases):
                val = _extract_literal_value(val_node)
                if val:
                    values.add(val)

    return values


def _detect_or_pattern(
    tree: exp.Expression,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> List[str]:
    violations = []

    for node in tree.walk():
        if not isinstance(node, exp.Or):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        values = _collect_eq_values(node, aliases, concept_aliases)

        if values & STANDARD_VALUES and values & CLASSIFICATION_VALUES:
            violations.append(
                f"OR mixes standard ('S') and classification ('C'): {node.sql()}"
            )

    return violations


def _detect_in_pattern(
    tree: exp.Expression,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> List[str]:
    violations = []

    for node in tree.walk():
        if not isinstance(node, exp.In):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        if not isinstance(node.this, exp.Column):
            continue

        if not _is_standard_concept_column(node.this, aliases, concept_aliases):
            continue

        exprs = node.args.get("expressions") or []
        values = {_extract_literal_value(e) for e in exprs}
        values.discard(None)

        if values & STANDARD_VALUES and values & CLASSIFICATION_VALUES:
            violations.append(
                f"IN mixes standard ('S') and classification ('C'): {node.sql()}"
            )

    return violations


# --- Rule ------------------------------------------------------------------

@register
class StandardConceptOrWithClassificationRule(Rule):
    """Detect OR/IN patterns mixing standard and classification concepts."""

    rule_id = "anti_patterns.standard_concept_or_with_classification"
    name = "Standard Concept OR with Classification"

    description = (
        "Flags queries that mix standard ('S') and classification ('C') concepts. "
        "Classification concepts should not be used in clinical queries."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use standard_concept = 'S' for clinical queries. "
        "Use 'C' only for vocabulary hierarchy analysis."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if STANDARD_CONCEPT not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree or not has_table_reference(tree, CONCEPT_TABLE):
                continue

            aliases = extract_aliases(tree)
            concept_aliases = _get_concept_aliases(aliases)

            if not concept_aliases:
                continue

            messages = (
                _detect_or_pattern(tree, aliases, concept_aliases) +
                _detect_in_pattern(tree, aliases, concept_aliases)
            )

            for msg in messages:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "standard_vs_classification_mix",
                            "values": ["S", "C"],
                        },
                    )
                )

        return violations


__all__ = ["StandardConceptOrWithClassificationRule"]
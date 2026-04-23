"""Maps To Target Standard Validation Rule.

OMOP semantic rule VOCAB_003:
When using concept_relationship with relationship_id = 'Maps to', the target
(concept_id_2) must be validated as a standard concept.

The Problem:
    'Maps to' relationships map source concepts to standard concepts, but:
    1. Mapping chains can exist (A → B → C) where intermediate concepts are not final
    2. Some concept_id_2 targets may have standard_concept = NULL (deprecated)
    3. Data quality issues may result in non-standard targets

    Without validating that concept_id_2 is actually standard (standard_concept = 'S'),
    queries may return:
    - Deprecated concepts
    - Intermediate non-standard concepts
    - Invalid mappings

Violation pattern:
    SELECT cr.concept_id_2
    FROM concept_relationship cr
    WHERE cr.concept_id_1 = 44836914
      AND cr.relationship_id = 'Maps to'
      AND cr.invalid_reason IS NULL
    -- Missing: verification that concept_id_2 is standard

Correct pattern:
    SELECT cr.concept_id_2
    FROM concept_relationship cr
    JOIN concept c2 ON cr.concept_id_2 = c2.concept_id
    WHERE cr.relationship_id = 'Maps to'
      AND cr.invalid_reason IS NULL
      AND c2.standard_concept = 'S'
"""

from typing import Dict, List, Optional

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
CONCEPT = "concept"
MAPS_TO_RELATIONSHIP = "maps to"
RELATIONSHIP_ID = "relationship_id"
CONCEPT_ID_2 = "concept_id_2"
STANDARD_CONCEPT = "standard_concept"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _resolve_table(table_or_alias: Optional[str], aliases: Dict[str, str]) -> Optional[str]:
    """Resolve alias to actual table name."""
    if not table_or_alias:
        return None

    norm_input = _norm(table_or_alias)

    # Check if it's an alias
    for alias, table in aliases.items():
        if _norm(alias) == norm_input:
            return _norm(table)

    # Check if it's already a table name
    for alias, table in aliases.items():
        if _norm(table) == norm_input:
            return _norm(table)

    # Return normalized input as fallback
    return norm_input


def _uses_maps_to_relationship(tree: exp.Expression) -> bool:
    """Check if query uses relationship_id = 'Maps to'."""
    for node in tree.walk():
        if isinstance(node, exp.EQ):
            if isinstance(node.this, exp.Column) and isinstance(node.expression, exp.Literal):
                col_name = _norm(node.this.name)
                value = _norm(node.expression.this)
                if col_name == RELATIONSHIP_ID and value == MAPS_TO_RELATIONSHIP:
                    return True
            elif isinstance(node.expression, exp.Column) and isinstance(node.this, exp.Literal):
                col_name = _norm(node.expression.name)
                value = _norm(node.this.this)
                if col_name == RELATIONSHIP_ID and value == MAPS_TO_RELATIONSHIP:
                    return True
        elif isinstance(node, exp.In):
            if isinstance(node.this, exp.Column):
                col_name = _norm(node.this.name)
                if col_name == RELATIONSHIP_ID:
                    # Check if 'Maps to' is in the IN list
                    for expr in node.expressions:
                        if isinstance(expr, exp.Literal):
                            if _norm(expr.this) == MAPS_TO_RELATIONSHIP:
                                return True

    return False


def _references_concept_id_2(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if query references concept_id_2 from concept_relationship."""
    for col in tree.find_all(exp.Column):
        table, column = resolve_table_col(col, aliases)
        table_resolved = _resolve_table(table, aliases)

        if table_resolved == CONCEPT_RELATIONSHIP and _norm(column) == CONCEPT_ID_2:
            return True

    return False


def _validates_concept_id_2_as_standard(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> bool:
    """
    Check if query validates concept_id_2 as standard via:
    1. Join to concept table on concept_id_2
    2. Filter with standard_concept = 'S'
    """
    # Step 1: Find if there's a join between concept_relationship.concept_id_2 and concept.concept_id
    has_join = False
    concept_alias = None

    # Check all equality conditions (in JOINs and WHERE)
    for eq in tree.find_all(exp.EQ):
        if not (isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)):
            continue

        lt, lc = resolve_table_col(eq.this, aliases)
        rt, rc = resolve_table_col(eq.expression, aliases)

        lt_resolved = _resolve_table(lt, aliases)
        rt_resolved = _resolve_table(rt, aliases)

        # concept_relationship.concept_id_2 = concept.concept_id
        if (lt_resolved == CONCEPT_RELATIONSHIP and _norm(lc) == CONCEPT_ID_2 and
            rt_resolved == CONCEPT and _norm(rc) == "concept_id"):
            has_join = True
            concept_alias = rt
        elif (rt_resolved == CONCEPT_RELATIONSHIP and _norm(rc) == CONCEPT_ID_2 and
              lt_resolved == CONCEPT and _norm(lc) == "concept_id"):
            has_join = True
            concept_alias = lt

    if not has_join:
        return False

    # Step 2: Check if there's a standard_concept = 'S' filter on the concept table
    for eq in tree.find_all(exp.EQ):
        # Check for column = literal pattern
        if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Literal):
            table, column = resolve_table_col(eq.this, aliases)
            table_resolved = _resolve_table(table, aliases)
            value = _norm(eq.expression.this)

            if (table_resolved == CONCEPT and _norm(column) == STANDARD_CONCEPT and value == "s"):
                return True
        # Check for literal = column pattern
        elif isinstance(eq.this, exp.Literal) and isinstance(eq.expression, exp.Column):
            table, column = resolve_table_col(eq.expression, aliases)
            table_resolved = _resolve_table(table, aliases)
            value = _norm(eq.this.this)

            if (table_resolved == CONCEPT and _norm(column) == STANDARD_CONCEPT and value == "s"):
                return True

    return False


# --- Rule ------------------------------------------------------------------

@register
class MapsToTargetStandardValidationRule(Rule):
    """Ensures 'Maps to' targets are validated as standard concepts."""

    rule_id = "concept_standardization.maps_to_target_standard_validation"
    name = "Maps To Target Standard Validation"

    description = (
        "When using concept_relationship with relationship_id = 'Maps to', "
        "the target (concept_id_2) should be validated as a standard concept "
        "via a join to concept table with standard_concept = 'S'. Without this, "
        "queries may return deprecated or intermediate non-standard concepts. "
        "This is a best practice recommendation - the query will execute correctly but may include non-standard targets."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Join concept_relationship.concept_id_2 to concept.concept_id "
        "and add filter: concept.standard_concept = 'S'"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations = []

        # Quick check: only relevant if concept_relationship is used
        if "concept_relationship" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            # Must use concept_relationship table
            if not has_table_reference(tree, CONCEPT_RELATIONSHIP):
                continue

            # Must use 'Maps to' relationship
            if not _uses_maps_to_relationship(tree):
                continue

            aliases = extract_aliases(tree)

            # Must reference concept_id_2
            if not _references_concept_id_2(tree, aliases):
                continue

            # Check if concept_id_2 is validated as standard
            if not _validates_concept_id_2_as_standard(tree, aliases):
                violations.append(
                    self.create_violation(
                        message=(
                            "Query uses 'Maps to' relationship and references concept_id_2, "
                            "but does not validate that the target is a standard concept. "
                            "Best practice: Join to concept table and add: concept.standard_concept = 'S' "
                            "to ensure only standard concepts are returned."
                        ),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details={
                            "relationship": "Maps to",
                            "missing_validation": "standard_concept = 'S' on concept_id_2",
                            "note": "best_practice_recommendation"
                        },
                    )
                )

        return violations


__all__ = ["MapsToTargetStandardValidationRule"]

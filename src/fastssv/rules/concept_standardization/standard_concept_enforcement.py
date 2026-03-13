"""Standard Concept Enforcement Rule.

OMOP semantic rule:
If query uses a STANDARD OMOP concept field, it must either:
  - enforce concept.standard_concept = 'S'
  OR
  - use mapping via concept_relationship relationship_id = 'Maps to'
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    check_condition,
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register
from fastssv.schemas import SOURCE_CONCEPT_FIELDS, STANDARD_CONCEPT_FIELDS

# relationship_id values commonly used for standard mapping in OMOP
MAPS_TO_RELATIONSHIP = "Maps to"


def _extract_concept_references(
    tree: exp.Expression, aliases: Dict[str, str]
) -> List[Tuple[str, str]]:
    """Extract all resolved (table, column) references for concept fields."""
    refs: List[Tuple[str, str]] = []

    for col in tree.find_all(exp.Column):
        table, col_name = resolve_table_col(col, aliases)

        if not table:
            continue

        if col_name == "concept_id" or col_name.endswith("_concept_id"):
            refs.append((table, col_name))

    return refs


def _enforces_standard_concept(tree: exp.Expression) -> bool:
    """Detect if query enforces standard concepts via standard_concept = 'S'."""
    if not uses_table(tree, "concept"):
        return False

    return check_condition(tree, "standard_concept", {"s"}, require_where_clause=True)


def _uses_maps_to_relationship(tree: exp.Expression) -> bool:
    """Detect if query uses concept_relationship relationship_id = 'Maps to'."""
    if not uses_table(tree, "concept_relationship"):
        return False

    return check_condition(
        tree,
        "relationship_id",
        {normalize_name(MAPS_TO_RELATIONSHIP)},
        require_where_clause=True
    )


@register
class StandardConceptEnforcementRule(Rule):
    """Ensures queries using STANDARD concept fields enforce standard concepts."""

    rule_id = "semantic.standard_concept_enforcement"
    name = "Standard Concept Enforcement"
    description = (
        "Ensures queries using STANDARD concept fields enforce standard concepts "
        "via concept.standard_concept = 'S' or concept_relationship 'Maps to'"
    )
    severity = Severity.ERROR
    suggested_fix = "JOIN concept table and add: WHERE concept.standard_concept = 'S'"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            # Parse errors handled elsewhere
            return []

        # Known standard/source fields from schema lists
        standard_fields: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in STANDARD_CONCEPT_FIELDS
        }
        source_fields: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in SOURCE_CONCEPT_FIELDS
        }

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)
            refs = _extract_concept_references(tree, aliases)

            used_standard = {(t, c) for (t, c) in refs if (t, c) in standard_fields}
            used_source = {(t, c) for (t, c) in refs if (t, c) in source_fields}

            # If no standard fields used, rule doesn't apply
            if not used_standard:
                continue

            has_standard_enforcement = _enforces_standard_concept(tree)
            has_maps_to = _uses_maps_to_relationship(tree)

            # Check the main rule: must have either standard enforcement OR maps_to
            if not has_standard_enforcement and not has_maps_to:
                used_standard_strs = sorted({f"{t}.{c}" for (t, c) in used_standard})
                used_source_strs = sorted({f"{t}.{c}" for (t, c) in used_source})

                message = (
                    f"Query uses STANDARD concept fields but does not ensure "
                    f"standard concepts. Must either: (A) filter with concept.standard_concept = 'S', or "
                    f"(B) use concept_relationship.relationship_id = 'Maps to'. "
                    f"STANDARD fields referenced: {', '.join(used_standard_strs)}"
                )
                if used_source_strs:
                    message += f", SOURCE fields referenced: {', '.join(used_source_strs)}"

                violations.append(self.create_violation(
                    message=message,
                    details={
                        "standard_fields": used_standard_strs,
                        "source_fields": used_source_strs,
                    }
                ))

        return violations


__all__ = ["StandardConceptEnforcementRule"]

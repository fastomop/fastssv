"""Maps To Direction Rule.

OMOP semantic rule:
Verify that 'Maps to' relationship is used in the correct direction:
- concept_id_1 should be the source concept
- concept_id_2 should be the standard concept

Example:
-- WRONG direction: joining source concept_id to standard field                                                                                                                               
SELECT co.*                                                                                                                                                                                   
FROM condition_occurrence co                                                                                                                                                                  
JOIN concept_relationship cr                                                                                                                                                                  
ON co.condition_concept_id = cr.concept_id_1  -- Wrong! This is source                                                                                                                      
WHERE cr.relationship_id = 'Maps to'                                                                                                                                                          
                                                                                                                                                                                            
-- CORRECT direction: joining standard concept_id to concept_id_2                                                                                                                             
SELECT co.*                                                                                                                                                                                   
FROM condition_occurrence co                                                                                                                                                                  
JOIN concept_relationship cr                                                                                                                                                                  
ON co.condition_concept_id = cr.concept_id_2  -- Correct! This is standard                                                                                                                  
WHERE cr.relationship_id = 'Maps to'
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    check_condition,
    extract_aliases,
    extract_join_conditions,
    normalize_name,
    parse_sql,
    uses_table,
)
from fastssv.core.registry import register
from fastssv.schemas import STANDARD_CONCEPT_FIELDS

MAPS_TO_RELATIONSHIP = "Maps to"


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


def _verify_maps_to_direction(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    """Verify that 'Maps to' relationship is used in the correct direction."""
    warnings: List[str] = []

    if not uses_table(tree, "concept_relationship"):
        return []

    if not _uses_maps_to_relationship(tree):
        return []

    join_conditions = extract_join_conditions(tree, aliases)
    standard_fields = {normalize_name(c) for _, c in STANDARD_CONCEPT_FIELDS}

    for lt, lc, rt, rc in join_conditions:
        # Check if concept_relationship.concept_id_1 is joined to a standard field
        # This would be incorrect - concept_id_1 should be the source
        if lt == "concept_relationship" and lc == "concept_id_1":
            if rc in standard_fields:
                warnings.append(
                    f"'Maps to' relationship may be used in reverse direction. "
                    f"concept_relationship.concept_id_1 (source) is joined to {rt}.{rc} "
                    f"which is a standard concept field. Consider using concept_id_2 instead."
                )
        elif rt == "concept_relationship" and rc == "concept_id_1":
            if lc in standard_fields:
                warnings.append(
                    f"'Maps to' relationship may be used in reverse direction. "
                    f"concept_relationship.concept_id_1 (source) is joined to {lt}.{lc} "
                    f"which is a standard concept field. Consider using concept_id_2 instead."
                )

    return warnings


@register
class MapsToDirectionRule(Rule):
    """Checks 'Maps to' relationship direction."""

    rule_id = "semantic.maps_to_direction"
    name = "Maps To Direction"
    description = (
        "Verifies that 'Maps to' relationship is used in the correct direction: "
        "concept_id_1 for source, concept_id_2 for standard concept"
    )
    severity = Severity.WARNING
    suggested_fix = "Use concept_id_1 for source, concept_id_2 for standard concept"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)
            warnings = _verify_maps_to_direction(tree, aliases)

            for warning in warnings:
                violations.append(self.create_violation(
                    message=warning,
                ))

        return violations


__all__ = ["MapsToDirectionRule"]

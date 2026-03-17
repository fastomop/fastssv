"""Type Concept ID Misuse Rule.

OMOP semantic rule (OMOP_014):
The *_type_concept_id columns (e.g., condition_type_concept_id, drug_type_concept_id)
represent the provenance of the record (e.g., EHR, claim, patient-reported), not clinical
categories. Do not use them to filter for clinical subtypes.

Example violation:
SELECT * FROM condition_occurrence
WHERE condition_type_concept_id = 201826  -- This is a condition concept, not a type concept!

Example correct:
SELECT * FROM condition_occurrence
WHERE condition_type_concept_id = 32817  -- EHR
AND condition_concept_id = 201826        -- Diabetes

Better practice:
Don't filter on type_concept_id for clinical purposes. Use it only for understanding
data provenance/lineage.
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register

# Type concept ID columns that should NOT be used for clinical filtering
TYPE_CONCEPT_ID_COLUMNS = {
    "condition_type_concept_id",
    "drug_type_concept_id",
    "procedure_type_concept_id",
    "measurement_type_concept_id",
    "observation_type_concept_id",
    "visit_type_concept_id",
    "visit_detail_type_concept_id",
    "device_type_concept_id",
    "specimen_type_concept_id",
    "note_type_concept_id",
    "death_type_concept_id",
    "episode_type_concept_id",
}

# Map type_concept_id to the primary concept_id for suggestions
TYPE_TO_PRIMARY_FIELD = {
    "condition_type_concept_id": "condition_concept_id",
    "drug_type_concept_id": "drug_concept_id",
    "procedure_type_concept_id": "procedure_concept_id",
    "measurement_type_concept_id": "measurement_concept_id",
    "observation_type_concept_id": "observation_concept_id",
    "visit_type_concept_id": "visit_concept_id",
    "visit_detail_type_concept_id": "visit_detail_concept_id",
    "device_type_concept_id": "device_concept_id",
    "specimen_type_concept_id": "specimen_concept_id",
    "note_type_concept_id": "note_class_concept_id",
    "death_type_concept_id": "cause_concept_id",
}


def _find_type_concept_id_filters(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[Tuple[str, str, str]]:
    """Find WHERE/HAVING/ON clauses that filter on type_concept_id columns.

    Returns list of (table, column, context) tuples.
    """
    violations: List[Tuple[str, str, str]] = []

    for node in tree.walk():
        if isinstance(node, (exp.EQ, exp.In, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NEQ)):
            left = node.this if hasattr(node, 'this') else None

            if isinstance(left, exp.Column):
                table, col = resolve_table_col(left, aliases)
                normalized_col = normalize_name(col)

                if normalized_col in TYPE_CONCEPT_ID_COLUMNS:
                    # Determine context
                    context = "WHERE clause"
                    parent = node.parent
                    while parent:
                        if isinstance(parent, exp.Join):
                            context = "JOIN ON clause"
                            break
                        elif isinstance(parent, exp.Having):
                            context = "HAVING clause"
                            break
                        parent = parent.parent if hasattr(parent, 'parent') else None

                    violations.append((table, normalized_col, context))

    return violations


@register
class TypeConceptIdMisuseRule(Rule):
    """Detects misuse of *_type_concept_id columns for clinical filtering."""

    rule_id = "semantic.type_concept_id_misuse"
    name = "Type Concept ID Not For Clinical Filtering"
    description = (
        "The *_type_concept_id columns represent record provenance (EHR, claim, etc.), "
        "not clinical categories. Use the primary *_concept_id column for clinical filtering."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Use the primary concept_id column (e.g., condition_concept_id) for clinical filtering. "
        "type_concept_id should only be used to understand data source/provenance."
    )

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
            filters = _find_type_concept_id_filters(tree, aliases)

            for table, col, context in filters:
                # Get the suggested primary field
                primary_field = TYPE_TO_PRIMARY_FIELD.get(col, "the primary concept_id column")

                message = (
                    f"Filtering on '{col}' in {context}. "
                    f"This column represents data provenance (EHR, claims, etc.), not clinical categories. "
                    f"For clinical filtering, use '{primary_field}' instead. "
                    f"Only use type_concept_id to understand where the data came from."
                )

                violations.append(self.create_violation(message=message))

        return violations


__all__ = ["TypeConceptIdMisuseRule"]

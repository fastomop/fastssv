"""Type Concept ID Misuse Rule.

OMOP semantic rule (OMOP_014):
The *_type_concept_id columns (e.g., condition_type_concept_id, drug_type_concept_id)
represent the provenance of the record (e.g., EHR, claim, patient-reported), not clinical
categories. Do not use them to filter for clinical subtypes in cohort definitions.

Legitimate uses (descriptive analytics):
- GROUP BY type_concept_id (understanding data source distribution) ✓
- SELECT type_concept_id (displaying provenance) ✓
- JOIN to concept for labeling (showing type names) ✓ (warning)

Misuse (cohort definition):
- WHERE type_concept_id = X to filter patients ✗ (error)
- HAVING type_concept_id = X to filter aggregates ✗ (error)

Example violation:
SELECT * FROM condition_occurrence
WHERE condition_type_concept_id = 201826  -- ERROR: Using for clinical filtering

Example correct:
-- Descriptive analytics (OK)
SELECT condition_type_concept_id, COUNT(*)
FROM condition_occurrence
GROUP BY condition_type_concept_id

-- Cohort + provenance filter (OK with warning)
SELECT * FROM condition_occurrence
WHERE condition_concept_id = 201826        -- Clinical filter
  AND condition_type_concept_id = 32817    -- Provenance filter (EHR only)
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


def _is_join_to_concept_for_labeling(node: exp.Expression, col: exp.Column, aliases: Dict[str, str]) -> bool:
    """Check if this is a join to concept table/subquery for labeling purposes.

    Returns True if joining type_concept_id to concept_id column.
    This is almost always for labeling (getting human-readable type names), not filtering.
    """
    # Check if the other side of the equality is concept_id
    right = node.expression if hasattr(node, 'expression') else None
    if isinstance(right, exp.Column):
        _, right_col = resolve_table_col(right, aliases)
        if normalize_name(right_col) == "concept_id":
            # Joining type_concept_id to concept_id is for labeling
            return True

    return False


def _find_type_concept_id_misuse(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[Tuple[str, str, str, str]]:
    """Find misuse of type_concept_id columns.

    Returns list of (table, column, context, severity) tuples.
    - severity: 'error' for cohort definition, 'warning' for labeling joins
    """
    violations: List[Tuple[str, str, str, str]] = []

    for node in tree.walk():
        if isinstance(node, (exp.EQ, exp.In, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NEQ)):
            left = node.this if hasattr(node, 'this') else None

            if isinstance(left, exp.Column):
                table, col = resolve_table_col(left, aliases)
                normalized_col = normalize_name(col)

                if normalized_col in TYPE_CONCEPT_ID_COLUMNS:
                    # Determine context
                    context = "WHERE clause"
                    severity = "error"  # Default: cohort definition misuse
                    parent = node.parent

                    while parent:
                        if isinstance(parent, exp.Join):
                            context = "JOIN ON clause"
                            # Check if this is a join to concept for labeling
                            if _is_join_to_concept_for_labeling(node, left, aliases):
                                # Joining to concept for labeling is acceptable (just a warning)
                                severity = "skip"  # Don't flag labeling joins
                            break
                        elif isinstance(parent, exp.Having):
                            context = "HAVING clause"
                            severity = "error"
                            break
                        parent = parent.parent if hasattr(parent, 'parent') else None

                    if severity != "skip":
                        violations.append((table, normalized_col, context, severity))

    return violations


@register
class TypeConceptIdMisuseRule(Rule):
    """Detects misuse of *_type_concept_id columns for clinical filtering.

    Context-aware: Only flags cohort definition misuse (WHERE/HAVING), not descriptive analytics.
    """

    rule_id = "anti_patterns.type_concept_id_misuse"
    name = "Type Concept ID Not For Clinical Filtering"
    description = (
        "The *_type_concept_id columns represent record provenance (EHR, claim, etc.), "
        "not clinical categories. Do not use them for cohort definition or clinical filtering in WHERE/HAVING clauses. "
        "Using them in GROUP BY, SELECT, or JOIN for labeling is acceptable."
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
            misuses = _find_type_concept_id_misuse(tree, aliases)

            for table, col, context, severity in misuses:
                # Get the suggested primary field
                primary_field = TYPE_TO_PRIMARY_FIELD.get(col, "the primary concept_id column")

                message = (
                    f"Filtering on '{col}' in {context}. "
                    f"This column represents data provenance (EHR, claims, etc.), not clinical categories. "
                    f"For clinical filtering, use '{primary_field}' instead. "
                    f"Only use type_concept_id to understand where the data came from."
                )

                violations.append(self.create_violation(
                    message=message,
                    severity=Severity.ERROR if severity == "error" else Severity.WARNING
                ))

        return violations


__all__ = ["TypeConceptIdMisuseRule"]

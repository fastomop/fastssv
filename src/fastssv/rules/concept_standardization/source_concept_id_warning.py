"""Source Concept ID Usage Warning Rule.

OMOP semantic rule OMOP_022:
The *_source_concept_id columns store the original source vocabulary concept.
For standard analytical queries and cohort identification, use the primary
*_concept_id (standard concept) rather than *_source_concept_id.

Valid uses of source_concept_id:
  - Data quality checks
  - ETL validation / mapping verification
  - Source code exploration
  - Provenance tracking

Invalid use (cohort identification):
  - SELECT person_id FROM condition_occurrence WHERE condition_source_concept_id = 123

Correct approach:
  - SELECT person_id FROM condition_occurrence WHERE condition_concept_id = 456
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
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


SOURCE_CONCEPT_ID_COLUMNS: Set[str] = {
    "condition_source_concept_id",
    "drug_source_concept_id",
    "procedure_source_concept_id",
    "measurement_source_concept_id",
    "observation_source_concept_id",
    "device_source_concept_id",
    "visit_source_concept_id",
    "specimen_source_concept_id",
    "visit_detail_source_concept_id",
    "gender_source_concept_id",
    "race_source_concept_id",
    "ethnicity_source_concept_id",
    "unit_source_concept_id",
}

SOURCE_TO_STANDARD: Dict[str, str] = {
    "condition_source_concept_id": "condition_concept_id",
    "drug_source_concept_id": "drug_concept_id",
    "procedure_source_concept_id": "procedure_concept_id",
    "measurement_source_concept_id": "measurement_concept_id",
    "observation_source_concept_id": "observation_concept_id",
    "device_source_concept_id": "device_concept_id",
    "visit_source_concept_id": "visit_concept_id",
    "specimen_source_concept_id": "specimen_concept_id",
    "visit_detail_source_concept_id": "visit_detail_concept_id",
    "gender_source_concept_id": "gender_concept_id",
    "race_source_concept_id": "race_concept_id",
    "ethnicity_source_concept_id": "ethnicity_concept_id",
    "unit_source_concept_id": "unit_concept_id",
}


def _is_in_where_or_having(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, (exp.Where, exp.Having)):
            return True
        if isinstance(parent, exp.Join):
            return False
        parent = parent.parent
    return False


def _find_source_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[tuple]:
    """Return (message, predicate_sql, source_col_name, standard_col_name) tuples."""
    issues: List[tuple] = []
    seen: Set[Tuple[str, str]] = set()

    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.NEQ, exp.In)):
            continue

        if not _is_in_where_or_having(node):
            continue

        left = node.this
        right = node.expression

        for col_node, _ in [(left, right), (right, left)]:
            if not isinstance(col_node, exp.Column):
                continue

            _, col = resolve_table_col(col_node, aliases)
            col_norm = normalize_name(col)

            if col_norm not in SOURCE_CONCEPT_ID_COLUMNS:
                continue

            key = (col_norm, node.sql())
            if key in seen:
                continue
            seen.add(key)

            standard_col = SOURCE_TO_STANDARD.get(
                col_norm,
                col_norm.replace("_source_", "_"),
            )

            message = (
                f"Filtering on '{col_norm}' for cohort/analytical logic is discouraged. "
                f"Use '{standard_col}' (standard concept) instead. "
                f"Source concept IDs are intended for ETL validation, mapping QA, or provenance analysis."
            )
            issues.append((message, node.sql(), col_norm, standard_col))

    return issues


def _is_source_exploration_query(tree: exp.Expression) -> bool:
    select = tree.find(exp.Select)
    if not select:
        return False

    for expr in select.expressions:
        for col in expr.find_all(exp.Column):
            name = normalize_name(col.name)

            if "source_value" in name or name.endswith("_source_concept_id"):
                return True

    return False


@register
class SourceConceptIdWarningRule(Rule):
    """Production-grade validation for source_concept_id misuse."""

    rule_id = "concept_standardization.source_concept_id_warning"
    name = "Source Concept ID Not For Analytical Filtering"
    description = (
        "Avoid using *_source_concept_id for cohort definition or analytical filtering. "
        "Use standard *_concept_id instead."
    )
    severity = Severity.WARNING
    suggested_fix = "REPLACE: `WHERE <table>.<x>_source_concept_id = <id>` WITH `WHERE <table>.<x>_concept_id = <id>`. Use *_source_concept_id only for ETL provenance / source exploration, not analytics."
    long_description = (
        "*_source_concept_id stores the original, unmapped code from the "
        "source system (ICD10CM, NDC, CPT, etc.) and is intended for audit "
        "and ETL provenance, not for cohort analytics. Filtering cohorts "
        "on source concepts produces results that do not federate across "
        "sites (each warehouse ETL maps slightly differently) and misses "
        "patients whose underlying data was mapped from a different "
        "source vocabulary. Use the paired *_concept_id (standard) column "
        "for any cohort or analytical query."
    )
    example_bad = "SELECT person_id\nFROM condition_occurrence\nWHERE condition_source_concept_id = 44831230;"
    example_good = "SELECT person_id\nFROM condition_occurrence\nWHERE condition_concept_id = 201820;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            # --- Context detection ---
            is_exploration = _is_source_exploration_query(tree)

            if is_exploration:
                continue

            issues = _find_source_filters(tree, aliases)

            for issue, predicate_sql, source_col, standard_col in issues:
                # Mechanical REPLACE: swap the source column name for the
                # standard column name inside the offending predicate.
                # Only apply when the rendered predicate text is uniquely
                # locatable in the original SQL.
                patch = None
                span = locate(sql, predicate_sql)
                if span is not None and source_col in predicate_sql.lower():
                    # Replace case-insensitively while preserving the rest
                    # of the predicate text.
                    import re as _re

                    new_text = _re.sub(
                        _re.escape(source_col),
                        standard_col,
                        predicate_sql,
                        flags=_re.IGNORECASE,
                    )
                    if new_text != predicate_sql:
                        patch = patch_replace(span, new_text)

                violations.append(
                    self.create_violation(
                        message=issue,
                        suggested_fix_patch=patch,
                    )
                )

        return violations


__all__ = ["SourceConceptIdWarningRule"]

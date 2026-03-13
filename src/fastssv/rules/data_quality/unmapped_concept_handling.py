"""Unmapped Concept Handling Rule.

OMOP semantic rule:
When filtering clinical tables by specific *_concept_id values,
warn if concept_id = 0 (unmapped records) is not explicitly handled.

In OMOP CDM, concept_id = 0 means "no matching concept was found" during ETL.
Queries that filter on specific concept_ids may silently exclude these
unmapped records, which could lead to incomplete results.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    is_numeric_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register

# Clinical tables where concept_id = 0 is semantically important
CLINICAL_CONCEPT_ID_COLUMNS = {
    "condition_occurrence": {"condition_concept_id", "condition_source_concept_id"},
    "drug_exposure": {"drug_concept_id", "drug_source_concept_id"},
    "procedure_occurrence": {"procedure_concept_id", "procedure_source_concept_id"},
    "measurement": {"measurement_concept_id", "measurement_source_concept_id"},
    "observation": {"observation_concept_id", "observation_source_concept_id"},
    "device_exposure": {"device_concept_id", "device_source_concept_id"},
    "visit_occurrence": {"visit_concept_id", "visit_source_concept_id"},
    "visit_detail": {"visit_detail_concept_id", "visit_detail_source_concept_id"},
    "death": {"cause_concept_id", "cause_source_concept_id"},
    "specimen": {"specimen_concept_id", "specimen_source_concept_id"},
    "episode": {"episode_concept_id", "episode_source_concept_id"},
    "person": {"gender_concept_id", "race_concept_id", "ethnicity_concept_id"},
}


def _infer_table_for_column(
    col_name: str,
    aliases: Dict[str, str]
) -> Optional[str]:
    """For unqualified columns, try to infer the table from CLINICAL_CONCEPT_ID_COLUMNS."""
    matching_tables = []
    for table, columns in CLINICAL_CONCEPT_ID_COLUMNS.items():
        if col_name in columns:
            if table in aliases.values():
                matching_tables.append(table)

    if len(matching_tables) == 1:
        return matching_tables[0]
    return None


def _extract_concept_id_filters(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[Tuple[str, str, exp.Expression]]:
    """Find all filters on *_concept_id columns with specific numeric values."""
    filters: List[Tuple[str, str, exp.Expression]] = []

    # Check equality comparisons: concept_id = 12345
    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        left, right = eq.left, eq.right

        # Normalize: Column = number
        if isinstance(right, exp.Column) and is_numeric_literal(left):
            left, right = right, left

        if not isinstance(left, exp.Column):
            continue

        col_name = normalize_name(left.name)
        if not (col_name.endswith("_concept_id") or col_name == "concept_id"):
            continue

        # Check if it's a specific numeric value (not 0)
        if is_numeric_literal(right) and not is_numeric_literal(right, 0):
            table, _ = resolve_table_col(left, aliases)
            if not table:
                table = _infer_table_for_column(col_name, aliases)
            if table:
                filters.append((table, col_name, eq))

    # Check IN clauses: concept_id IN (12345, 67890)
    for in_expr in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_expr):
            continue

        if not isinstance(in_expr.this, exp.Column):
            continue

        col_name = normalize_name(in_expr.this.name)
        if not (col_name.endswith("_concept_id") or col_name == "concept_id"):
            continue

        # Check if IN clause contains specific numeric values
        has_specific_values = False
        for val in in_expr.expressions or []:
            if is_numeric_literal(val) and not is_numeric_literal(val, 0):
                has_specific_values = True
                break

        if has_specific_values:
            table, _ = resolve_table_col(in_expr.this, aliases)
            if not table:
                table = _infer_table_for_column(col_name, aliases)
            if table:
                filters.append((table, col_name, in_expr))

    return filters


def _handles_zero_concept_id(
    tree: exp.Expression,
    aliases: Dict[str, str],
    table: str,
    column: str
) -> bool:
    """Check if the query explicitly handles concept_id = 0 for the given column.

    Patterns that indicate handling:
    - column = 0
    - column != 0 / column <> 0
    - column > 0
    - column >= 1
    - COALESCE(column, 0)
    - CASE WHEN column = 0 THEN ...
    """
    # Check for equality with 0: column = 0
    for eq in tree.find_all(exp.EQ):
        left, right = eq.left, eq.right

        if isinstance(right, exp.Column) and is_numeric_literal(left, 0):
            left, right = right, left

        if isinstance(left, exp.Column) and is_numeric_literal(right, 0):
            resolved_table, resolved_col = resolve_table_col(left, aliases)
            if resolved_col == column:
                if not resolved_table or resolved_table == table:
                    return True

    # Check for inequality with 0: column != 0 or column <> 0
    for neq in tree.find_all(exp.NEQ):
        left, right = neq.left, neq.right

        if isinstance(right, exp.Column) and is_numeric_literal(left, 0):
            left, right = right, left

        if isinstance(left, exp.Column) and is_numeric_literal(right, 0):
            resolved_table, resolved_col = resolve_table_col(left, aliases)
            if resolved_col == column:
                if not resolved_table or resolved_table == table:
                    return True

    # Check for > 0: column > 0
    for gt in tree.find_all(exp.GT):
        left, right = gt.left, gt.right

        if isinstance(left, exp.Column) and is_numeric_literal(right, 0):
            resolved_table, resolved_col = resolve_table_col(left, aliases)
            if resolved_col == column:
                if not resolved_table or resolved_table == table:
                    return True

    # Check for >= 1: column >= 1 (equivalent to > 0 for integers)
    for gte in tree.find_all(exp.GTE):
        left, right = gte.left, gte.right

        if isinstance(left, exp.Column) and is_numeric_literal(right, 1):
            resolved_table, resolved_col = resolve_table_col(left, aliases)
            if resolved_col == column:
                if not resolved_table or resolved_table == table:
                    return True

    # Check for COALESCE usage
    for coalesce in tree.find_all(exp.Coalesce):
        for arg in coalesce.expressions or [coalesce.this]:
            if isinstance(arg, exp.Column):
                resolved_table, resolved_col = resolve_table_col(arg, aliases)
                if resolved_col == column:
                    if not resolved_table or resolved_table == table:
                        return True

    # Check for CASE WHEN column = 0
    for case in tree.find_all(exp.Case):
        for when in case.args.get("ifs", []):
            if isinstance(when, exp.If):
                cond = when.this
                if isinstance(cond, exp.EQ):
                    left, right = cond.left, cond.right
                    if isinstance(right, exp.Column) and is_numeric_literal(left, 0):
                        left, right = right, left
                    if isinstance(left, exp.Column) and is_numeric_literal(right, 0):
                        resolved_table, resolved_col = resolve_table_col(left, aliases)
                        if resolved_col == column:
                            if not resolved_table or resolved_table == table:
                                return True

    return False


@register
class UnmappedConceptHandlingRule(Rule):
    """Warns when filtering by concept_id without handling unmapped records."""

    rule_id = "semantic.unmapped_concept_handling"
    name = "Unmapped Concept Handling"
    description = (
        "Warns when filtering clinical tables by specific *_concept_id values "
        "without explicitly handling concept_id = 0 (unmapped records)"
    )
    severity = Severity.WARNING
    suggested_fix = "Add: column > 0 to explicitly exclude unmapped, or handle them separately"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []  # Don't add warnings if we can't parse

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)
            concept_filters = _extract_concept_id_filters(tree, aliases)

            # Group by (table, column) to avoid duplicate warnings
            checked: Set[Tuple[str, str]] = set()

            for table, column, _ in concept_filters:
                key = (table, column)
                if key in checked:
                    continue
                checked.add(key)

                # Check if this is a clinical table concept_id column
                is_clinical = False
                for clinical_table, clinical_cols in CLINICAL_CONCEPT_ID_COLUMNS.items():
                    if table == clinical_table and column in clinical_cols:
                        is_clinical = True
                        break

                if not is_clinical:
                    continue

                # Check if concept_id = 0 is explicitly handled
                if not _handles_zero_concept_id(tree, aliases, table, column):
                    violations.append(self.create_violation(
                        message=(
                            f"Query filters {table}.{column} by specific value(s) but does not "
                            f"explicitly handle concept_id = 0 (unmapped records). Records where the source "
                            f"code could not be mapped to a standard concept will be silently excluded."
                        ),
                        suggested_fix=f"Add: {column} > 0",
                        details={
                            "table": table,
                            "column": column,
                        }
                    ))

        return violations


__all__ = ["UnmappedConceptHandlingRule"]

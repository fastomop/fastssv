"""Source Value Field Usage Warning Rule.

OMOP semantic rule:
The *_source_value columns store original source codes as free text.
These values are not standardized and vary across data sources.
For analytical queries, prefer using *_concept_id (standard concepts) instead.

Valid uses of source_value:
  - Data quality checks
  - ETL debugging
  - Source code exploration
  - Provenance tracking
  - Displaying original source codes to users

Potentially problematic use:
  - GROUP BY plan_source_value (aggregating by unstandardized codes)
  - WHERE condition_source_value = 'xyz' (filtering by source codes)

Recommended approach:
  - Use standard concept_id fields for filtering and aggregation
  - Use concept tables to map to standard concepts
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


def _extract_source_value_columns_in_group_by(
    tree: exp.Expression,
    aliases: dict
) -> List[str]:
    """Find *_source_value columns used in GROUP BY clause."""
    source_value_cols = []

    for group in tree.find_all(exp.Group):
        for expr in group.expressions:
            if isinstance(expr, exp.Column):
                _, col = resolve_table_col(expr, aliases)
                col_norm = normalize_name(col)

                if col_norm.endswith('_source_value'):
                    source_value_cols.append(col)

    return source_value_cols


@register
class SourceValueFieldUsageRule(Rule):
    """Warns about using *_source_value fields for analytical aggregation."""

    rule_id = "data_quality.source_value_field_usage"
    name = "Source Value Field Usage"
    description = (
        "Warns when *_source_value fields (unstandardized source codes) are used "
        "for GROUP BY or analytical aggregation instead of standard concept fields"
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Use standard concept fields (*_concept_id) instead of source_value fields "
        "for consistent analytical results across data sources"
    )
    long_description = (
        "*_source_value columns store the raw, unstandardised source "
        "strings from the originating system and are meant for audit or "
        "provenance. Aggregating or grouping by source_value produces "
        "results that aren't comparable across sites (different ETLs "
        "map differently) and won't federate in multi-site studies. "
        "Aggregate on the paired *_concept_id for portability."
    )
    example_bad = (
        "SELECT condition_source_value, COUNT(*) AS n\n"
        "FROM condition_occurrence\n"
        "GROUP BY condition_source_value;"
    )
    example_good = (
        "SELECT condition_concept_id, COUNT(*) AS n\n"
        "FROM condition_occurrence\n"
        "GROUP BY condition_concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)

            # Find source_value columns in GROUP BY
            source_value_in_group_by = _extract_source_value_columns_in_group_by(tree, aliases)

            if source_value_in_group_by:
                # De-duplicate
                unique_cols = sorted(set(source_value_in_group_by))

                for col in unique_cols:
                    # Suggest the corresponding standard field
                    # e.g., plan_source_value -> plan_concept_id
                    standard_field = col.replace('_source_value', '_concept_id')

                    message = (
                        f"Grouping by '{col}' (unstandardized source field) may produce "
                        f"inconsistent results across data sources. Consider using '{standard_field}' "
                        f"with concept table joins for standardized analytics."
                    )

                    violations.append(self.create_violation(
                        message=message,
                        suggested_fix=f"Use '{standard_field}' with concept table for standardized grouping",
                        details={
                            "source_value_field": col,
                            "suggested_standard_field": standard_field,
                        }
                    ))

        return violations


__all__ = ["SourceValueFieldUsageRule"]

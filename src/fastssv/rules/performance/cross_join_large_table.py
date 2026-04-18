"""Cross Join Large Table Rule.

OMOP semantic rule:
CROSS JOIN with large clinical tables can cause severe performance issues.
Each row from the left table is combined with every row from the right table,
resulting in a Cartesian product that can be extremely large.
"""

from typing import List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    normalize_name,
    parse_sql,
)
from fastssv.core.registry import register

# Large OMOP CDM tables that should not be used in CROSS JOIN
LARGE_CLINICAL_TABLES = {
    "person",
    "observation_period",
    "visit_occurrence",
    "visit_detail",
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "device_exposure",
    "measurement",
    "observation",
    "death",
    "note",
    "note_nlp",
    "specimen",
    "fact_relationship",
    "location",
    "care_site",
    "provider",
}


def _extract_cross_join_tables(tree: exp.Expression) -> Set[str]:
    """Extract table names used in CROSS JOIN."""
    cross_join_tables = set()

    for join in tree.find_all(exp.Join):
        # Check if it's a CROSS JOIN
        # In sqlglot, CROSS JOIN might be represented as:
        # - join.kind == "CROSS"
        # - join.side is None and join.on is None (implicit cross join)
        is_cross_join = False

        if hasattr(join, 'kind') and join.kind == "CROSS":
            is_cross_join = True
        elif join.side is None:
            # Check if there's no ON clause (implicit cross join)
            if not join.args.get('on'):
                is_cross_join = True

        if is_cross_join:
            # Get the table being joined
            table_expr = join.this
            if isinstance(table_expr, exp.Table):
                table_name = normalize_name(table_expr.name)
                cross_join_tables.add(table_name)

    # Also check for comma-separated FROM clause (implicit CROSS JOIN)
    for select in tree.find_all(exp.Select):
        from_clause = select.args.get("from")
        if from_clause:
            # Check for multiple tables in FROM (comma-separated)
            expressions = from_clause.expressions if hasattr(from_clause, 'expressions') else []
            if len(expressions) > 1:
                for expr in expressions:
                    if isinstance(expr, exp.Table):
                        table_name = normalize_name(expr.name)
                        cross_join_tables.add(table_name)

    return cross_join_tables


@register
class CrossJoinLargeTableRule(Rule):
    """Warns when CROSS JOIN is used with large clinical tables."""

    rule_id = "performance.cross_join_large_table"
    name = "Cross Join Large Table"
    description = (
        "Warns when CROSS JOIN is used with large clinical tables, "
        "which can cause severe performance issues"
    )
    severity = Severity.WARNING
    suggested_fix = "Avoid CROSS JOIN or pre-aggregate statistics before joining"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if tree is None:
                continue

            # Find tables used in CROSS JOIN
            cross_join_tables = _extract_cross_join_tables(tree)

            # Check if any are large clinical tables
            large_tables = cross_join_tables & LARGE_CLINICAL_TABLES

            if large_tables:
                for table in sorted(large_tables):
                    message = (
                        f"CROSS JOIN with '{table}' may cause significant performance degradation "
                        f"on large datasets."
                    )

                    violations.append(self.create_violation(
                        message=message,
                        suggested_fix="Avoid CROSS JOIN or pre-aggregate statistics before joining.",
                        details={
                            "table": table,
                        }
                    ))

        return violations


__all__ = ["CrossJoinLargeTableRule"]

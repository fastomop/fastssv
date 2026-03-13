"""Concept Name Lookup Anti-pattern Rule.

OMOP vocabulary rule:
Filtering by concept_name is an anti-pattern because:
1. Concept names are not guaranteed to be unique (multiple concepts can share a name)
2. Concept names can change across vocabulary versions, breaking queries silently
3. Concept names may have variations (spelling, abbreviations, etc.)

Best practice: Use concept_code + vocabulary_id or concept_id directly.
"""

from typing import Dict, List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


@register
class ConceptNameLookupRule(Rule):
    """Warns against filtering by concept_name instead of concept_code/concept_id."""

    rule_id = "vocabulary.concept_name_lookup"
    name = "Concept Name Lookup Anti-pattern"
    description = (
        "Warns when queries filter by concept_name instead of using concept_code + vocabulary_id "
        "or concept_id. Concept names are not guaranteed to be unique or stable across versions."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Use concept_code + vocabulary_id instead: "
        "WHERE c.concept_code = '...' AND c.vocabulary_id = '...', "
        "or use concept_id directly if known"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)

            # Check for concept_name in equality comparisons
            for eq in tree.find_all(exp.EQ):
                left = eq.left
                right = eq.right

                # Swap if needed so column is on left
                if isinstance(right, exp.Column) and is_string_literal(left):
                    left, right = right, left

                if not (isinstance(left, exp.Column) and is_string_literal(right)):
                    continue

                table, col = resolve_table_col(left, aliases)

                if table == "concept" and col == "concept_name":
                    violations.append(self.create_violation(
                        message=(
                            f"Query filters by concept_name ('{right.this}'). "
                            f"Concept names are not unique and can change across vocabulary versions. "
                            f"Use concept_code + vocabulary_id or concept_id instead."
                        ),
                        details={
                            "concept_name": right.this,
                            "table": table,
                            "column": col
                        }
                    ))

            # Check for concept_name in IN clauses
            for in_expr in tree.find_all(exp.In):
                col_expr = in_expr.this
                if not isinstance(col_expr, exp.Column):
                    continue

                table, col = resolve_table_col(col_expr, aliases)

                if table == "concept" and col == "concept_name":
                    # Get some of the values
                    values = []
                    for val in (in_expr.expressions or [])[:3]:
                        if is_string_literal(val):
                            values.append(val.this)

                    values_str = ", ".join(f"'{v}'" for v in values)
                    if len(in_expr.expressions or []) > 3:
                        values_str += ", ..."

                    violations.append(self.create_violation(
                        message=(
                            f"Query filters by concept_name IN ({values_str}). "
                            f"Concept names are not unique and can change across vocabulary versions. "
                            f"Use concept_code + vocabulary_id or concept_id instead."
                        ),
                        details={
                            "concept_names": values,
                            "table": table,
                            "column": col
                        }
                    ))

            # Check for concept_name in LIKE/ILIKE
            for like_expr in tree.find_all((exp.Like, exp.ILike)):
                left = like_expr.this
                if not isinstance(left, exp.Column):
                    continue

                table, col = resolve_table_col(left, aliases)

                if table == "concept" and col == "concept_name":
                    pattern = like_expr.expression
                    violations.append(self.create_violation(
                        message=(
                            f"Query filters by concept_name with pattern matching ({pattern.sql() if pattern else 'unknown'}). "
                            f"This is highly unreliable as concept names can vary. "
                            f"Use concept_code + vocabulary_id or concept_id instead."
                        ),
                        details={
                            "table": table,
                            "column": col,
                            "pattern": pattern.sql() if pattern else None
                        }
                    ))

        return violations


__all__ = ["ConceptNameLookupRule"]

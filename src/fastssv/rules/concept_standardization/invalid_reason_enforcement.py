"""Invalid Reason Enforcement Rule.

OMOP semantic rule:
When querying vocabulary/concept tables (concept, concept_relationship,
concept_ancestor, etc.), queries must filter by invalid_reason to ensure
only valid concepts are used, or explicitly handle invalid concepts.

This rule does NOT apply to clinical event tables (condition_occurrence,
drug_exposure, etc.) as those contain historical data that was valid at
the time of recording.
"""

from typing import List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    uses_table,
)
from fastssv.core.registry import register

# Vocabulary tables that have an invalid_reason column
# These tables should filter by invalid_reason IS NULL
VOCABULARY_TABLES_WITH_INVALID_REASON = {
    "concept",
    "concept_relationship",
}

# Derived vocabulary tables without invalid_reason column
# For these, the user should JOIN to concept and filter there
DERIVED_VOCABULARY_TABLES = {
    "concept_ancestor",
    "concept_synonym",
    "drug_strength",
    "source_to_concept_map",
}


def _has_invalid_reason_filter(tree: exp.Expression) -> bool:
    """Check if the query filters by invalid_reason.

    Looks for patterns like:
    - invalid_reason IS NULL
    - invalid_reason IS NOT NULL
    - invalid_reason = '...'
    - invalid_reason IN (...)

    Args:
        tree: The SQL AST to search

    Returns:
        True if an invalid_reason filter is found in WHERE/JOIN clauses
    """
    # Check for IS NULL / IS NOT NULL
    for is_node in tree.find_all(exp.Is):
        if not is_in_where_or_join_clause(is_node):
            continue

        this = is_node.this
        if isinstance(this, exp.Column) and normalize_name(this.name) == "invalid_reason":
            return True

    # Check for equality: invalid_reason = '...'
    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        left, right = eq.left, eq.right

        if isinstance(left, exp.Column) and normalize_name(left.name) == "invalid_reason":
            return True
        if isinstance(right, exp.Column) and normalize_name(right.name) == "invalid_reason":
            return True

    # Check for IN clause: invalid_reason IN (...)
    for in_expr in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_expr):
            continue

        if isinstance(in_expr.this, exp.Column):
            if normalize_name(in_expr.this.name) == "invalid_reason":
                return True

    # Check for NOT IN clause: invalid_reason NOT IN (...)
    for not_expr in tree.find_all(exp.Not):
        if not is_in_where_or_join_clause(not_expr):
            continue

        inner = not_expr.this
        if isinstance(inner, exp.In) and isinstance(inner.this, exp.Column):
            if normalize_name(inner.this.name) == "invalid_reason":
                return True

    return False


def _get_vocabulary_tables_used(tree: exp.Expression) -> Tuple[Set[str], Set[str]]:
    """Extract vocabulary tables used in the query.

    Args:
        tree: The SQL AST to search

    Returns:
        Tuple of (tables_with_invalid_reason, derived_tables)
    """
    tables_with_invalid_reason = set()
    derived_tables = set()

    for table_name in VOCABULARY_TABLES_WITH_INVALID_REASON:
        if uses_table(tree, table_name):
            tables_with_invalid_reason.add(table_name)

    for table_name in DERIVED_VOCABULARY_TABLES:
        if uses_table(tree, table_name):
            derived_tables.add(table_name)

    return tables_with_invalid_reason, derived_tables


def _has_concept_join_with_invalid_reason_filter(tree: exp.Expression) -> bool:
    """Check if query has both a concept table join AND an invalid_reason filter.

    This is used to check if derived tables (like concept_ancestor) properly
    filter concepts by joining to the concept table and filtering by invalid_reason.

    Args:
        tree: The SQL AST to search

    Returns:
        True if both concept table is joined AND invalid_reason filter is present
    """
    # Check if concept table is used (joined)
    if not uses_table(tree, "concept"):
        return False

    # Check if there's an invalid_reason filter
    return _has_invalid_reason_filter(tree)


@register
class InvalidReasonEnforcementRule(Rule):
    """Ensures queries on vocabulary tables filter by invalid_reason."""

    rule_id = "semantic.invalid_reason_enforcement"
    name = "Invalid Reason Enforcement"
    description = (
        "Ensures queries on vocabulary/concept tables filter by invalid_reason "
        "to ensure only valid concepts are used (invalid_reason IS NULL)"
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Add 'WHERE invalid_reason IS NULL' to ensure only valid concepts are used, "
        "or explicitly handle invalid concepts if needed"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            # Parse errors handled elsewhere
            return []

        for tree in trees:
            if tree is None:
                continue

            # Find vocabulary tables used in the query
            tables_with_invalid_reason, derived_tables = _get_vocabulary_tables_used(tree)

            # If no vocabulary tables are used, rule doesn't apply
            if not tables_with_invalid_reason and not derived_tables:
                continue

            # Check if invalid_reason is filtered
            has_invalid_reason_filter = _has_invalid_reason_filter(tree)

            # Handle tables that have invalid_reason column
            if tables_with_invalid_reason and not has_invalid_reason_filter:
                tables_str = ", ".join(sorted(tables_with_invalid_reason))

                message = (
                    f"Query uses vocabulary table(s) [{tables_str}] without filtering by invalid_reason. "
                    f"Vocabulary tables may contain deprecated or superseded concepts. "
                    f"Add 'invalid_reason IS NULL' to ensure only currently-valid concepts are used."
                )

                violations.append(self.create_violation(
                    message=message,
                    details={
                        "vocabulary_tables": sorted(tables_with_invalid_reason),
                        "recommendation": "Add WHERE condition: invalid_reason IS NULL",
                    }
                ))

            # Handle derived tables (concept_ancestor, etc.)
            if derived_tables:
                # Check if they JOIN to concept with invalid_reason filter
                has_concept_join_with_invalid_reason_filter = _has_concept_join_with_invalid_reason_filter(tree)

                if not has_concept_join_with_invalid_reason_filter:
                    tables_str = ", ".join(sorted(derived_tables))

                    message = (
                        f"Query uses derived vocabulary table(s) [{tables_str}] which do not have an invalid_reason column. "
                        f"To ensure only valid concepts are used, JOIN to the concept table and add "
                        f"'concept.invalid_reason IS NULL' to filter out deprecated concepts."
                    )

                    violations.append(self.create_violation(
                        message=message,
                        severity=Severity.WARNING,  # Warning, not error, since the column does not exist
                        suggested_fix=(
                            "JOIN to concept table and add: WHERE concept.invalid_reason IS NULL"
                        ),
                        details={
                            "derived_tables": sorted(derived_tables),
                            "recommendation": "JOIN concept c ON c.concept_id = <table>.concept_id WHERE c.invalid_reason IS NULL",
                        }
                    ))

        return violations


__all__ = ["InvalidReasonEnforcementRule"]

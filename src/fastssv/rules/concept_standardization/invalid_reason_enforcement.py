"""Invalid Reason Enforcement Rule.

OMOP semantic rule OMOP_501:
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
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
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

# Columns whose use in WHERE/HAVING indicates the concept table is being
# queried as a SOURCE (for concept selection/filtering), not merely joined
# as a lookup to decode a stored concept_id into a name. invalid_reason
# filtering is only meaningful in the former case.
CONCEPT_SOURCE_FILTER_COLUMNS = {
    "vocabulary_id",
    "domain_id",
    "concept_class_id",
    "standard_concept",
    "concept_name",
    "concept_code",
    "relationship_id",
}


def _has_standard_concept_filter(tree: exp.Expression) -> bool:
    """True if the query filters `standard_concept = 'S'` (or IN ('S', ...)).

    Standard concepts are almost always also valid (`invalid_reason IS NULL`),
    so when the user has already narrowed to standard concepts, demanding an
    additional `invalid_reason IS NULL` filter is belt-and-suspenders noise.
    """
    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue
        left, right = eq.left, eq.right
        for col_node, val_node in ((left, right), (right, left)):
            if not isinstance(col_node, exp.Column):
                continue
            if normalize_name(col_node.name) != "standard_concept":
                continue
            if isinstance(val_node, exp.Literal) and val_node.is_string:
                if str(val_node.this).strip().upper() == "S":
                    return True
    for in_expr in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_expr):
            continue
        if not isinstance(in_expr.this, exp.Column):
            continue
        if normalize_name(in_expr.this.name) != "standard_concept":
            continue
        for val in in_expr.expressions or []:
            if isinstance(val, exp.Literal) and val.is_string:
                if str(val.this).strip().upper() == "S":
                    return True
    return False


def _derived_table_in_from(tree: exp.Expression, table_name: str) -> bool:
    """True if the given derived vocabulary table (concept_ancestor,
    concept_synonym, etc.) appears in the primary FROM clause of any
    SELECT — i.e. the query is sourcing from it, not merely joining it
    as a lookup from some other table.
    """
    for select in tree.find_all(exp.Select):
        from_node = select.args.get("from_") or select.args.get("from")
        if from_node is None:
            continue
        for tbl in from_node.find_all(exp.Table):
            if normalize_name(tbl.name) == table_name:
                return True
    return False


def _concept_used_as_source(tree: exp.Expression) -> bool:
    """True if a vocabulary table is queried as a SOURCE, not merely a lookup.

    Distinguishes:
    - SOURCE usage: WHERE/HAVING filters on concept.vocabulary_id /
      domain_id / relationship_id / standard_concept / concept_name /
      concept_code / concept_class_id — the query is building/filtering
      a concept set.
    - LOOKUP usage: vocabulary table appears only to resolve a specific
      concept_id (e.g., `WHERE c.concept_id = 192671`), with no filters
      on selection columns.

    Only SOURCE usage warrants an invalid_reason filter warning.
    """
    aliases = extract_aliases(tree)
    for select in tree.find_all(exp.Select):
        for clause_key in ("where", "having"):
            clause = select.args.get(clause_key)
            if clause is None:
                continue
            for col in clause.find_all(exp.Column):
                col_name = normalize_name(col.name)
                if col_name not in CONCEPT_SOURCE_FILTER_COLUMNS:
                    continue
                resolved_table, _ = resolve_table_col(col, aliases)
                if not resolved_table or resolved_table in (
                    "concept",
                    "concept_relationship",
                ):
                    return True
    return False


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


def _has_date_validity_check(tree: exp.Expression) -> bool:
    """Check if the query validates concepts using valid_start_date and valid_end_date.

    This is an alternative to checking invalid_reason IS NULL.
    Looks for patterns like:
    - current_date BETWEEN valid_start_date AND valid_end_date
    - getdate() >= valid_start_date AND getdate() <= valid_end_date
    - NOW() >= valid_start_date AND NOW() <= valid_end_date

    Args:
        tree: The SQL AST to search

    Returns:
        True if date validity check is found
    """
    has_start_check = False
    has_end_check = False

    # Look for comparisons involving valid_start_date and valid_end_date
    for comparison in tree.find_all((exp.GTE, exp.GT, exp.LTE, exp.LT)):
        if not is_in_where_or_join_clause(comparison):
            continue

        left = comparison.left
        right = comparison.right

        # Check for valid_start_date
        if isinstance(left, exp.Column) and normalize_name(left.name) == "valid_start_date":
            has_start_check = True
        if isinstance(right, exp.Column) and normalize_name(right.name) == "valid_start_date":
            has_start_check = True

        # Check for valid_end_date
        if isinstance(left, exp.Column) and normalize_name(left.name) == "valid_end_date":
            has_end_check = True
        if isinstance(right, exp.Column) and normalize_name(right.name) == "valid_end_date":
            has_end_check = True

    return has_start_check and has_end_check


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
        if has_table_reference(tree, table_name):
            tables_with_invalid_reason.add(table_name)

    for table_name in DERIVED_VOCABULARY_TABLES:
        if has_table_reference(tree, table_name):
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
    if not has_table_reference(tree, "concept"):
        return False

    # Check if there's an invalid_reason filter
    return _has_invalid_reason_filter(tree)


@register
class InvalidReasonEnforcementRule(Rule):
    """Ensures queries on vocabulary tables filter by invalid_reason."""

    rule_id = "concept_standardization.invalid_reason_enforcement"
    name = "Invalid Reason Enforcement"
    description = (
        "Ensures queries on vocabulary/concept tables filter by invalid_reason "
        "to ensure only valid concepts are used (invalid_reason IS NULL)"
    )
    severity = Severity.WARNING  # Best practice, not correctness issue
    suggested_fix = (
        "Add 'WHERE invalid_reason IS NULL' to ensure only valid concepts are used, "
        "or explicitly handle invalid concepts if needed"
    )
    long_description = (
        "Concept tables (concept, concept_relationship, concept_ancestor) "
        "carry an invalid_reason column that marks retired or replaced "
        "entries: 'D' for deprecated, 'U' for upgraded to a newer concept, "
        "NULL for currently valid. Queries that omit an "
        "`invalid_reason IS NULL` predicate silently include retired rows, "
        "which can pull in concept_ids that your site no longer records or "
        "that map onward to a better successor. Add the filter to stick "
        "to current concepts."
    )
    example_bad = (
        "SELECT concept_id, concept_name\n"
        "FROM concept\n"
        "WHERE vocabulary_id = 'SNOMED';"
    )
    example_good = (
        "SELECT concept_id, concept_name\n"
        "FROM concept\n"
        "WHERE vocabulary_id = 'SNOMED'\n"
        "  AND invalid_reason IS NULL;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            # Parse errors handled elsewhere
            return []

        # Check validation context for severity adjustment
        from fastssv.core.validation_context import get_validation_context
        ctx = get_validation_context()

        # Default: WARNING (best practice)
        # Strict mode: escalate to ERROR
        severity = Severity.WARNING
        if ctx.should_escalate_rule(self.rule_id):
            severity = Severity.ERROR

        for tree in trees:
            if tree is None:
                continue

            # Find vocabulary tables used in the query
            tables_with_invalid_reason, derived_tables = _get_vocabulary_tables_used(tree)

            # If no vocabulary tables are used, rule doesn't apply
            if not tables_with_invalid_reason and not derived_tables:
                continue

            # Check if invalid_reason is filtered OR date validity is checked
            has_invalid_reason_filter = _has_invalid_reason_filter(tree)
            has_date_validity = _has_date_validity_check(tree)
            # Standard concepts are nearly always also valid in OMOP — filtering
            # by standard_concept = 'S' effectively narrows to the valid-concept
            # subset, so the additional invalid_reason check is redundant noise
            # on OHDSI-standard queries.
            has_standard_filter = _has_standard_concept_filter(tree)

            # Handle tables that have invalid_reason column.
            # Only warn when concept is used as a SOURCE (filtered by
            # vocabulary_id, domain_id, etc.), not as a pure lookup join.
            if (
                tables_with_invalid_reason
                and not has_invalid_reason_filter
                and not has_date_validity
                and not has_standard_filter
                and _concept_used_as_source(tree)
            ):
                tables_str = ", ".join(sorted(tables_with_invalid_reason))

                message = (
                    f"Query uses vocabulary table(s) [{tables_str}] without filtering by invalid_reason. "
                    f"Vocabulary tables may contain deprecated or superseded concepts. "
                    f"Add 'invalid_reason IS NULL' to ensure only currently-valid concepts are used."
                )

                violations.append(self.create_violation(
                    severity=severity,  # Context-aware severity
                    message=message,
                    suggested_fix="Add WHERE condition: invalid_reason IS NULL",
                    details={
                        "vocabulary_tables": sorted(tables_with_invalid_reason),
                        "recommendation": "Add WHERE condition: invalid_reason IS NULL",
                        "strict_mode_escalated": severity == Severity.ERROR
                    }
                ))

            # Handle derived tables (concept_ancestor, etc.).
            # Skip when the derived table only appears in a JOIN (auxiliary
            # lookup) rather than in a primary FROM clause. Example:
            #   FROM concept c JOIN concept_synonym s ON c.concept_id = s.concept_id
            #   WHERE c.concept_id = 192671
            # — here concept_synonym is a lookup to fetch synonyms for a
            # specific concept, not a source to filter against, so demanding
            # invalid_reason would exclude legitimate historical results.
            derived_tables_as_source = {
                t for t in derived_tables
                if _derived_table_in_from(tree, t)
            }
            if derived_tables_as_source:
                # Check if they JOIN to concept with invalid_reason filter OR date validity
                has_concept_join_with_invalid_reason_filter = _has_concept_join_with_invalid_reason_filter(tree)

                # If the main concept table has date validity checks OR the query
                # filters standard_concept = 'S' (which implies validity), derived
                # tables are also considered validated.
                if (
                    not has_concept_join_with_invalid_reason_filter
                    and not has_date_validity
                    and not has_standard_filter
                ):
                    tables_str = ", ".join(sorted(derived_tables_as_source))

                    message = (
                        f"Query uses derived vocabulary table(s) [{tables_str}] which do not have an invalid_reason column. "
                        f"To ensure only valid concepts are used, JOIN to the concept table and add "
                        f"'concept.invalid_reason IS NULL' to filter out deprecated concepts."
                    )

                    violations.append(self.create_violation(
                        message=message,
                        severity=severity,  # Context-aware: WARNING by default, ERROR in strict mode
                        suggested_fix=(
                            "JOIN to concept table and add: WHERE concept.invalid_reason IS NULL"
                        ),
                        details={
                            "derived_tables": sorted(derived_tables_as_source),
                            "recommendation": "JOIN concept c ON c.concept_id = <table>.concept_id WHERE c.invalid_reason IS NULL",
                            "strict_mode_escalated": severity == Severity.ERROR,
                        }
                    ))

        return violations


__all__ = ["InvalidReasonEnforcementRule"]

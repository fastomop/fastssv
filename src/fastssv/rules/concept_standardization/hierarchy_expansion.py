"""Hierarchy Expansion Recommendation Rule.

OMOP semantic rule:
When filtering on drug_concept_id or condition_concept_id, consider using
concept_ancestor table to capture all descendants (child concepts).

Whether hierarchy expansion is needed depends on analytical intent:
- If querying an ingredient/class, use concept_ancestor to get all formulations
- If querying a specific clinical drug product, direct filtering may be correct

A single concept_id (e.g., "Metformin ingredient") misses specific formulations
(Metformin 500mg, Metformin XR, etc.). Hierarchy expansion ensures complete capture.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    extract_join_conditions,
    is_in_where_or_join_clause,
    is_numeric_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register

# Columns that require hierarchy expansion when filtered
HIERARCHY_REQUIRED_COLUMNS = {
    ("drug_exposure", "drug_concept_id"),
    ("condition_occurrence", "condition_concept_id"),
}


def _infer_table_for_hierarchy_column(
    col_name: str,
    aliases: Dict[str, str]
) -> Optional[str]:
    """For unqualified columns, try to infer the table from HIERARCHY_REQUIRED_COLUMNS."""
    col_name_norm = normalize_name(col_name)
    matching_tables = []
    
    for table, column in HIERARCHY_REQUIRED_COLUMNS:
        if col_name_norm == column:
            # Check if this table is referenced in the query
            if table in aliases.values():
                matching_tables.append(table)
    
    # Only return if we have exactly one match
    if len(matching_tables) == 1:
        return matching_tables[0]
    return None


def _extract_hierarchy_concept_filters(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[Tuple[str, str, exp.Expression]]:
    """
    Find all filters on drug_concept_id or condition_concept_id with specific numeric values.
    
    Excludes:
    - Filters on concept_id = 0 (unmapped records)
    
    Returns list of (table, column, filter_expression) tuples.
    """
    filters: List[Tuple[str, str, exp.Expression]] = []
    
    # Build normalized set of target columns
    target_columns = {col for _, col in HIERARCHY_REQUIRED_COLUMNS}
    
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
        if col_name not in target_columns:
            continue
        
        # Skip if filtering on 0 (unmapped)
        if is_numeric_literal(right, 0):
            continue
        
        # Check if it's a specific numeric value
        if is_numeric_literal(right):
            table, _ = resolve_table_col(left, aliases)
            if not table:
                table = _infer_table_for_hierarchy_column(col_name, aliases)
            
            # Verify this is a hierarchy-required column
            if table and (table, col_name) in HIERARCHY_REQUIRED_COLUMNS:
                filters.append((table, col_name, eq))
    
    # Check IN clauses: concept_id IN (12345, 67890)
    for in_expr in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_expr):
            continue
        
        if not isinstance(in_expr.this, exp.Column):
            continue
        
        col_name = normalize_name(in_expr.this.name)
        if col_name not in target_columns:
            continue
        
        # Check if IN clause contains specific numeric values (excluding 0)
        has_specific_values = False
        for val in in_expr.expressions or []:
            if is_numeric_literal(val) and not is_numeric_literal(val, 0):
                has_specific_values = True
                break
        
        if has_specific_values:
            table, _ = resolve_table_col(in_expr.this, aliases)
            if not table:
                table = _infer_table_for_hierarchy_column(col_name, aliases)
            
            # Verify this is a hierarchy-required column
            if table and (table, col_name) in HIERARCHY_REQUIRED_COLUMNS:
                filters.append((table, col_name, in_expr))
    
    return filters


def _uses_concept_ancestor(tree: exp.Expression) -> bool:
    """Check if query uses the concept_ancestor table."""
    return uses_table(tree, "concept_ancestor")


def _verify_concept_ancestor_join_direction(
    tree: exp.Expression,
    aliases: Dict[str, str],
    filtered_columns: Set[Tuple[str, str]]
) -> Tuple[bool, List[str]]:
    """
    Verify that concept_ancestor is joined correctly:
    - clinical_table.*_concept_id should join to concept_ancestor.descendant_concept_id
    
    Returns (is_valid, list_of_warnings)
    """
    warnings: List[str] = []
    
    if not _uses_concept_ancestor(tree):
        return True, []
    
    join_conditions = extract_join_conditions(tree, aliases)
    
    # Also check WHERE clause for join-like conditions (some people write implicit joins)
    # We'll focus on explicit JOINs for now
    
    for lt, lc, rt, rc in join_conditions:
        # Check if concept_ancestor is involved
        ca_table = None
        ca_col = None
        other_table = None
        other_col = None
        
        if lt == "concept_ancestor":
            ca_table, ca_col = lt, lc
            other_table, other_col = rt, rc
        elif rt == "concept_ancestor":
            ca_table, ca_col = rt, rc
            other_table, other_col = lt, lc
        else:
            continue
        
        # Check if the other side is one of our filtered columns
        if (other_table, other_col) not in filtered_columns:
            continue
        
        # The correct pattern is: clinical.concept_id = concept_ancestor.descendant_concept_id
        # Warn if joining on ancestor_concept_id instead
        if ca_col == "ancestor_concept_id":
            warnings.append(
                f"Incorrect concept_ancestor join direction: {other_table}.{other_col} is joined to "
                f"concept_ancestor.ancestor_concept_id. For hierarchy expansion, join to "
                f"concept_ancestor.descendant_concept_id instead (ancestor_concept_id should hold "
                f"your target parent concept)."
            )
    
    return len(warnings) == 0, warnings


@register
class HierarchyExpansionRule(Rule):
    """Recommends using concept_ancestor for hierarchy expansion when appropriate."""

    rule_id = "semantic.hierarchy_expansion_required"
    name = "Hierarchy Expansion Required"
    description = (
        "Requires using concept_ancestor table when filtering on drug_concept_id or "
        "condition_concept_id to capture descendant concepts. OMOP data is typically recorded "
        "using specific descendant codes, not parent concepts. Without hierarchy expansion, "
        "queries often return 0 patients when data exists under child concepts. "
        "Over-expansion is safer than under-expansion in clinical queries."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Use concept_ancestor for hierarchy expansion: "
        "JOIN concept_ancestor ca ON table.concept_id = ca.descendant_concept_id "
        "WHERE ca.ancestor_concept_id = <your_target_concept>. "
        "This ensures all descendant concepts are captured. Remove the direct concept_id filter "
        "and use only the ancestor_concept_id filter in the concept_ancestor join."
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
            
            # Find all filters on drug_concept_id or condition_concept_id
            concept_filters = _extract_hierarchy_concept_filters(tree, aliases)
            
            if not concept_filters:
                # No hierarchy-requiring filters found, rule doesn't apply
                continue
            
            # Check if concept_ancestor is used
            uses_ancestor = _uses_concept_ancestor(tree)
            
            # Group by (table, column) for reporting
            filtered_columns: Set[Tuple[str, str]] = set()
            for table, column, _ in concept_filters:
                filtered_columns.add((table, column))
            
            if not uses_ancestor:
                # Recommendation: consider hierarchy expansion
                columns_str = ", ".join(
                    sorted(f"{t}.{c}" for t, c in filtered_columns)
                )
                violations.append(self.create_violation(
                    message=(
                        f"Query filters on {columns_str} without hierarchy expansion using concept_ancestor. "
                        f"In OMOP CDM, data is recorded using specific descendant codes (e.g., 'Iron deficiency anemia'), "
                        f"not parent concepts (e.g., 'Anemia'). Filtering directly on concept_id will likely return "
                        f"0 or incomplete results. Use concept_ancestor to capture all descendant concepts."
                    ),
                    details={
                        "filtered_columns": sorted(
                            f"{t}.{c}" for t, c in filtered_columns
                        ),
                        "fix_pattern": (
                            "JOIN concept_ancestor ca ON {table}.{column} = ca.descendant_concept_id "
                            "WHERE ca.ancestor_concept_id = <target_concept_id>"
                        ),
                        "explanation": (
                            "Remove direct concept_id filter and use ancestor_concept_id filter on concept_ancestor table. "
                            "This ensures all specific types/formulations are included in results."
                        )
                    }
                ))
            else:
                # concept_ancestor is used, but verify the join direction
                _, direction_warnings = _verify_concept_ancestor_join_direction(
                    tree, aliases, filtered_columns
                )
                
                for warning in direction_warnings:
                    violations.append(self.create_violation(
                        message=warning,
                        severity=Severity.WARNING,
                        suggested_fix=(
                            "Join clinical table to concept_ancestor.descendant_concept_id, "
                            "and filter on concept_ancestor.ancestor_concept_id"
                        ),
                    ))
        
        return violations


__all__ = ["HierarchyExpansionRule", "HIERARCHY_REQUIRED_COLUMNS"]

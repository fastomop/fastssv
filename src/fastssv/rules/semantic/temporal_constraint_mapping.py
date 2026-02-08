"""Observation Period Anchoring Rule.

OMOP semantic rule:
Queries with temporal constraints (washout, follow-up, event windows) MUST
join to observation_period on person_id.

Temporal constraints are only valid within a patient's observation window.
Events before observation_period_start_date or after observation_period_end_date
may be incomplete or missing.
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    extract_join_conditions,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register

# Clinical tables that have temporal data and require observation_period anchoring
CLINICAL_TABLES_WITH_DATES = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "visit_occurrence",
    "visit_detail",
    "device_exposure",
    "death",
    "specimen",
    "note",
    "episode",
}

# Date columns in clinical tables that indicate temporal constraints when filtered
TEMPORAL_DATE_COLUMNS = {
    # Condition
    "condition_start_date", "condition_start_datetime",
    "condition_end_date", "condition_end_datetime",
    # Drug
    "drug_exposure_start_date", "drug_exposure_start_datetime",
    "drug_exposure_end_date", "drug_exposure_end_datetime",
    # Procedure
    "procedure_date", "procedure_datetime",
    # Measurement
    "measurement_date", "measurement_datetime",
    # Observation
    "observation_date", "observation_datetime",
    # Visit
    "visit_start_date", "visit_start_datetime",
    "visit_end_date", "visit_end_datetime",
    # Visit detail
    "visit_detail_start_date", "visit_detail_start_datetime",
    "visit_detail_end_date", "visit_detail_end_datetime",
    # Device
    "device_exposure_start_date", "device_exposure_start_datetime",
    "device_exposure_end_date", "device_exposure_end_datetime",
    # Death
    "death_date", "death_datetime",
    # Specimen
    "specimen_date", "specimen_datetime",
    # Note
    "note_date", "note_datetime",
    # Episode
    "episode_start_date", "episode_start_datetime",
    "episode_end_date", "episode_end_datetime",
}

# Date functions that indicate temporal logic
DATE_FUNCTION_NAMES = {
    "dateadd", "date_add", "datediff", "date_diff", "timestampdiff",
    "date_sub", "date_trunc", "extract", "age", "interval",
    "months_between", "days", "add_months", "add_days",
}


def _is_date_column(col_name: str) -> bool:
    """Check if column name looks like a date/temporal column."""
    col_lower = normalize_name(col_name)
    return (
        col_lower in TEMPORAL_DATE_COLUMNS or
        col_lower.endswith("_date") or
        col_lower.endswith("_datetime") or
        col_lower.endswith("_time")
    )


def _is_date_literal(node: exp.Expression) -> bool:
    """Check if expression is a date literal or cast to date."""
    if isinstance(node, exp.Literal) and node.is_string:
        # Check if it looks like a date string (YYYY-MM-DD pattern)
        val = str(node.this)
        if len(val) >= 10 and val[4:5] == "-" and val[7:8] == "-":
            return True
    
    # Check for DATE 'literal' or CAST(... AS DATE)
    if isinstance(node, exp.Cast):
        to_type = node.to
        if to_type and normalize_name(str(to_type)).startswith("date"):
            return True
    
    # Check for date function calls like DATE('2020-01-01')
    if isinstance(node, exp.Anonymous):
        func_name = normalize_name(node.name) if hasattr(node, 'name') else ""
        if func_name in {"date", "timestamp", "datetime"}:
            return True
    
    return False


def _has_date_function(tree: exp.Expression) -> bool:
    """Check if query uses date manipulation functions."""
    for node in tree.walk():
        if isinstance(node, (exp.Anonymous, exp.Func)):
            func_name = ""
            if hasattr(node, 'name'):
                func_name = normalize_name(node.name)
            elif hasattr(node, 'key'):
                func_name = normalize_name(node.key)
            
            if func_name in DATE_FUNCTION_NAMES:
                return True
        
        # Check for INTERVAL expressions
        if isinstance(node, exp.Interval):
            return True
    
    return False


def _extract_temporal_constraints(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[Tuple[str, str, exp.Expression]]:
    """
    Find temporal constraints in the query.
    
    Returns list of (table, column, constraint_expression) tuples.
    """
    constraints: List[Tuple[str, str, exp.Expression]] = []
    
    # Comparison operators that indicate filtering
    comparison_types = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)
    
    for node in tree.walk():
        if not isinstance(node, comparison_types):
            continue
        
        if not is_in_where_or_join_clause(node):
            continue
        
        # Extract columns involved in the comparison
        columns_in_node = list(node.find_all(exp.Column))
        
        for col in columns_in_node:
            col_name = normalize_name(col.name)
            
            if not _is_date_column(col_name):
                continue
            
            table, _ = resolve_table_col(col, aliases)
            
            # If we can identify the table and it's a clinical table
            if table and table in CLINICAL_TABLES_WITH_DATES:
                constraints.append((table, col_name, node))
            elif not table and col_name in TEMPORAL_DATE_COLUMNS:
                # Try to infer table from aliases
                for alias_table in aliases.values():
                    if alias_table in CLINICAL_TABLES_WITH_DATES:
                        constraints.append((alias_table, col_name, node))
                        break
    
    return constraints


def _uses_observation_period(tree: exp.Expression) -> bool:
    """Check if query uses the observation_period table."""
    return uses_table(tree, "observation_period")


def _joins_observation_period_on_person_id(
    tree: exp.Expression,
    aliases: Dict[str, str],
    clinical_tables: Set[str]
) -> Tuple[bool, List[str]]:
    """
    Verify that observation_period is properly joined to clinical tables on person_id.
    
    Returns (is_valid, list_of_issues)
    """
    if not _uses_observation_period(tree):
        return False, ["Query does not use observation_period table"]
    
    join_conditions = extract_join_conditions(tree, aliases)
    
    # Check if any join involves observation_period and person_id
    op_joined_to_clinical = False
    
    for lt, lc, rt, rc in join_conditions:
        # Check if observation_period is involved
        if lt == "observation_period" or rt == "observation_period":
            # Check if join is on person_id
            if lc == "person_id" and rc == "person_id":
                # Check if the other table is a clinical table
                other_table = rt if lt == "observation_period" else lt
                if other_table in clinical_tables or other_table in CLINICAL_TABLES_WITH_DATES:
                    op_joined_to_clinical = True
                    break
    
    if not op_joined_to_clinical:
        return False, [
            "observation_period table is not properly joined to clinical tables on person_id"
        ]
    
    return True, []


@register
class ObservationPeriodAnchoringRule(Rule):
    """Ensures queries with temporal constraints join to observation_period."""
    
    rule_id = "semantic.observation_period_anchoring"
    name = "Observation Period Anchoring"
    description = (
        "Ensures queries with temporal constraints (washout, follow-up, event windows) "
        "join to observation_period on person_id. Events outside the observation window "
        "may be incomplete or missing."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "JOIN observation_period op ON clinical_table.person_id = op.person_id "
        "AND clinical_table.date BETWEEN op.observation_period_start_date "
        "AND op.observation_period_end_date"
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
            
            # Find temporal constraints in the query
            temporal_constraints = _extract_temporal_constraints(tree, aliases)
            
            # Also check for date functions
            has_date_functions = _has_date_function(tree)
            
            if not temporal_constraints and not has_date_functions:
                # No temporal constraints found, rule doesn't apply
                continue
            
            # Get the clinical tables involved
            clinical_tables = {t for t, _, _ in temporal_constraints}
            
            # Also include any clinical tables referenced in the query
            for table in aliases.values():
                if table in CLINICAL_TABLES_WITH_DATES:
                    clinical_tables.add(table)
            
            # Check if observation_period is used and properly joined
            uses_op = _uses_observation_period(tree)
            
            if not uses_op:
                # Build informative message
                constraint_details = []
                for table, col, _ in temporal_constraints:
                    constraint_details.append(f"{table}.{col}")
                
                # Deduplicate
                constraint_details = sorted(set(constraint_details))
                
                message = (
                    f"Query has temporal constraints but does not join to observation_period. "
                    f"Temporal filters on: {', '.join(constraint_details) if constraint_details else 'date functions'}. "
                    f"Events outside a patient's observation window may be incomplete."
                )
                
                violations.append(self.create_violation(
                    message=message,
                    details={
                        "temporal_columns": constraint_details,
                        "clinical_tables": sorted(clinical_tables),
                        "has_date_functions": has_date_functions,
                    }
                ))
            else:
                # observation_period is used, verify the join
                is_valid, issues = _joins_observation_period_on_person_id(
                    tree, aliases, clinical_tables
                )
                
                if not is_valid:
                    for issue in issues:
                        violations.append(self.create_violation(
                            message=issue,
                            severity=Severity.WARNING,
                            suggested_fix=(
                                "Ensure observation_period is joined on person_id: "
                                "JOIN observation_period op ON table.person_id = op.person_id"
                            ),
                        ))
        
        return violations


__all__ = ["ObservationPeriodAnchoringRule", "CLINICAL_TABLES_WITH_DATES", "TEMPORAL_DATE_COLUMNS"]

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


def is_date_column(col_name: str) -> bool:
    """Check if column name looks like a date/temporal column."""
    col_lower = normalize_name(col_name)
    return (
        col_lower in TEMPORAL_DATE_COLUMNS or
        col_lower.endswith("_date") or
        col_lower.endswith("_datetime") or
        col_lower.endswith("_time")
    )


# Private alias kept for backward compatibility within this module
_is_date_column = is_date_column


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


def _is_cohort_or_patient_level_query(tree: exp.Expression) -> bool:
    """Check if query is doing cohort selection or patient-level inference.

    This distinguishes between:
    - Cohort/patient-level queries: observation_period anchoring required
    - Descriptive aggregations: observation_period NOT required

    Cohort/patient-level indicators:
    - Selecting DISTINCT person_id in outer query
    - Patient-level filtering (WHERE on person-level attributes)

    Descriptive aggregation indicators (observation_period NOT required):
    - Aggregating over events (COUNT(*), AVG(duration))
    - Era-level summaries
    - Distribution statistics without patient selection
    """
    # Check if query selects DISTINCT person_id in outer SELECT
    if isinstance(tree, exp.Select):
        for select_col in tree.find_all(exp.Column):
            # Only check columns directly in the SELECT expressions, not nested
            parent = select_col.parent
            is_in_select_expr = False
            while parent and not isinstance(parent, exp.Select):
                parent = parent.parent
            if isinstance(parent, exp.Select) and parent == tree:
                # This is a top-level SELECT column
                if normalize_name(select_col.name) == "person_id":
                    # Check if it's wrapped in DISTINCT
                    if tree.find(exp.Distinct):
                        return True

    return False


def _has_washout_or_followup_logic(tree: exp.Expression) -> bool:
    """Check if query implements washout or follow-up period logic.

    These patterns require observation_period anchoring:
    - DATEDIFF between clinical event dates
    - DATEADD for follow-up windows
    - Explicit washout period filtering with date comparisons
    """
    # Check for DateDiff, DateAdd expressions
    date_expr_types = []
    for expr_name in ['DateDiff', 'DateAdd', 'DateSub', 'TsOrDsAdd']:
        if hasattr(exp, expr_name):
            date_expr_types.append(getattr(exp, expr_name))

    if date_expr_types:
        for node in tree.find_all(tuple(date_expr_types)):
            # Check if this is in a WHERE or JOIN clause (temporal filtering)
            parent = node.parent
            while parent:
                if isinstance(parent, (exp.Where, exp.Join)):
                    return True
                parent = parent.parent

    # Also check for Anonymous functions with date arithmetic names
    for func in tree.find_all(exp.Anonymous):
        func_name = ""
        if hasattr(func, 'name'):
            func_name = normalize_name(func.name)
        elif hasattr(func, 'this') and isinstance(func.this, str):
            func_name = normalize_name(func.this)

        # DATEDIFF, DATEADD in WHERE/JOIN clause indicates temporal logic
        if func_name in {"datediff", "date_diff", "timestampdiff", "dateadd", "date_add"}:
            parent = func.parent
            while parent:
                if isinstance(parent, (exp.Where, exp.Join)):
                    return True
                parent = parent.parent

    # Check for date comparisons with INTERVAL in WHERE/JOIN
    for comparison in tree.find_all((exp.GTE, exp.GT, exp.LTE, exp.LT, exp.Between)):
        if not is_in_where_or_join_clause(comparison):
            continue

        # Check if involves INTERVAL
        if comparison.find(exp.Interval):
            return True

    return False


@register
class ObservationPeriodAnchoringRule(Rule):
    """Detects incomplete temporal anchoring when observation_period is used."""

    rule_id = "temporal.observation_period_anchoring"
    name = "Observation Period Anchoring"
    description = (
        "When observation_period is included in a query, ensures it is properly joined "
        "on person_id to clinical tables. Does not flag queries that simply don't use "
        "observation_period (valid design choice for descriptive queries)."
    )
    severity = Severity.WARNING
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

            # Check if observation_period is used
            uses_op = _uses_observation_period(tree)

            # Only flag if observation_period is referenced but NEVER joined on person_id
            # This is very conservative - we're only catching obvious mistakes
            if not uses_op:
                # No observation_period - skip
                continue

            # NEW: Check if observation_period is the PRIMARY table being analyzed
            # If so, it doesn't need to be joined to anything - it IS the subject
            is_primary_table = self._is_observation_period_primary_table(tree, aliases)
            if is_primary_table:
                # observation_period is the main subject of analysis, not a temporal anchor
                # No violation - this is a valid descriptive query
                continue

            # observation_period is present - check if it's joined on person_id AT ALL
            # (not checking completeness, just checking for obvious errors)
            join_conditions = extract_join_conditions(tree, aliases)

            has_any_person_id_join = False
            for lt, lc, rt, rc in join_conditions:
                if (lt == "observation_period" or rt == "observation_period"):
                    if lc == "person_id" and rc == "person_id":
                        has_any_person_id_join = True
                        break

            if not has_any_person_id_join:
                # observation_period is in the query but never joined on person_id
                # AND it's not the primary table being analyzed
                # This is likely a mistake
                violations.append(self.create_violation(
                    message=(
                        "observation_period table is referenced but not joined on person_id. "
                        "If using observation_period, it should typically be joined via person_id."
                    ),
                    severity=Severity.WARNING,
                    suggested_fix=(
                        "JOIN observation_period op ON table.person_id = op.person_id"
                    ),
                ))

        return violations

    def _is_observation_period_primary_table(self, tree: exp.Expression, aliases: Dict[str, str]) -> bool:
        """Check if observation_period is the primary table being analyzed.

        Returns True if:
        - observation_period is in FROM clause (not just in a join)
        - No clinical tables are being joined with temporal constraints
        - Query is selecting/aggregating observation_period columns
        - Query is grouping by person_id from observation_period (analyzing periods per person)
        """
        # Check if observation_period appears in FROM clause
        has_op_in_from = False
        has_clinical_table_in_from = False

        for select in tree.find_all(exp.Select):
            from_clause = select.args.get("from_")
            if from_clause and isinstance(from_clause, exp.From):
                for table_expr in from_clause.find_all(exp.Table):
                    table_name = normalize_name(table_expr.name)
                    if table_name == "observation_period":
                        has_op_in_from = True
                    elif table_name in CLINICAL_TABLES_WITH_DATES:
                        has_clinical_table_in_from = True

        # If observation_period is in FROM and no clinical tables, it's the primary subject
        if has_op_in_from and not has_clinical_table_in_from:
            return True

        # Additional check: if observation_period columns are being selected/aggregated
        # and there are no temporal constraints on other clinical tables
        has_op_columns = False
        for col in tree.find_all(exp.Column):
            col_name = normalize_name(col.name)
            # Check if this is an observation_period column
            if col_name.startswith("observation_period_") or col.table and normalize_name(str(col.table)) == "observation_period":
                has_op_columns = True
                break

        # If we're selecting observation_period columns and have no clinical tables, it's primary
        if has_op_columns and not has_clinical_table_in_from:
            return True

        # NEW: Check if ANY SELECT in the tree has observation_period with GROUP BY person_id
        # This indicates the query is analyzing observation periods per person, not using
        # observation_period as a temporal anchor for clinical events
        for select in tree.find_all(exp.Select):
            # Check if this SELECT has observation_period in its FROM
            select_has_op = False
            from_clause = select.args.get("from_")
            if from_clause and isinstance(from_clause, exp.From):
                for table_expr in from_clause.find_all(exp.Table):
                    if normalize_name(table_expr.name) == "observation_period":
                        select_has_op = True
                        break

            # If this SELECT has observation_period, check if it groups by person_id
            if select_has_op:
                group_by = select.args.get("group")
                if group_by and isinstance(group_by, exp.Group):
                    for group_expr in group_by.expressions:
                        if isinstance(group_expr, exp.Column):
                            col_name = normalize_name(group_expr.name)
                            if col_name == "person_id":
                                # This SELECT groups observation_period by person_id
                                # No clinical tables in the overall query
                                if not has_clinical_table_in_from:
                                    return True

        return False


__all__ = ["ObservationPeriodAnchoringRule", "CLINICAL_TABLES_WITH_DATES", "TEMPORAL_DATE_COLUMNS", "is_date_column"]

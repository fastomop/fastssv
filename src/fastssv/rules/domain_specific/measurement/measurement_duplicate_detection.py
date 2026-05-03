"""Measurement Duplicate Detection Rule.

OMOP semantic rule OMOP_238: measurement_duplicate_detection

Duplicate measurement records for the same person/concept/date may occur due to
ETL errors, data quality issues, or integration artifacts. Unlike drug_exposure
or condition_occurrence where multiple records per person are expected, duplicate
measurements often represent data quality issues.

The Problem:
    The measurement table can contain duplicate records with the same:
    - person_id
    - measurement_concept_id
    - measurement_date

    These duplicates can occur from:
    - ETL processing errors (same source record loaded multiple times)
    - Data quality issues (duplicate submissions from source systems)
    - Integration artifacts (same measurement from different data feeds)
    - Multiple precision levels (e.g., 5.5 and 5.52 for same measurement)

    Unlike drug_exposure (where refills are expected) or condition_occurrence
    (where recurring diagnoses are expected), duplicate measurements at the
    same date typically indicate data quality issues rather than clinical reality.

Detection patterns:
    Query performs aggregations or counts on measurement table without:
    - Grouping by natural key (person_id, measurement_concept_id, measurement_date)
    - Using DISTINCT
    - Using deduplication logic (ROW_NUMBER, etc.)

Violation example:
    -- BAD: Counts may include duplicates
    SELECT person_id, AVG(value_as_number) AS avg_glucose
    FROM measurement
    WHERE measurement_concept_id = 3004410
    GROUP BY person_id
    -- If person has 2 identical measurements on same date, average is skewed

Correct patterns:
    -- GOOD: Group by natural key to handle duplicates
    SELECT person_id, measurement_date, measurement_concept_id,
           AVG(value_as_number) AS avg_value
    FROM measurement
    WHERE measurement_concept_id = 3004410
    GROUP BY person_id, measurement_date, measurement_concept_id

    -- GOOD: Use ROW_NUMBER to deduplicate
    WITH ranked AS (
      SELECT *,
        ROW_NUMBER() OVER (
          PARTITION BY person_id, measurement_concept_id, measurement_date
          ORDER BY measurement_datetime NULLS LAST
        ) AS rn
      FROM measurement
    )
    SELECT * FROM ranked WHERE rn = 1

    -- GOOD: Use DISTINCT
    SELECT DISTINCT person_id, measurement_concept_id, measurement_date
    FROM measurement
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

TABLE_MEASUREMENT = "measurement"

PERSON_ID = "person_id"
MEASUREMENT_CONCEPT_ID = "measurement_concept_id"
MEASUREMENT_DATE = "measurement_date"

NATURAL_KEY_COLUMNS = {
    PERSON_ID,
    MEASUREMENT_CONCEPT_ID,
    MEASUREMENT_DATE,
}

AGGREGATION_TYPES = (exp.Count, exp.Avg, exp.Sum, exp.Min, exp.Max)


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _has_table(tree: exp.Expression, target: str) -> bool:
    return any(_norm(t.name) == _norm(target) for t in tree.find_all(exp.Table))


def _is_measurement_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    """Check if column is from measurement table or unqualified with measurement in scope."""
    table, _ = resolve_table_col(col, aliases)

    # Explicitly from measurement
    if _norm(table) == TABLE_MEASUREMENT:
        return True

    # Unqualified column with only measurement table in query
    if not table:
        tables = {_norm(t) for t in aliases.values()}
        if len(tables) == 1 and TABLE_MEASUREMENT in tables:
            return True

    return False


def _has_aggregation(tree: exp.Expression) -> bool:
    return any(tree.find_all(AGGREGATION_TYPES))


def _has_measurement_aggregation(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if query aggregates on measurement table (including COUNT(*))."""
    for agg in tree.find_all(AGGREGATION_TYPES):
        # COUNT(*) - treat as measurement aggregation if measurement table is present
        if isinstance(agg, exp.Count) and (agg.this is None or isinstance(agg.this, exp.Star)):
            return True

        # Check if any column in aggregation is from measurement
        for col in agg.find_all(exp.Column):
            if _is_measurement_column(col, aliases):
                return True

    return False


def _extract_group_by_columns(select: exp.Select, aliases: Dict[str, str]) -> Set[str]:
    group = select.args.get("group")
    if not group:
        return set()

    cols = set()

    for expr in group.expressions:
        for col in expr.find_all(exp.Column):
            _, col_name = resolve_table_col(col, aliases)
            cols.add(_norm(col_name))

    return cols


def _has_natural_key_grouping(select: exp.Select, aliases: Dict[str, str]) -> bool:
    group_cols = _extract_group_by_columns(select, aliases)
    return NATURAL_KEY_COLUMNS.issubset(group_cols)


def _distinct_covers_natural_key(select: exp.Select, aliases: Dict[str, str]) -> bool:
    if not select.args.get("distinct"):
        return False

    cols = set()

    for expr in select.expressions:
        for col in expr.find_all(exp.Column):
            _, col_name = resolve_table_col(col, aliases)
            cols.add(_norm(col_name))

    return NATURAL_KEY_COLUMNS.issubset(cols)


def _has_row_number_dedup(tree: exp.Expression) -> bool:
    """Detect ROW_NUMBER() partitioned by natural key AND filtered to = 1."""

    found_partition = False
    found_filter = False

    for window in tree.find_all(exp.Window):
        func = window.this

        if not isinstance(func, exp.RowNumber):
            continue

        partition = window.args.get("partition_by")
        if not partition:
            continue

        cols = set()

        exprs = partition if isinstance(partition, list) else partition.expressions

        for expr in exprs:
            for col in expr.find_all(exp.Column):
                cols.add(_norm(col.name))

        if NATURAL_KEY_COLUMNS.issubset(cols):
            found_partition = True

    # Check filter rn = 1
    for eq in tree.find_all(exp.EQ):
        if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Literal):
            if str(eq.expression.this) == "1":
                found_filter = True

    return found_partition and found_filter


def _is_safe_aggregation(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if aggregation is safe (won't be affected by duplicates)."""

    # Check for low-risk patterns first
    if _is_low_risk_query(tree, aliases):
        return True

    # Check each SELECT for proper grouping/distinct
    for select in tree.find_all(exp.Select):
        # GROUP BY natural key
        if _has_natural_key_grouping(select, aliases):
            continue

        # DISTINCT natural key
        if _distinct_covers_natural_key(select, aliases):
            continue

        return False

    return True


def _is_low_risk_query(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Detect queries with low duplicate risk."""

    # Check if filtering on single person_id (= literal)
    for eq in tree.find_all(exp.EQ):
        if not isinstance(eq.this, exp.Column) or not isinstance(eq.expression, exp.Literal):
            continue

        table, col_name = resolve_table_col(eq.this, aliases)

        if _norm(col_name) == PERSON_ID:
            # Only from measurement or unqualified (when measurement is only table)
            if _norm(table) == TABLE_MEASUREMENT:
                return True
            if not table:
                tables = {_norm(t) for t in aliases.values()}
                if len(tables) == 1 and TABLE_MEASUREMENT in tables:
                    return True

    # Check if using measurement_datetime (implies time precision, less likely to be duplicates)
    for col in tree.find_all(exp.Column):
        table, col_name = resolve_table_col(col, aliases)

        # measurement_datetime in WHERE clause suggests time-level precision
        if _norm(col_name) == "measurement_datetime":
            if _norm(table) == TABLE_MEASUREMENT or (
                not table and TABLE_MEASUREMENT in {_norm(t) for t in aliases.values()}
            ):
                # Check if it's in a filtering context (not just SELECT list)
                parent = col.parent
                while parent:
                    if isinstance(parent, (exp.Where, exp.Join, exp.Between, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
                        return True
                    parent = parent.parent

    return False


# --- Rule ------------------------------------------------------------------


@register
class MeasurementDuplicateDetectionRule(Rule):
    """Detect aggregation without handling measurement duplicates."""

    rule_id = "domain_specific.measurement_duplicate_detection"
    name = "Measurement Duplicate Detection"

    description = (
        "Detects aggregation on measurement data without handling duplicates. "
        "Duplicate measurement records can exist and affect results."
    )

    severity = Severity.WARNING

    suggested_fix = "ADD: GROUP BY (person_id, measurement_concept_id, measurement_date), OR SELECT DISTINCT on those columns, OR deduplicate explicitly with ROW_NUMBER() OVER (PARTITION BY person_id, measurement_concept_id, measurement_date ORDER BY measurement_datetime NULLS LAST) and keep rn = 1."
    example_bad = "SELECT AVG(value_as_number) FROM measurement;"
    example_good = (
        "SELECT person_id, measurement_date, measurement_concept_id,\n"
        "       AVG(value_as_number) AS avg_value\n"
        "FROM measurement\n"
        "GROUP BY person_id, measurement_date, measurement_concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            if not _has_table(tree, TABLE_MEASUREMENT):
                continue

            aliases = extract_aliases(tree)

            # Must have aggregation
            if not _has_aggregation(tree):
                continue

            if not _has_measurement_aggregation(tree, aliases):
                continue

            # Safe cases
            if _is_safe_aggregation(tree, aliases):
                continue

            if _has_row_number_dedup(tree):
                continue

            key = "measurement_duplicate_risk"
            if key in seen:
                continue
            seen.add(key)

            violations.append(
                self.create_violation(
                    message=(
                        "Query aggregates measurement records without handling potential duplicates. "
                        "The measurement table may contain duplicate records for the same "
                        "person/concept/date due to ETL errors or data quality issues. Consider "
                        "grouping by the natural key (person_id, measurement_concept_id, measurement_date) "
                        "or using explicit deduplication logic."
                    ),
                    severity=self.severity,
                    details={
                        "table": "measurement",
                        "natural_key": list(NATURAL_KEY_COLUMNS),
                    },
                )
            )

        return violations


__all__ = ["MeasurementDuplicateDetectionRule"]

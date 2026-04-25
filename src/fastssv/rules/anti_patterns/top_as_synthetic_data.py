"""TOP N for Synthetic Data Generation Rule.

Detects when TOP N (SQL Server) or LIMIT N is used to generate synthetic lookup
data from actual tables, which is fragile and depends on data distribution.

The Problem:
    Using TOP N with ROW_NUMBER() to generate lookup tables is a fragile pattern:

    BAD:
    SELECT TOP 12 ROW_NUMBER() OVER (ORDER BY person_id) AS month
    FROM observation_period

    Issues:
    1. SQL Server-specific (not portable)
    2. Fragile: Depends on having at least N rows in the table
    3. Unpredictable: Results depend on data distribution
    4. Inefficient: Scans actual data to generate constants
    5. Semantically wrong: Using clinical data to generate month numbers

Correct patterns:
    -- PostgreSQL: generate_series
    SELECT generate_series(1, 12) AS month

    -- SQL Server: Recursive CTE or VALUES
    WITH months AS (
        SELECT 1 AS month
        UNION ALL
        SELECT month + 1 FROM months WHERE month < 12
    )
    SELECT month FROM months

    -- Universal: VALUES clause
    SELECT month FROM (VALUES (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12)) AS months(month)
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


def _is_synthetic_data_pattern(select: exp.Select) -> bool:
    """Check if SELECT uses TOP/LIMIT with ROW_NUMBER to generate synthetic data.

    Returns True if:
    - SELECT has TOP or LIMIT
    - SELECT contains ROW_NUMBER() window function
    - FROM clause references actual tables (not VALUES, not CTEs)
    """
    # Check for TOP (SQL Server) or LIMIT
    has_limit = False
    limit_expr = select.args.get("limit")
    if limit_expr:
        has_limit = True

    # SQL Server TOP is in the "hint" field
    hint = select.args.get("hint")
    if hint:
        hint_sql = str(hint).upper()
        if "TOP" in hint_sql:
            has_limit = True

    if not has_limit:
        return False

    # Check if any selected column uses ROW_NUMBER()
    has_row_number = False
    for expr in select.expressions or []:
        # Check for ROW_NUMBER as a Window function (most common)
        for window in expr.find_all(exp.Window):
            if isinstance(window.this, exp.RowNumber):
                has_row_number = True
                break

        # Also check for Anonymous functions (fallback)
        if not has_row_number:
            for func in expr.find_all(exp.Anonymous):
                func_name = ""
                if hasattr(func, 'name'):
                    func_name = normalize_name(func.name)
                elif hasattr(func, 'this') and isinstance(func.this, str):
                    func_name = normalize_name(func.this)

                if func_name == 'row_number':
                    has_row_number = True
                    break

        if has_row_number:
            break

    if not has_row_number:
        return False

    # Check if FROM clause references actual tables (not VALUES or synthetic)
    from_clause = select.args.get("from_")
    if not from_clause or not isinstance(from_clause, exp.From):
        return False

    # Look for actual table references
    for table in from_clause.find_all(exp.Table):
        # If we find a real table name, this is the anti-pattern
        return True

    return False


@register
class TopAsSyntheticDataRule(Rule):
    """Detects fragile use of TOP/LIMIT to generate synthetic lookup data."""

    rule_id = "anti_patterns.top_as_synthetic_data"
    name = "TOP/LIMIT for Synthetic Data Generation"

    description = (
        "Detects when TOP N or LIMIT N is used with ROW_NUMBER() to generate synthetic "
        "lookup data from actual tables. This is fragile, non-portable, and depends on "
        "data distribution. Use explicit VALUES, generate_series(), or recursive CTEs instead."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `SELECT TOP/LIMIT N ROW_NUMBER() OVER (...) FROM <real_table>` WITH a synthetic generator. Examples: `SELECT n FROM (VALUES (1),(2),(3),...,(12)) AS numbers(n)`; OR `SELECT generate_series(1, 12) AS n` (Postgres / DuckDB)."
    long_description = (
        "Using `SELECT TOP N ROW_NUMBER() OVER (...) FROM some_table` (or "
        "the `LIMIT N` equivalent) to generate the integers 1..N from an "
        "arbitrary clinical table is an anti-pattern: the result depends "
        "on the table having at least N rows, it isn't portable across "
        "dialects, and it silently breaks when the reference table "
        "changes size. Use a real constant-generator — an explicit VALUES "
        "list, generate_series() in PostgreSQL, or a recursive CTE — so "
        "the synthetic data is deterministic and dialect-agnostic."
    )
    example_bad = (
        "SELECT TOP 10 ROW_NUMBER() OVER (ORDER BY person_id) AS n\n"
        "FROM person;"
    )
    example_good = (
        "SELECT n\n"
        "FROM (VALUES (1),(2),(3),(4),(5),(6),(7),(8),(9),(10)) AS t(n);"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            # Check all SELECT statements
            for select in tree.find_all(exp.Select):
                if _is_synthetic_data_pattern(select):
                    # Get the limit value if possible
                    limit_value = None
                    limit_expr = select.args.get("limit")
                    if limit_expr:
                        if isinstance(limit_expr, exp.Limit):
                            limit_value = str(limit_expr.expression)

                    # Check for TOP in hint
                    hint = select.args.get("hint")
                    if hint:
                        hint_sql = str(hint).upper()
                        if "TOP" in hint_sql:
                            # Try to extract the number
                            import re
                            match = re.search(r'TOP\s+(\d+)', hint_sql)
                            if match:
                                limit_value = match.group(1)

                    message = (
                        f"Using TOP/LIMIT{(' ' + limit_value) if limit_value else ''} with ROW_NUMBER() "
                        f"to generate synthetic lookup data from actual tables is fragile and non-portable. "
                        f"This depends on table having at least {limit_value or 'N'} rows and assumes data distribution. "
                    )

                    violations.append(self.create_violation(
                        message=message,
                        suggested_fix=(
                            f"Use explicit VALUES clause or generate_series(). "
                            f"Example for {limit_value or 'N'} rows: "
                            f"VALUES (1), (2), ..., ({limit_value or 'N'})"
                        ),
                        details={
                            "limit_value": limit_value,
                            "pattern": "top_with_row_number_from_table",
                        }
                    ))

        return violations


__all__ = ["TopAsSyntheticDataRule"]

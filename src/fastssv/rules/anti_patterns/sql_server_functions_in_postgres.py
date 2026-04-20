"""SQL Server Function Dialect Mismatch Rule.

Detects SQL Server-specific functions used in PostgreSQL dialect queries.
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register

# SQL Server functions and their PostgreSQL equivalents
SQL_SERVER_FUNCTIONS = {
    "getdate": {
        "postgres_alternatives": ["CURRENT_DATE", "NOW()"],
        "description": "getdate() is not valid in Postgres dialect",
        "fix": "Replace getdate() with CURRENT_DATE or NOW()"
    },
    "getutcdate": {
        "postgres_alternatives": ["CURRENT_TIMESTAMP", "NOW() AT TIME ZONE 'UTC'"],
        "description": "getutcdate() is not valid in Postgres dialect",
        "fix": "Replace getutcdate() with CURRENT_TIMESTAMP or NOW() AT TIME ZONE 'UTC'"
    },
    "datediff": {
        "postgres_alternatives": ["DATE_PART()", "EXTRACT()", "AGE()"],
        "description": "DATEDIFF() syntax differs between SQL Server and Postgres",
        "fix": "Use DATE_PART(), EXTRACT(), or AGE() for date arithmetic in Postgres"
    },
    "dateadd": {
        "postgres_alternatives": ["DATE + INTERVAL"],
        "description": "DATEADD() is not valid in Postgres dialect",
        "fix": "Use DATE + INTERVAL syntax in Postgres (e.g., date_column + INTERVAL '30 days')"
    },
    "datefromparts": {
        "postgres_alternatives": ["MAKE_DATE()", "TO_DATE()"],
        "description": "DATEFROMPARTS() is not valid in Postgres dialect",
        "fix": "Use MAKE_DATE(year, month, day) or TO_DATE() in Postgres"
    },
    "isnull": {
        "postgres_alternatives": ["COALESCE()", "NULLIF()"],
        "description": "ISNULL() is not valid in Postgres dialect",
        "fix": "Use COALESCE(column, default_value) in Postgres"
    },
    "len": {
        "postgres_alternatives": ["LENGTH()", "CHAR_LENGTH()"],
        "description": "LEN() is not valid in Postgres dialect",
        "fix": "Use LENGTH() or CHAR_LENGTH() in Postgres"
    },
    "charindex": {
        "postgres_alternatives": ["POSITION()", "STRPOS()"],
        "description": "CHARINDEX() is not valid in Postgres dialect",
        "fix": "Use POSITION(substring IN string) or STRPOS(string, substring) in Postgres"
    },
}


@register
class SQLServerFunctionsInPostgresRule(Rule):
    """Detects SQL Server-specific functions for portability warnings.

    Layer: BEST_PRACTICE
    This rule provides portability warnings, not correctness errors.
    SQL Server functions are valid in TSQL dialect but may limit portability.
    """

    rule_id = "anti_patterns.sql_server_functions_in_postgres"
    name = "SQL Server Functions - Portability Warning"
    description = (
        "Detects SQL Server-specific functions that may limit portability to PostgreSQL. "
        "If you plan to run queries across multiple database platforms, consider using "
        "standard SQL functions or dialect-agnostic alternatives."
    )
    severity = Severity.WARNING  # Changed from ERROR - this is portability, not correctness
    suggested_fix = "For maximum portability, replace SQL Server functions with standard SQL equivalents"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        # Only warn when dialect is postgres (portability issue)
        # When dialect is tsql, these functions are valid
        if dialect != "postgres":
            return []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            # Track which functions we've already reported
            reported_functions = set()

            # Check for typed date expressions (DATEDIFF, DATEFROMPARTS)
            for node_type_name in ['DateDiff', 'DateFromParts']:
                if hasattr(exp, node_type_name):
                    node_type = getattr(exp, node_type_name)
                    for node in tree.find_all(node_type):
                        func_name = node_type_name.lower()
                        # Map to our dictionary key
                        if func_name == 'datefromparts':
                            dict_key = 'datefromparts'
                        else:
                            dict_key = func_name

                        if dict_key in SQL_SERVER_FUNCTIONS and dict_key not in reported_functions:
                            info = SQL_SERVER_FUNCTIONS[dict_key]
                            violations.append(self.create_violation(
                                message=f"Portability: {info['description']}. For cross-platform compatibility, consider PostgreSQL alternatives.",
                                suggested_fix=info["fix"],
                                severity=Severity.WARNING,
                                details={
                                    "function": dict_key,
                                    "postgres_alternatives": info["postgres_alternatives"],
                                    "layer": "best_practice",
                                    "type": "portability_warning"
                                }
                            ))
                            reported_functions.add(dict_key)

            # Find all Anonymous function calls (ISNULL, DATEADD, etc.)
            for func in tree.find_all(exp.Anonymous):
                func_name = func.this.lower() if isinstance(func.this, str) else str(func.this).lower()

                if func_name in SQL_SERVER_FUNCTIONS and func_name not in reported_functions:
                    info = SQL_SERVER_FUNCTIONS[func_name]
                    alternatives = " or ".join(info["postgres_alternatives"])

                    violations.append(self.create_violation(
                        message=f"Portability: {info['description']}. For cross-platform compatibility, consider PostgreSQL alternatives.",
                        suggested_fix=info["fix"],
                        severity=Severity.WARNING,
                        details={
                            "function": func_name,
                            "postgres_alternatives": info["postgres_alternatives"],
                            "layer": "best_practice",
                            "type": "portability_warning"
                        }
                    ))

                    # Mark this function as reported to avoid duplicates
                    reported_functions.add(func_name)

        return violations


__all__ = ["SQLServerFunctionsInPostgresRule"]

"""Destructive Operations on Clinical Tables Rule.

GAP_004: delete_truncate_on_clinical_tables

Clinical event tables and the person table contain patient-level data. Analytical
SQL should be read-only. DELETE, UPDATE, INSERT, TRUNCATE, DROP, and ALTER operations
on these tables should only occur through:
- Controlled ETL pipelines
- Data curation workflows with proper governance
- Administrative DBA operations with authorization

Never through ad-hoc analytical queries!

The Problem:
    Analysts may accidentally run destructive operations on production data:

    DELETE FROM measurement WHERE measurement_date < '2010-01-01'
    -- Just deleted thousands of historical measurements!

    UPDATE condition_occurrence SET condition_end_date = condition_start_date
    -- Modified production patient data without governance!

    TRUNCATE TABLE drug_exposure
    -- Deleted ALL drug exposure records!

    DROP TABLE visit_occurrence
    -- DISASTER! Lost all visit data!

Protected Tables (patient-level data):
    - condition_occurrence
    - drug_exposure
    - procedure_occurrence
    - measurement
    - observation
    - visit_occurrence
    - visit_detail
    - death
    - person

Violation patterns:
    DELETE FROM measurement WHERE person_id = 12345
    UPDATE condition_occurrence SET condition_concept_id = 201826
    INSERT INTO drug_exposure VALUES (...)
    TRUNCATE TABLE visit_occurrence
    DROP TABLE procedure_occurrence
    ALTER TABLE observation ADD COLUMN custom_field VARCHAR(100)

Correct patterns (read-only):
    SELECT * FROM measurement WHERE person_id = 12345
    CREATE TEMP TABLE my_cohort AS SELECT * FROM person
    INSERT INTO my_analysis_table SELECT * FROM condition_occurrence
"""

from typing import List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

PROTECTED_TABLES: Set[str] = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "visit_occurrence",
    "visit_detail",
    "death",
    "person",
}

DESTRUCTIVE_STATEMENT_TYPES = (
    exp.Delete,
    exp.Update,
    exp.Insert,
    exp.TruncateTable,
    exp.Drop,
    exp.Alter,
)


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_protected_table(table_name: Optional[str]) -> bool:
    return _norm(table_name) in PROTECTED_TABLES


def _extract_target_tables(statement: exp.Expression) -> Set[str]:
    """
    Extract target tables for destructive operations.
    Only extracts the table being modified, not tables in subqueries.
    """
    tables: Set[str] = set()

    if isinstance(statement, (exp.Delete, exp.Update)):
        # DELETE FROM table, UPDATE table
        # statement.this is the Table
        if statement.this:
            if isinstance(statement.this, exp.Table):
                tables.add(statement.this.name)
            elif hasattr(statement.this, "name"):
                tables.add(statement.this.name)

    elif isinstance(statement, exp.TruncateTable):
        # TRUNCATE TABLE table
        # Uses expressions list for table(s)
        if statement.expressions:
            for table_expr in statement.expressions:
                if isinstance(table_expr, exp.Table):
                    tables.add(table_expr.name)

    elif isinstance(statement, exp.Insert):
        # INSERT INTO table
        # statement.this is a Schema, Schema.this is the Table
        if statement.this and hasattr(statement.this, "this"):
            table_expr = statement.this.this
            if isinstance(table_expr, exp.Table):
                tables.add(table_expr.name)
            elif hasattr(table_expr, "name"):
                tables.add(table_expr.name)

    elif isinstance(statement, (exp.Drop, exp.Alter)):
        # DROP TABLE table, ALTER TABLE table
        if statement.this:
            if isinstance(statement.this, exp.Table):
                tables.add(statement.this.name)
            elif hasattr(statement.this, "name"):
                tables.add(statement.this.name)

    return tables


def _get_statement_type(statement: exp.Expression) -> str:
    if isinstance(statement, exp.Delete):
        return "DELETE"
    if isinstance(statement, exp.Update):
        return "UPDATE"
    if isinstance(statement, exp.Insert):
        return "INSERT"
    if isinstance(statement, exp.TruncateTable):
        return "TRUNCATE"
    if isinstance(statement, exp.Drop):
        return "DROP"
    if isinstance(statement, exp.Alter):
        return "ALTER TABLE"
    return statement.__class__.__name__.upper()


# --- Rule ------------------------------------------------------------------


@register
class DestructiveOperationsOnClinicalTablesRule(Rule):
    """Prevent destructive operations on clinical event tables."""

    rule_id = "anti_patterns.destructive_operations_on_clinical_tables"
    name = "Destructive Operations on Clinical Tables"

    description = (
        "Detects destructive SQL operations (DELETE, UPDATE, INSERT, TRUNCATE, "
        "DROP, ALTER) on protected clinical tables. Analytical SQL should be read-only."
    )

    severity = Severity.ERROR

    suggested_fix = "REMOVE: DELETE / UPDATE / INSERT / TRUNCATE / DROP / ALTER / MERGE on clinical tables. Analytical queries are SELECT-only; data modifications belong in ETL pipelines."
    long_description = (
        "Analytical SQL against OMOP clinical tables should be strictly "
        "read-only. DELETE, UPDATE, INSERT, TRUNCATE, DROP, and ALTER "
        "statements against person, condition_occurrence, drug_exposure, "
        "etc. either corrupt cohort history or break reproducibility for "
        "anyone else using the same warehouse. Any data modification "
        "belongs in a governed ETL pipeline, not in a cohort-exploration "
        "query."
    )
    example_bad = "DELETE FROM condition_occurrence\nWHERE person_id = 1;"
    example_good = "SELECT *\nFROM condition_occurrence\nWHERE person_id = 1;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            # Ensure root-level detection
            statements = []
            if isinstance(tree, DESTRUCTIVE_STATEMENT_TYPES):
                statements.append(tree)

            statements.extend(tree.find_all(DESTRUCTIVE_STATEMENT_TYPES))

            for stmt in statements:
                target_tables = _extract_target_tables(stmt)

                protected = {t for t in target_tables if _is_protected_table(t)}

                if not protected:
                    continue

                stmt_type = _get_statement_type(stmt)
                tables_str = ", ".join(sorted(protected))

                key = f"{stmt_type}|{tables_str}"
                if key in seen:
                    continue
                seen.add(key)

                violations.append(
                    self.create_violation(
                        message=(
                            f"{stmt_type} operation detected on protected clinical table(s): {tables_str}. "
                            "Clinical data must not be modified via analytical queries."
                        ),
                        severity=self.severity,
                        details={
                            "operation": stmt_type,
                            "protected_tables": list(protected),
                        },
                    )
                )

        return violations


__all__ = ["DestructiveOperationsOnClinicalTablesRule"]

"""Schema Validation Rule.

OMOP CDM schema validation:
Validates that columns referenced in SQL queries exist in the OMOP CDM schema.
Catches common errors like using concept_ancestor columns on concept_relationship table.
"""

from typing import Dict, List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import extract_aliases, parse_sql, resolve_table_col
from fastssv.core.registry import register
from fastssv.schemas import CDM_COLUMNS, get_table_columns


@register
class SchemaValidationRule(Rule):
    """Validates column references against OMOP CDM schema."""

    rule_id = "vocabulary.schema_validation"
    name = "Schema Validation"
    description = (
        "Validates that columns referenced in queries exist in the OMOP CDM schema. "
        "Catches errors like using concept_ancestor columns on concept_relationship."
    )
    severity = Severity.ERROR
    suggested_fix = "Check OMOP CDM documentation for correct column names"

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

            # Track which columns we've already reported to avoid duplicates
            reported: Set[tuple] = set()

            for col in tree.find_all(exp.Column):
                table_name, col_name = resolve_table_col(col, aliases)

                if not table_name:
                    continue

                if table_name not in CDM_COLUMNS:
                    continue

                if (table_name, col_name) in reported:
                    continue

                valid_columns = get_table_columns(table_name)

                if not valid_columns:
                    continue

                if col_name not in valid_columns:
                    reported.add((table_name, col_name))

                    suggestion = self.suggested_fix
                    if table_name == "concept_relationship" and col_name in {"ancestor_concept_id", "descendant_concept_id"}:
                        suggestion = (
                            f"Column '{col_name}' belongs to concept_ancestor table, not concept_relationship. "
                            f"concept_relationship uses concept_id_1 and concept_id_2."
                        )
                    elif table_name == "concept_ancestor" and col_name in {"concept_id_1", "concept_id_2"}:
                        suggestion = (
                            f"Column '{col_name}' belongs to concept_relationship table, not concept_ancestor. "
                            f"concept_ancestor uses ancestor_concept_id and descendant_concept_id."
                        )

                    violations.append(self.create_violation(
                        message=f"Column '{col_name}' does not exist in table '{table_name}'",
                        suggested_fix=suggestion,
                        details={
                            "table": table_name,
                            "column": col_name,
                            "valid_columns": sorted(list(valid_columns))[:10]  # Show first 10
                        }
                    ))

        return violations


__all__ = ["SchemaValidationRule"]

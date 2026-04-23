"""Procedure Occurrence Quantity Semantics Rule.

OMOP semantic rule CLIN_023:
Validates that procedure_occurrence.quantity is not confused with record counts.

CLIN_023 (quantity semantics):
procedure_occurrence.quantity represents the number of times a procedure was performed
in a SINGLE record (e.g., 2 units of physical therapy), NOT the number of procedure
records for a patient.

The Problem:
    quantity is the number of units performed in ONE procedure event.
    It is NOT equivalent to COUNT(*) for counting procedure records.

    Common mistakes:
    - SUM(quantity) aliased as "procedure_count" (suggests counting records)
    - SUM(quantity) AS "number_of_procedures" (implies record count)
    - Using SUM(quantity) when COUNT(*) is intended

Violation patterns:
    SELECT person_id, SUM(quantity) AS procedure_count
    FROM procedure_occurrence
    GROUP BY person_id
    -- "procedure_count" suggests counting records, not summing units

    SELECT SUM(quantity) AS num_procedures
    FROM procedure_occurrence
    -- Should be COUNT(*) to count records

Correct patterns:
    -- To count procedure records:
    SELECT person_id, COUNT(*) AS procedure_count
    FROM procedure_occurrence
    GROUP BY person_id

    -- To sum procedure units (with clear alias):
    SELECT person_id, SUM(quantity) AS total_procedure_units
    FROM procedure_occurrence
    GROUP BY person_id
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

TABLE_NAME = "procedure_occurrence"
COLUMN_NAME = "quantity"

STRONG_COUNT_KEYWORDS = {"count", "cnt"}
WEAK_COUNT_KEYWORDS = {"number", "num", "n_"}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_quantity_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != COLUMN_NAME:
        return False

    if table:
        return _norm(table) == TABLE_NAME

    return TABLE_NAME in {_norm(t) for t in aliases.values()}


def _suggests_counting_records(alias: str) -> bool:
    if not alias:
        return False

    alias_norm = _norm(alias)
    if not alias_norm:
        return False

    # Strong signals
    if any(k in alias_norm for k in STRONG_COUNT_KEYWORDS):
        return True

    # Weak signals only if combined with "procedure"
    if "procedure" in alias_norm:
        if any(k in alias_norm for k in WEAK_COUNT_KEYWORDS):
            return True

    return False


def _get_alias_name(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Alias):
        alias_expr = node.alias
        if hasattr(alias_expr, "name"):
            return alias_expr.name
        return str(alias_expr)
    return None


def _is_direct_sum_of_quantity(sum_func: exp.Sum, aliases: Dict[str, str]) -> bool:
    """
    Ensure SUM is directly applied to quantity (not expressions).
    """
    arg = sum_func.this

    if isinstance(arg, exp.Column):
        return _is_quantity_column(arg, aliases)

    return False


# --- Detection -------------------------------------------------------------

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues: List[str] = []
    seen: Set[str] = set()

    for select in tree.find_all(exp.Select):
        for expr in select.expressions or []:

            alias_name = None
            expr_to_check = expr

            if isinstance(expr, exp.Alias):
                alias_name = _get_alias_name(expr)
                expr_to_check = expr.this

            for sum_func in expr_to_check.find_all(exp.Sum):

                if not _is_direct_sum_of_quantity(sum_func, aliases):
                    continue

                # Only flag when alias strongly suggests counting
                if alias_name and _suggests_counting_records(alias_name):

                    key = f"sum_quantity|{alias_name}"
                    if key in seen:
                        continue
                    seen.add(key)

                    issues.append(
                        f"SUM(quantity) aliased as '{alias_name}' suggests counting records. "
                        f"quantity represents units per procedure event, not record counts. "
                        f"Use COUNT(*) to count records, or rename to reflect units (e.g., 'total_units')."
                    )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class ProcedureOccurrenceQuantitySemanticsRule(Rule):
    """Validate procedure_occurrence.quantity semantics."""

    rule_id = "domain_specific.procedure_occurrence_quantity_semantics"
    name = "Procedure Occurrence Quantity Semantics"

    description = (
        "Ensures procedure_occurrence.quantity is not confused with record counts. "
        "quantity represents units per procedure event, not the number of procedure records."
    )

    severity = Severity.WARNING
    suggested_fix = "Use COUNT(*) to count records, or use clearer aliases like 'total_units'"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, TABLE_NAME):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for message in issues:
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["ProcedureOccurrenceQuantitySemanticsRule"]

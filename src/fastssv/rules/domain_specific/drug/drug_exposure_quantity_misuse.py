"""Drug Exposure Quantity Misuse Rule.

OMOP semantic rule OMOP_055:
drug_exposure.quantity represents the amount dispensed (e.g., 30 tablets),
not the duration of the prescription. Duration should be derived from days_supply
or from drug_exposure_start_date to drug_exposure_end_date.

The Problem:
    Quantity is the NUMBER of units dispensed (pills, ml, etc.), not days.
    Using it in date arithmetic leads to incorrect duration calculations.

    Common mistakes:
    - DATEADD(day, quantity, start_date) -- 30 tablets ≠ 30 days
    - DATEDIFF(day, quantity, end_date)
    - start_date + INTERVAL quantity DAY

Violation pattern:
    SELECT DATEADD(day, quantity, drug_exposure_start_date) AS end_date
    FROM drug_exposure
    -- Assumes 30 tablets = 30 days, which is wrong

Correct pattern:
    SELECT DATEADD(day, days_supply, drug_exposure_start_date) AS end_date
    FROM drug_exposure
    -- Uses the actual supply duration
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

DRUG_EXPOSURE = "drug_exposure"
QUANTITY = "quantity"

DATE_FUNCTIONS = {
    "dateadd",
    "date_add",
    "adddate",
    "datediff",
    "date_diff",
    "timestampdiff",
    "date_sub",
}


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_quantity(col: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if expression refers to drug_exposure.quantity."""
    if isinstance(col, exp.Column):
        table, name = resolve_table_col(col, aliases)
        if _norm(name) != QUANTITY:
            return False

        # qualified
        if _norm(table) == DRUG_EXPOSURE:
            return True

        # unqualified
        if not table:
            return any(_norm(t) == DRUG_EXPOSURE for t in aliases.values())

    # Handle Var nodes (unquoted identifiers in function arguments like DATEDIFF)
    elif isinstance(col, exp.Var):
        if _norm(str(col.this)) == QUANTITY:
            # Check if drug_exposure table is referenced in query
            return any(_norm(t) == DRUG_EXPOSURE for t in aliases.values())

    return False


def _contains_quantity(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if expression contains quantity anywhere (deep search)."""
    # Check Column nodes
    for col in node.find_all(exp.Column):
        if _is_quantity(col, aliases):
            return True
    # Check Var nodes (function arguments)
    for var in node.find_all(exp.Var):
        if _is_quantity(var, aliases):
            return True
    return False


def _is_date_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    """Heuristic: detect date/datetime columns."""
    _, name = resolve_table_col(col, aliases)
    name = _norm(name)
    return name and (name.endswith("_date") or name.endswith("_datetime"))


def _contains_date_column(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    for col in node.find_all(exp.Column):
        if _is_date_column(col, aliases):
            return True
    return False


def _get_function_name(func: exp.Expression) -> str:
    """Robust function name extraction."""
    if isinstance(func, exp.Anonymous):
        return _norm(str(func.this))
    if hasattr(func, "sql_name"):
        return _norm(func.sql_name())
    if hasattr(func, "name"):
        return _norm(func.name)
    return _norm(str(func))


# --- Detection -------------------------------------------------------------


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues = []
    seen: Set[str] = set()
    flagged_nodes: Set[int] = set()  # Track nodes already flagged

    # --- 1. Date functions ---
    for func in tree.find_all(exp.Func):
        func_name = _get_function_name(func)
        if func_name not in DATE_FUNCTIONS:
            continue

        if _contains_quantity(func, aliases):
            key = func.sql()
            if key in seen:
                continue
            seen.add(key)
            flagged_nodes.add(id(func))  # Mark this function as flagged

            issues.append(
                f"drug_exposure.quantity used in {func_name.upper()}(). "
                f"Quantity represents amount dispensed, not duration. "
                f"Use days_supply or date differences instead."
            )

    # --- 2. Date arithmetic (STRICT) ---
    for node in tree.walk():
        if not isinstance(node, (exp.Add, exp.Sub)):
            continue

        # Skip if this node is inside an already-flagged node
        parent = node.parent
        while parent:
            if id(parent) in flagged_nodes:
                break
            parent = parent.parent if hasattr(parent, "parent") else None
        else:
            # Not inside a flagged node, check it
            if _contains_quantity(node, aliases) and _contains_date_column(node, aliases):
                key = node.sql()
                if key not in seen:
                    seen.add(key)
                    flagged_nodes.add(id(node))  # Mark this arithmetic node as flagged
                    issues.append(
                        "drug_exposure.quantity used in date arithmetic. "
                        "Quantity is not duration. Use days_supply instead."
                    )

    # --- 3. INTERVAL usage ---
    for interval in tree.find_all(exp.Interval):
        # Skip if this interval is inside an already-flagged node
        parent = interval.parent
        while parent:
            if id(parent) in flagged_nodes:
                break
            parent = parent.parent if hasattr(parent, "parent") else None
        else:
            # Not inside a flagged node, check it
            if _contains_quantity(interval, aliases):
                key = interval.sql()
                if key not in seen:
                    seen.add(key)
                    issues.append(
                        "drug_exposure.quantity used in INTERVAL expression. Quantity is not duration. Use days_supply."
                    )

    return issues


# --- Rule ------------------------------------------------------------------


@register
class DrugExposureQuantityMisuseRule(Rule):
    """Detects misuse of quantity as duration."""

    rule_id = "domain_specific.drug_exposure_quantity_misuse"
    name = "Drug Exposure Quantity Misuse"
    description = "Detects use of drug_exposure.quantity as duration in date logic."
    severity = Severity.WARNING
    suggested_fix = "REPLACE: `<start_date> + de.quantity` (or quantity-as-duration arithmetic) WITH `de.drug_exposure_start_date + de.days_supply`, OR use date_diff(de.drug_exposure_end_date, de.drug_exposure_start_date)."
    example_bad = "SELECT person_id,\n       drug_exposure_start_date + quantity AS end_date\nFROM drug_exposure;"
    example_good = "SELECT person_id,\n       drug_exposure_start_date + days_supply AS end_date\nFROM drug_exposure;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast filters
        if "quantity" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            # Only relevant if drug_exposure is used
            if not has_table_reference(tree, DRUG_EXPOSURE):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["DrugExposureQuantityMisuseRule"]

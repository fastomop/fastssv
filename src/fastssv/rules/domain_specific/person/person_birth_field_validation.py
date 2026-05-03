"""Person Birth Field Validation Rule.

OMOP semantic rules CLIN_006, CLIN_007, CLIN_008:
Validates that person birth-related fields contain plausible values.

CLIN_006 (year_of_birth): Should be between 1900 and current year
CLIN_007 (month_of_birth): Must be between 1 and 12
CLIN_008 (day_of_birth): Must be between 1 and 31

The Problem:
    Birth fields in the person table should contain plausible values.
    Values outside valid ranges indicate data quality issues or query logic errors.

    Common mistakes:
    - year_of_birth = 1850 (too far in the past)
    - year_of_birth = 2050 (in the future)
    - month_of_birth = 13 (invalid month)
    - day_of_birth = 32 (invalid day)

Violation patterns:
    SELECT * FROM person WHERE year_of_birth = 1850
    -- Year before 1900 is implausible

    SELECT * FROM person WHERE month_of_birth = 13
    -- Months must be 1-12

    SELECT * FROM person WHERE day_of_birth = 32
    -- Days must be 1-31

Correct patterns:
    SELECT * FROM person WHERE year_of_birth BETWEEN 1950 AND 2000
    SELECT * FROM person WHERE month_of_birth = 6
    SELECT * FROM person WHERE day_of_birth = 15
"""

from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Field Configuration ---------------------------------------------------


class FieldConfig:
    """Configuration for birth field validation."""

    def __init__(self, min_val: int, max_val, severity: Severity, field_name: str):
        self.min_val = min_val
        self.max_val = max_val  # int or callable
        self.severity = severity
        self.field_name = field_name

    def get_max_val(self) -> int:
        if callable(self.max_val):
            return self.max_val()
        return self.max_val

    def validate_value(self, value: int) -> Optional[str]:
        max_val = self.get_max_val()

        if value < self.min_val:
            return f"{self.field_name} = {value} is below minimum value {self.min_val}"
        if value > max_val:
            return f"{self.field_name} = {value} is above maximum value {max_val}"
        return None


BIRTH_FIELD_CONFIGS = {
    "year_of_birth": FieldConfig(
        min_val=1900,
        max_val=lambda: datetime.now().year,
        severity=Severity.WARNING,
        field_name="year_of_birth",
    ),
    "month_of_birth": FieldConfig(
        min_val=1,
        max_val=12,
        severity=Severity.ERROR,
        field_name="month_of_birth",
    ),
    "day_of_birth": FieldConfig(
        min_val=1,
        max_val=31,
        severity=Severity.ERROR,
        field_name="day_of_birth",
    ),
}


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_birth_field(col: exp.Column, aliases: Dict[str, str]) -> Optional[str]:
    """Return birth field name if column belongs to person table."""
    table, col_name = resolve_table_col(col, aliases)
    col_norm = _norm(col_name)

    if col_norm not in BIRTH_FIELD_CONFIGS:
        return None

    if table:
        if _norm(table) != "person":
            return None
    else:
        # Allow unqualified column only if person table exists in query
        if "person" not in {_norm(t) for t in aliases.values()}:
            return None

    return col_norm


def _extract_int(node: exp.Expression) -> Optional[int]:
    """Extract integer value from literal (including negative numbers)."""
    if isinstance(node, exp.Neg):
        inner = node.this
        if isinstance(inner, exp.Literal) and inner.is_int:
            try:
                return -int(inner.this)
            except Exception:
                return None
        return None

    if isinstance(node, exp.Literal) and node.is_int:
        try:
            return int(node.this)
        except Exception:
            return None

    return None


def _safe_sql(node: exp.Expression, max_len: int = 50) -> str:
    """Safely stringify SQL node with length cap."""
    try:
        return str(node)[:max_len]
    except Exception:
        return "<expr>"


# --- Detection -------------------------------------------------------------


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, Severity]]:
    issues: List[Tuple[str, str, Severity]] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        # --- Binary comparisons ---
        if isinstance(node, (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                field_name = _is_birth_field(col_node, aliases)
                if not field_name:
                    continue

                value = _extract_int(val_node)
                if value is None:
                    continue

                config = BIRTH_FIELD_CONFIGS[field_name]
                error_msg = config.validate_value(value)

                if error_msg:
                    key = f"{field_name}|{value}|{type(node).__name__}"
                    if key in seen:
                        continue
                    seen.add(key)

                    issues.append((field_name, error_msg, config.severity))

        # --- BETWEEN ---
        elif isinstance(node, exp.Between):
            col_node = node.this
            if not isinstance(col_node, exp.Column):
                continue

            field_name = _is_birth_field(col_node, aliases)
            if not field_name:
                continue

            low = _extract_int(node.args.get("low"))
            high = _extract_int(node.args.get("high"))

            config = BIRTH_FIELD_CONFIGS[field_name]

            # Invalid range check
            if low is not None and high is not None and low > high:
                key = f"{field_name}|between_invalid_range|{low}|{high}"
                if key not in seen:
                    seen.add(key)
                    issues.append(
                        (
                            field_name,
                            f"{field_name} BETWEEN {low} AND {high} has invalid range (low > high)",
                            config.severity,
                        )
                    )

            for bound_val in [low, high]:
                if bound_val is None:
                    continue

                error_msg = config.validate_value(bound_val)
                if error_msg:
                    key = f"{field_name}|between|{bound_val}"
                    if key in seen:
                        continue
                    seen.add(key)

                    issues.append((field_name, error_msg, config.severity))

        # --- IN ---
        elif isinstance(node, exp.In):
            col_node = node.this
            if not isinstance(col_node, exp.Column):
                continue

            field_name = _is_birth_field(col_node, aliases)
            if not field_name:
                continue

            config = BIRTH_FIELD_CONFIGS[field_name]

            invalid_values = []
            non_int_values = []

            for val in node.expressions or []:
                v = _extract_int(val)
                if v is None:
                    non_int_values.append(_safe_sql(val))
                    continue

                error_msg = config.validate_value(v)
                if error_msg:
                    invalid_values.append(v)

            if invalid_values or non_int_values:
                key = f"{field_name}|in|{tuple(sorted(invalid_values))}|{tuple(non_int_values)}"
                if key in seen:
                    continue
                seen.add(key)

                max_val = config.get_max_val()

                parts = []
                if invalid_values:
                    parts.append(f"invalid values: {invalid_values}")
                if non_int_values:
                    parts.append(f"non-integer values: {non_int_values}")

                error_msg = (
                    f"{field_name} IN clause contains {', '.join(parts)}. Valid range is {config.min_val} to {max_val}."
                )

                issues.append((field_name, error_msg, config.severity))

    return issues


# --- Rule ------------------------------------------------------------------


@register
class PersonBirthFieldValidationRule(Rule):
    """Validate person birth fields for plausible values."""

    rule_id = "domain_specific.person_birth_field_validation"
    name = "Person Birth Field Validation"

    description = (
        "Ensures person birth fields (year_of_birth, month_of_birth, day_of_birth) "
        "use plausible values within accepted ranges."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: implausible birth-field literals WITH valid ranges: year_of_birth BETWEEN 1900 AND <current_year>, month_of_birth BETWEEN 1 AND 12, day_of_birth BETWEEN 1 AND 31."
    example_bad = "SELECT person_id FROM person\nWHERE year_of_birth = 1800;"
    example_good = "SELECT person_id FROM person\nWHERE year_of_birth BETWEEN 1900 AND 2024;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for field_name, error_msg, severity in issues:
                config = BIRTH_FIELD_CONFIGS[field_name]
                max_val = config.get_max_val()

                violations.append(
                    self.create_violation(
                        severity=severity,
                        message=error_msg,
                        suggested_fix=(
                            f"REPLACE: implausible `{field_name}` filter literals "
                            f"WITH `{field_name} BETWEEN {config.min_val} AND {max_val}`."
                        ),
                        details={
                            "field": field_name,
                            "min_value": config.min_val,
                            "max_value": max_val,
                        },
                    )
                )

        return violations


__all__ = ["PersonBirthFieldValidationRule"]

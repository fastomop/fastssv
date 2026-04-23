from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


# --- Configuration ---------------------------------------------------------

CLINICAL_EVENT_TABLES_DATES: Dict[str, Set[str]] = {
    "condition_occurrence": {
        "condition_start_date", "condition_start_datetime",
        "condition_end_date", "condition_end_datetime",
    },
    "drug_exposure": {
        "drug_exposure_start_date", "drug_exposure_start_datetime",
        "drug_exposure_end_date", "drug_exposure_end_datetime",
    },
    "procedure_occurrence": {
        "procedure_date", "procedure_datetime",
    },
    "measurement": {
        "measurement_date", "measurement_datetime",
    },
    "observation": {
        "observation_date", "observation_datetime",
    },
    "visit_occurrence": {
        "visit_start_date", "visit_start_datetime",
        "visit_end_date", "visit_end_datetime",
    },
    "visit_detail": {
        "visit_detail_start_date", "visit_detail_start_datetime",
        "visit_detail_end_date", "visit_detail_end_datetime",
    },
    "device_exposure": {
        "device_exposure_start_date", "device_exposure_start_datetime",
        "device_exposure_end_date", "device_exposure_end_datetime",
    },
    "specimen": {
        "specimen_date", "specimen_datetime",
    },
    "note": {
        "note_date", "note_datetime",
    },
    "episode": {
        "episode_start_date", "episode_start_datetime",
        "episode_end_date", "episode_end_datetime",
    },
}

CLINICAL_EVENT_DATE_COLUMNS: Set[str] = set()
for cols in CLINICAL_EVENT_TABLES_DATES.values():
    CLINICAL_EVENT_DATE_COLUMNS.update(cols)


CURRENT_YEAR = datetime.now().year
FAR_FUTURE_THRESHOLD_YEAR = CURRENT_YEAR + 10


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_in_or(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Or):
            return True
        parent = parent.parent
    return False


def _extract_date_literal_year(node: exp.Expression) -> Optional[int]:
    if isinstance(node, exp.Literal):
        date_str = str(node.this).strip("'\"")
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(date_str, fmt).year
            except ValueError:
                continue

    if isinstance(node, (exp.Date, exp.Timestamp)):
        for lit in node.find_all(exp.Literal):
            year = _extract_date_literal_year(lit)
            if year is not None:
                return year

    return None


def _is_current_date(node: exp.Expression) -> bool:
    if isinstance(node, (exp.CurrentDate, exp.CurrentTimestamp)):
        return True

    if isinstance(node, exp.Anonymous):
        name = _norm(node.name if hasattr(node, "name") else str(node.this))
        if name in {"now", "getdate", "sysdate", "current_date", "current_timestamp"}:
            return True

    for sub in node.walk():
        if isinstance(sub, (exp.CurrentDate, exp.CurrentTimestamp)):
            return True

    return False


def _contains_event_date(
    node: exp.Expression,
    aliases: Dict[str, str],
) -> Optional[Tuple[str, str]]:
    """Find clinical event date column inside expression."""
    for col in node.find_all(exp.Column):
        table, col_name = resolve_table_col(col, aliases)
        col_norm = _norm(col_name)

        if col_norm not in CLINICAL_EVENT_DATE_COLUMNS:
            continue

        if table:
            table_norm = _norm(table)
            if (
                table_norm in CLINICAL_EVENT_TABLES_DATES
                and col_norm in CLINICAL_EVENT_TABLES_DATES[table_norm]
            ):
                return table_norm, col_norm
        else:
            # Only allow unqualified if single table
            if len(aliases) == 1:
                table_norm = _norm(next(iter(aliases.values())))
                if table_norm in CLINICAL_EVENT_TABLES_DATES:
                    return table_norm, col_norm

    return None


# --- Detection -------------------------------------------------------------

def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    violations: List[Tuple[str, str, str]] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_or(node):
            continue

        if isinstance(node, (exp.Is, exp.Not)):
            continue

        # --- Comparisons ---
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            # Only check relevant sides based on operator type
            if isinstance(node, (exp.GT, exp.GTE)):
                # col > value or col >= value (col on left, looking for future values)
                pairs = [(node.this, node.expression)]
            else:  # LT or LTE
                # value < col or value <= col (col on right, value is being compared)
                pairs = [(node.expression, node.this)]

            for left, right in pairs:
                info = _contains_event_date(left, aliases)
                if not info:
                    continue

                table, col = info

                # CURRENT_DATE
                if _is_current_date(right):
                    key = f"{node.sql()}_{table}_{col}"
                    if key not in seen:
                        seen.add(key)
                        violations.append((
                            f"{table}.{col} is compared to CURRENT_DATE in a way that implies future dates. "
                            f"This may indicate data quality issues or logic errors.",
                            table,
                            col,
                        ))

                # Far future literal
                year = _extract_date_literal_year(right)
                if year is not None and year > FAR_FUTURE_THRESHOLD_YEAR:
                    key = f"{node.sql()}_{table}_{col}"
                    if key not in seen:
                        seen.add(key)
                        violations.append((
                            f"{table}.{col} is compared to far-future year ({year}). "
                            f"This may indicate data quality issues or logic errors.",
                            table,
                            col,
                        ))

        # --- BETWEEN ---
        elif isinstance(node, exp.Between):
            info = _contains_event_date(node.this, aliases)
            if info:
                table, col = info
                for bound in (node.args.get("low"), node.args.get("high")):
                    year = _extract_date_literal_year(bound)
                    if year and year > FAR_FUTURE_THRESHOLD_YEAR:
                        violations.append((
                            f"{table}.{col} BETWEEN includes far-future year ({year}).",
                            table,
                            col,
                        ))

        # --- IN ---
        elif isinstance(node, exp.In):
            info = _contains_event_date(node.this, aliases)
            if info:
                table, col = info
                for val in node.expressions or []:
                    year = _extract_date_literal_year(val)
                    if year and year > FAR_FUTURE_THRESHOLD_YEAR:
                        violations.append((
                            f"{table}.{col} IN clause includes far-future year ({year}).",
                            table,
                            col,
                        ))

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ClinicalEventDateInFutureValidationRule(Rule):
    """Validate that clinical event dates are not filtered for future dates."""

    rule_id = "temporal.clinical_event_date_in_future_validation"
    name = "Clinical Event Date Should Not Be In Future"

    description = (
        "Detects filtering logic that implies clinical event dates occur in the future. "
        "Future event dates may indicate data quality issues or incorrect query logic."
    )

    severity = Severity.WARNING
    suggested_fix = (
        "Use date filters consistent with past or present events. "
        "Avoid filtering for future clinical dates unless explicitly intended."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not any(
                has_table_reference(tree, t) for t in CLINICAL_EVENT_TABLES_DATES.keys()
            ):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg, table, col in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        details={
                            "table": table,
                            "column": col,
                            "current_year": CURRENT_YEAR,
                            "far_future_threshold": FAR_FUTURE_THRESHOLD_YEAR,
                        },
                    )
                )

        return violations


__all__ = ["ClinicalEventDateInFutureValidationRule"]

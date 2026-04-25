"""Dose-Era Cross-Unit Comparison Rule.

Mirror of ``domain_specific.measurement_cross_unit_comparison`` but for the
``dose_era`` table. Aggregating ``dose_era.dose_value`` (AVG / SUM / MIN /
MAX) without constraining ``unit_concept_id`` mixes incompatible drug
dose units (mg, mcg, IU, mL, mEq, …) and produces a numeric average that
has no clinical interpretation.

Detection pattern:
    Any aggregation on ``dose_era.dose_value`` (qualified or unqualified
    when dose_era is the only table in scope) where the query does not
    restrict ``unit_concept_id`` via WHERE / JOIN ON or GROUP BY.
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    has_table_reference,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


DOSE_ERA = "dose_era"
DOSE_VALUE = "dose_value"
UNIT_CONCEPT_ID = "unit_concept_id"

AGG_TYPES = (exp.Avg, exp.Sum, exp.Min, exp.Max)


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_dose_era_column(col: exp.Column, aliases: Dict[str, str], target_col: str) -> bool:
    """True if ``col`` resolves to ``dose_era.target_col`` (qualified or
    unqualified when dose_era is in scope alone).
    """
    table, col_name = resolve_table_col(col, aliases)
    if _norm(col_name) != target_col:
        return False
    if table:
        return _norm(table) == DOSE_ERA
    real_tables = {_norm(t) for t in aliases.values()}
    return real_tables == {DOSE_ERA}


def _has_dose_value_aggregation(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    for agg in tree.find_all(AGG_TYPES):
        for col in agg.find_all(exp.Column):
            if _is_dose_era_column(col, aliases, DOSE_VALUE):
                return True
    return False


def _has_unit_concept_constraint(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """True if ``dose_era.unit_concept_id`` is filtered (WHERE/JOIN ON) or
    grouped on. Either is sufficient evidence the analyst is aware of the
    unit dimension.
    """
    # Filter / join condition
    for col in tree.find_all(exp.Column):
        if not _is_dose_era_column(col, aliases, UNIT_CONCEPT_ID):
            continue
        if is_in_where_or_join_clause(col):
            return True

    # GROUP BY
    for select in tree.find_all(exp.Select):
        group = select.args.get("group")
        if not group:
            continue
        for col in group.find_all(exp.Column):
            if _is_dose_era_column(col, aliases, UNIT_CONCEPT_ID):
                return True

    return False


@register
class DoseEraCrossUnitComparisonRule(Rule):
    """Warn when dose_era.dose_value is aggregated without unit_concept_id."""

    rule_id = "domain_specific.dose_era_cross_unit_comparison"
    name = "Dose Era Cross-Unit Comparison"

    description = (
        "Aggregating dose_era.dose_value without constraining unit_concept_id "
        "mixes incompatible drug dose units (mg, mcg, IU, mL, …) and produces "
        "meaningless averages."
    )

    severity = Severity.WARNING

    suggested_fix = "ADD: `WHERE de.unit_concept_id = <unit_id>` (single unit), OR GROUP BY de.unit_concept_id so each aggregate row is in one unit. Drug doses live in mg, mcg, IU, mL, mEq, … — averaging across units is meaningless."
    long_description = (
        "``dose_era.dose_value`` is stored alongside ``unit_concept_id``; "
        "the same drug ingredient may have rows in mg, mcg, IU, mL, mEq, "
        "etc. across sites and patients. Computing AVG / SUM / MIN / MAX "
        "without filtering or grouping by unit_concept_id mixes these "
        "into a single number that has no clinical meaning. The same "
        "discipline applies as for ``measurement.value_as_number``: pick "
        "one unit, or report per-unit aggregates."
    )

    example_bad = (
        "SELECT AVG(dose_value) AS avg_dose\n"
        "FROM dose_era\n"
        "WHERE drug_concept_id = 1124300;"
    )
    example_good = (
        "SELECT AVG(dose_value) AS avg_dose_mg\n"
        "FROM dose_era\n"
        "WHERE drug_concept_id = 1124300\n"
        "  AND unit_concept_id = 8576;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if DOSE_ERA not in sql.lower() or DOSE_VALUE not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue
            if not has_table_reference(tree, DOSE_ERA):
                continue

            aliases = extract_aliases(tree)

            if not _has_dose_value_aggregation(tree, aliases):
                continue
            if _has_unit_concept_constraint(tree, aliases):
                continue

            key = "dose_era.dose_value:no_unit_constraint"
            if key in seen:
                continue
            seen.add(key)

            violations.append(
                self.create_violation(
                    message=(
                        "Aggregation on dose_era.dose_value without a "
                        "unit_concept_id constraint. Drug doses live in "
                        "different units (mg, mcg, IU, mL, mEq, …); the "
                        "average across mixed units has no clinical meaning."
                    ),
                    details={
                        "table": DOSE_ERA,
                        "column": DOSE_VALUE,
                    },
                )
            )

        return violations


__all__ = ["DoseEraCrossUnitComparisonRule"]

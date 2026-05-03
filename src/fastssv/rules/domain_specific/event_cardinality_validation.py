"""Event Cardinality Validation Rule.

A person can have many rows in ``observation`` or ``device_exposure``,
and many ``visit_detail`` rows per ``visit_occurrence``. Joining these
to a parent table without explicit aggregation produces fan-out —
multiple rows per (person | visit_occurrence) — which silently inflates
counts and breaks any downstream ``COUNT(*) AS patients`` reasoning.

This rule covers three parallel cases:

- ``person`` joined to ``observation`` → multiple rows per person.
- ``person`` joined to ``device_exposure`` → multiple rows per person.
- ``visit_occurrence`` joined to ``visit_detail`` (without aggregation)
  → multiple rows per visit.

Sibling rules already cover ``condition_occurrence``, ``drug_exposure``,
and ``measurement``; this rule fills the remaining v5.4 gaps in one
parameterized check rather than duplicating each rule per table.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    has_table_reference,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


# (parent_table, child_table, join_column, child_label)
TARGET_PAIRS: List[Tuple[str, str, str, str]] = [
    ("person", "observation", "person_id", "person → observation"),
    ("person", "device_exposure", "person_id", "person → device_exposure"),
    (
        "visit_occurrence",
        "visit_detail",
        "visit_occurrence_id",
        "visit_occurrence → visit_detail",
    ),
]


def _aliases_of(table: str, aliases: Dict[str, str]) -> Set[str]:
    return {a for a, real in aliases.items() if _norm(real) == _norm(table)}


def _has_join_on_column(
    tree: exp.Expression,
    aliases: Dict[str, str],
    parent_aliases: Set[str],
    child_aliases: Set[str],
    join_column: str,
) -> bool:
    """True if there's an equality `<parent>.<col> = <child>.<col>` in the
    query (or the reverse), where <col> matches ``join_column``.
    """
    target = _norm(join_column)

    def _matches(col: exp.Column, expected_aliases: Set[str]) -> bool:
        table, col_name = resolve_table_col(col, aliases)
        if _norm(col_name) != target:
            return False
        if table:
            return _norm(table) in expected_aliases
        return False

    for eq in tree.find_all(exp.EQ):
        left, right = eq.this, eq.expression
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue
        if (_matches(left, parent_aliases) and _matches(right, child_aliases)) or (
            _matches(left, child_aliases) and _matches(right, parent_aliases)
        ):
            return True
    return False


def _has_aggregation(select: exp.Select) -> bool:
    if select.args.get("group"):
        return True
    if select.args.get("distinct"):
        return True
    AGG = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
    return any(isinstance(node, AGG) for node in select.expressions)


@register
class EventCardinalityValidationRule(Rule):
    """Warn about fan-out from person→observation and visit_occurrence→visit_detail."""

    rule_id = "domain_specific.event_cardinality_validation"
    name = "Event Cardinality Risk"

    description = (
        "Joining person to observation, or visit_occurrence to visit_detail, without "
        "aggregation produces multiple rows per parent record. Silent fan-out distorts "
        "downstream counts."
    )

    severity = Severity.WARNING

    suggested_fix = "ADD: GROUP BY on the parent's key (person_id or visit_occurrence_id), OR SELECT DISTINCT, OR explicit aggregation (COUNT(DISTINCT person_id), MAX(...)) when joining person → observation / device_exposure or visit_occurrence → visit_detail."
    long_description = (
        "OMOP allows multiple ``observation`` rows per person (a single visit can "
        "produce dozens of observations) and multiple ``visit_detail`` rows per "
        "``visit_occurrence`` (different units, transfers, sub-encounters). Plain "
        "joins from the parent table to either of these without aggregation produce "
        "row-level fan-out: a downstream ``COUNT(*) AS patients`` returns "
        "observation rows, not patients; ``COUNT(*) AS visits`` returns visit-detail "
        "rows, not visits. Use GROUP BY on the parent key, ``DISTINCT``, or explicit "
        "aggregation. Sibling rules already enforce this pattern for "
        "``condition_occurrence``, ``drug_exposure``, and ``measurement``."
    )

    example_bad = (
        "SELECT p.person_id, o.observation_concept_id\nFROM person p\nJOIN observation o ON p.person_id = o.person_id;"
    )
    example_good = (
        "SELECT p.person_id, COUNT(DISTINCT o.observation_concept_id) AS n_obs\n"
        "FROM person p\n"
        "JOIN observation o ON p.person_id = o.person_id\n"
        "GROUP BY p.person_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        # Fast pre-filter: at least one (parent, child) pair must be mentioned
        if not any(parent in sql_lower and child in sql_lower for parent, child, _, _ in TARGET_PAIRS):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            for parent, child, join_col, label in TARGET_PAIRS:
                if not (has_table_reference(tree, parent) and has_table_reference(tree, child)):
                    continue

                parent_aliases = _aliases_of(parent, aliases)
                child_aliases = _aliases_of(child, aliases)
                if not parent_aliases or not child_aliases:
                    continue

                if not _has_join_on_column(tree, aliases, parent_aliases, child_aliases, join_col):
                    continue

                # Any SELECT in this tree has aggregation? Then OK.
                # If at least one SELECT lacks aggregation, the pattern fires.
                un_aggregated = False
                for select in tree.find_all(exp.Select):
                    if not _has_aggregation(select):
                        un_aggregated = True
                        break
                if not un_aggregated:
                    continue

                key = label
                if key in seen:
                    continue
                seen.add(key)

                violations.append(
                    self.create_violation(
                        message=(
                            f"Query joins {label} on {join_col} without aggregation. "
                            f"This produces multiple {child} rows per {parent} record; "
                            f"downstream counts will reflect {child} rows, not {parent} rows."
                        ),
                        details={
                            "parent_table": parent,
                            "child_table": child,
                            "join_column": join_col,
                        },
                    )
                )

        return violations


__all__ = ["EventCardinalityValidationRule"]

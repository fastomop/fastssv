"""Cost Table Domain Validation Rule.

OMOP semantic rule OMOP_038:
The cost table is domain-agnostic (polymorphic). To link cost records to specific
clinical events, cost.cost_event_id must be joined with the appropriate clinical
table's primary key, AND cost.cost_domain_id must match the domain.

The Problem:
    Without the domain filter, a join can produce incorrect results because
    cost_event_id is a polymorphic foreign key that can reference different tables.

    Example: drug_exposure_id = 123 and procedure_occurrence_id = 123 are DIFFERENT
    events, but without cost_domain_id filter, both would match cost_event_id = 123.

Violation pattern:
    SELECT * FROM cost c
    JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
    -- Missing: WHERE c.cost_domain_id = 'Drug'

Correct pattern:
    SELECT * FROM cost c
    JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id
    WHERE c.cost_domain_id = 'Drug'
"""

from typing import Dict, List, Set, Tuple, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

COST = "cost"
COST_EVENT_ID = "cost_event_id"
COST_DOMAIN_ID = "cost_domain_id"

CLINICAL_TABLE_TO_DOMAIN = {
    "drug_exposure": "drug",
    "procedure_occurrence": "procedure",
    "condition_occurrence": "condition",
    "measurement": "measurement",
    "observation": "observation",
    "device_exposure": "device",
    "visit_occurrence": "visit",
    "specimen": "specimen",
}

CLINICAL_TABLE_PK = {
    "drug_exposure": "drug_exposure_id",
    "procedure_occurrence": "procedure_occurrence_id",
    "condition_occurrence": "condition_occurrence_id",
    "measurement": "measurement_id",
    "observation": "observation_id",
    "device_exposure": "device_exposure_id",
    "visit_occurrence": "visit_occurrence_id",
    "specimen": "specimen_id",
}


# --- Helpers ---------------------------------------------------------------

def _is_cost(table: Optional[str]) -> bool:
    return table and normalize_name(table) == COST


def _is_cost_event(col: Optional[str]) -> bool:
    return col and normalize_name(col) == COST_EVENT_ID


def _get_clinical_info(table: Optional[str]) -> Optional[Tuple[str, str]]:
    if not table:
        return None
    t = normalize_name(table)
    if t in CLINICAL_TABLE_TO_DOMAIN:
        return CLINICAL_TABLE_TO_DOMAIN[t], CLINICAL_TABLE_PK[t]
    return None


def _extract_cost_aliases(aliases: Dict[str, str]) -> Set[str]:
    """Return aliases that map to cost table."""
    return {
        alias for alias, table in aliases.items()
        if normalize_name(table) == COST
    }


def _match_cost_join(eq: exp.EQ, aliases: Dict[str, str]):
    """Detect cost ↔ clinical join and return (cost_alias, expected_domain)."""
    left, right = eq.this, eq.expression

    if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
        return None

    lt, lc = resolve_table_col(left, aliases)
    rt, rc = resolve_table_col(right, aliases)

    # cost → clinical
    if _is_cost(lt) and _is_cost_event(lc):
        info = _get_clinical_info(rt)
        if info and normalize_name(rc) == normalize_name(info[1]):
            return (lt, info[0])

    # clinical → cost
    if _is_cost(rt) and _is_cost_event(rc):
        info = _get_clinical_info(lt)
        if info and normalize_name(lc) == normalize_name(info[1]):
            return (rt, info[0])

    return None


def _collect_domain_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cost_aliases: Set[str],
) -> Dict[str, Set[str]]:
    """
    Map cost alias → set of domain filters.
    """
    result: Dict[str, Set[str]] = {}

    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.In)):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        right = getattr(node, "expression", None)

        for col, val in [(left, right), (right, left)]:
            if not isinstance(col, exp.Column):
                continue

            table, column = resolve_table_col(col, aliases)

            # Must be cost_domain_id column
            if normalize_name(column) != COST_DOMAIN_ID:
                continue

            # Handle qualified: must be cost table
            if table and not _is_cost(table):
                continue

            # Handle unqualified: only if cost table exists
            if not table and not cost_aliases:
                continue

            # Use table if qualified, otherwise use normalized 'cost'
            alias = normalize_name(table) if table else COST

            result.setdefault(alias, set())

            # EQ
            if isinstance(node, exp.EQ) and isinstance(val, exp.Literal):
                result[alias].add(normalize_name(val.this))

            # IN
            if isinstance(node, exp.In):
                for v in node.expressions or []:
                    if isinstance(v, exp.Literal):
                        result[alias].add(normalize_name(v.this))

    return result


# --- Core Logic ------------------------------------------------------------

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues = []
    seen = set()

    cost_joins: List[Tuple[str, str]] = []

    # Find joins
    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        # ensure inside JOIN
        parent = eq.parent
        in_join = False
        while parent:
            if isinstance(parent, exp.Join):
                in_join = True
                break
            parent = parent.parent
        if not in_join:
            continue

        match = _match_cost_join(eq, aliases)
        if match:
            cost_joins.append(match)

    if not cost_joins:
        return []

    cost_aliases = _extract_cost_aliases(aliases)
    domain_filters = _collect_domain_filters(tree, aliases, cost_aliases)

    for cost_alias, expected in cost_joins:
        alias = normalize_name(cost_alias)
        expected_norm = normalize_name(expected)

        filters = domain_filters.get(alias, set())

        key = f"{alias}:{expected_norm}"
        if key in seen:
            continue
        seen.add(key)

        # No filter
        if not filters:
            issues.append(
                f"Missing cost_domain_id filter for cost join (expected '{expected}'). "
                f"Add: {alias}.cost_domain_id = '{expected}'"
            )
            continue

        # Wrong domain
        if expected_norm not in filters:
            actual = ", ".join(sorted(filters))
            issues.append(
                f"Cost domain mismatch for alias '{alias}': expected '{expected}', "
                f"found ({actual})"
            )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class CostTableDomainValidationRule(Rule):
    """Robust cost domain validation."""

    rule_id = "joins.cost_table_domain_validation"
    name = "Cost Table Domain Validation"
    description = (
        "Ensures cost joins use correct cost_domain_id to disambiguate polymorphic keys."
    )
    severity = Severity.WARNING  # Changed from ERROR to WARNING
    suggested_fix = (
        "Add cost.cost_domain_id = '<domain>' matching the joined clinical table."
    )
    example_bad = (
        "SELECT * FROM cost c\n"
        "JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id;"
    )
    example_good = (
        "SELECT * FROM cost c\n"
        "JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id\n"
        "WHERE c.cost_domain_id = 'Drug';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["CostTableDomainValidationRule"]

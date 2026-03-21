"""Provider Join Validation Rule.

OMOP semantic rule JOIN_001:
Clinical event tables must join to provider via provider_id on both sides.
Joining on person_id, care_site_id, or any other column is incorrect.

The Problem:
    Clinical tables have provider_id (foreign key to provider.provider_id).
    Joining on other columns (e.g., person_id to provider_id) is semantically
    incorrect and produces wrong results.

Violation pattern:
    SELECT * FROM condition_occurrence co
    JOIN provider p ON co.person_id = p.provider_id
    -- Wrong: person_id ≠ provider_id (different identifiers!)

Correct pattern:
    SELECT * FROM condition_occurrence co
    JOIN provider p ON co.provider_id = p.provider_id
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

PROVIDER = "provider"
PROVIDER_ID = "provider_id"

CLINICAL_TABLES = {
    "visit_occurrence",
    "visit_detail",
    "condition_occurrence",
    "procedure_occurrence",
    "drug_exposure",
    "device_exposure",
    "measurement",
    "observation",
    "note",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_provider(table: Optional[str]) -> bool:
    return _norm(table) == PROVIDER


def _is_clinical(table: Optional[str]) -> bool:
    return _norm(table) in CLINICAL_TABLES


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_join_conditions(join: exp.Join) -> List[exp.Expression]:
    """Extract equality conditions from JOIN ON clause."""
    on_clause = join.args.get("on")
    if not on_clause:
        return []
    return list(on_clause.find_all(exp.EQ))


# --- Detection -------------------------------------------------------------

def _check_provider_join(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """
    Detect incorrect joins between clinical tables and provider.

    Returns:
        List of (clinical_table, clinical_col, provider_table, provider_col)
    """
    violations: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    for join in tree.find_all(exp.Join):
        conditions = _extract_join_conditions(join)

        has_correct_join = False
        incorrect_pairs: List[Tuple[str, str, str, str]] = []

        for eq in conditions:
            left, right = eq.this, eq.expression

            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue

            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            if not (lt and lc and rt and rc):
                continue

            # Check both directions
            for (t1, c1, t2, c2) in [
                (lt, lc, rt, rc),
                (rt, rc, lt, lc),
            ]:
                if _is_clinical(t1) and _is_provider(t2):
                    if _is_col(c1, PROVIDER_ID) and _is_col(c2, PROVIDER_ID):
                        has_correct_join = True
                    else:
                        key = (t1, c1, t2, c2)
                        if key not in seen:
                            incorrect_pairs.append(key)
                            seen.add(key)

        # Only report violations if we found incorrect joins and no correct join
        if incorrect_pairs and not has_correct_join:
            violations.extend(incorrect_pairs)

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ProviderJoinValidationRule(Rule):
    """Validate that clinical tables join to provider via provider_id."""

    rule_id = "joins.provider_join_validation"
    name = "Provider Join Validation"

    description = (
        "Clinical event tables must join to the provider table via provider_id. "
        "Joining on person_id, care_site_id, or other columns is incorrect."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Join clinical tables to provider using provider_id: "
        "clinical_table.provider_id = provider.provider_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-filter
        if "provider" not in sql.lower():
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not uses_table(tree, PROVIDER):
                continue

            aliases = extract_aliases(tree)
            bad_joins = _check_provider_join(tree, aliases)

            for clinical_table, clinical_col, provider_table, provider_col in bad_joins:
                violations.append(
                    self.create_violation(
                        message=(
                            f"Incorrect join between {clinical_table} and {provider_table}. "
                            f"Found {clinical_table}.{clinical_col} = {provider_table}.{provider_col}. "
                            f"Expected provider_id = provider_id."
                        ),
                        suggested_fix=self.suggested_fix,
                        details={
                            "clinical_table": clinical_table,
                            "clinical_column": clinical_col,
                            "provider_table": provider_table,
                            "provider_column": provider_col,
                            "expected": f"{clinical_table}.provider_id = {provider_table}.provider_id",
                        },
                    )
                )

        return violations


__all__ = ["ProviderJoinValidationRule"]

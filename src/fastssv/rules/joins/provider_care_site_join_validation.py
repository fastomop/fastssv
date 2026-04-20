"""Provider to Care Site Join Validation Rule.

OMOP semantic rule JOIN_005:
provider joins to care_site via provider.care_site_id = care_site.care_site_id.
Joining on provider_id, specialty_concept_id, or any other column is incorrect.

The Problem:
    provider has care_site_id (foreign key to care_site.care_site_id) to identify
    the provider's practice location. Joining on other columns (e.g., provider_id
    to care_site_id) is semantically incorrect and produces wrong results.

Violation pattern:
    SELECT * FROM provider p
    JOIN care_site cs ON p.provider_id = cs.care_site_id
    -- Wrong: should use p.care_site_id, not p.provider_id

Correct pattern:
    SELECT * FROM provider p
    JOIN care_site cs ON p.care_site_id = cs.care_site_id
"""

from typing import Dict, List, Optional, Set, Tuple

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

PROVIDER = "provider"
CARE_SITE = "care_site"
CARE_SITE_ID = "care_site_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: str) -> str:
    """Normalize table name and strip schema if present."""
    return _norm(name.split(".")[-1])


def _is_provider(table: Optional[str]) -> bool:
    return _norm(table) == PROVIDER


def _is_care_site(table: Optional[str]) -> bool:
    return _norm(table) == CARE_SITE


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_join_conditions(join: exp.Join) -> List[exp.Expression]:
    """Extract equality conditions from JOIN ON clause."""
    on_clause = join.args.get("on")
    if not on_clause:
        return []
    return list(on_clause.find_all(exp.EQ))


# --- Detection -------------------------------------------------------------

def _check_provider_care_site_join(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """
    Detect incorrect joins between provider and care_site.

    Returns:
        List of (provider_table, provider_col, care_site_table, care_site_col)
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

            lt = _normalize_table(lt)
            rt = _normalize_table(rt)

            # Check both directions
            for (t1, c1, t2, c2) in [
                (lt, lc, rt, rc),
                (rt, rc, lt, lc),
            ]:
                if _is_provider(t1) and _is_care_site(t2):
                    if _is_col(c1, CARE_SITE_ID) and _is_col(c2, CARE_SITE_ID):
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
class ProviderCareSiteJoinValidationRule(Rule):
    """Validate that provider joins to care_site via care_site_id."""

    rule_id = "joins.provider_care_site_join_validation"
    name = "Provider to Care Site Join Validation"

    description = (
        "provider must join to care_site via care_site_id to get practice location. "
        "Joining on provider_id, specialty_concept_id, or other columns is incorrect."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Join provider to care_site using care_site_id: "
        "provider.care_site_id = care_site.care_site_id"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-filter
        sql_lower = sql.lower()
        if "provider" not in sql_lower or "care_site" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            # Must have both tables
            if not (has_table_reference(tree, PROVIDER) and has_table_reference(tree, CARE_SITE)):
                continue

            aliases = extract_aliases(tree)
            bad_joins = _check_provider_care_site_join(tree, aliases)

            for provider_table, provider_col, care_site_table, care_site_col in bad_joins:
                violations.append(
                    self.create_violation(
                        message=(
                            f"Incorrect join between {provider_table} and {care_site_table}. "
                            f"Found {provider_table}.{provider_col} = {care_site_table}.{care_site_col}. "
                            f"Expected care_site_id = care_site_id."
                        ),
                        suggested_fix=self.suggested_fix,
                        details={
                            "provider_table": provider_table,
                            "provider_column": provider_col,
                            "care_site_table": care_site_table,
                            "care_site_column": care_site_col,
                            "expected": f"{provider_table}.care_site_id = {care_site_table}.care_site_id",
                        },
                    )
                )

        return violations


__all__ = ["ProviderCareSiteJoinValidationRule"]

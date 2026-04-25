"""Union Concept ID Domain Indicator Rule.

OMOP semantic rule OMOP_067:
UNION queries should not combine concept_id columns from different domains without a domain
indicator. Mixing condition_concept_id and drug_concept_id in a single column without context
makes results uninterpretable.

The Problem:
    Each OMOP domain has its own concept_id column:
    - condition_occurrence.condition_concept_id
    - drug_exposure.drug_concept_id
    - procedure_occurrence.procedure_concept_id
    - measurement.measurement_concept_id
    - observation.observation_concept_id

    UNION queries that mix these without domain labels create ambiguous results.

Example impact:
    SELECT condition_concept_id AS concept_id
    FROM condition_occurrence
    UNION ALL
    SELECT drug_concept_id AS concept_id
    FROM drug_exposure
    -- Returns: [201826, 1545958, 313217, ...]
    -- Which are conditions? Which are drugs? UNKNOWN!
    -- Results are uninterpretable without domain context

Violation pattern:
    SELECT condition_concept_id AS concept_id
    FROM condition_occurrence
    UNION ALL
    SELECT drug_concept_id AS concept_id
    FROM drug_exposure
    -- No domain indicator!

Correct patterns:
    -- Option 1: Add literal domain column
    SELECT 'Condition' AS domain, condition_concept_id AS concept_id
    FROM condition_occurrence
    UNION ALL
    SELECT 'Drug' AS domain, drug_concept_id AS concept_id
    FROM drug_exposure

    -- Option 2: Use domain_id from concept table
    SELECT c.domain_id, co.condition_concept_id AS concept_id
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    UNION ALL
    SELECT c.domain_id, de.drug_concept_id AS concept_id
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

TABLE_TO_DOMAIN = {
    "condition_occurrence": "condition",
    "drug_exposure": "drug",
    "procedure_occurrence": "procedure",
    "measurement": "measurement",
    "observation": "observation",
    "device_exposure": "device",
}

VALID_DOMAIN_LITERALS = set(TABLE_TO_DOMAIN.values())


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


# 1. Flatten UNION chains properly
def _collect_union_selects(node: exp.Expression) -> List[exp.Select]:
    selects: List[exp.Select] = []

    def _walk(n: exp.Expression):
        if isinstance(n, exp.Union):
            _walk(n.this)
            _walk(n.expression)
        elif isinstance(n, exp.Select):
            selects.append(n)

    _walk(node)
    return selects


# 2. Extract concept_id columns WITH table lineage
def _get_concept_id_domains(
    select: exp.Select,
    aliases: Dict[str, str],
) -> Set[str]:
    domains: Set[str] = set()

    for expr in select.expressions:
        col = None

        if isinstance(expr, exp.Alias):
            if isinstance(expr.this, exp.Column):
                col = expr.this
        elif isinstance(expr, exp.Column):
            col = expr

        if not col:
            continue

        col_name = _norm(col.name)
        if not col_name or not col_name.endswith("_concept_id"):
            continue

        table, _ = resolve_table_col(col, aliases)
        table = _norm(table)

        # If qualified, use the table name
        if table and table in TABLE_TO_DOMAIN:
            domains.add(TABLE_TO_DOMAIN[table])
        # If unqualified, infer from tables in aliases
        elif not table:
            # Check tables referenced in this SELECT
            for alias_table in aliases.values():
                norm_table = _norm(alias_table)
                if norm_table in TABLE_TO_DOMAIN:
                    # Infer domain from column name matching table
                    # e.g., condition_concept_id → condition_occurrence
                    expected_prefix = col_name.replace("_concept_id", "")
                    if expected_prefix in norm_table:
                        domains.add(TABLE_TO_DOMAIN[norm_table])

    return domains


# 3. Strong domain indicator detection
def _has_domain_indicator(select: exp.Select) -> bool:
    for expr in select.expressions:
        # Case 1: explicit domain column
        if isinstance(expr, exp.Column):
            if _norm(expr.name) in {"domain", "domain_id"}:
                return True

        # Case 2: aliased literal with domain meaning
        if isinstance(expr, exp.Alias):
            alias_name = _norm(expr.alias)

            if isinstance(expr.this, exp.Literal):
                value = _norm(str(expr.this.this))

                # Must match known domain values
                if value in VALID_DOMAIN_LITERALS:
                    return True

                # OR alias explicitly says domain
                if alias_name in {"domain", "domain_id"}:
                    return True

    return False


# 4. Core detection
def _find_violations(tree: exp.Expression) -> List[str]:
    issues: List[str] = []
    seen: Set[str] = set()

    for union in tree.find_all(exp.Union):
        # Only process top-level UNIONs (skip nested ones to avoid duplicates)
        parent = union.parent
        is_nested = False
        while parent:
            if isinstance(parent, exp.Union):
                is_nested = True
                break
            parent = parent.parent if hasattr(parent, 'parent') else None

        if is_nested:
            continue

        selects = _collect_union_selects(union)

        if len(selects) < 2:
            continue

        all_domains: Set[str] = set()

        for select in selects:
            aliases = extract_aliases(select)
            domains = _get_concept_id_domains(select, aliases)
            all_domains.update(domains)

        # No concept_id usage → skip
        if not all_domains:
            continue

        # Single domain → OK
        if len(all_domains) <= 1:
            continue

        # Check for domain indicator
        has_indicator = any(_has_domain_indicator(s) for s in selects)

        if has_indicator:
            continue

        key = tuple(sorted(all_domains))
        if key in seen:
            continue
        seen.add(key)

        domains_str = ", ".join(sorted(d.capitalize() for d in all_domains))

        issues.append(
            f"UNION combines concept_id columns from multiple domains ({domains_str}) "
            f"without a domain indicator column. This makes concept_id values ambiguous."
        )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class UnionConceptIdDomainIndicatorRule(Rule):
    """Validates domain disambiguation in UNION queries with concept_id."""

    rule_id = "data_quality.union_concept_id_domain_indicator"
    name = "Union Concept ID Domain Indicator"
    description = (
        "UNION queries combining concept_id values from multiple domains must include "
        "a domain indicator column to avoid ambiguity."
    )
    severity = Severity.WARNING
    suggested_fix = "ADD: a literal domain indicator column to each branch of the UNION (e.g. `SELECT '<domain>' AS domain, <table>.<x>_concept_id ...`). Without it, identical concept_ids from different domains collapse."
    long_description = (
        "UNION-ing concept_id columns from multiple domains into a single "
        "output column loses the semantic difference — the downstream "
        "consumer cannot tell whether a given concept_id came from the "
        "Condition or Drug side. Always add an explicit domain-indicator "
        "column so each row's origin is preserved."
    )
    example_bad = (
        "SELECT condition_concept_id AS concept_id FROM condition_occurrence\n"
        "UNION\n"
        "SELECT drug_concept_id AS concept_id FROM drug_exposure;"
    )
    example_good = (
        "SELECT 'Condition' AS domain, condition_concept_id AS concept_id FROM condition_occurrence\n"
        "UNION ALL\n"
        "SELECT 'Drug'      AS domain, drug_concept_id      AS concept_id FROM drug_exposure;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()

        # Fast pre-check
        if "union" not in sql_lower or "_concept_id" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            issues = _find_violations(tree)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["UnionConceptIdDomainIndicatorRule"]

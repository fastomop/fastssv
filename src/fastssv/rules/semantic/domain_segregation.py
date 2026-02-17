"""Domain Segregation Rule.

OMOP semantic rule:
Each clinical table is tied to a specific OMOP domain. When a query joins a
clinical table to the concept table, the domain_id filter on the concept table
must match that clinical table's expected domain.

Examples of correct usage:
  - condition_occurrence.condition_concept_id → domain_id = 'Condition'
  - drug_exposure.drug_concept_id            → domain_id = 'Drug'
  - procedure_occurrence.procedure_concept_id → domain_id = 'Procedure'
  - measurement.measurement_concept_id       → domain_id = 'Measurement'
  - observation.observation_concept_id       → domain_id = 'Observation'
  - device_exposure.device_concept_id        → domain_id = 'Device'
  - visit_occurrence.visit_concept_id        → domain_id = 'Visit'
  - specimen.specimen_concept_id             → domain_id = 'Specimen'
  - death.cause_concept_id                   → domain_id = 'Condition'

Two violation levels:
  - ERROR:   domain_id filter is present but specifies the wrong domain.
  - WARNING: concept table is joined without any domain_id filter at all.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register

# Maps (clinical_table, primary_concept_column) -> expected OMOP domain_id value (lowercase)
CLINICAL_TABLE_DOMAIN: Dict[Tuple[str, str], str] = {
    ("condition_occurrence", "condition_concept_id"): "condition",
    ("drug_exposure", "drug_concept_id"): "drug",
    ("procedure_occurrence", "procedure_concept_id"): "procedure",
    ("measurement", "measurement_concept_id"): "measurement",
    ("observation", "observation_concept_id"): "observation",
    ("device_exposure", "device_concept_id"): "device",
    ("visit_occurrence", "visit_concept_id"): "visit",
    ("specimen", "specimen_concept_id"): "specimen",
    ("death", "cause_concept_id"): "condition",
}


def _find_concept_joins_with_aliases(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, Optional[str]]]:
    """Find clinical table → concept table joins, capturing the concept alias.

    Scans all JOIN ON equality conditions for the pattern:
        clinical_table.some_concept_id = concept_alias.concept_id
    (or reversed).

    Returns a list of (clinical_table, concept_column, concept_alias) triples.
    concept_alias is the qualifier used for the concept table in this join (may be
    None for unqualified references).
    """
    results: List[Tuple[str, str, Optional[str]]] = []

    for eq in tree.find_all(exp.EQ):
        # Only consider conditions inside JOIN ON clauses
        parent = eq.parent
        in_join = False
        while parent:
            if isinstance(parent, exp.Join):
                in_join = True
                break
            parent = parent.parent
        if not in_join:
            continue

        left, right = eq.left, eq.right
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        left_qualifier = normalize_name(left.table) if left.table else None
        right_qualifier = normalize_name(right.table) if right.table else None

        # Pattern: clinical_table.xxx_concept_id = concept.concept_id
        if rt == "concept" and rc == "concept_id" and lc.endswith("_concept_id"):
            results.append((lt, lc, right_qualifier))
        # Pattern: concept.concept_id = clinical_table.xxx_concept_id
        elif lt == "concept" and lc == "concept_id" and rc.endswith("_concept_id"):
            results.append((rt, rc, left_qualifier))

    return results


def _get_domain_id_values_for_alias(
    tree: exp.Expression,
    concept_alias: Optional[str],
) -> Set[str]:
    """Extract domain_id filter values scoped to a specific concept table alias.

    Recognizes:
      - concept_alias.domain_id = 'Value'  (or unqualified domain_id = 'Value')
      - concept_alias.domain_id IN ('Value1', 'Value2', ...)

    Returns a set of normalized (lowercase) domain values.
    """
    values: Set[str] = set()

    def _alias_matches(col: exp.Column) -> bool:
        col_qualifier = normalize_name(col.table) if col.table else None
        # Accept both the specific alias and unqualified references
        return col_qualifier is None or col_qualifier == concept_alias

    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue
        left, right = eq.left, eq.right

        for col, val in [(left, right), (right, left)]:
            if not (isinstance(col, exp.Column) and is_string_literal(val)):
                continue
            if normalize_name(col.name) != "domain_id":
                continue
            if _alias_matches(col):
                values.add(normalize_name(val.this))

    for in_expr in tree.find_all(exp.In):
        if not is_in_where_or_join_clause(in_expr):
            continue
        col = in_expr.this
        if not isinstance(col, exp.Column):
            continue
        if normalize_name(col.name) != "domain_id":
            continue
        if not _alias_matches(col):
            continue
        for val in (in_expr.expressions or []):
            if is_string_literal(val):
                values.add(normalize_name(val.this))

    return values


@register
class DomainSegregationRule(Rule):
    """Ensures clinical table concept joins use the correct OMOP domain."""

    rule_id = "semantic.domain_segregation"
    name = "Domain Segregation"
    description = (
        "Ensures that when a clinical table is joined to the concept table, "
        "the domain_id filter matches the expected OMOP domain for that table. "
        "For example, condition_occurrence.condition_concept_id should only "
        "reference concepts with domain_id = 'Condition'."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Add or correct the domain_id filter on the concept table to match "
        "the expected domain for the clinical table being queried."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations: List[RuleViolation] = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            if not uses_table(tree, "concept"):
                continue

            aliases = extract_aliases(tree)
            concept_joins = _find_concept_joins_with_aliases(tree, aliases)

            for clinical_table, concept_col, concept_alias in concept_joins:
                key = (clinical_table, concept_col)
                if key not in CLINICAL_TABLE_DOMAIN:
                    continue

                expected = CLINICAL_TABLE_DOMAIN[key]
                expected_display = expected.capitalize()
                domain_values = _get_domain_id_values_for_alias(tree, concept_alias)

                if not domain_values:
                    # no domain filter at all
                    violations.append(self.create_violation(
                        severity=Severity.WARNING,
                        message=(
                            f"Query joins {clinical_table}.{concept_col} to the concept table "
                            f"without a domain_id filter. "
                            f"Consider adding: concept.domain_id = '{expected_display}' "
                            f"to guard against cross-domain concept matches."
                        ),
                        suggested_fix=(
                            f"Add to WHERE or JOIN ON: concept.domain_id = '{expected_display}'"
                        ),
                        details={
                            "table": clinical_table,
                            "column": concept_col,
                            "expected_domain": expected_display,
                        },
                    ))

                elif expected not in domain_values:
                    # wrong domain
                    actual_display = ", ".join(
                        f"'{v.capitalize()}'" for v in sorted(domain_values)
                    )
                    violations.append(self.create_violation(
                        severity=Severity.ERROR,
                        message=(
                            f"Domain mismatch: {clinical_table}.{concept_col} requires "
                            f"domain_id = '{expected_display}', but the query filters "
                            f"on domain_id IN ({actual_display})."
                        ),
                        suggested_fix=(
                            f"Change the domain_id filter to: concept.domain_id = '{expected_display}'"
                        ),
                        details={
                            "table": clinical_table,
                            "column": concept_col,
                            "expected_domain": expected_display,
                            "actual_domains": sorted(domain_values),
                        },
                    ))

        return violations


__all__ = ["DomainSegregationRule"]

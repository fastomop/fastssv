"""Drug Exposure Cardinality Validation Rule.

OMOP semantic rule CLIN_057: drug_exposure_multiple_records_per_person

A person can have multiple drug_exposure records for the same drug (refills, restarts).
Use drug_era for consolidated exposure periods or apply appropriate aggregation when
counting distinct patients or first exposures.

The Problem:
    Counting rows in drug_exposure without awareness of multiple records per person
    per drug can produce misleading statistics. For example:

    - Patient A has 3 drug_exposure records for metformin (3 refills)
    - Query: SELECT drug_concept_id, COUNT(*) FROM drug_exposure GROUP BY drug_concept_id
    - Result: exposure_count = 3 for metformin
    - Misleading: This counts prescription fills, not unique patients

    Common mistake: Using COUNT(*) when you want to count unique patients.

Detection patterns:
    - Query uses COUNT(*) or COUNT(column) on drug_exposure table
    - COUNT does NOT use DISTINCT person_id
    - Suggests using COUNT(DISTINCT person_id) or drug_era table

Violation pattern:
    SELECT drug_concept_id, COUNT(*) AS exposure_count
    FROM drug_exposure
    GROUP BY drug_concept_id
    -- Counts fills/prescriptions, not unique patients

Correct patterns:
    -- Count distinct patients
    SELECT drug_concept_id, COUNT(DISTINCT person_id) AS patient_count
    FROM drug_exposure
    WHERE drug_concept_id != 0
    GROUP BY drug_concept_id

    -- Use drug_era for consolidated periods
    SELECT drug_concept_id, COUNT(*) AS era_count
    FROM drug_era
    GROUP BY drug_concept_id

    -- Count total exposures (if that's what you actually want)
    SELECT drug_concept_id, COUNT(*) AS total_exposures
    FROM drug_exposure
    GROUP BY drug_concept_id
    -- This is fine if you explicitly want exposure counts
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

TABLE_DRUG_EXPOSURE = "drug_exposure"
TABLE_DRUG_ERA = "drug_era"
PERSON_ID = "person_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _get_aliases_for_table(target: str, aliases: Dict[str, str]) -> Set[str]:
    return {
        alias for alias, table in aliases.items()
        if _norm(table) == _norm(target)
    }


def _has_table(target: str, aliases: Dict[str, str]) -> bool:
    return any(_norm(t) == _norm(target) for t in aliases.values())


def _is_count_distinct_person_id(count_node: exp.Count, aliases: Dict[str, str]) -> bool:
    expr = count_node.this

    if isinstance(expr, exp.Distinct):
        if expr.expressions and isinstance(expr.expressions[0], exp.Column):
            _, col = resolve_table_col(expr.expressions[0], aliases)
            return _norm(col) == PERSON_ID

    if count_node.args.get("distinct") and isinstance(expr, exp.Column):
        _, col = resolve_table_col(expr, aliases)
        return _norm(col) == PERSON_ID

    return False


def _is_count_star(count: exp.Count) -> bool:
    """Robust COUNT(*) detection."""
    return count.this is None or isinstance(count.this, exp.Star)


def _is_subquery_safe(count: exp.Count) -> bool:
    """Skip COUNT over subqueries that may already be deduplicated."""
    parent = count.parent
    return isinstance(parent, exp.Select) and isinstance(parent.parent, exp.Subquery)


# Alias-name fragments that strongly indicate the user is counting patients,
# not records. Only warn when the user's intent actually is patient-level.
PATIENT_INTENT_FRAGMENTS = ("person", "patient", "people", "member", "subject")


def _count_alias_suggests_patient_intent(count: exp.Count) -> bool:
    """True if the alias wrapping this COUNT names patients/persons.

    Examples:
      COUNT(*) AS num_persons      -> True
      COUNT(*) AS patient_count    -> True
      COUNT(*) AS num_records      -> False
      COUNT(*) AS exposure_count   -> False
      COUNT(*) AS cn               -> False
    """
    parent = count.parent
    if isinstance(parent, exp.Alias):
        alias = normalize_name(parent.alias) if parent.alias else ""
        return any(frag in alias for frag in PATIENT_INTENT_FRAGMENTS)
    return False


def _has_problematic_count(select: exp.Select, aliases: Dict[str, str]) -> bool:
    drug_aliases = _get_aliases_for_table(TABLE_DRUG_EXPOSURE, aliases)

    if not drug_aliases:
        return False

    for count in select.find_all(exp.Count):
        if _is_subquery_safe(count):
            continue

        # Skip safe case: COUNT(DISTINCT person_id)
        if _is_count_distinct_person_id(count, aliases):
            continue

        # Only warn when the user's alias makes patient-level intent explicit
        # (e.g. "num_persons", "patient_count"). Record-level aliases like
        # "num_records", "exposure_count", "cn" are intentionally left alone
        # -- the user is being explicit that they want rows, not patients.
        if not _count_alias_suggests_patient_intent(count):
            continue

        # COUNT(*) or COUNT(non-person-id column) with patient-intent alias
        # is the classic overcounting pattern worth flagging.
        if _is_count_star(count):
            return True

        if isinstance(count.this, exp.Distinct):
            return True

        if isinstance(count.this, exp.Column):
            table, _ = resolve_table_col(count.this, aliases)

            if not table:
                if len(aliases) == 1:
                    return True
                continue

            if _norm(table) == TABLE_DRUG_EXPOSURE:
                return True

    return False


# --- Detection -------------------------------------------------------------

def _detect_violation(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    if not _has_table(TABLE_DRUG_EXPOSURE, aliases):
        return False

    if _has_table(TABLE_DRUG_ERA, aliases):
        return False

    for select in tree.find_all(exp.Select):
        if _has_problematic_count(select, aliases):
            return True

    return False


# --- Rule ------------------------------------------------------------------

@register
class DrugExposureCardinalityValidationRule(Rule):
    """Warn about counting drug_exposure rows instead of patients."""

    rule_id = "domain_specific.drug_exposure_cardinality_validation"
    name = "Drug Exposure Cardinality Awareness"

    description = (
        "Counting drug_exposure rows may overcount patients due to multiple exposures "
        "(e.g., refills, restarts)."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `COUNT(*)` (or COUNT(person_id)) WITH `COUNT(DISTINCT person_id)` for patient counts, OR query drug_era for consolidated exposure periods."
    example_bad = (
        "SELECT drug_concept_id, COUNT(*) AS patient_count\n"
        "FROM drug_exposure\n"
        "GROUP BY drug_concept_id;"
    )
    example_good = (
        "SELECT drug_concept_id, COUNT(DISTINCT person_id) AS patient_count\n"
        "FROM drug_exposure\n"
        "GROUP BY drug_concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            if _detect_violation(tree, aliases):
                violations.append(
                    self.create_violation(
                        message=(
                            "Query counts drug_exposure records without DISTINCT person_id "
                            "and appears to be patient-level. This may overcount patients due "
                            "to multiple exposures per person."
                        ),
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["DrugExposureCardinalityValidationRule"]

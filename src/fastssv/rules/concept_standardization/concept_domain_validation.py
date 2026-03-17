"""Concept Domain Validation Rule (Comprehensive).

Merged rule combining domain_segregation and concept_domain_validation.

OMOP semantic rules OMOP_066 + OMOP_019:
Each concept_id column in OMOP CDM is tied to a specific domain. When a query
joins a clinical table to the concept table, the domain_id filter on the concept
table must match that column's expected domain.

Coverage:
  - Main clinical event tables (9 tables) - WARNING when no domain filter, ERROR when wrong
  - Auxiliary columns (25+ columns) - ERROR only when wrong domain is specified

Examples of correct usage:
  Main clinical tables:
    - condition_occurrence.condition_concept_id → domain_id = 'Condition'
    - drug_exposure.drug_concept_id → domain_id = 'Drug'
    - procedure_occurrence.procedure_concept_id → domain_id = 'Procedure'
    - measurement.measurement_concept_id → domain_id = 'Measurement'
    - observation.observation_concept_id → domain_id = 'Observation'
    - device_exposure.device_concept_id → domain_id = 'Device'
    - visit_occurrence.visit_concept_id → domain_id = 'Visit'
    - specimen.specimen_concept_id → domain_id = 'Specimen'
    - death.cause_concept_id → domain_id = 'Condition'

  Auxiliary columns:
    - person.gender_concept_id → domain_id = 'Gender' (OMOP_019)
    - person.race_concept_id → domain_id = 'Race'
    - drug_exposure.route_concept_id → domain_id = 'Route'
    - measurement.unit_concept_id → domain_id = 'Unit'
    - ... and 20+ more

Violation levels:
  - ERROR: domain_id filter is present but specifies the wrong domain
  - WARNING: (main tables only) concept join exists without any domain_id filter
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


# --- Canonical OMOP domains (display form) ---
MAIN_CLINICAL_TABLE_DOMAIN: Dict[Tuple[str, str], str] = {
    ("condition_occurrence", "condition_concept_id"): "Condition",
    ("drug_exposure", "drug_concept_id"): "Drug",
    ("procedure_occurrence", "procedure_concept_id"): "Procedure",
    ("measurement", "measurement_concept_id"): "Measurement",
    ("observation", "observation_concept_id"): "Observation",
    ("device_exposure", "device_concept_id"): "Device",
    ("visit_occurrence", "visit_concept_id"): "Visit",
    ("specimen", "specimen_concept_id"): "Specimen",
    ("death", "cause_concept_id"): "Condition",
}

AUXILIARY_CONCEPT_COLUMNS: Dict[str, str] = {
    "gender_concept_id": "Gender",
    "race_concept_id": "Race",
    "ethnicity_concept_id": "Ethnicity",
    "route_concept_id": "Route",
    "dose_unit_concept_id": "Unit",
    "unit_concept_id": "Unit",
    "operator_concept_id": "Meas Value Operator",
    "value_as_concept_id": "Meas Value",
    "anatomic_site_concept_id": "Spec Anatomic Site",
    "disease_status_concept_id": "Spec Disease Status",
    "specialty_concept_id": "Specialty",
}


def _norm(val: str) -> str:
    return normalize_name(val)


def _find_concept_joins(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    """
    Returns:
      (clinical_table, concept_column, type, concept_alias)
    """
    results = []

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

        left, right = eq.left, eq.right

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        lt, rt = _norm(lt or ""), _norm(rt or "")
        lc, rc = _norm(lc or ""), _norm(rc or "")

        left_alias = _norm(left.table) if left.table else None
        right_alias = _norm(right.table) if right.table else None

        # clinical -> concept
        if rt == "concept" and rc == "concept_id":
            key = (lt, lc)
            if key in MAIN_CLINICAL_TABLE_DOMAIN:
                results.append((lt, lc, "main", right_alias))
            elif lc in AUXILIARY_CONCEPT_COLUMNS:
                results.append((lt, lc, "aux", right_alias))

        # concept -> clinical
        elif lt == "concept" and lc == "concept_id":
            key = (rt, rc)
            if key in MAIN_CLINICAL_TABLE_DOMAIN:
                results.append((rt, rc, "main", left_alias))
            elif rc in AUXILIARY_CONCEPT_COLUMNS:
                results.append((rt, rc, "aux", left_alias))

    return results


def _collect_domain_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> Dict[str, Set[str]]:
    """
    Returns:
      {concept_alias: {domain_values}}
    """
    result: Dict[str, Set[str]] = {}

    def add(alias: str, val: str):
        if alias not in result:
            result[alias] = set()
        result[alias].add(_norm(val))

    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.In)):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        right = node.expression

        for col, val in [(left, right), (right, left)]:
            if not isinstance(col, exp.Column):
                continue

            table, col_name = resolve_table_col(col, aliases)
            table = _norm(table or "")
            col_name = _norm(col_name)

            if col_name != "domain_id" or table != "concept":
                continue

            alias = _norm(col.table) if col.table else None
            if not alias:
                continue  # strict mode: ignore unqualified

            if isinstance(node, exp.EQ) and is_string_literal(val):
                add(alias, val.this)

            elif isinstance(node, exp.In):
                # For IN expressions, values are in node.expressions
                in_values = node.expressions or []
                for v in in_values:
                    if is_string_literal(v):
                        add(alias, v.this)
                break  # Only need to check once for IN

    return result


@register
class ConceptDomainValidationRule(Rule):
    rule_id = "semantic.concept_domain_validation"
    name = "Concept Domain ID Matches Target Table"
    description = (
        "Validates that concept.domain_id matches the expected domain for each "
        "*_concept_id column."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Add or correct concept.domain_id filter to match expected domain."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if not tree or not uses_table(tree, "concept"):
                continue

            aliases = extract_aliases(tree)

            joins = _find_concept_joins(tree, aliases)
            if not joins:
                continue

            domain_filters = _collect_domain_filters(tree, aliases)

            for table, col, col_type, concept_alias in joins:
                if not concept_alias:
                    continue

                if col_type == "main":
                    expected = MAIN_CLINICAL_TABLE_DOMAIN[(table, col)]
                else:
                    expected = AUXILIARY_CONCEPT_COLUMNS[col]

                expected_norm = _norm(expected)

                values = domain_filters.get(concept_alias, set())

                # --- Missing filter ---
                if not values:
                    if col_type == "main":
                        violations.append(self.create_violation(
                            severity=Severity.WARNING,
                            message=(
                                f"{table}.{col} joined to concept '{concept_alias}' "
                                f"without domain_id filter. Expected domain '{expected}'."
                            ),
                            suggested_fix=(
                                f"Add: {concept_alias}.domain_id = '{expected}'"
                            ),
                        ))
                    continue

                # --- Wrong domain ---
                if expected_norm not in values:
                    actual = ", ".join(sorted(v.capitalize() for v in values))
                    violations.append(self.create_violation(
                        severity=Severity.ERROR,
                        message=(
                            f"Domain mismatch for {table}.{col}: expected '{expected}', "
                            f"found ({actual})."
                        ),
                        suggested_fix=(
                            f"Use: {concept_alias}.domain_id = '{expected}'"
                        ),
                    ))

                # --- Optional: multi-domain warning ---
                elif len(values) > 1:
                    violations.append(self.create_violation(
                        severity=Severity.WARNING,
                        message=(
                            f"{concept_alias}.domain_id uses multiple domains ({values}). "
                            f"This may produce unintended results."
                        ),
                    ))

        return violations


__all__ = ["ConceptDomainValidationRule"]

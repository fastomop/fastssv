"""Fact Relationship Polymorphic Join Validation Rule.

OMOP semantic rule JOIN_031:
fact_relationship uses polymorphic foreign keys: fact_id_1 and fact_id_2 can
reference different clinical tables depending on domain_concept_id_1 and
domain_concept_id_2. Joins must account for the domain to resolve the correct
target table.

The Problem:
    fact_relationship links ANY two facts across the entire OMOP CDM.
    Without domain filtering, ID values collide across tables:
    - measurement_id = 123 (in measurement table)
    - condition_occurrence_id = 123 (in condition_occurrence table)
    - procedure_occurrence_id = 123 (in procedure_occurrence table)

    These are COMPLETELY DIFFERENT clinical events!

Violation pattern:
    SELECT * FROM fact_relationship fr
    JOIN measurement m ON fr.fact_id_1 = m.measurement_id
    -- WRONG: Missing domain_concept_id_1 filter!
    -- Could match wrong table's ID 123

Correct pattern:
    SELECT * FROM fact_relationship fr
    JOIN measurement m ON fr.fact_id_1 = m.measurement_id
    WHERE fr.domain_concept_id_1 = 21  -- 21 = Measurement domain
"""

from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.patch import (
    add as patch_add,
    freeform,
    locate,
    _qualifiers_for_table,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

FACT_RELATIONSHIP = "fact_relationship"

FACT_ID_1 = "fact_id_1"
FACT_ID_2 = "fact_id_2"

DOMAIN_CONCEPT_ID_1 = "domain_concept_id_1"
DOMAIN_CONCEPT_ID_2 = "domain_concept_id_2"


CLINICAL_TABLE_TO_DOMAIN_CONCEPT_ID: Dict[str, int] = {
    "condition_occurrence": 19,
    "drug_exposure": 13,
    "procedure_occurrence": 10,
    "measurement": 21,
    "observation": 27,
    "device_exposure": 17,
    "visit_occurrence": 8,
    "specimen": 36,
    "note": 5085,
}

CLINICAL_TABLE_PK: Dict[str, str] = {
    "condition_occurrence": "condition_occurrence_id",
    "drug_exposure": "drug_exposure_id",
    "procedure_occurrence": "procedure_occurrence_id",
    "measurement": "measurement_id",
    "observation": "observation_id",
    "device_exposure": "device_exposure_id",
    "visit_occurrence": "visit_occurrence_id",
    "specimen": "specimen_id",
    "note": "note_id",
}


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_fact(table: Optional[str]) -> bool:
    return _norm(table) == FACT_RELATIONSHIP


def _get_clinical_info(table: Optional[str]) -> Optional[Tuple[str, int]]:
    t = _norm(table)
    if not t or t not in CLINICAL_TABLE_TO_DOMAIN_CONCEPT_ID:
        return None
    return CLINICAL_TABLE_PK[t], CLINICAL_TABLE_TO_DOMAIN_CONCEPT_ID[t]


def _extract_literal(node: exp.Expression) -> Optional[int]:
    if isinstance(node, exp.Literal):
        try:
            return int(node.this)
        except Exception:
            return None
    return None


# --- Detection -------------------------------------------------------------


def _detect_fact_joins(tree: exp.Expression, aliases: Dict[str, str]):
    """
    Returns list of:
        (clinical_table, fact_id_col, expected_domain_id)
    """
    results = []

    for eq in tree.find_all(exp.EQ):
        if not isinstance(eq.this, exp.Column) or not isinstance(eq.expression, exp.Column):
            continue

        lt, lc = resolve_table_col(eq.this, aliases)
        rt, rc = resolve_table_col(eq.expression, aliases)

        lt_norm = _norm(lt)
        rt_norm = _norm(rt)
        lc_norm = _norm(lc)
        rc_norm = _norm(rc)

        if not lc_norm or not rc_norm:
            continue

        # Skip self joins
        if lt_norm == rt_norm:
            continue

        # fact → clinical
        if _is_fact(lt_norm):
            info = _get_clinical_info(rt_norm)
            if info and rc_norm == _norm(info[0]):
                if lc_norm == FACT_ID_1:
                    results.append((rt_norm, FACT_ID_1, info[1]))
                elif lc_norm == FACT_ID_2:
                    results.append((rt_norm, FACT_ID_2, info[1]))

        elif _is_fact(rt_norm):
            info = _get_clinical_info(lt_norm)
            if info and lc_norm == _norm(info[0]):
                if rc_norm == FACT_ID_1:
                    results.append((lt_norm, FACT_ID_1, info[1]))
                elif rc_norm == FACT_ID_2:
                    results.append((lt_norm, FACT_ID_2, info[1]))

    return results


def _collect_domain_filters(tree: exp.Expression, aliases: Dict[str, str]):
    filters = {
        DOMAIN_CONCEPT_ID_1: set(),
        DOMAIN_CONCEPT_ID_2: set(),
    }

    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.In)):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        right = getattr(node, "expression", None)

        # EQ
        if isinstance(node, exp.EQ):
            for col, val in [(left, right), (right, left)]:
                if not isinstance(col, exp.Column):
                    continue

                t, c = resolve_table_col(col, aliases)

                if not _is_fact(t):
                    continue

                c_norm = _norm(c)

                if c_norm not in filters:
                    continue

                val_int = _extract_literal(val)
                if val_int is not None:
                    filters[c_norm].add(val_int)

        # IN
        elif isinstance(node, exp.In):
            if not isinstance(left, exp.Column):
                continue

            t, c = resolve_table_col(left, aliases)

            if not _is_fact(t):
                continue

            c_norm = _norm(c)
            if c_norm not in filters:
                continue

            for expr in node.args.get("expressions", []):
                val_int = _extract_literal(expr)
                if val_int is not None:
                    filters[c_norm].add(val_int)

    return filters


def _detect(tree: exp.Expression, aliases: Dict[str, str]):
    violations = []
    seen = set()

    fact_joins = _detect_fact_joins(tree, aliases)
    if not fact_joins:
        return []

    filters = _collect_domain_filters(tree, aliases)

    for table, fact_id_col, expected_domain in fact_joins:
        # Map correct domain column
        domain_col = DOMAIN_CONCEPT_ID_1 if fact_id_col == FACT_ID_1 else DOMAIN_CONCEPT_ID_2

        values = filters.get(domain_col, set())

        # --- STRICT VALIDATION --------------------------------------------

        valid = values == {expected_domain}

        if not valid:
            key = (table, fact_id_col, expected_domain)

            if key not in seen:
                violations.append(key)
                seen.add(key)

    return violations


# --- Rule ------------------------------------------------------------------


@register
class FactRelationshipJoinValidationRule(Rule):
    """
    Validate polymorphic joins on fact_relationship.
    """

    rule_id = "joins.fact_relationship_join_validation"
    name = "Fact Relationship Polymorphic Join Validation"

    description = (
        "Ensures fact_relationship joins include correct domain_concept_id filtering. "
        "fact_id columns are polymorphic and must be disambiguated using domain_concept_id."
    )

    severity = Severity.ERROR

    suggested_fix = "ADD: `AND fr.domain_concept_id_1 = <id1> AND fr.domain_concept_id_2 = <id2>` to disambiguate the polymorphic fact_id_1 / fact_id_2 columns. Each pair points into a different clinical-event table depending on the domain."
    example_bad = (
        "SELECT fr.fact_id_1 FROM fact_relationship fr\n"
        "JOIN visit_occurrence vo ON fr.fact_id_1 = vo.visit_occurrence_id;"
    )
    example_good = (
        "SELECT fr.fact_id_1 FROM fact_relationship fr\n"
        "JOIN visit_occurrence vo ON fr.fact_id_1 = vo.visit_occurrence_id\n"
        "WHERE fr.domain_concept_id_1 = 8;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        if FACT_RELATIONSHIP not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            detected = _detect(tree, aliases)

            for table, fact_id_col, expected_domain in detected:
                domain_col = DOMAIN_CONCEPT_ID_1 if fact_id_col == FACT_ID_1 else DOMAIN_CONCEPT_ID_2

                msg = (
                    f"Invalid join: fact_relationship.{fact_id_col} → {table}. "
                    f"Missing or incorrect filter {domain_col} = {expected_domain}. "
                    f"Without this, polymorphic IDs may match wrong tables."
                )

                # Build an ADD patch that inserts the missing domain_concept_id
                # filter directly after the offending fact_id_X predicate.
                fix_text = (
                    f"ADD: `AND fact_relationship.{domain_col} = {expected_domain}` "
                    f"to disambiguate the polymorphic fact_id."
                )
                patch = freeform(fix_text)
                clinical_pk = CLINICAL_TABLE_PK.get(table)
                if clinical_pk:
                    fr_quals = _qualifiers_for_table(FACT_RELATIONSHIP, aliases)
                    clin_quals = _qualifiers_for_table(table, aliases)
                    located = None
                    chosen_fr = FACT_RELATIONSHIP
                    for fq in fr_quals:
                        for cq in clin_quals:
                            for predicate in (
                                f"{fq}.{fact_id_col} = {cq}.{clinical_pk}",
                                f"{cq}.{clinical_pk} = {fq}.{fact_id_col}",
                            ):
                                span = locate(sql, predicate)
                                if span is not None:
                                    located = span
                                    chosen_fr = fq
                                    break
                            if located:
                                break
                        if located:
                            break
                    if located:
                        patch = patch_add(
                            located[1],
                            f" AND {chosen_fr}.{domain_col} = {expected_domain}",
                        )

                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=patch,
                        details={
                            "type": "fact_relationship_domain_mismatch",
                            "clinical_table": table,
                            "fact_id_column": fact_id_col,
                            "domain_column": domain_col,
                            "expected_domain_concept_id": expected_domain,
                        },
                    )
                )

        return violations


__all__ = ["FactRelationshipJoinValidationRule"]

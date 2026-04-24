"""Clinical to Visit Detail Join Validation Rule.

OMOP semantic rule JOIN_007:
Clinical event tables join to visit_detail via visit_detail_id on both sides.
Joining on visit_occurrence_id to visit_detail_id is a type mismatch that silently
produces wrong results.

The Problem:
    Clinical tables have both visit_occurrence_id and visit_detail_id columns.
    These represent different ID spaces:
    - visit_occurrence_id: Links to parent visit (visit_occurrence table)
    - visit_detail_id: Links to detailed sub-visit (visit_detail table)

    Both are integers, so joining visit_occurrence_id to visit_detail_id produces
    NO TYPE ERROR, but randomly matches unrelated records where IDs happen to be
    equal. This silently corrupts analytical results.

Violation pattern:
    SELECT * FROM measurement m
    JOIN visit_detail vd ON m.visit_occurrence_id = vd.visit_detail_id
    -- WRONG: Different ID types! Silent data corruption.

Correct pattern:
    SELECT * FROM measurement m
    JOIN visit_detail vd ON m.visit_detail_id = vd.visit_detail_id
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

VISIT_DETAIL = "visit_detail"
VISIT_DETAIL_ID = "visit_detail_id"
VISIT_OCCURRENCE_ID = "visit_occurrence_id"

CLINICAL_TABLES = {
    "condition_occurrence",
    "procedure_occurrence",
    "drug_exposure",
    "device_exposure",
    "measurement",
    "observation",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_visit_detail(table: Optional[str]) -> bool:
    return _norm(table) == VISIT_DETAIL


def _is_clinical(table: Optional[str]) -> bool:
    return _norm(table) in CLINICAL_TABLES


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_all_equalities(tree: exp.Expression) -> List[exp.EQ]:
    """
    Extract equality conditions from:
    - JOIN ON clauses
    - WHERE clauses (implicit joins)
    """
    return list(tree.find_all(exp.EQ))


def _classify_join(
    lt: str,
    lc: str,
    rt: str,
    rc: str,
) -> Optional[Tuple[str, str, str, str, str]]:
    """
    Detect invalid clinical <-> visit_detail joins.

    Returns:
        (clinical_table, clinical_col, visit_detail_table, visit_detail_col, error_type)
    """

    for (t1, c1, t2, c2) in [
        (lt, lc, rt, rc),
        (rt, rc, lt, lc),
    ]:
        if _is_clinical(t1) and _is_visit_detail(t2):

            # Case 1: visit_occurrence_id -> visit_detail_id (invalid)
            if _is_col(c1, VISIT_OCCURRENCE_ID) and _is_col(c2, VISIT_DETAIL_ID):
                return (t1, c1, t2, c2, "visit_occurrence_to_visit_detail_id")

            # Case 2: visit_detail_id -> visit_occurrence_id (invalid)
            if _is_col(c1, VISIT_DETAIL_ID) and _is_col(c2, VISIT_OCCURRENCE_ID):
                return (t1, c1, t2, c2, "visit_detail_to_visit_occurrence_id")

    return None


def _detect_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str, str]]:
    """
    Detect all invalid joins between clinical tables and visit_detail.
    """

    violations: List[Tuple[str, str, str, str, str]] = []
    seen: Set[Tuple[str, str, str, str, str]] = set()

    for eq in _extract_all_equalities(tree):
        left, right = eq.this, eq.expression

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt = _normalize_table(lt)
        rt = _normalize_table(rt)

        result = _classify_join(lt, lc, rt, rc)
        if result and result not in seen:
            violations.append(result)
            seen.add(result)

    return violations


# --- Rule ------------------------------------------------------------------

@register
class ClinicalVisitDetailJoinValidationRule(Rule):
    """
    Validate correct joins between clinical tables and visit_detail.

    Behavior:
    - Detects ID-type mismatches between visit_occurrence_id and visit_detail_id
    - Does not assume visit_detail_id is always present
    - Suggests correct join strategies depending on context
    """

    rule_id = "joins.clinical_visit_detail_join_validation"
    name = "Clinical to Visit Detail Join Validation"

    description = (
        "When joining clinical tables to visit_detail, visit_detail_id should be used. "
        "Joining visit_occurrence_id to visit_detail_id (or vice versa) is an ID type mismatch."
    )

    severity = Severity.ERROR

    def _build_message(
        self,
        clinical_table: str,
        clinical_col: str,
        visit_detail_table: str,
        visit_detail_col: str,
        error_type: str,
    ) -> Tuple[str, str]:
        """
        Generate context-aware message and suggested fix.
        """

        message = (
            f"ID type mismatch: {clinical_table}.{clinical_col} joined to "
            f"{visit_detail_table}.{visit_detail_col}."
        )

        if error_type == "visit_occurrence_to_visit_detail_id":
            suggested_fix = (
                f"If visit_detail_id is available, use:\n"
                f"  {clinical_table}.visit_detail_id = {visit_detail_table}.visit_detail_id\n"
                f"Otherwise, join via visit_occurrence:\n"
                f"  {clinical_table}.visit_occurrence_id = visit_occurrence.visit_occurrence_id"
            )

        elif error_type == "visit_detail_to_visit_occurrence_id":
            suggested_fix = (
                f"Correct join should be:\n"
                f"  {clinical_table}.visit_detail_id = {visit_detail_table}.visit_detail_id"
            )

        else:
            suggested_fix = "Review join keys for semantic correctness."

        return message, suggested_fix

    example_bad = (
        "SELECT * FROM condition_occurrence co\n"
        "JOIN visit_detail vd ON co.visit_occurrence_id = vd.visit_detail_id;"
    )
    example_good = (
        "SELECT * FROM condition_occurrence co\n"
        "JOIN visit_detail vd ON co.visit_detail_id = vd.visit_detail_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Fast pre-filter
        if "visit_detail" not in sql.lower():
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, VISIT_DETAIL):
                continue

            aliases = extract_aliases(tree)
            bad_joins = _detect_violations(tree, aliases)

            for (
                clinical_table,
                clinical_col,
                visit_detail_table,
                visit_detail_col,
                error_type,
            ) in bad_joins:

                message, suggested_fix = self._build_message(
                    clinical_table,
                    clinical_col,
                    visit_detail_table,
                    visit_detail_col,
                    error_type,
                )

                violations.append(
                    self.create_violation(
                        message=message,
                        suggested_fix=suggested_fix,
                        details={
                            "clinical_table": clinical_table,
                            "clinical_column": clinical_col,
                            "visit_detail_table": visit_detail_table,
                            "visit_detail_column": visit_detail_col,
                            "error_type": error_type,
                            "expected": (
                                f"{clinical_table}.visit_detail_id = "
                                f"{visit_detail_table}.visit_detail_id"
                            ),
                        },
                    )
                )

        return violations


__all__ = ["ClinicalVisitDetailJoinValidationRule"]

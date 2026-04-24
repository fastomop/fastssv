"""Era Table Forbidden Join Validation Rule.

OMOP semantic rule JOIN_024:
Era tables (condition_era, drug_era, dose_era) cannot be joined to
visit_occurrence, visit_detail, provider, or care_site tables.

The Problem:
    Era tables are DERIVED/AGGREGATED tables built from event tables:
    - condition_era is derived from condition_occurrence
    - drug_era is derived from drug_exposure
    - dose_era is derived from drug_exposure

    They represent continuous time periods, not discrete clinical events.
    Era tables have NO foreign keys to visit, provider, or care_site.

    They ONLY have:
    - person_id (FK to person)
    - *_concept_id (FK to concept)

    Any join to visit/provider/care_site is semantically impossible.

Violation pattern:
    SELECT *
    FROM drug_era de
    JOIN visit_occurrence vo ON de.person_id = vo.person_id
    -- WRONG: Era tables have no visit context!
    -- This creates a Cartesian product of all visits for the person

Correct pattern:
    -- If you need visit context, use the EVENT table, not ERA table
    SELECT *
    FROM drug_exposure de  -- NOT drug_era
    JOIN visit_occurrence vo ON de.visit_occurrence_id = vo.visit_occurrence_id

    -- Era tables can only join to person and concept
    SELECT *
    FROM drug_era de
    JOIN person p ON de.person_id = p.person_id
    JOIN concept c ON de.drug_concept_id = c.concept_id
"""

from typing import Dict, List, Optional, Set, Tuple

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

ERA_TABLES = {"condition_era", "drug_era", "dose_era"}

FORBIDDEN_TABLES = {
    "visit_occurrence",
    "visit_detail",
    "provider",
    "care_site",
}

PERSON = "person"
PERSON_ID = "person_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_era(t: Optional[str]) -> bool:
    return t in ERA_TABLES


def _is_forbidden(t: Optional[str]) -> bool:
    return t in FORBIDDEN_TABLES


def _is_person(t: Optional[str]) -> bool:
    return t == PERSON


def _extract_eq_conditions(tree: exp.Expression) -> List[exp.EQ]:
    eqs: List[exp.EQ] = []

    has_join_on = False

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause:
            has_join_on = True
            for eq in on_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    eqs.append(eq)

    if not has_join_on:
        where_clause = tree.find(exp.Where)
        if where_clause:
            for eq in where_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    eqs.append(eq)

    return eqs


# --- Detection -------------------------------------------------------------

def _detect(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:

    violations: List[Tuple[str, str, str, str]] = []
    seen_pairs: Set[Tuple[str, str]] = set()

    # --- Track tables present ---------------------------------------------
    tables_present: Set[str] = set()

    for table in tree.find_all(exp.Table):
        t = _normalize_table(table.name)
        if t:
            tables_present.add(t)

    era_tables = {t for t in tables_present if _is_era(t)}
    forbidden_tables = {t for t in tables_present if _is_forbidden(t)}

    if not era_tables or not forbidden_tables:
        return violations

    # --- Graph construction (table-level join graph) -----------------------
    graph: Dict[str, Set[str]] = {t: set() for t in tables_present}

    for eq in _extract_eq_conditions(tree):
        lt, lc = resolve_table_col(eq.this, aliases)
        rt, rc = resolve_table_col(eq.expression, aliases)

        if not (lt and rt):
            continue

        lt = _normalize_table(lt)
        rt = _normalize_table(rt)

        if not (lt and rt):
            continue

        graph.setdefault(lt, set()).add(rt)
        graph.setdefault(rt, set()).add(lt)

        # --- Direct violation detection ------------------------------------
        if _is_era(lt) and _is_forbidden(rt):
            pair = (lt, rt)
            if pair not in seen_pairs:
                violations.append((lt, _norm(lc), rt, _norm(rc)))
                seen_pairs.add(pair)

        elif _is_era(rt) and _is_forbidden(lt):
            pair = (rt, lt)
            if pair not in seen_pairs:
                violations.append((rt, _norm(rc), lt, _norm(lc)))
                seen_pairs.add(pair)

    # --- Multi-hop detection (graph traversal) -----------------------------
    def _reachable(start: str, targets: Set[str]) -> Set[str]:
        visited = set()
        stack = [start]

        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    stack.append(neighbor)

        return visited.intersection(targets)

    for era in era_tables:
        reachable_forbidden = _reachable(era, forbidden_tables)

        for forbidden in reachable_forbidden:
            pair = (era, forbidden)

            if pair in seen_pairs:
                continue

            # Avoid trivial case where no real join exists
            if forbidden not in graph or not graph[era]:
                continue

            violations.append((era, "PATH", forbidden, "PATH"))
            seen_pairs.add(pair)

    return violations


# --- Rule ------------------------------------------------------------------

@register
class EraForbiddenJoinValidationRule(Rule):
    """
    Validate that era tables are not joined (directly or indirectly)
    to visit/provider/care_site tables.
    """

    rule_id = "joins.era_forbidden_join_validation"
    name = "Era Table Forbidden Join Validation"

    description = (
        "Ensures era tables (condition_era, drug_era, dose_era) are not joined "
        "to visit_occurrence, visit_detail, provider, or care_site tables, "
        "either directly or through intermediate tables."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Do not join era tables with visit/provider/care_site. "
        "Use event tables (condition_occurrence, drug_exposure) for visit-level analysis."
    )
    example_bad = (
        "SELECT * FROM drug_era de\n"
        "JOIN visit_occurrence vo ON de.person_id = vo.person_id;"
    )
    example_good = (
        "SELECT * FROM drug_era de;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        if not any(t in sql.lower() for t in ERA_TABLES):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            detected = _detect(tree, aliases)

            for era, era_col, forbidden, forbidden_col in detected:

                if era_col == "PATH":
                    msg = (
                        f"Invalid indirect join: {era} is connected to {forbidden} via intermediate tables. "
                        f"Era tables must not be associated with visit/provider context."
                    )
                else:
                    msg = (
                        f"Invalid join: {era}.{era_col} → {forbidden}.{forbidden_col}. "
                        f"Era tables must not be associated with visit/provider context."
                    )

                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "era_granularity_violation",
                            "era_table": era,
                            "forbidden_table": forbidden,
                            "era_column": era_col,
                            "forbidden_column": forbidden_col,
                        },
                    )
                )

        return violations


__all__ = ["EraForbiddenJoinValidationRule"]

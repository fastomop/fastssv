"""Clinical Tables Person ID Linkage Validation Rule.

OMOP semantic rules CLIN_055, OMOP_517: all_clinical_tables_require_person_id_for_patient_query

When combining data across multiple clinical tables for patient-level analysis,
all tables must be linked through person_id (directly or transitively). Missing
person_id linkage produces cross-patient contamination in results.

The Problem:
    Joining clinical tables without person_id linkage can match data from different
    patients, leading to completely invalid results. For example:

    - Patient A has condition_start_date = '2020-01-15'
    - Patient B has drug_exposure_start_date = '2020-01-15'

    Joining on dates alone would incorrectly associate Patient A's condition with
    Patient B's drug exposure.

Clinical tables that require person_id linkage:
    - condition_occurrence
    - drug_exposure
    - procedure_occurrence
    - measurement
    - observation
    - visit_occurrence
    - visit_detail
    - death
    - person

Violation pattern:
    SELECT co.condition_concept_id, de.drug_concept_id
    FROM condition_occurrence co
    JOIN drug_exposure de ON co.condition_start_date = de.drug_exposure_start_date
    -- WRONG: No person_id linkage, cross-patient contamination

Correct patterns:
    -- Direct person_id join
    SELECT co.condition_concept_id, de.drug_concept_id
    FROM condition_occurrence co
    JOIN drug_exposure de ON co.person_id = de.person_id

    -- Transitive through person table
    SELECT co.condition_concept_id, de.drug_concept_id
    FROM condition_occurrence co
    JOIN person p ON co.person_id = p.person_id
    JOIN drug_exposure de ON p.person_id = de.person_id
"""

from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict, deque

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

CLINICAL_TABLES: Set[str] = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "visit_occurrence",
    "visit_detail",
    "death",
    "person",
}

PERSON_ID = "person_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_clinical_table(table: Optional[str]) -> bool:
    return _norm(table) in CLINICAL_TABLES


def _get_clinical_aliases(aliases: Dict[str, str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for alias, table in aliases.items():
        if _is_clinical_table(table) and alias != table:
            result[alias] = table
    return result


def _build_table_to_aliases(aliases: Dict[str, str]) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = defaultdict(list)
    for alias, table in aliases.items():
        mapping[_norm(table)].append(alias)
    return mapping


def _extract_join_conditions(tree: exp.Expression) -> List[Tuple[exp.Column, exp.Column]]:
    conditions: List[Tuple[exp.Column, exp.Column]] = []

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if on_clause:
            for eq in on_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    conditions.append((eq.this, eq.expression))

    for where in tree.find_all(exp.Where):
        for eq in where.find_all(exp.EQ):
            if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                conditions.append((eq.this, eq.expression))

    return conditions


def _extract_using_edges(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str]]:
    edges: List[Tuple[str, str]] = []

    # For each SELECT statement, track tables in order
    for select in tree.find_all(exp.Select):
        seen_tables: List[str] = []

        # Get the FROM table first
        from_clause = select.args.get("from_")
        if from_clause and isinstance(from_clause.this, exp.Table):
            seen_tables.append(from_clause.this.alias_or_name)

        # Process joins in order
        for join in select.args.get("joins", []):
            using = join.args.get("using")
            if not using:
                # Not a USING join, but still add the table to seen
                if isinstance(join.this, exp.Table):
                    seen_tables.append(join.this.alias_or_name)
                continue

            cols = set()

            if isinstance(using, exp.Tuple):
                cols = {_norm(e.name) for e in using.expressions if isinstance(e, exp.Identifier)}
            elif isinstance(using, list):
                cols = {_norm(e.name) for e in using if isinstance(e, exp.Identifier)}
            elif isinstance(using, exp.Identifier):
                cols = {_norm(using.name)}

            if PERSON_ID in cols and isinstance(join.this, exp.Table):
                # USING(person_id) connects this table to all previously seen tables
                right_alias = join.this.alias_or_name
                for left_alias in seen_tables:
                    edges.append((left_alias, right_alias))
                    edges.append((right_alias, left_alias))

            # Add this table to seen
            if isinstance(join.this, exp.Table):
                seen_tables.append(join.this.alias_or_name)

    return edges


def _build_graph(
    conditions: List[Tuple[exp.Column, exp.Column]],
    using_edges: List[Tuple[str, str]],
    aliases: Dict[str, str],
    clinical_aliases: Dict[str, str],
) -> Dict[str, Set[str]]:
    graph: Dict[str, Set[str]] = defaultdict(set)

    table_to_aliases = _build_table_to_aliases(aliases)

    # Explicit ON / WHERE joins
    for left, right in conditions:
        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if _norm(lc) != PERSON_ID or _norm(rc) != PERSON_ID:
            continue

        if not lt or not rt:
            continue

        left_aliases = table_to_aliases.get(_norm(lt), [])
        right_aliases = table_to_aliases.get(_norm(rt), [])

        for la in left_aliases:
            for ra in right_aliases:
                if la in clinical_aliases and ra in clinical_aliases:
                    graph[la].add(ra)
                    graph[ra].add(la)

    # USING edges (correctly scoped)
    for a, b in using_edges:
        if a in clinical_aliases and b in clinical_aliases:
            graph[a].add(b)
            graph[b].add(a)

    return graph


def _connected_components(graph: Dict[str, Set[str]], nodes: List[str]) -> List[Set[str]]:
    visited: Set[str] = set()
    components: List[Set[str]] = []

    for node in nodes:
        if node in visited:
            continue

        comp = set()
        queue = deque([node])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue

            visited.add(current)
            comp.add(current)

            for neigh in graph.get(current, set()):
                if neigh not in visited:
                    queue.append(neigh)

        components.append(comp)

    return components


# --- Detection -------------------------------------------------------------

def _detect_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[List[str], List[str]]]:
    clinical_aliases = _get_clinical_aliases(aliases)

    if len(clinical_aliases) < 2:
        return []

    conditions = _extract_join_conditions(tree)
    using_edges = _extract_using_edges(tree, aliases)

    # No joins at all → immediate violation
    if not conditions and not using_edges:
        return [(
            list(clinical_aliases.keys()),
            []
        )]

    graph = _build_graph(conditions, using_edges, aliases, clinical_aliases)

    components = _connected_components(graph, list(clinical_aliases.keys()))

    if len(components) <= 1:
        return []

    # Multiple disconnected components - return single violation with all aliases
    return [(list(clinical_aliases.keys()), [])]


# --- Rule ------------------------------------------------------------------

@register
class ClinicalPersonIdLinkageValidationRule(Rule):
    """Ensure clinical tables are linked via person_id."""

    rule_id = "joins.clinical_person_id_linkage_validation"
    name = "Clinical Tables Require Person ID Linkage"

    description = (
        "Clinical tables must be connected via person_id to ensure patient-level correctness."
    )

    severity = Severity.ERROR

    suggested_fix = "Add joins on person_id between clinical tables."

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            issues = _detect_violations(tree, aliases)

            for component, _ in issues:
                tables = [aliases[a] for a in component]

                msg = (
                    f"Clinical tables {tables} are not connected via person_id. "
                    f"This can cause cross-patient contamination."
                )

                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        details={"tables": tables},
                    )
                )

        return violations


__all__ = ["ClinicalPersonIdLinkageValidationRule"]
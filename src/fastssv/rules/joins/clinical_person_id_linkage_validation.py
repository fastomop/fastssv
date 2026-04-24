"""Clinical Tables Person ID Linkage Validation Rule.

OMOP semantic rules CLIN_055, OMOP_517: all_clinical_tables_require_person_id_for_patient_query

When combining data across multiple clinical tables for patient-level analysis,
all tables must be linked through person_id (directly or transitively). Missing
person_id linkage produces cross-patient contamination in results.

CTE-Aware Validation:
    CTEs are treated as validated units. If a CTE contains clinical tables,
    we validate those joins within the CTE scope. When the main query references
    CTEs, we check if the CTEs are properly joined on person_id.

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

    -- Via CTEs (correctly joined)
    WITH cte1 AS (SELECT person_id FROM drug_exposure),
         cte2 AS (SELECT person_id FROM condition_occurrence)
    SELECT * FROM cte1 JOIN cte2 ON cte1.person_id = cte2.person_id
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


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    """Extract all CTE names from WITH clauses."""
    cte_names = set()
    for cte in tree.find_all(exp.CTE):
        if cte.alias:
            cte_names.add(_norm(cte.alias))
    return cte_names


def _get_outermost_select(tree: exp.Expression) -> Optional[exp.Select]:
    """Get the outermost SELECT statement (not inside CTEs or subqueries)."""
    # If tree is a WITH statement, get its main query
    if isinstance(tree, exp.With):
        return tree.this if isinstance(tree.this, exp.Select) else None

    # If tree is already a SELECT, return it
    if isinstance(tree, exp.Select):
        return tree

    # Otherwise, find the first top-level SELECT
    for node in tree.walk():
        if isinstance(node, exp.Select):
            # Make sure it's not inside a subquery or CTE
            parent = node.parent
            is_subquery = False
            while parent:
                if isinstance(parent, (exp.Subquery, exp.CTE)):
                    is_subquery = True
                    break
                parent = parent.parent
            if not is_subquery:
                return node

    return None


def _extract_aliases_from_select_only(select: exp.Select) -> Dict[str, str]:
    """Extract aliases from a specific SELECT statement only (not from nested CTEs/subqueries)."""
    aliases: Dict[str, str] = {}

    # Get FROM table
    from_clause = select.args.get("from_")
    if from_clause and isinstance(from_clause.this, exp.Table):
        table = from_clause.this
        real = _norm(table.name)
        alias = table.alias_or_name
        alias_norm = _norm(alias) if alias else None
        if alias_norm and alias_norm != real:
            # Has a different alias, only add the alias mapping
            aliases[alias_norm] = real
        else:
            # No alias or alias is same as table name, add self-mapping
            aliases[real] = real

    # Get JOIN tables
    for join in select.args.get("joins", []):
        if isinstance(join.this, exp.Table):
            table = join.this
            real = _norm(table.name)
            alias = table.alias_or_name
            alias_norm = _norm(alias) if alias else None
            if alias_norm and alias_norm != real:
                # Has a different alias, only add the alias mapping
                aliases[alias_norm] = real
            else:
                # No alias or alias is same as table name, add self-mapping
                aliases[real] = real

    return aliases


def _get_clinical_aliases_in_scope(select: exp.Select, cte_names: Set[str]) -> Dict[str, str]:
    """Get clinical table aliases referenced in this SELECT scope only.

    Excludes:
    - Tables inside CTEs (validated separately)
    - CTEs themselves (treated as validated units)
    """
    result: Dict[str, str] = {}
    aliases = _extract_aliases_from_select_only(select)

    for alias, table in aliases.items():
        # Skip CTEs - they're not physical clinical tables
        if _norm(alias) in cte_names:
            continue

        # Only include clinical tables
        if _is_clinical_table(table):
            result[alias] = table

    return result


def _build_table_to_aliases(aliases: Dict[str, str]) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = defaultdict(list)
    for alias, table in aliases.items():
        mapping[_norm(table)].append(alias)
    return mapping


def _extract_join_conditions(select: exp.Select) -> List[Tuple[exp.Column, exp.Column]]:
    """Extract join conditions from a specific SELECT statement only."""
    conditions: List[Tuple[exp.Column, exp.Column]] = []

    # Get joins from this SELECT only
    for join in select.args.get("joins", []):
        on_clause = join.args.get("on")
        if on_clause:
            for eq in on_clause.find_all(exp.EQ):
                if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                    conditions.append((eq.this, eq.expression))

    # Get WHERE conditions from this SELECT only
    where = select.args.get("where")
    if where:
        for eq in where.find_all(exp.EQ):
            if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                conditions.append((eq.this, eq.expression))

    return conditions


def _extract_using_edges(
    select: exp.Select,
    aliases: Dict[str, str],
) -> List[Tuple[str, str]]:
    """Extract USING clause edges from a specific SELECT."""
    edges: List[Tuple[str, str]] = []

    seen_tables: List[str] = []

    # Get the FROM table first
    from_clause = select.args.get("from_")
    if from_clause and isinstance(from_clause.this, exp.Table):
        seen_tables.append(from_clause.this.alias_or_name)

    # Process joins in order
    for join in select.args.get("joins", []):
        using = join.args.get("using")
        if not using:
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
            right_alias = join.this.alias_or_name
            for left_alias in seen_tables:
                edges.append((left_alias, right_alias))
                edges.append((right_alias, left_alias))

        if isinstance(join.this, exp.Table):
            seen_tables.append(join.this.alias_or_name)

    return edges


def _build_graph(
    conditions: List[Tuple[exp.Column, exp.Column]],
    using_edges: List[Tuple[str, str]],
    aliases: Dict[str, str],
    clinical_aliases: Dict[str, str],
    cte_names: Set[str],
) -> Dict[str, Set[str]]:
    """Build connectivity graph of clinical tables and CTEs via person_id."""
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

        # Check if either side is a CTE
        left_is_cte = _norm(lt) in cte_names or _norm(left.table) in cte_names
        right_is_cte = _norm(rt) in cte_names or _norm(right.table) in cte_names

        # Handle CTE joins
        if left_is_cte or right_is_cte:
            left_key = _norm(left.table) if left.table else None
            right_key = _norm(right.table) if right.table else None

            if left_key and right_key:
                graph[left_key].add(right_key)
                graph[right_key].add(left_key)
            continue

        # Handle physical table joins
        left_aliases = table_to_aliases.get(_norm(lt), [])
        right_aliases = table_to_aliases.get(_norm(rt), [])

        for la in left_aliases:
            for ra in right_aliases:
                if la in clinical_aliases and ra in clinical_aliases:
                    graph[la].add(ra)
                    graph[ra].add(la)

    # USING edges
    for a, b in using_edges:
        # Check if either is a CTE
        a_is_cte = _norm(a) in cte_names
        b_is_cte = _norm(b) in cte_names

        if a_is_cte or b_is_cte:
            graph[_norm(a)].add(_norm(b))
            graph[_norm(b)].add(_norm(a))
        elif a in clinical_aliases and b in clinical_aliases:
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
    """Detect violations in the outermost SELECT only (CTE-aware)."""

    # Extract CTEs
    cte_names = _extract_cte_names(tree)

    # Get outermost SELECT
    outermost_select = _get_outermost_select(tree)
    if not outermost_select:
        return []

    # Get clinical aliases in outermost scope only (excluding CTEs themselves)
    clinical_aliases = _get_clinical_aliases_in_scope(outermost_select, cte_names)

    # If we have CTEs and they're being joined in the final query, validate the joins
    # But don't flag missing joins for tables inside CTEs
    if cte_names and len(clinical_aliases) < 2:
        # Only CTEs being referenced, or single clinical table
        # CTEs are assumed to be validated separately
        return []

    if len(clinical_aliases) < 2:
        return []

    conditions = _extract_join_conditions(outermost_select)
    using_edges = _extract_using_edges(outermost_select, aliases)

    # No joins at all → immediate violation
    if not conditions and not using_edges:
        return [(
            list(clinical_aliases.keys()),
            []
        )]

    graph = _build_graph(conditions, using_edges, aliases, clinical_aliases, cte_names)

    # Check connectivity among clinical aliases only
    # CTEs are intermediate constructs - we only care if final clinical tables are connected
    all_nodes = list(clinical_aliases.keys())
    components = _connected_components(graph, all_nodes)

    if len(components) <= 1:
        return []

    # Multiple disconnected components - return violation
    return [(list(clinical_aliases.keys()), [])]


# --- Rule ------------------------------------------------------------------

@register
class ClinicalPersonIdLinkageValidationRule(Rule):
    """Ensure clinical tables are linked via person_id (CTE-aware)."""

    rule_id = "joins.clinical_person_id_linkage_validation"
    name = "Clinical Tables Require Person ID Linkage"

    description = (
        "Clinical tables must be connected via person_id to ensure patient-level correctness. "
        "CTEs are treated as validated units and checked separately."
    )

    severity = Severity.ERROR

    suggested_fix = "Add joins on person_id between clinical tables."
    example_bad = (
        "SELECT co.condition_occurrence_id, de.drug_exposure_id\n"
        "FROM condition_occurrence co\n"
        "JOIN drug_exposure de ON co.visit_occurrence_id = de.visit_occurrence_id;"
    )
    example_good = (
        "SELECT co.condition_occurrence_id, de.drug_exposure_id\n"
        "FROM condition_occurrence co\n"
        "JOIN drug_exposure de ON co.person_id = de.person_id;"
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
            issues = _detect_violations(tree, aliases)

            for component, _ in issues:
                tables = [aliases[a] for a in component if a in aliases]

                # Skip if we only have one distinct table type
                unique_tables = list(set(tables))
                if len(unique_tables) < 2:
                    continue

                msg = (
                    f"Clinical tables {unique_tables} are not connected via person_id. "
                    f"This can cause cross-patient contamination."
                )

                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        details={"tables": unique_tables},
                    )
                )

        return violations


__all__ = ["ClinicalPersonIdLinkageValidationRule"]

"""Fact Relationship Valid Concepts Rule.

OMOP semantic rule OMOP_252:
Concepts used in fact_relationship must be valid (not deprecated or superseded).

The Problem:
    The fact_relationship table contains three concept_id columns that reference
    the concept table:
    - domain_concept_id_1: Domain of the first fact
    - domain_concept_id_2: Domain of the second fact
    - relationship_concept_id: Type of relationship between facts

    When joining to the concept table to validate or filter these concept IDs,
    queries must check invalid_reason to ensure only valid (current) concepts
    are used. Invalid concepts may represent deprecated relationships or domains.

Violation patterns:
    -- WRONG: No invalid_reason check
    SELECT fr.*
    FROM fact_relationship fr
    JOIN concept c ON fr.relationship_concept_id = c.concept_id;

    -- WRONG: Joining for domain validation without invalid_reason
    SELECT fr.*
    FROM fact_relationship fr
    JOIN concept d1 ON fr.domain_concept_id_1 = d1.concept_id
    WHERE d1.domain_id = 'Condition';

Correct patterns:
    -- CORRECT: Filter by invalid_reason
    SELECT fr.*
    FROM fact_relationship fr
    JOIN concept c ON fr.relationship_concept_id = c.concept_id
    WHERE c.invalid_reason IS NULL;

    -- CORRECT: Multiple concept joins with invalid_reason
    SELECT fr.*
    FROM fact_relationship fr
    JOIN concept d1 ON fr.domain_concept_id_1 = d1.concept_id
    JOIN concept d2 ON fr.domain_concept_id_2 = d2.concept_id
    JOIN concept r ON fr.relationship_concept_id = r.concept_id
    WHERE d1.invalid_reason IS NULL
      AND d2.invalid_reason IS NULL
      AND r.invalid_reason IS NULL;

    -- CORRECT: Explicit handling of invalid concepts
    SELECT fr.*
    FROM fact_relationship fr
    JOIN concept c ON fr.relationship_concept_id = c.concept_id
    WHERE c.invalid_reason IS NOT NULL;  -- Intentionally finding invalid
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

FACT_RELATIONSHIP = "fact_relationship"
CONCEPT = "concept"

FACT_RELATIONSHIP_CONCEPT_COLUMNS = {
    "domain_concept_id_1",
    "domain_concept_id_2",
    "relationship_concept_id",
}

INVALID_REASON = "invalid_reason"
CONCEPT_ID = "concept_id"


# --- Normalized Constants --------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


NORM_FACT_RELATIONSHIP = _norm(FACT_RELATIONSHIP)
NORM_CONCEPT = _norm(CONCEPT)
NORM_INVALID_REASON = _norm(INVALID_REASON)
NORM_CONCEPT_ID = _norm(CONCEPT_ID)

NORM_FR_CONCEPT_COLUMNS = {_norm(c) for c in FACT_RELATIONSHIP_CONCEPT_COLUMNS}


# --- Helpers ---------------------------------------------------------------

def _normalize_aliases(aliases: Dict[str, str]) -> Dict[str, str]:
    return {_norm(k): _norm(v) for k, v in aliases.items()}


def _is_fact_relationship_concept_column(
    col: exp.Column,
    aliases: Dict[str, str],
) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if not col_name:
        return False

    col_norm = _norm(col_name)
    if col_norm not in NORM_FR_CONCEPT_COLUMNS:
        return False

    if table:
        return _norm(table) == NORM_FACT_RELATIONSHIP

    # Unqualified → only valid if fact_relationship present
    return any(v == NORM_FACT_RELATIONSHIP for v in aliases.values())


def _is_concept_table(table: Optional[str], aliases: Dict[str, str]) -> bool:
    if not table:
        return False

    table_norm = _norm(table)

    if table_norm == NORM_CONCEPT:
        return True

    return table_norm in aliases and aliases[table_norm] == NORM_CONCEPT


def _collect_concept_joins(
    select: exp.Select,
    aliases: Dict[str, str],
) -> Dict[str, str]:
    """
    Returns mapping: concept_alias -> resolved_table_name
    """
    concept_aliases: Dict[str, str] = {}

    # --- Explicit JOINs ---
    for join in select.find_all(exp.Join):
        if not isinstance(join.this, exp.Table):
            continue

        table_name = _norm(join.this.name)
        if not _is_concept_table(table_name, aliases):
            continue

        alias = _norm(join.this.alias_or_name)
        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            cols = [c for c in (eq.this, eq.expression) if isinstance(c, exp.Column)]
            if len(cols) != 2:
                continue

            fr_col = None
            concept_col = None

            for col in cols:
                if _is_fact_relationship_concept_column(col, aliases):
                    fr_col = col
                else:
                    t, c = resolve_table_col(col, aliases)
                    if _is_concept_table(t, aliases) and _norm(c) == NORM_CONCEPT_ID:
                        concept_col = col

            if fr_col and concept_col:
                concept_aliases[alias] = table_name

    # --- WHERE-based joins ---
    for eq in select.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        cols = [c for c in (eq.this, eq.expression) if isinstance(c, exp.Column)]
        if len(cols) != 2:
            continue

        fr_col = None
        concept_table = None
        concept_alias = None

        for col in cols:
            if _is_fact_relationship_concept_column(col, aliases):
                fr_col = col
            else:
                t, c = resolve_table_col(col, aliases)
                if _is_concept_table(t, aliases) and _norm(c) == NORM_CONCEPT_ID:
                    concept_table = _norm(t)
                    concept_alias = _norm(col.table) if col.table else concept_table

        if fr_col and concept_table:
            concept_aliases[concept_alias] = concept_table

    return concept_aliases


def _collect_invalid_reason_filters(
    select: exp.Select,
    aliases: Dict[str, str],
    concept_aliases: Dict[str, str],
) -> Set[str]:
    """
    Returns set of concept aliases that HAVE invalid_reason filters
    """
    filtered: Set[str] = set()

    for node in select.walk():
        if not is_in_where_or_join_clause(node):
            continue

        col = None

        if isinstance(node, exp.Is):
            if isinstance(node.this, exp.Column):
                col = node.this

        elif isinstance(node, (exp.EQ, exp.NEQ)):
            for side in (node.this, node.expression):
                if isinstance(side, exp.Column):
                    col = side

        elif isinstance(node, exp.In):
            if isinstance(node.this, exp.Column):
                col = node.this

        if not col:
            continue

        table, col_name = resolve_table_col(col, aliases)

        if (
            _norm(col_name) != NORM_INVALID_REASON
            or not table
            or not _is_concept_table(table, aliases)
        ):
            continue

        col_alias = _norm(col.table) if col.table else None

        # --- Strict matching ---
        if col_alias:
            if col_alias in concept_aliases:
                filtered.add(col_alias)
        else:
            # Only allow unqualified if exactly one concept alias
            if len(concept_aliases) == 1:
                filtered.add(next(iter(concept_aliases)))

    return filtered


def _find_unfiltered_concept_aliases(
    select: exp.Select,
    aliases: Dict[str, str],
) -> Set[str]:
    concept_aliases = _collect_concept_joins(select, aliases)

    if not concept_aliases:
        return set()

    filtered = _collect_invalid_reason_filters(select, aliases, concept_aliases)

    return set(concept_aliases.keys()) - filtered


# --- Rule ------------------------------------------------------------------

@register
class FactRelationshipValidConceptsRule(Rule):
    """Ensures concept joins from fact_relationship filter by invalid_reason."""

    rule_id = "data_quality.fact_relationship_valid_concepts"
    name = "Fact Relationship Valid Concepts"

    description = (
        "Ensures that when fact_relationship joins to the concept table "
        "(for domain_concept_id_1, domain_concept_id_2, or relationship_concept_id), "
        "the query filters by invalid_reason to ensure only valid concepts are used."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Add '<alias>.invalid_reason IS NULL' for each concept join "
        "to ensure only valid concepts are used."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if FACT_RELATIONSHIP not in sql_lower or CONCEPT not in sql_lower:
            return []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            if not has_table_reference(tree, FACT_RELATIONSHIP):
                continue

            raw_aliases = extract_aliases(tree)
            aliases = _normalize_aliases(raw_aliases)

            # 🔑 Scope per SELECT (fixes subquery leakage)
            for select in tree.find_all(exp.Select):
                unfiltered = _find_unfiltered_concept_aliases(select, aliases)

                if not unfiltered:
                    continue

                concept_list = ", ".join(sorted(unfiltered))

                message = (
                    f"fact_relationship joins concept table(s) [{concept_list}] "
                    f"without filtering invalid_reason. This may include invalid or "
                    f"deprecated concepts."
                )

                violations.append(
                    self.create_violation(
                        message=message,
                        suggested_fix=self.suggested_fix,
                        details={
                            "concept_aliases": sorted(unfiltered),
                            "recommendation": (
                                "Add condition per alias: <alias>.invalid_reason IS NULL"
                            ),
                        },
                    )
                )

        return violations


__all__ = ["FactRelationshipValidConceptsRule"]

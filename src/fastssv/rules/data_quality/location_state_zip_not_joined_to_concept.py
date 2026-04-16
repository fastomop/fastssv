"""Location State/Zip Not Joined to Concept Rule.

OMOP semantic rule OMOP_106:
location.state and location.zip are free-text VARCHAR fields, not concept_ids.
They should not be joined to the concept table. Only location.country_concept_id
is a foreign key to concept.

The Problem:
    The location table contains both free-text fields and concept_id references:
    - state: VARCHAR field (e.g., 'CA', 'New York')
    - zip: VARCHAR field (e.g., '90210', '10001')
    - country_concept_id: INTEGER foreign key to concept.concept_id

    Developers might mistakenly try to join the free-text fields to the concept
    table, treating them as if they were concept references. This is incorrect
    and will produce unexpected results.

Violation patterns:
    -- WRONG: Joining state to concept
    SELECT * FROM location l
    JOIN concept c ON l.state = c.concept_code;

    -- WRONG: Joining zip to concept_id
    SELECT * FROM location l
    JOIN concept c ON l.zip = c.concept_id;

    -- WRONG: Joining state in WHERE clause
    SELECT * FROM location l, concept c
    WHERE l.state = c.concept_name;

Correct patterns:
    -- CORRECT: Using state as free text
    SELECT * FROM location WHERE state = 'CA';

    -- CORRECT: Joining country_concept_id
    SELECT * FROM location l
    JOIN concept c ON l.country_concept_id = c.concept_id
    WHERE c.domain_id = 'Geography';

    -- CORRECT: Using zip in filtering
    SELECT * FROM location WHERE zip LIKE '902%';
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    uses_table,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

LOCATION = "location"
CONCEPT = "concept"

LOCATION_FREE_TEXT_FIELDS = {"state", "zip"}
LOCATION_CONCEPT_FIELD = "country_concept_id"


# --- Normalized Constants --------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


NORM_LOCATION = _norm(LOCATION)
NORM_CONCEPT = _norm(CONCEPT)

NORM_FREE_TEXT_FIELDS = {_norm(f) for f in LOCATION_FREE_TEXT_FIELDS}
NORM_LOCATION_CONCEPT_FIELD = _norm(LOCATION_CONCEPT_FIELD)


# --- Helpers ---------------------------------------------------------------

def _normalize_aliases(aliases: Dict[str, str]) -> Dict[str, str]:
    return {_norm(k): _norm(v) for k, v in aliases.items()}


def _get_table_aliases(
    aliases: Dict[str, str],
    table_name: str,
) -> Set[str]:
    return {k for k, v in aliases.items() if v == table_name}


def _resolve_location_column(
    col: exp.Column,
    aliases: Dict[str, str],
    location_aliases: Set[str],
) -> Optional[Tuple[str, str]]:
    """
    Returns (alias, column_name) if column is from location table.
    """
    table, col_name = resolve_table_col(col, aliases)

    if not col_name:
        return None

    col_norm = _norm(col_name)

    # Only care about free-text fields
    if col_norm not in NORM_FREE_TEXT_FIELDS:
        return None

    if table:
        table_norm = _norm(table)
        if table_norm in location_aliases:
            return table_norm, col_norm
        return None

    # Unqualified → only if exactly one location alias
    if len(location_aliases) == 1:
        return next(iter(location_aliases)), col_norm

    return None


def _resolve_concept_column(
    col: exp.Column,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> Optional[Tuple[str, str]]:
    """
    Returns (alias, column_name) if column is from concept table.
    """
    table, col_name = resolve_table_col(col, aliases)

    if not col_name:
        return None

    if table:
        table_norm = _norm(table)
        if table_norm in concept_aliases:
            return table_norm, _norm(col_name)
        return None

    # Unqualified → only if exactly one concept alias
    if len(concept_aliases) == 1:
        return next(iter(concept_aliases)), _norm(col_name)

    return None


def _detect_invalid_location_concept_comparisons(
    select: exp.Select,
    aliases: Dict[str, str],
) -> List[str]:
    """
    Detect invalid comparisons between location.state/zip and concept columns.
    """
    violations: List[str] = []

    location_aliases = _get_table_aliases(aliases, NORM_LOCATION)
    concept_aliases = _get_table_aliases(aliases, NORM_CONCEPT)

    if not location_aliases or not concept_aliases:
        return violations

    for node in select.walk():
        if not isinstance(node, exp.EQ):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        right = node.expression

        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        left_loc = _resolve_location_column(left, aliases, location_aliases)
        right_loc = _resolve_location_column(right, aliases, location_aliases)

        left_con = _resolve_concept_column(left, aliases, concept_aliases)
        right_con = _resolve_concept_column(right, aliases, concept_aliases)

        # location ↔ concept comparison
        if left_loc and right_con:
            loc_alias, loc_col = left_loc
            con_alias, con_col = right_con

        elif right_loc and left_con:
            loc_alias, loc_col = right_loc
            con_alias, con_col = left_con

        else:
            continue

        # ✅ Explicitly allow valid join
        if loc_col == NORM_LOCATION_CONCEPT_FIELD:
            continue

        violations.append(
            f"{loc_alias}.{loc_col} = {con_alias}.{con_col}"
        )

    return violations


# --- Rule ------------------------------------------------------------------

@register
class LocationStateZipNotJoinedToConceptRule(Rule):
    """Detects incorrect joins of location.state/zip to concept table."""

    rule_id = "data_quality.location_state_zip_not_joined_to_concept"
    name = "Location State/Zip Not Joined to Concept"

    description = (
        "Ensures that location.state and location.zip (free-text VARCHAR fields) "
        "are not compared or joined to the concept table. Only "
        "location.country_concept_id should be used for concept joins."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Remove comparisons between location.state/zip and concept table. "
        "Use location.country_concept_id for joins, and treat state/zip "
        "as free-text fields."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if LOCATION not in sql_lower or CONCEPT not in sql_lower:
            return []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            if not (uses_table(tree, LOCATION) and uses_table(tree, CONCEPT)):
                continue

            raw_aliases = extract_aliases(tree)
            aliases = _normalize_aliases(raw_aliases)

            seen_patterns: Set[str] = set()

            # Scope per SELECT
            for select in tree.find_all(exp.Select):
                detected = _detect_invalid_location_concept_comparisons(
                    select, aliases
                )

                if not detected:
                    continue

                for pattern in detected:
                    if pattern in seen_patterns:
                        continue

                    seen_patterns.add(pattern)

                    message = (
                        f"Invalid comparison detected: {pattern}. "
                        f"location.state and location.zip are free-text fields "
                        f"and must not be joined or compared to concept table."
                    )

                    violations.append(
                        self.create_violation(
                            message=message,
                            suggested_fix=self.suggested_fix,
                            details={
                                "pattern": pattern,
                                "recommendation": (
                                    "Use location.country_concept_id for concept joins. "
                                    "Do not compare free-text fields to concept columns."
                                ),
                            },
                        )
                    )

        return violations


__all__ = ["LocationStateZipNotJoinedToConceptRule"]
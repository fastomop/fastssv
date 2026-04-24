"""No DISTINCT on Primary Key Column Rule.

OMOP semantic rule OMOP_110:
Using DISTINCT on a primary key column is redundant since primary keys are unique
by definition. This suggests a misunderstanding of the data or an unintended
Cartesian join.

The Problem:
    Primary key columns in OMOP CDM tables are unique by definition:
    - condition_occurrence_id, drug_exposure_id, procedure_occurrence_id, etc.

    Using DISTINCT on these columns is either:
    1. Redundant: If querying a single table without joins
    2. Hiding a join problem: If joins introduce duplicates (Cartesian product)

    The presence of DISTINCT on a primary key suggests:
    - Misunderstanding of data uniqueness
    - Missing or incorrect join conditions
    - Unnecessary performance overhead

Violation patterns:
    -- WRONG: Redundant DISTINCT on primary key
    SELECT DISTINCT condition_occurrence_id
    FROM condition_occurrence
    WHERE condition_concept_id = 201826;

    -- WRONG: Hiding join issues
    SELECT DISTINCT de.drug_exposure_id
    FROM drug_exposure de, person p;  -- Missing join condition

Correct patterns:
    -- CORRECT: No DISTINCT needed
    SELECT condition_occurrence_id
    FROM condition_occurrence
    WHERE condition_concept_id = 201826;

    -- CORRECT: DISTINCT on non-PK column
    SELECT DISTINCT person_id
    FROM condition_occurrence
    WHERE condition_concept_id = 201826;

    -- CORRECT: DISTINCT with joins (though may indicate join issue)
    SELECT DISTINCT de.drug_exposure_id
    FROM drug_exposure de
    JOIN person p ON de.person_id = p.person_id;
"""

from typing import Dict, List, Optional, Tuple

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

PRIMARY_KEY_COLUMNS: Dict[str, str] = {
    "condition_occurrence_id": "condition_occurrence",
    "drug_exposure_id": "drug_exposure",
    "procedure_occurrence_id": "procedure_occurrence",
    "measurement_id": "measurement",
    "observation_id": "observation",
    "device_exposure_id": "device_exposure",
    "visit_occurrence_id": "visit_occurrence",
    "visit_detail_id": "visit_detail",
    "specimen_id": "specimen",
    "note_id": "note",
    "note_nlp_id": "note_nlp",
    "death_id": "death",
    "episode_id": "episode",
    "episode_event_id": "episode_event",
    "cost_id": "cost",
    "payer_plan_period_id": "payer_plan_period",
    "care_site_id": "care_site",
    "location_id": "location",
    "provider_id": "provider",
    "person_id": "person",
}


# --- Normalized Constants --------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


NORM_PRIMARY_KEY_COLUMNS = {
    _norm(k): _norm(v) for k, v in PRIMARY_KEY_COLUMNS.items()
}


# --- Helpers ---------------------------------------------------------------

def _normalize_aliases(aliases: Dict[str, str]) -> Dict[str, str]:
    return {_norm(k): _norm(v) for k, v in aliases.items()}


def _has_joins(select: exp.Select) -> bool:
    return any(select.find_all(exp.Join))


def _is_distinct_on(select: exp.Select) -> bool:
    distinct = select.args.get("distinct")
    if isinstance(distinct, exp.Distinct) and distinct.args.get("on"):
        return True
    return False


def _analyze_select_columns(
    select: exp.Select,
    aliases: Dict[str, str],
) -> Tuple[List[str], List[str]]:
    """
    Returns (pk_columns, non_pk_columns)
    """
    pk_columns: List[str] = []
    non_pk_columns: List[str] = []

    # Get tables in THIS SELECT's FROM clause only
    from_tables = set()
    from_clause = select.args.get("from_")
    if from_clause and isinstance(from_clause, exp.From):
        for table_expr in from_clause.find_all(exp.Table):
            table_name = _norm(table_expr.name)
            from_tables.add(aliases.get(table_name, table_name))

    expressions = select.expressions or []

    for expr in expressions:
        if isinstance(expr, exp.Alias):
            expr = expr.this

        if not isinstance(expr, exp.Column):
            non_pk_columns.append("expr")
            continue

        table, col_name = resolve_table_col(expr, aliases)

        if not col_name:
            non_pk_columns.append("unknown")
            continue

        col_norm = _norm(col_name)

        if col_norm not in NORM_PRIMARY_KEY_COLUMNS:
            non_pk_columns.append(col_name)
            continue

        expected_table = NORM_PRIMARY_KEY_COLUMNS[col_norm]

        if table:
            table_norm = _norm(table)
            actual_table = aliases.get(table_norm, table_norm)

            if actual_table == expected_table:
                pk_columns.append(col_name)
            else:
                non_pk_columns.append(col_name)
        else:
            # Unqualified column → check if expected table is in THIS SELECT's FROM clause
            # NOT just anywhere in the query
            if expected_table in from_tables:
                pk_columns.append(col_name)
            else:
                non_pk_columns.append(col_name)

    return pk_columns, non_pk_columns


# --- Rule ------------------------------------------------------------------

@register
class NoDistinctOnPrimaryKeyColumnRule(Rule):
    """Detects redundant DISTINCT on primary key columns."""

    rule_id = "anti_patterns.no_distinct_on_primary_key_column"
    name = "No DISTINCT on Primary Key Column"

    description = (
        "Detects redundant use of DISTINCT on primary key columns, which are "
        "unique by definition. This may indicate a misunderstanding of the data "
        "or missing join conditions."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Remove DISTINCT when selecting only primary key columns. "
        "If joins are present, review join conditions for unintended duplicates."
    )
    long_description = (
        "DISTINCT on a primary-key column is a tautology: primary keys are "
        "unique by definition, so the de-duplication never actually "
        "removes a row. Its appearance usually signals one of two things: "
        "the author doesn't realise the column is a primary key (worth "
        "double-checking the table model), or they suspect a JOIN is "
        "causing duplicates and are papering over it with DISTINCT instead "
        "of fixing the join. Remove the DISTINCT and, if duplicates do "
        "appear, tighten the join predicate."
    )
    example_bad = (
        "SELECT DISTINCT person_id\n"
        "FROM person;"
    )
    example_good = (
        "SELECT person_id\n"
        "FROM person;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            aliases = _normalize_aliases(extract_aliases(tree))

            for select in tree.find_all(exp.Select):
                # Must have DISTINCT
                if not select.args.get("distinct"):
                    continue

                # Skip DISTINCT ON (Postgres-specific valid use case)
                if _is_distinct_on(select):
                    continue

                pk_columns, non_pk_columns = _analyze_select_columns(select, aliases)

                if not pk_columns:
                    continue

                has_joins = _has_joins(select)

                for pk_col in pk_columns:
                    if has_joins:
                        message = (
                            f"DISTINCT used on primary key column '{pk_col}' in a query with joins. "
                            f"Primary keys are unique by definition. This may indicate incorrect joins."
                        )
                        suggestion = (
                            "Review join conditions to ensure they do not introduce duplicates."
                        )
                    else:
                        message = (
                            f"Redundant DISTINCT used on primary key column '{pk_col}'. "
                            f"Primary keys are unique by definition."
                        )
                        suggestion = (
                            f"Remove DISTINCT when selecting '{pk_col}'."
                        )

                    violations.append(
                        self.create_violation(
                            message=message,
                            suggested_fix=suggestion,
                            details={
                                "column": pk_col,
                                "has_joins": has_joins,
                                "recommendation": (
                                    "Primary keys are inherently unique; DISTINCT is unnecessary."
                                ),
                            },
                        )
                    )

        return violations


__all__ = ["NoDistinctOnPrimaryKeyColumnRule"]

"""Comma-Separated Cross Join Rule.

OMOP semantic rule GAP_035:
Listing tables in FROM with commas and no WHERE join condition produces a
Cartesian cross join. In OMOP with tables containing millions of rows, this
generates billions of rows and crashes queries.

The Problem:
    Comma-separated FROM clauses without proper join conditions create
    Cartesian products (cross joins):

    SELECT * FROM condition_occurrence, drug_exposure
    WHERE condition_concept_id = 201826
    -- WRONG: No join condition! Creates 10M × 50M = 500 BILLION rows!

    Each clinical table in OMOP has millions of rows:
    - condition_occurrence: ~10M rows
    - drug_exposure: ~50M rows
    - measurement: ~100M rows
    - observation: ~50M rows

    Without a join condition (co.person_id = de.person_id), the query
    creates every possible combination of rows from both tables.

    This causes:
    - Out of memory errors
    - Database crashes
    - Production system locks
    - Hours of wasted compute time

Common mistakes:
    1. Forgot to add WHERE join condition
    2. Should have used JOIN...ON instead of comma syntax
    3. Accidentally omitted join predicate in WHERE clause
    4. Mixed old comma syntax with modern JOIN syntax

Violation pattern:
    SELECT *
    FROM condition_occurrence, drug_exposure
    WHERE condition_concept_id = 201826
    -- WRONG: Filters condition, but no join between tables!

    SELECT co.person_id, de.drug_concept_id
    FROM condition_occurrence co, drug_exposure de, measurement m
    WHERE co.condition_concept_id = 201826
      AND de.drug_concept_id = 1545999
    -- WRONG: Multiple clinical tables with no join conditions!

Correct pattern:
    SELECT *
    FROM condition_occurrence co
    JOIN drug_exposure de ON co.person_id = de.person_id
    WHERE co.condition_concept_id = 201826
    -- CORRECT: Explicit JOIN...ON

    SELECT *
    FROM condition_occurrence co, drug_exposure de
    WHERE co.person_id = de.person_id
      AND co.condition_concept_id = 201826
    -- CORRECT: Comma with WHERE join condition
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

# Large clinical tables where cross joins are catastrophic
LARGE_CLINICAL_TABLES = {
    "condition_occurrence",
    "drug_exposure",
    "procedure_occurrence",
    "measurement",
    "observation",
    "device_exposure",
    "visit_occurrence",
    "visit_detail",
    "specimen",
    "note",
    "episode",
    "person",
    "death",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_large_clinical_table(table: Optional[str]) -> bool:
    return _norm(table) in LARGE_CLINICAL_TABLES if table else False


def _get_comma_separated_tables(tree: exp.Expression) -> List[List[str]]:
    """Find FROM clauses with comma-separated tables.

    Sqlglot represents comma-separated tables as Join nodes with kind=None.
    Returns list of table groups, where each group has 2+ comma-separated tables.
    """
    comma_groups: List[List[str]] = []

    # Look for Join nodes with kind=None (these are comma joins)
    for select in tree.find_all(exp.Select):
        tables: List[str] = []

        # Get the FROM table
        from_node = select.find(exp.From)
        if from_node:
            # Get the first table from FROM clause
            from_table = from_node.this
            if isinstance(from_table, exp.Table):
                tables.append(from_table.name)

        # Find comma joins (Join with kind=None and no ON clause)
        for join in select.find_all(exp.Join):
            kind = join.args.get("kind")
            on_clause = join.args.get("on")

            # Comma joins have kind=None and no ON clause
            if kind is None and on_clause is None:
                join_table = join.this
                if isinstance(join_table, exp.Table):
                    tables.append(join_table.name)

        # If we have 2+ tables, this is a comma-separated FROM
        if len(tables) >= 2:
            comma_groups.append(tables)

    return comma_groups


def _has_join_condition_in_where(
    tree: exp.Expression,
    tables: List[str],
    aliases: Dict[str, str],
) -> bool:
    """Check if WHERE clause has column-to-column equality joining the tables."""

    # Normalize table names for comparison
    table_set = {_norm(t) for t in tables}

    # Find WHERE clause
    where = tree.find(exp.Where)
    if not where:
        return False

    # Look for column = column equalities
    for eq in where.find_all(exp.EQ):
        left = eq.this
        right = eq.expression

        # Both sides must be columns
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        # Resolve table names for both columns
        left_table, _ = resolve_table_col(left, aliases)
        right_table, _ = resolve_table_col(right, aliases)

        # Skip if we can't resolve table names
        if not left_table or not right_table:
            continue

        # Normalize
        left_table_norm = _norm(left_table)
        right_table_norm = _norm(right_table)

        # Check if this equality joins two of our comma-separated tables
        if left_table_norm in table_set and right_table_norm in table_set:
            if left_table_norm != right_table_norm:
                # Found a join condition between different tables
                return True

    return False


# --- Rule ------------------------------------------------------------------

@register
class CommaSeparatedCrossJoinRule(Rule):
    """Detect accidental Cartesian products from comma-separated tables."""

    rule_id = "anti_patterns.comma_separated_cross_join"
    name = "Comma-Separated Cross Join"

    description = (
        "Detects comma-separated FROM clauses with multiple clinical tables "
        "that have no join condition in WHERE clause. This creates Cartesian "
        "products that can generate billions of rows and crash queries."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Use explicit JOIN...ON syntax, or add a WHERE clause with column-to-column "
        "equality to join the tables (e.g., WHERE co.person_id = de.person_id)."
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

            # Find comma-separated table groups
            comma_groups = _get_comma_separated_tables(tree)

            for tables in comma_groups:
                # Check if any are large clinical tables
                has_large_table = any(_is_large_clinical_table(t) for t in tables)

                if not has_large_table:
                    # Skip if no large clinical tables (e.g., vocabulary table cross joins)
                    continue

                # Check if there's a join condition in WHERE
                has_join = _has_join_condition_in_where(tree, tables, aliases)

                if not has_join:
                    # Violation: comma-separated tables with no join condition
                    table_list = ", ".join(tables)

                    violations.append(
                        self.create_violation(
                            message=(
                                f"Comma-separated FROM clause with large clinical tables "
                                f"({table_list}) but no join condition in WHERE clause. "
                                f"This creates a Cartesian product that can generate billions "
                                f"of rows and crash the query."
                            ),
                            severity=self.severity,
                            suggested_fix=self.suggested_fix,
                            details={
                                "issue": "comma_separated_cross_join",
                                "tables": tables,
                            },
                        )
                    )

        return violations


__all__ = ["CommaSeparatedCrossJoinRule"]

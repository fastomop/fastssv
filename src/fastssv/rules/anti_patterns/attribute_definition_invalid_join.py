"""Attribute Definition Invalid Join Rule.

OMOP semantic rule GAP_040:
The attribute_definition table is an optional legacy table in OMOP CDM v5.4 with
no foreign key relationships to any other table. It should only be queried standalone.

The Problem:
    attribute_definition is a metadata table with:
    - Single column: attribute_definition_id (primary key)
    - No foreign keys: edges = {} in CDM schema
    - No semantic relationships to clinical or vocabulary data
    - Legacy/optional status: May not be used in modern CDM implementations

    Any JOIN involving attribute_definition is semantically incorrect because there
    are no valid foreign key relationships. The table cannot be meaningfully joined
    to person, condition_occurrence, concept, or any other OMOP table.

Common mistakes:
    1. Assuming attribute_definition_id links to other tables
    2. Trying to cross-reference with clinical event IDs
    3. Attempting to join to vocabulary tables
    4. Including in multi-table queries without understanding its isolation

Violation patterns:
    -- WRONG: Joining to person table
    SELECT * FROM attribute_definition ad
    JOIN person p ON ad.attribute_definition_id = p.person_id
    -- No semantic relationship - these IDs are unrelated

    -- WRONG: Cross join with clinical tables
    SELECT * FROM attribute_definition ad, condition_occurrence co
    -- Creates meaningless Cartesian product

    -- WRONG: LEFT JOIN from other tables
    SELECT * FROM person p
    LEFT JOIN attribute_definition ad ON p.person_id = ad.attribute_definition_id
    -- No foreign key relationship exists

Correct patterns:
    -- CORRECT: Standalone query
    SELECT * FROM attribute_definition

    -- CORRECT: Standalone with filter
    SELECT * FROM attribute_definition
    WHERE attribute_definition_id = 123

    -- CORRECT: Not using it at all (it's optional)
    SELECT * FROM person
    JOIN condition_occurrence ON ...

Note: This table is rarely used in modern OMOP implementations. If you're seeing
this table in your query, verify that you actually need it.
"""

from typing import List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql, has_table_reference
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

ATTRIBUTE_DEFINITION = "attribute_definition"
NORM_ATTRIBUTE_DEFINITION = normalize_name(ATTRIBUTE_DEFINITION)


# --- Helpers ---------------------------------------------------------------


def _norm(x: str) -> str:
    return normalize_name(x) if x else ""


def _get_tables_in_select_scope(select: exp.Select) -> Set[str]:
    """
    Extract table names from a single SELECT scope only.
    Does not include tables from nested subqueries.
    """
    tables: Set[str] = set()

    def belongs_to_select(node: exp.Expression) -> bool:
        parent_select = node.find_ancestor(exp.Select)
        return parent_select is select

    for table in select.find_all(exp.Table):
        if not belongs_to_select(table):
            continue

        if table.name:
            tables.add(_norm(table.name))

    return tables


# --- Rule ------------------------------------------------------------------


@register
class AttributeDefinitionInvalidJoinRule(Rule):
    """
    Detect invalid joins involving the attribute_definition table.

    attribute_definition is a legacy OMOP table with no foreign key relationships.
    It should not be used in multi-table queries.
    """

    rule_id = "anti_patterns.attribute_definition_invalid_join"
    name = "Attribute Definition Invalid Join"

    description = (
        "attribute_definition is a legacy table with no foreign key relationships "
        "to other OMOP tables. It cannot be meaningfully joined and should only "
        "be queried standalone."
    )

    severity = Severity.ERROR

    suggested_fix = "REMOVE: any JOIN involving attribute_definition. The table has no FK relationships in OMOP CDM. Either query it standalone (SELECT ... FROM attribute_definition WHERE ...) or drop it from the query entirely."
    long_description = (
        "attribute_definition is a legacy OMOP table with no foreign-key "
        "relationships to any other CDM table. Joining it to clinical or "
        "vocabulary tables produces either a Cartesian product or spurious "
        "matches on coincidentally-equal integer values. Query it on its "
        "own when you need attribute metadata, and drop any JOINs against "
        "it."
    )
    example_bad = "SELECT *\nFROM person p\nJOIN attribute_definition ad ON p.person_id = ad.attribute_definition_id;"
    example_good = "SELECT *\nFROM attribute_definition;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            # Early skip if attribute_definition not used anywhere in query
            if not has_table_reference(tree, ATTRIBUTE_DEFINITION):
                continue

            # Check each SELECT scope independently
            for select in tree.find_all(exp.Select):
                tables = _get_tables_in_select_scope(select)

                if NORM_ATTRIBUTE_DEFINITION not in tables:
                    continue

                # If more than one table in this SELECT → invalid usage
                if len(tables) > 1:
                    other_tables = [t for t in tables if t != NORM_ATTRIBUTE_DEFINITION]

                    other_tables_str = ", ".join(sorted(other_tables)[:3])
                    if len(other_tables) > 3:
                        other_tables_str += f", ... ({len(other_tables)} total)"

                    violations.append(
                        self.create_violation(
                            message=(
                                f"Invalid join: attribute_definition is used with other tables "
                                f"({other_tables_str}). This legacy table has no foreign key "
                                f"relationships in OMOP CDM and cannot be meaningfully joined."
                            ),
                            severity=self.severity,
                            suggested_fix=self.suggested_fix,
                            details={
                                "table": ATTRIBUTE_DEFINITION,
                                "other_tables": list(other_tables),
                                "table_count": len(tables),
                            },
                        )
                    )

        return violations


__all__ = ["AttributeDefinitionInvalidJoinRule"]

"""Vocabulary Table Protection Rule.

OMOP semantic rule OMOP_081:
Vocabulary tables (concept, concept_relationship, concept_ancestor, concept_synonym,
vocabulary, domain, concept_class, relationship, drug_strength, source_to_concept_map)
are reference tables managed by the OHDSI vocabulary team.

Analytical queries should never issue DELETE, UPDATE, INSERT, TRUNCATE, or DROP
against vocabulary tables, as this can corrupt the reference data.

The Problem:
    Vocabulary tables contain standardized reference data that all analytical queries
    depend on. Modifying these tables can:
    - Break all downstream queries that reference affected concepts
    - Corrupt the vocabulary structure
    - Require a full vocabulary reload to recover

    Common mistakes:
    - DELETE FROM concept WHERE concept_id = 0
    - UPDATE concept SET concept_name = 'xyz' WHERE ...
    - INSERT INTO vocabulary VALUES (...)
    - TRUNCATE TABLE concept_ancestor

Correct approach:
    Vocabulary tables should only be queried (SELECT), never modified in analytical
    queries. Vocabulary updates are managed through official OHDSI vocabulary releases.
"""

from typing import List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

VOCABULARY_TABLES = {
    "concept",
    "concept_relationship",
    "concept_ancestor",
    "concept_synonym",
    "vocabulary",
    "domain",
    "concept_class",
    "relationship",
    "drug_strength",
    "source_to_concept_map",
}

DML_DDL_NODES = (
    exp.Delete,
    exp.Update,
    exp.Insert,
    exp.TruncateTable,
    exp.Drop,
    exp.Merge,
)


# --- Helpers ---------------------------------------------------------------

def _normalize_table_name(name: str) -> str:
    """Normalize table name and strip schema if present."""
    return normalize_name(name.split(".")[-1])


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    """Collect CTE names to avoid false positives."""
    cte_names: Set[str] = set()

    with_node = tree.find(exp.With)
    if not with_node:
        return cte_names

    for cte in with_node.find_all(exp.CTE):
        if cte.alias:
            cte_names.add(normalize_name(cte.alias))

    return cte_names


def _get_target_tables(node: exp.Expression) -> List[str]:
    """
    Extract all target tables from a DML/DDL statement.
    Includes primary target and any explicitly referenced tables.
    """
    tables: List[str] = []

    # Primary target (DELETE/UPDATE/INSERT/MERGE/etc.)
    if hasattr(node, "this") and isinstance(node.this, exp.Table):
        tables.append(str(node.this.name))

    # INSERT INTO schema.table (Schema node)
    if isinstance(node, exp.Insert) and isinstance(node.this, exp.Schema):
        if isinstance(node.this.this, exp.Table):
            tables.append(str(node.this.this.name))

    # Collect all table references
    for table in node.find_all(exp.Table):
        name = str(table.name)
        if name not in tables:
            tables.append(name)

    return tables


def _get_statement_type(node: exp.Expression) -> str:
    """Get human-readable statement type."""
    if isinstance(node, exp.Delete):
        return "DELETE"
    if isinstance(node, exp.Update):
        return "UPDATE"
    if isinstance(node, exp.Insert):
        return "INSERT"
    if isinstance(node, exp.TruncateTable):
        return "TRUNCATE"
    if isinstance(node, exp.Drop):
        return "DROP"
    if isinstance(node, exp.Merge):
        return "MERGE"
    return "UNKNOWN"


def _is_valid_drop_table(node: exp.Drop) -> bool:
    """Ensure DROP applies to TABLE (not VIEW, INDEX, etc.)."""
    if node.kind:
        return node.kind.upper() == "TABLE"
    return True  # assume table if unspecified


# --- Rule ------------------------------------------------------------------

@register
class VocabularyTableProtectionRule(Rule):
    """Prevents DML/DDL operations on OMOP vocabulary tables."""

    rule_id = "data_quality.vocabulary_table_protection"
    name = "Vocabulary Table Protection"

    description = (
        "Prevents DELETE, UPDATE, INSERT, TRUNCATE, MERGE, or DROP TABLE "
        "operations on OMOP vocabulary tables. These are reference datasets "
        "managed by OHDSI and must remain read-only in analytical workflows."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Do not modify vocabulary tables. Use them as read-only reference data. "
        "Vocabulary updates should be performed via official OHDSI releases."
    )
    long_description = (
        "OMOP vocabulary tables (concept, concept_ancestor, "
        "concept_relationship, vocabulary, domain, concept_class) are "
        "reference data distributed and versioned by OHDSI. Any local "
        "modification breaks reproducibility, invalidates cross-site "
        "comparisons, and typically conflicts with the next vocabulary "
        "release. Treat them as strictly read-only; vocabulary updates "
        "must be performed via official OHDSI releases."
    )
    example_bad = (
        "DELETE FROM concept\n"
        "WHERE concept_id = 0;"
    )
    example_good = (
        "SELECT concept_id\n"
        "FROM concept\n"
        "WHERE concept_id = 0;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        for tree in trees:
            if not tree:
                continue

            # Extract CTE names (to avoid false positives)
            cte_names = _extract_cte_names(tree)

            # Walk all nodes to catch nested DML/DDL
            for node in tree.walk():
                if not isinstance(node, DML_DDL_NODES):
                    continue

                # Special handling for DROP
                if isinstance(node, exp.Drop) and not _is_valid_drop_table(node):
                    continue

                table_names = _get_target_tables(node)
                if not table_names:
                    continue

                statement_type = _get_statement_type(node)

                for table_name in table_names:
                    normalized = _normalize_table_name(table_name)

                    # Skip CTE shadowing
                    if normalized in cte_names:
                        continue

                    if normalized not in VOCABULARY_TABLES:
                        continue

                    violations.append(
                        self.create_violation(
                            message=(
                                f"{statement_type} operation on vocabulary table '{table_name}'. "
                                f"OMOP vocabulary tables are read-only reference data and must "
                                f"not be modified."
                            ),
                            suggested_fix=self.suggested_fix,
                            details={
                                "statement_type": statement_type,
                                "table": table_name,
                                "normalized_table": normalized,
                                "vocabulary_tables": sorted(VOCABULARY_TABLES),
                            },
                        )
                    )

        return violations


__all__ = ["VocabularyTableProtectionRule"]

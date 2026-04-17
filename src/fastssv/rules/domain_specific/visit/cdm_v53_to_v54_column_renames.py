"""CDM v5.3 to v5.4 Column Renames Rule.

OMOP semantic rule OMOP_601:
OMOP CDM v5.4 renamed several columns from v5.3. Using the old column names
will cause errors on v5.4 databases.

The Problem:
    OMOP CDM v5.4 introduced column name changes in visit_occurrence and visit_detail
    tables. Queries using v5.3 column names will fail when executed against v5.4 databases.

    Column renames in v5.4:
    - admitting_source_concept_id → admitted_from_concept_id
    - admitting_source_value → admitted_from_source_value
    - discharge_to_concept_id → discharged_to_concept_id
    - discharge_to_source_value → discharged_to_source_value

Why this is wrong:
    Using deprecated column names:
    - Causes runtime errors on OMOP CDM v5.4 databases
    - Breaks query portability between CDM versions
    - Indicates outdated code that needs migration
    - Prevents queries from executing successfully

Violation pattern:
    SELECT admitting_source_concept_id, discharge_to_concept_id
    FROM visit_occurrence
    WHERE visit_concept_id = 9201

Correct pattern:
    SELECT admitted_from_concept_id, discharged_to_concept_id
    FROM visit_occurrence
    WHERE visit_concept_id = 9201
"""

import logging
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


logger = logging.getLogger(__name__)


# --- Constants -------------------------------------------------------------

DEPRECATED_COLUMNS: Dict[str, Dict[str, str]] = {
    "visit_occurrence": {
        "admitting_source_concept_id": "admitted_from_concept_id",
        "admitting_source_value": "admitted_from_source_value",
        "discharge_to_concept_id": "discharged_to_concept_id",
        "discharge_to_source_value": "discharged_to_source_value",
    },
    "visit_detail": {
        "admitting_source_concept_id": "admitted_from_concept_id",
        "admitting_source_value": "admitted_from_source_value",
        "discharge_to_concept_id": "discharged_to_concept_id",
        "discharge_to_source_value": "discharged_to_source_value",
    },
}


# --- Precomputed Normalized Structures -------------------------------------

DEPRECATED_COLUMNS_NORM: Dict[str, Dict[str, str]] = {
    normalize_name(table): {
        normalize_name(old): new
        for old, new in cols.items()
    }
    for table, cols in DEPRECATED_COLUMNS.items()
}

RELEVANT_TABLES_NORM: Set[str] = set(DEPRECATED_COLUMNS_NORM.keys())


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


# --- Rule ------------------------------------------------------------------

@register
class CdmV53ToV54ColumnRenamesRule(Rule):
    """
    Detect usage of deprecated OMOP CDM v5.3 column names that were renamed in v5.4.
    """

    rule_id = "domain_specific.cdm_v53_to_v54_column_renames"
    name = "CDM v5.3 to v5.4 Column Renames"

    description = (
        "Detects usage of deprecated OMOP CDM v5.3 column names "
        "that were renamed in v5.4."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Replace deprecated columns with v5.4 equivalents: "
        "admitting_source_* → admitted_from_*, "
        "discharge_to_* → discharged_to_*"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter
        if not any(col in sql_lower for cols in DEPRECATED_COLUMNS.values() for col in cols):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            logger.warning(f"[{self.rule_id}] SQL parse error: {err}")
            return []

        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, str]] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            referenced_tables = {_norm(t) for t in aliases.values()}
            relevant_tables = referenced_tables & RELEVANT_TABLES_NORM

            if not relevant_tables:
                continue

            for col_node in tree.find_all(exp.Column):
                col_name = col_node.name
                if not col_name:
                    continue

                col_norm = _norm(col_name)
                if not col_norm:
                    continue

                table_name, _ = resolve_table_col(col_node, aliases)
                table_norm = _norm(table_name) if table_name else None

                # --- Case 1: Qualified column ---
                if table_norm:
                    if table_norm not in RELEVANT_TABLES_NORM:
                        continue

                    deprecated_cols = DEPRECATED_COLUMNS_NORM[table_norm]

                    if col_norm not in deprecated_cols:
                        continue

                    key = (table_norm, col_norm)
                    if key in seen:
                        continue
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                f"Column '{col_name}' on table '{table_name}' "
                                f"is deprecated in OMOP CDM v5.4. "
                                f"Use '{deprecated_cols[col_norm]}' instead."
                            ),
                            severity=self.severity,
                        )
                    )

                # --- Case 2: Unqualified column ---
                else:
                    # Only safe if exactly one relevant table
                    if len(relevant_tables) != 1:
                        continue

                    table_norm = next(iter(relevant_tables))
                    deprecated_cols = DEPRECATED_COLUMNS_NORM[table_norm]

                    if col_norm not in deprecated_cols:
                        continue

                    key = (table_norm, col_norm)
                    if key in seen:
                        continue
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                f"Column '{col_name}' is deprecated for table '{table_norm}'. "
                                f"Use '{deprecated_cols[col_norm]}' instead."
                            ),
                            severity=self.severity,
                        )
                    )

        return violations


__all__ = ["CdmV53ToV54ColumnRenamesRule"]
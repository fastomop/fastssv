"""Specimen Source ID Not Specimen ID Rule.

OMOP semantic rule OMOP_119:
specimen.specimen_source_id is a free-text identifier from the source system,
not the OMOP primary key. The primary key is specimen.specimen_id.
Do not use specimen_source_id as a join key to other OMOP tables.

The Problem:
    The specimen table has a confusing naming pattern:
    - specimen_id (INTEGER): OMOP primary key
    - specimen_source_id (VARCHAR): Source system identifier (free-text)

    The naming confusion:
    - Most OMOP *_id columns are INTEGER foreign keys or primary keys
    - Most source identifiers use *_source_value (VARCHAR)
    - But specimen_source_id breaks this pattern - it LOOKS like a FK but is VARCHAR

    This is the ONLY column in OMOP CDM that ends with _source_id as a free-text field.
    All other *_source_id columns would be *_source_concept_id (INTEGER FKs to concept).

Common mistake:
    Developers see "specimen_source_id" and assume it's a numeric foreign key
    that can be joined to other tables. This is incorrect.

Violation patterns:
    SELECT * FROM specimen s
    JOIN measurement m ON s.specimen_source_id = m.measurement_id
    -- ERROR: specimen_source_id is VARCHAR free-text, not a FK

    SELECT * FROM specimen s
    JOIN person p ON s.specimen_source_id = p.person_id
    -- ERROR: specimen_source_id is not a join key

Correct patterns:
    SELECT * FROM specimen s
    JOIN measurement m ON s.specimen_id = m.specimen_id
    -- OK: Using the actual primary key

    SELECT * FROM specimen WHERE specimen_source_id = 'LAB-2024-001'
    -- OK: Filtering by source identifier (not joining)
"""

import logging
from typing import List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

SPECIMEN = "specimen"
SPECIMEN_SOURCE_ID = "specimen_source_id"


# --- Helpers -----------------------------------------------------------------

def _find_invalid_joins(tree: exp.Expression) -> List[dict]:
    """Find JOINs using specimen_source_id as a join key.

    Returns a list of dicts with keys ``message`` and ``column_sql`` (the
    exact ``alias.specimen_source_id`` text as written in the source) so
    callers can build a structured REPLACE patch.
    """
    issues: List[dict] = []
    seen: Set = set()

    aliases = extract_aliases(tree)

    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if not on:
            continue

        for eq in on.find_all(exp.EQ):
            left = eq.this
            right = eq.expression

            for col_expr in (left, right):
                if not isinstance(col_expr, exp.Column):
                    continue

                table, column = resolve_table_col(col_expr, aliases)

                if normalize_name(column) != SPECIMEN_SOURCE_ID:
                    continue

                # Case 1: qualified column → must be specimen
                if table:
                    if normalize_name(table) != SPECIMEN:
                        continue
                else:
                    # Case 2: unqualified → only flag if specimen is clearly present
                    specimen_present = any(
                        normalize_name(t) == SPECIMEN
                        for t in aliases.values()
                    )
                    if not specimen_present:
                        continue

                col_sql = col_expr.sql()
                if col_sql in seen:
                    continue
                seen.add(col_sql)

                issues.append({
                    "message": (
                        "specimen_source_id is used in a JOIN condition. "
                        "specimen_source_id is a VARCHAR free-text identifier from the source system, "
                        "not an OMOP foreign key. Use specimen.specimen_id instead."
                    ),
                    "column_sql": col_sql,
                })

    return issues


# --- Rule --------------------------------------------------------------------

@register
class SpecimenSourceIdNotSpecimenIdRule(Rule):
    """
    OMOP_119: Prevent using specimen_source_id as a join key.
    """

    rule_id = "domain_specific.specimen_source_id_not_specimen_id"
    name = "Specimen Source ID Not Specimen ID"

    description = (
        "specimen_source_id is a free-text identifier from the source system, "
        "not an OMOP foreign key. Use specimen.specimen_id for joins."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: joins on `specimen.specimen_source_id` WITH `specimen.specimen_id`. specimen_source_id is free-text from the source system, not the OMOP FK. Use specimen_source_id only as a filter, never in JOIN ON."
    example_bad = (
        "SELECT s.specimen_id FROM specimen s\n"
        "JOIN measurement m ON s.specimen_source_id = m.measurement_source_value;"
    )
    example_good = (
        "SELECT s.specimen_id FROM specimen s\n"
        "JOIN measurement m ON s.specimen_id = m.specimen_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if SPECIMEN_SOURCE_ID not in sql_lower:
            return []

        if SPECIMEN not in sql_lower or "join" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_119",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            issues = _find_invalid_joins(tree)

            for issue in issues:
                col_sql = issue["column_sql"]
                # Replace `<qual>.specimen_source_id` with `<qual>.specimen_id`
                # in the source. If the original column was unqualified (no
                # alias prefix), we replace the bare column name.
                replacement = col_sql.rsplit(
                    SPECIMEN_SOURCE_ID, 1
                )[0] + "specimen_id"
                patch = None
                span = locate(sql, col_sql)
                if span is not None:
                    patch = patch_replace(span, replacement)

                violations.append(
                    self.create_violation(
                        message=issue["message"],
                        severity=self.severity,
                        suggested_fix_patch=patch,
                    )
                )

        return violations


__all__ = ["SpecimenSourceIdNotSpecimenIdRule"]

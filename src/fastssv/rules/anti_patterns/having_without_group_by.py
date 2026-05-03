"""Having Without Group By Rule.

OMOP semantic rule OMOP_131:
HAVING clauses without GROUP BY are syntactically valid in some SQL dialects but
almost always indicate a query logic error in OMOP analytical queries.

The Problem:
    In SQL, HAVING without GROUP BY treats the entire result set as a single group.
    While syntactically valid in some databases (MySQL, PostgreSQL), this pattern
    is almost always a mistake in OMOP queries because:

    - HAVING is meant to filter aggregated groups created by GROUP BY
    - Without GROUP BY, you should use WHERE instead for filtering
    - This indicates the developer forgot to add GROUP BY
    - Results in unexpected behavior where aggregate functions apply to entire table

Why this is wrong:
    The intent is usually to group by some column and filter those groups,
    but the developer forgot the GROUP BY clause. This produces incorrect results
    where the HAVING condition applies to the entire dataset as one group.

Violation patterns:
    SELECT * FROM condition_occurrence
    HAVING COUNT(*) > 5
    -- ERROR: HAVING without GROUP BY - filters entire table as one group

    SELECT person_id, COUNT(*) FROM drug_exposure
    HAVING COUNT(*) > 3
    -- ERROR: Missing GROUP BY person_id

    SELECT concept_id FROM measurement
    WHERE measurement_date > '2020-01-01'
    HAVING SUM(value_as_number) > 100
    -- ERROR: Should use GROUP BY concept_id

Correct patterns:
    SELECT condition_concept_id, COUNT(*)
    FROM condition_occurrence
    GROUP BY condition_concept_id
    HAVING COUNT(*) > 5
    -- OK: HAVING with GROUP BY to filter aggregated groups

    SELECT person_id, COUNT(*)
    FROM drug_exposure
    GROUP BY person_id
    HAVING COUNT(*) > 3
    -- OK: Groups by person_id, filters groups

    SELECT * FROM condition_occurrence
    WHERE person_id = 123
    -- OK: Use WHERE for non-aggregated filtering, no HAVING

Note:
    This is an ERROR, not a WARNING. HAVING without GROUP BY almost always
    indicates a logic error and produces unexpected results.
"""

import logging
import re
from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import add as patch_add
from fastssv.core.registry import register


logger = logging.getLogger(__name__)


ERROR_MSG = (
    "Query has HAVING clause without GROUP BY. "
    "HAVING without GROUP BY treats the entire result as a single group, "
    "which is almost always a logic error. Add GROUP BY clause or use WHERE instead."
)


# --- Helpers -----------------------------------------------------------------

# Match an unqualified HAVING keyword (case-insensitive). We rely on the parser
# having confirmed presence of a HAVING clause; we just need a stable byte
# offset for the insertion. ``\b`` keeps us off "having" embedded in
# identifiers/strings.
_HAVING_RE = re.compile(r"\bhaving\b", re.IGNORECASE)


def _find_having_offset(sql: str) -> Optional[int]:
    """Return the byte offset of the (unique) HAVING keyword, or None.

    If the SQL contains multiple HAVING clauses we can't pick one safely, so
    we return None and let the rule fall back to FREEFORM.
    """
    matches = list(_HAVING_RE.finditer(sql))
    if len(matches) == 1:
        return matches[0].start()
    return None


def _find_violations(tree: exp.Expression) -> List[str]:
    """Find SELECT statements with HAVING but no GROUP BY."""
    issues: List[str] = []

    for select in tree.find_all(exp.Select):
        has_having = select.args.get("having") is not None
        has_group_by = select.args.get("group") is not None

        if has_having and not has_group_by:
            issues.append(ERROR_MSG)

    return issues


# --- Rule --------------------------------------------------------------------


@register
class HavingWithoutGroupByRule(Rule):
    """
    OMOP_131: Detect HAVING clauses without GROUP BY.
    """

    rule_id = "anti_patterns.having_without_group_by"
    name = "Having Without Group By"

    description = (
        "HAVING clauses without GROUP BY are almost always a logic error. Add GROUP BY clause or use WHERE instead."
    )

    severity = Severity.ERROR

    suggested_fix = "ADD: GROUP BY <columns> if the predicate filters aggregates, OR REPLACE the HAVING clause with WHERE if the predicate is on non-aggregated columns."
    long_description = (
        "HAVING filters aggregate groups; it is designed to run after "
        "GROUP BY collapses rows. Without a GROUP BY clause, HAVING "
        "filters over an implicit single-group aggregate, which some "
        "dialects accept with surprising semantics and others reject. In "
        "OMOP analytics this is almost always a mistake — the author "
        "wanted WHERE (for row-level filtering) or forgot to add the "
        "GROUP BY for their aggregates."
    )
    example_bad = "SELECT person_id\nFROM person\nHAVING person_id > 1;"
    example_good = "SELECT person_id\nFROM person\nWHERE person_id > 1;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filter
        if "having" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_131",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            issues = _find_violations(tree)

            # Structured patch: ADD `GROUP BY <group_by_cols>\n` immediately
            # before the HAVING keyword. We can't statically determine the
            # correct grouping columns (the analyst may want WHERE instead),
            # so we leave a placeholder. If the SQL has more than one HAVING
            # we can't pick a target deterministically — fall back to
            # FREEFORM via auto-default.
            having_offset = _find_having_offset(sql) if issues else None
            patch = None
            if having_offset is not None and len(issues) == 1:
                patch = patch_add(
                    having_offset,
                    "GROUP BY <group_by_cols>\n",
                )

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        suggested_fix_patch=patch,
                    )
                )

        return violations


__all__ = ["HavingWithoutGroupByRule"]

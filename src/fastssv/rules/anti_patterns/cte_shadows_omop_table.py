"""CTE Shadows OMOP Table Rule.

OMOP semantic rule (anti-pattern):
Naming a CTE after an OMOP CDM table (``cohort``, ``concept``, ``person``,
``condition_occurrence``, etc.) shadows the real OMOP table within the
query's scope. Any unqualified reference to that name now binds to the
CTE — not to the OMOP table — which:

- Makes the query harder to read: every ``FROM cohort`` is ambiguous to a
  reader who doesn't have the WITH-clause in mind.
- Breaks any later edit that adds a real OMOP reference (the editor and
  every linter will silently bind it to the CTE).
- Produces surprising results when a suggested fix from another rule
  ("ADD: JOIN concept c ON ...") is applied verbatim: the JOIN binds to
  the CTE, which lacks the columns the fix assumes (``standard_concept``,
  ``invalid_reason``, …), and execution fails with a missing-column error.

The fix is purely lexical: rename the CTE to something that doesn't
collide (``my_concepts``, ``target_cohort``, ``patient_set`` etc.).
Schema-qualifying every OMOP reference (``FROM omop.concept``) is a
workaround but doesn't address the readability cost — the rename is
preferable.
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import collect_cte_names, normalize_name, parse_sql
from fastssv.core.registry import register
from fastssv.schemas import CDM_COLUMN_TYPES


_OMOP_TABLE_NAMES = frozenset(normalize_name(t) for t in CDM_COLUMN_TYPES)


@register
class CteShadowsOmopTableRule(Rule):
    """Warn when a CTE's alias collides with an OMOP CDM table name."""

    rule_id = "anti_patterns.cte_shadows_omop_table"
    name = "CTE Shadows OMOP CDM Table Name"

    description = (
        "A CTE whose alias matches an OMOP CDM table name (e.g. `WITH cohort AS …`, "
        "`WITH concept AS …`) shadows the real OMOP table within the query. "
        "Subsequent unqualified references bind to the CTE, not the OMOP table — "
        "a frequent source of confusion and broken downstream rule fixes."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "RENAME: the CTE to a non-colliding name (e.g. `WITH my_concepts AS …` instead of "
        "`WITH concept AS …`). If renaming is impractical, every OMOP reference inside the "
        "query must be schema-qualified (`omop.concept` rather than `concept`)."
    )

    example_bad = (
        "WITH concept AS (\n"
        "    SELECT 4112343 AS concept_id UNION ALL SELECT 201826\n"
        ")\n"
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept ON co.condition_concept_id = concept.concept_id;"
    )
    example_good = (
        "WITH my_concepts AS (\n"
        "    SELECT 4112343 AS concept_id UNION ALL SELECT 201826\n"
        ")\n"
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN my_concepts ON co.condition_concept_id = my_concepts.concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        # Fast pre-filter — no CTE means no shadow.
        sql_lower = sql.lower()
        if "with" not in sql_lower:
            return []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        seen: set = set()

        for tree in trees:
            if tree is None:
                continue

            # Single source of truth for "what CTEs exist in scope" — the
            # same helper used by ~30 OMOP-table-targeting rules to suppress
            # downstream FPs on these shadows.
            shadows = collect_cte_names(tree) & _OMOP_TABLE_NAMES
            if not shadows:
                continue

            # Walk CTE nodes only to recover the *original* alias casing
            # (collect_cte_names returns the normalized set); membership is
            # already decided by the intersection above.
            for cte in tree.find_all(exp.CTE):
                if not cte.alias:
                    continue
                name_norm = normalize_name(cte.alias)
                if name_norm not in shadows:
                    continue
                if name_norm in seen:
                    continue
                seen.add(name_norm)

                violations.append(
                    self.create_violation(
                        message=(
                            f"CTE named `{cte.alias}` shadows the OMOP CDM table `{name_norm}`. "
                            f"Unqualified references to `{name_norm}` within this query bind to "
                            f"the CTE, not the OMOP table — rename the CTE to avoid confusion "
                            f"and to keep suggested fixes from other rules executable."
                        ),
                        severity=self.severity,
                        details={"cte_name": cte.alias, "omop_table": name_norm},
                    )
                )

        return violations


__all__ = ["CteShadowsOmopTableRule"]

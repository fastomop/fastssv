"""CTE / Subquery-Alias Shadows OMOP Table Rule.

OMOP semantic rule (anti-pattern):
Naming a CTE OR a derived-table subquery alias after an OMOP CDM table
(``cohort``, ``concept``, ``person``, ``condition_occurrence``, etc.)
re-uses the OMOP name for a local construct within the query.

For **CTEs** (``WITH concept AS …``), the alias is visible to every
sibling FROM clause in the query, so the harms are severe:

- Readability: every ``FROM concept`` is ambiguous to a reader who
  doesn't have the WITH-clause in mind.
- Edit hazard: a later real OMOP reference silently binds to the CTE.
- **Downstream fix breakage**: a suggested fix from another rule
  ("ADD: JOIN concept c ON ...") is applied verbatim, the JOIN binds to
  the CTE, which lacks the columns the fix assumes (``standard_concept``,
  ``invalid_reason``, …), and execution fails with a missing-column error.

For **derived-table subqueries** (``FROM (SELECT …) AS concept``), the
alias is scope-local — sibling FROM items don't see it — so the third
harm (downstream-fix breakage) does NOT apply: a ``JOIN concept c``
introduced anywhere else in the query resolves to OMOP ``concept``, not
the subquery. But the readability and edit-hazard concerns still hold,
which is why we still flag these (with a tuned message that drops the
breakage framing).

The fix is purely lexical: rename the CTE or alias to something that
doesn't collide (``my_concepts``, ``target_cohort``, ``patient_set``).
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
    """Warn when a CTE alias OR a derived-table subquery alias collides
    with an OMOP CDM table name."""

    rule_id = "anti_patterns.cte_shadows_omop_table"
    name = "CTE or Subquery Alias Shadows OMOP CDM Table"

    description = (
        "A CTE or derived-table subquery whose alias matches an OMOP CDM table "
        "name (e.g. `WITH concept AS …` or `FROM (SELECT …) AS concept`) re-uses "
        "an OMOP name for a local construct. For CTEs this also breaks suggested "
        "fixes from other rules (the JOIN binds to the CTE, which lacks OMOP "
        "columns); for derived-table subqueries the harm is limited to readability "
        "and edit hazards, but the rename is still the right call."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "RENAME: the CTE or derived-table alias to a non-colliding name "
        "(e.g. `WITH my_concepts AS …` or `FROM (SELECT …) AS my_concepts`, "
        "instead of `concept`). If renaming is impractical, every OMOP reference "
        "inside the query must be schema-qualified (`omop.concept` rather than `concept`)."
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

        # Fast pre-filter — no CTE keyword AND no parenthesis means no
        # derived-table subquery either. Trivial queries like `SELECT 1`
        # and `SELECT * FROM t` short-circuit here without parsing.
        sql_lower = sql.lower()
        if "with" not in sql_lower and "(" not in sql_lower:
            return []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        # Dedup key is (kind, normalized-name): a single SQL string with
        # both a CTE named `cohort` and a derived-table aliased `cohort`
        # (legal across nested scopes) emits one warning per kind.
        seen: set = set()

        for tree in trees:
            if tree is None:
                continue

            # --- CTEs ----------------------------------------------------
            # `collect_cte_names` is intentionally tree-global here: every
            # CTE in the SQL contributes a shadow regardless of its lexical
            # depth, because the rename advice applies uniformly. The
            # scope-aware version (`has_table_reference` in helpers.py) is
            # for the *opposite* question: "is this Table reference
            # shadowed by a visible CTE?".
            shadows = collect_cte_names(tree) & _OMOP_TABLE_NAMES
            if shadows:
                # Walk CTE nodes to recover the *original* alias casing
                # (collect_cte_names returns the normalized set).
                for cte in tree.find_all(exp.CTE):
                    if not cte.alias:
                        continue
                    name_norm = normalize_name(cte.alias)
                    if name_norm not in shadows:
                        continue
                    key = ("cte", name_norm)
                    if key in seen:
                        continue
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                f"CTE named `{cte.alias}` shadows the OMOP CDM table "
                                f"`{name_norm}`. Unqualified references to `{name_norm}` "
                                f"within this query bind to the CTE, not the OMOP table — "
                                f"rename the CTE to avoid confusion and to keep suggested "
                                f"fixes from other rules executable."
                            ),
                            severity=self.severity,
                            details={
                                "alias_kind": "cte",
                                "alias": cte.alias,
                                "cte_name": cte.alias,
                                "omop_table": name_norm,
                            },
                        )
                    )

            # --- Derived-table subqueries -------------------------------
            # `exp.Subquery` covers `FROM (SELECT …) AS x` and the JOIN
            # equivalent. Scalar subqueries in SELECT (`SELECT (SELECT …)
            # AS x FROM …`) keep their alias on the outer `exp.Alias`
            # wrapper, not the Subquery itself, so `sub.alias` is empty
            # there and the filter naturally excludes them. Anonymous
            # subqueries in WHERE IN / EXISTS predicates have no alias
            # at all and are likewise skipped.
            for sub in tree.find_all(exp.Subquery):
                alias = sub.alias
                if not alias:
                    continue
                name_norm = normalize_name(alias)
                if name_norm not in _OMOP_TABLE_NAMES:
                    continue
                key = ("subquery", name_norm)
                if key in seen:
                    continue
                seen.add(key)

                violations.append(
                    self.create_violation(
                        message=(
                            f"Subquery aliased `{alias}` reuses the OMOP CDM table name "
                            f"`{name_norm}`. References like `{name_norm}.<col>` in this "
                            f"query block resolve to the derived table, not the OMOP "
                            f"table — rename the alias to avoid the readability cost when "
                            f"scanning the query."
                        ),
                        severity=self.severity,
                        details={
                            "alias_kind": "subquery",
                            "alias": alias,
                            "omop_table": name_norm,
                        },
                    )
                )

        return violations


__all__ = ["CteShadowsOmopTableRule"]

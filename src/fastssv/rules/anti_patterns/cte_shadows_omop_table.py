"""CTE Shadows OMOP Table Rule.

PostgreSQL: "the WITH query hides any real table of the same name for the purposes of the primary query. If
necessary, you can refer to a real table of the same name by schema-qualifying the table's name." A CTE named after
a CDM table (``WITH concept AS (...)``) therefore changes what every unqualified reference to that table in the
statement means: a reader, a later edit, or a fix suggested by another rule (``JOIN concept c ON ...``) reaches the
CTE, which lacks the table's columns, instead of the CDM table.

The rule warns for every CTE whose name is a CDM v5.4 table. A derived-table alias (``FROM (SELECT ...) AS
concept``) is not reported: it is visible only as a qualifier inside its own SELECT and hides no table.
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register
from fastssv.rules.anti_patterns._ap_cdm_fields import CDM_TABLES


@register
class CteShadowsOmopTableRule(Rule):
    """CTE named after a CDM table."""

    rule_id = "anti_patterns.cte_shadows_omop_table"
    name = "CTE Shadows OMOP CDM Table"

    description = (
        "A CTE whose name is a CDM table (e.g. `WITH concept AS ...`) hides the real table for the whole statement: "
        "every unqualified reference to that name, including one added later or by another rule's fix, reaches the "
        "CTE instead."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "RENAME: the CTE to a name that is not a CDM table (e.g. `WITH my_concepts AS ...` instead of `concept`). "
        "If renaming is impractical, schema-qualify every reference to the real table (`cdm.concept`)."
    )
    long_description = (
        "A WITH query hides any real table of the same name for the rest of the statement; only a "
        "schema-qualified name still reaches the real table. Naming a CTE after a CDM table (concept, person, "
        "cohort, condition_occurrence, ...) therefore changes what every unqualified reference to that table "
        "means. A reader, a later edit, or a fix suggested by another rule (`JOIN concept c ON ...`) reaches the "
        "CTE, which lacks the table's columns. The table names are the 39 tables of the CDM v5.4 specification. "
        "Derived-table aliases are not reported: they are visible only as a qualifier inside their own SELECT."
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
    references = (
        Reference(
            "PostgreSQL documentation: SELECT, FROM clause (WITH query names)",
            "https://www.postgresql.org/docs/current/sql-select.html#SQL-FROM",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql or "with" not in sql.lower():
            return []
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        seen: set = set()
        for tree in trees:
            if tree is None:
                continue
            for cte in tree.find_all(exp.CTE):
                if not cte.alias:
                    continue
                name = normalize_name(cte.alias)
                if name not in CDM_TABLES or name in seen:
                    continue
                seen.add(name)
                violations.append(
                    self.create_violation(
                        message=(
                            f"CTE named `{cte.alias}` hides the CDM table `{name}`: every unqualified reference to "
                            f"`{name}` in this statement reaches the CTE, not the table. Rename the CTE so that "
                            "references, later edits and suggested fixes reach the table."
                        ),
                        details={"alias_kind": "cte", "alias": cte.alias, "omop_table": name},
                    )
                )
        return violations


__all__ = ["CteShadowsOmopTableRule"]

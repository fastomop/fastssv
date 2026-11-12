"""Comma-Separated Cross Join Rule.

Two sources of one SELECT that no predicate ties together, directly or through other sources, form a Cartesian
product: if they hold N and M rows, the result holds N * M (PostgreSQL, Table Expressions). The comma, ``CROSS
JOIN`` and ``JOIN ... ON TRUE`` are the same operation, so the rule judges the predicates, not the spelling.

When at least one side of such a product is a patient-level table (a table with person_id, or one that refers to
such a table), each clinical record is paired with every row of the other side, and the combinations correspond to
no recorded event. Usually a ``person_id`` (or ``visit_occurrence_id``) predicate is missing. Whether a product was
intended cannot be read from the SQL, so the finding is a WARNING.

    SELECT co.condition_occurrence_id, c.concept_name
    FROM condition_occurrence co, concept c
    WHERE co.condition_concept_id = 201820
    -- no predicate ties concept to condition_occurrence

Not reported:
- a side that returns one row: a CDM table whose primary key is equated to a literal, or an aggregate / LIMIT 1
  query (``FROM person p, (SELECT COUNT(*) AS n FROM person) t``), in either FROM position;
- row generators (``VALUES``, ``UNNEST``, ``generate_series``), which exist to expand rows;
- CDM_SOURCE and METADATA, which ``anti_patterns.singleton_metadata_clinical_join`` reports;
- two person-level tables (``joins._person_keys.PERSON_TABLES``) left untied, which
  ``joins.clinical_person_id_linkage_validation`` reports;
- products in which no side is patient-level.

What ties two sources is defined in ``_ap_product``: equality, range and ``IN`` predicates in WHERE or ON, ``USING``
/ ``NATURAL``, two columns equated to the same literal, and a column of an enclosing query.
"""

from typing import List, Set

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register
from fastssv.rules.anti_patterns._ap_product import Scope, Source
from fastssv.rules.joins._join_scope import shadowed_tables
from fastssv.rules.joins._person_keys import PERSON_TABLES
from fastssv.rules.joins._person_links import ScopeLinks


def _counts(src: Source) -> bool:
    """Whether ``src`` multiplies the rows it is paired with."""
    return src.kind != "generator" and not src.metadata and not src.one_row


def _person_linkage_judges(group: List[Source], judged: Set[str]) -> bool:
    """The group holds a person-level table that joins.clinical_person_id_linkage_validation sees as a source of
    this SELECT (``judged``): an untied pair of two such groups is that rule's finding."""
    return any(s.kind == "table" and s.name in PERSON_TABLES and s.alias in judged for s in group)


def _reported_groups(groups: List[List[Source]], judged: Set[str]) -> List[List[Source]]:
    """Groups that take part in an untied pair this rule reports: at least one side patient-level, and not both
    sides person-level tables judged by the linkage rule."""
    involved = set()
    for i, a in enumerate(groups):
        for j in range(i + 1, len(groups)):
            b = groups[j]
            if not any(s.patient_level for s in a + b):
                continue
            if _person_linkage_judges(a, judged) and _person_linkage_judges(b, judged):
                continue
            involved.update((i, j))
    return [g for i, g in enumerate(groups) if i in involved]


@register
class CommaSeparatedCrossJoinRule(Rule):
    """Patient-level source paired with another source that no predicate ties to it."""

    rule_id = "anti_patterns.comma_separated_cross_join"
    name = "Cartesian Product With Clinical Rows"

    description = (
        "Warns when no predicate ties a patient-level table to another FROM/JOIN source of the same SELECT "
        "(comma join, CROSS JOIN or JOIN ... ON TRUE): every clinical row is paired with every row of the other "
        "side."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "ADD: a predicate tying the sources, typically on person_id (or visit_occurrence_id when both sides "
        "belong to the same visit). Range predicates such as `event_date BETWEEN op.observation_period_start_date "
        "AND op.observation_period_end_date` count as ties too."
    )
    long_description = (
        "Two sources that no predicate ties together form a Cartesian product: N rows times M rows. The comma, "
        "CROSS JOIN and JOIN ... ON TRUE are the same operation, so the rule looks at the predicates, not the "
        "spelling. When one side is a patient-level table, each clinical record is paired with every row of the "
        "other side and the combinations correspond to no recorded event; usually a person_id predicate is "
        "missing. Equality, range and IN predicates in WHERE or ON, USING / NATURAL, two columns equated to the "
        "same literal, and a column of an enclosing query all tie sources. A side that returns one row (a primary "
        "key equated to a literal, an aggregate or LIMIT 1 subquery) multiplies nothing, and row generators "
        "(VALUES, UNNEST, generate_series) are expanded on purpose; neither is reported. Two person-level tables "
        "left untied (condition_occurrence crossed with drug_exposure) are reported by "
        "joins.clinical_person_id_linkage_validation instead. Whether a product was intended cannot be read from "
        "the SQL, so the finding is a WARNING."
    )
    example_bad = (
        "SELECT co.condition_occurrence_id, c.concept_name\n"
        "FROM condition_occurrence co, concept c\n"
        "WHERE co.condition_concept_id = 201820;"
    )
    example_good = (
        "SELECT co.condition_occurrence_id, c.concept_name\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c\n"
        "  ON co.condition_concept_id = c.concept_id\n"
        "WHERE co.condition_concept_id = 201820;"
    )
    references = (
        Reference(
            "PostgreSQL documentation: Table Expressions, joined tables",
            "https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            shadowed = shadowed_tables(tree)
            for select in tree.find_all(exp.Select):
                if not select.args.get("joins"):
                    continue
                scope = Scope(select)
                groups = [
                    [scope.sources[i] for i in members if _counts(scope.sources[i])] for members in scope.groups()
                ]
                judged = set(ScopeLinks(select, shadowed).person_level())
                groups = _reported_groups([g for g in groups if g], judged)
                if not groups:
                    continue
                described = [", ".join(s.label for s in g) for g in groups]
                untied = " and ".join(f"[{d}]" for d in described)
                violations.append(
                    self.create_violation(
                        message=(
                            f"No predicate ties {untied} together, so every row of one is paired with every row of "
                            "the other (a Cartesian product): the combinations correspond to no recorded clinical "
                            "event and counts are multiplied. Add the missing join predicate, usually on "
                            "person_id; if every pairing is intended, this warning can be ignored."
                        ),
                        details={"untied_groups": [[s.label for s in g] for g in groups]},
                    )
                )
        return violations


__all__ = ["CommaSeparatedCrossJoinRule"]

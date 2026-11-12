"""Cohort to Clinical Table Join Validation Rule.

COHORT rows name their members in ``subject_id``. The CDM v5.4 specification lets a cohort's subjects be persons,
providers or visits; COHORT_DEFINITION.subject_concept_id records which. Person cohorts are by far the common case,
and the Book of OHDSI joins them to clinical tables with ``subject_id = person_id``.

The rule looks at every SELECT that reads a cohort source (the COHORT table, or a CTE / derived table that passes
cohort.subject_id on, under any name) together with person-level sources (the CDM tables that carry person_id, or
CTEs / derived tables passing their keys on). Predicates that must hold put columns into classes of equal values, so
links run through casts, unqualified columns, USING, WHERE, other tables and the same literal. It reports, as
WARNINGs:

* a person-level source not tied to cohort.subject_id, directly (subject_id in one class with its person_id) or
  through other person-level sources linked to the same person (``cohort -> drug_exposure -> condition_occurrence``):
  each cohort subject is paired with rows of other patients unless that is intended;
* cohort.subject_id equated with another entity's key (a visit, provider or event id) while the SELECT does not
  restrict COHORT_DEFINITION.subject_concept_id: correct only for a cohort whose subjects are of that kind.

Violation pattern:
    SELECT c.subject_id, co.condition_concept_id
    FROM cohort c
    JOIN condition_occurrence co ON co.condition_start_date >= c.cohort_start_date

Correct pattern:
    SELECT c.subject_id, co.condition_concept_id
    FROM cohort c
    JOIN condition_occurrence co ON c.subject_id = co.person_id
      AND co.condition_start_date >= c.cohort_start_date
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import column_origins, parse_sql, scope_nodes, scope_predicates
from fastssv.core.references import BOOK_COHORTS, cdm_table, section
from fastssv.core.registry import register

from ._join_scope import hoist_joins, shadowed_tables
from ._person_keys import key_domain
from ._person_links import ScopeLinks

SUBJECT = ("cohort", "subject_id")
PERSON_KEY = ("person", "person_id")
SUBJECT_CONCEPT = ("cohort_definition", "subject_concept_id")


def _subject_kind_restricted(select: exp.Select) -> bool:
    """The SELECT filters COHORT_DEFINITION.subject_concept_id, so the kind of subject is explicit."""
    for predicate in scope_predicates(select):
        for col in scope_nodes(predicate):
            if isinstance(col, exp.Column) and (column_origins(col) or set()) == {SUBJECT_CONCEPT}:
                return True
    return False


def _check_select(links: ScopeLinks) -> Tuple[List[Tuple[str, List[str]]], List[Tuple[str, str]]]:
    """(unlinked targets with the cohort-column equalities they do have, subject_id joins to non-person keys)."""
    cohort_aliases = {a for a, cols in links.exposed.items() if any(o == SUBJECT for o in cols.values())}
    if not cohort_aliases:
        return [], []
    level = links.person_level()
    targets = {a: label for a, label in level.items() if a not in cohort_aliases}
    if not targets:
        return [], []

    classes: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    for node in list(links._parent):
        classes.setdefault(links._find(node), []).append(node)

    attached: Set[str] = set()
    non_person: List[Tuple[str, str]] = []
    wrong: Dict[str, List[str]] = {}
    for nodes in classes.values():
        cohort_cols = [(a, c, links.origin((a, c))) for a, c in nodes if a in cohort_aliases]
        if not cohort_cols:
            continue
        has_subject = any(origin == SUBJECT for _, _, origin in cohort_cols)
        for alias, column in nodes:
            if alias not in targets:
                continue
            origin = links.origin((alias, column))
            domain = key_domain(*origin) if origin else None
            if domain is None:
                continue
            if has_subject and domain == PERSON_KEY:
                attached.add(alias)
            elif has_subject:
                non_person.append((alias, f"{origin[0]}.{origin[1]}"))
            else:
                for c_alias, c_col, c_origin in cohort_cols:
                    if c_origin and c_origin[0] == "cohort":
                        wrong.setdefault(alias, []).append(f"cohort.{c_origin[1]} = {origin[0]}.{origin[1]}")

    graph = links.person_graph()
    linked: Set[str] = set()
    stack = list(attached)
    while stack:
        cur = stack.pop()
        if cur in linked:
            continue
        linked.add(cur)
        stack.extend(graph.get(cur, ()))
    non_person_aliases = {a for a, _ in non_person}
    unlinked = [
        (targets[a], sorted(set(wrong.get(a, [])))) for a in targets if a not in linked and a not in non_person_aliases
    ]
    restricted = _subject_kind_restricted(links.select)
    return unlinked, ([] if restricted else [(targets[a], key) for a, key in non_person])


@register
class CohortClinicalJoinValidationRule(Rule):
    """A person-level source read with a cohort should be tied to cohort.subject_id."""

    rule_id = "joins.cohort_clinical_join_validation"
    name = "Cohort to Clinical Table Join Validation"

    description = (
        "A clinical table read together with a cohort should be tied to cohort.subject_id (subject_id = person_id, "
        "directly or through other tables). subject_id joined to a visit, provider or event key is right only for "
        "a cohort whose subjects are of that kind."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "ADD: `cohort.subject_id = <clinical>.person_id` to the join (or link the table to another one already tied "
        "to subject_id). If the cohort's subjects are visits or providers, restrict "
        "cohort_definition.subject_concept_id accordingly."
    )
    example_bad = (
        "SELECT c.subject_id, co.condition_concept_id\n"
        "FROM cohort c\n"
        "JOIN condition_occurrence co ON co.condition_start_date >= c.cohort_start_date;"
    )
    example_good = (
        "SELECT c.subject_id, co.condition_concept_id\n"
        "FROM cohort c\n"
        "JOIN condition_occurrence co ON c.subject_id = co.person_id\n"
        "  AND co.condition_start_date >= c.cohort_start_date;"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT c.subject_id, co.condition_concept_id\n"
                "FROM cohort c, condition_occurrence co\n"
                "WHERE c.cohort_definition_id = 1;"
            ),
            severity=Severity.WARNING,
            note="cohort and a clinical table read together with no join condition at all.",
        ),
        RuleExample(
            sql="SELECT * FROM cohort c\nJOIN condition_occurrence co ON c.subject_id = co.visit_occurrence_id;",
            severity=Severity.WARNING,
            note="subject_id joined to a visit key, without restricting the cohort definition's subject kind.",
        ),
    )
    references = (
        cdm_table("cohort"),
        cdm_table("cohort_definition"),
        section(BOOK_COHORTS, "require-prior-hypertension", "Require prior hypertension"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "cohort" not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees:
            if not tree:
                continue
            tree = hoist_joins(tree)
            shadowed = shadowed_tables(tree)
            for select in tree.find_all(exp.Select):
                if not select.args.get("joins"):
                    continue
                unlinked, non_person = _check_select(ScopeLinks(select, shadowed))
                for label, wrong in unlinked:
                    detail = f" It is equated only through {', '.join(wrong)}." if wrong else ""
                    message = (
                        f"cohort and {label} are read in one SELECT, but {label} is not joined to cohort.subject_id, directly or through other tables."
                        f"{detail} Each cohort subject is paired with rows of other patients unless that is intended."
                    )
                    if message not in seen:
                        seen.add(message)
                        violations.append(self.create_violation(message=message, details={"table": label}))
                for label, key in non_person:
                    message = (
                        f"cohort.subject_id is joined to {key}. Cohort subjects are persons unless the cohort "
                        f"definition says otherwise (COHORT_DEFINITION.subject_concept_id: Person, Provider, Visit); "
                        f"for a person cohort join subject_id to {label}.person_id."
                    )
                    if message not in seen:
                        seen.add(message)
                        violations.append(self.create_violation(message=message, details={"table": label, "key": key}))
        return violations


__all__ = ["CohortClinicalJoinValidationRule"]

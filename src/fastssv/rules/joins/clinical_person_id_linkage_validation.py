"""Person-level sources read together without a person-scoped link.

In the OMOP CDM every clinical event row belongs to one person: CDM v5.4 gives 18 tables a ``person_id`` foreign key
to PERSON. When one SELECT reads two of them (directly, or through a CTE or derived table that passes their keys on)
and no predicate ties them to the same person, each row of one is paired with rows of every other person. That is
almost always a mistake, but it is the intended result for some analyses (matching controls to cases, counting per
provider or care site), so the finding is a WARNING.

Two sources are linked when predicates that must hold (AND-connected equalities in ON / WHERE, or USING) put a key of
the same person-level entity of each into one class of equal values:

* ``person_id`` on both sides;
* a visit key (``visit_occurrence_id`` / ``preceding_visit_occurrence_id``) or visit-detail key
  (``visit_detail_id`` / ``parent_visit_detail_id`` / ``preceding_visit_detail_id``): each is a foreign key to one
  visit, and a visit belongs to one person;
* an event primary key matched with itself or with a polymorphic event key (``measurement_event_id``,
  ``observation_event_id``, ``note_event_id``), which the specification defines as the primary key of the linked
  record;
* both attached to one FACT_RELATIONSHIP or EPISODE_EVENT row.

Classes run through casts, through any other table, CTE or derived table
(``co.person_id = c.subject_id AND de.person_id = c.subject_id``) and through the same literal
(``co.person_id = 42 AND p.person_id = 42``), including a literal pinned inside a CTE or derived source. Each
argument of ``COALESCE(a.person_id, b.person_id)`` joins the class. An OR links a source only when every
alternative ties it to one already linked group; any other equality under OR, and every one under NOT, links nothing. Every SELECT in the
statement is checked, including CTE bodies, derived tables and each branch of a set operation. A LATERAL / APPLY
source is not checked: its link is the correlation inside it.

Violation pattern:
    SELECT co.condition_concept_id, de.drug_concept_id
    FROM condition_occurrence co
    JOIN drug_exposure de ON co.condition_start_date = de.drug_exposure_start_date

Correct patterns:
    SELECT co.condition_concept_id, de.drug_concept_id
    FROM condition_occurrence co
    JOIN drug_exposure de ON co.person_id = de.person_id

    SELECT vd.visit_detail_id, nxt.visit_start_date
    FROM visit_detail vd
    JOIN visit_occurrence nxt ON nxt.preceding_visit_occurrence_id = vd.visit_occurrence_id
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.deduplication import predicate_key
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.patch import add as patch_add, locate
from fastssv.core.references import BOOK_CDM, cdm_table, section
from fastssv.core.registry import register

from ._person_keys import PERSON_TABLES
from ._join_scope import hoist_joins, shadowed_tables
from ._person_links import ScopeLinks


def _add_person_link_patch(sql: str, links: ScopeLinks, components: List[Set[str]]) -> Optional[dict]:
    """ADD `AND a.person_id = b.person_id` to the ON clause of the first join that brings in an unlinked source,
    when both sides expose person_id. None (prose fix only) for comma joins, CROSS JOIN or no person_id."""
    component_of = {alias: i for i, comp in enumerate(components) for alias in comp}
    earlier: List[str] = []
    from_node = links.select.args.get("from_") or links.select.args.get("from")
    if from_node is not None and from_node.this.alias_or_name:
        earlier.append(normalize_name(from_node.this.alias_or_name))
    for join in links.select.args.get("joins") or []:
        alias = normalize_name(join.this.alias_or_name) if join.this.alias_or_name else None
        if alias in component_of:
            partners = [e for e in earlier if e in component_of and component_of[e] != component_of[alias]]
            if partners and not any(component_of.get(e) == component_of[alias] for e in earlier):
                partner = partners[0]
                on = join.args.get("on")
                if on is None or "person_id" not in links.exposed.get(alias, {}):
                    return None
                if "person_id" not in links.exposed.get(partner, {}):
                    return None
                span = locate(sql, on.sql())
                if span is None:
                    return None
                return patch_add(span[1], f" AND {partner}.person_id = {alias}.person_id")
        if alias:
            earlier.append(alias)
    return None


def _cross_predicates(links: ScopeLinks, components: List[Set[str]]) -> List[str]:
    """Normalised equalities of ``links.select`` whose two operands read sources of different components."""
    component_of = {alias: i for i, comp in enumerate(components) for alias in comp}
    roots = [j.args["on"] for j in links.select.args.get("joins") or [] if j.args.get("on") is not None]
    if links.select.args.get("where") is not None:
        roots.append(links.select.args["where"].this)
    found: Set[str] = set()
    for root in roots:
        for eq in root.find_all(exp.EQ):
            if eq.find_ancestor(exp.Select) is not links.select:
                continue
            left = {component_of.get(alias) for alias, _ in links._nodes(eq.this)} - {None}
            right = {component_of.get(alias) for alias, _ in links._nodes(eq.expression)} - {None}
            if any(a != b for a in left for b in right):
                found.add(predicate_key(eq))
    return sorted(found)


@register
class ClinicalPersonIdLinkageValidationRule(Rule):
    """Person-level sources of one SELECT should be tied to the same person."""

    rule_id = "joins.clinical_person_id_linkage_validation"
    name = "Clinical Tables Without a Person-Scoped Link"

    description = (
        "Two person-level tables (or CTEs / derived tables carrying their keys) are read in one SELECT with no "
        "predicate tying them to the same person (person_id, a visit or visit-detail key, an event key). Rows of "
        "different patients are paired; this is right only for deliberately cross-person analyses."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "ADD: `<a>.person_id = <b>.person_id` (or a shared visit key such as visit_occurrence_id) to the join "
        "between the two sources, unless pairing rows of different patients is intended."
    )
    example_bad = (
        "SELECT co.condition_concept_id, de.drug_concept_id\n"
        "FROM condition_occurrence co\n"
        "JOIN drug_exposure de ON co.condition_start_date = de.drug_exposure_start_date;"
    )
    example_good = (
        "SELECT co.condition_concept_id, de.drug_concept_id\n"
        "FROM condition_occurrence co\n"
        "JOIN drug_exposure de ON co.person_id = de.person_id\n"
        "  AND co.condition_start_date = de.drug_exposure_start_date;"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT d.person_id, co.condition_start_date\n"
                "FROM death d\n"
                "JOIN condition_occurrence co ON d.cause_concept_id = co.condition_concept_id;"
            ),
            severity=Severity.WARNING,
            note="death matched to conditions by concept only: every death pairs with other patients' conditions.",
        ),
        RuleExample(
            sql=(
                "SELECT d.person_id, vo.visit_occurrence_id\n"
                "FROM death d, visit_occurrence vo\n"
                "WHERE d.death_date BETWEEN vo.visit_start_date AND vo.visit_end_date;"
            ),
            severity=Severity.WARNING,
            note="A comma join with only a date condition.",
        ),
    )
    references = (
        section(BOOK_CDM, "model-Conv", "General conventions of the model"),
        cdm_table("person"),
        cdm_table("visit_occurrence"),
        cdm_table("fact_relationship"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if sum(table in lowered for table in PERSON_TABLES) < 1:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, ...]] = set()
        for tree in trees:
            if not tree:
                continue
            tree = hoist_joins(tree)
            shadowed = shadowed_tables(tree)
            for select in tree.find_all(exp.Select):
                if len(select.args.get("joins") or []) < 1:
                    continue
                links = ScopeLinks(select, shadowed)
                level = links.person_level()
                if len(level) < 2:
                    continue
                components = links.components()
                if len(components) <= 1:
                    continue
                groups = sorted(sorted(level[a] for a in comp) for comp in components)
                key = tuple(" + ".join(g) for g in groups)
                if key in seen:
                    continue
                seen.add(key)
                listed = " | ".join(key)
                violations.append(
                    self.create_violation(
                        message=(
                            f"Person-level sources are read in one SELECT without a person-scoped link (person_id, a "
                            f"visit key or an event key) between these groups: {listed}. Rows of different patients are paired unless that is intended "
                            f"(for example matching controls to cases, or counting per provider)."
                        ),
                        details={
                            "groups": groups,
                            "tables": sorted({t for g in groups for t in g}),
                            "predicates": _cross_predicates(links, components),
                        },
                        suggested_fix_patch=_add_person_link_patch(sql, links, components),
                    )
                )
        return violations


__all__ = ["ClinicalPersonIdLinkageValidationRule"]

"""Drug Era Concept Class Validation Rule.

CDM v5.4, DRUG_ERA.drug_concept_id: "The drug_concept_id should conform to the concept class 'ingredient' as the
drug_era is an era of time where a person is exposed to a particular drug ingredient." The CDM v5.4 field-level
specification sets fkClass = Ingredient for drug_era.drug_concept_id and dose_era.drug_concept_id, and the Data
Quality Dashboard checks it with threshold 0. On a conformant CDM every such concept is an Ingredient.
drug_strength.ingredient_concept_id is not constrained: the specification leaves its fkClass empty, and the
vocabulary's drug_strength rows also point at Precise Ingredient concepts.

The rule follows the concept row that is matched with one of those fields (a join, an ``IN`` subquery, a CTE or a
derived table, through casts) and judges the ``concept_class_id`` restrictions that hold for that same concept row:

- ERROR: the restriction admits no Ingredient (``= 'Clinical Drug'``, ``IN ('Branded Drug', ...)``,
  ``LIKE 'Branded%'``). The query returns no row of the era / strength table.
- WARNING: the restriction excludes Ingredient (``<> 'Ingredient'``, ``NOT IN ('Ingredient')``). On a conformant CDM
  nothing is left; the shape is useful only as a conformance check.
- WARNING: the restriction admits no Ingredient but sits in an outer join's ON, so it only stops the concept from
  being attached.
- WARNING: the restriction admits no Ingredient, but the concept is matched with the field only in one branch of an
  OR (``de.drug_concept_id IN (SELECT ...) OR ...``), so the other branches still return rows.

A literal that differs from 'Ingredient' only in case is left to the canonical-string rule, which reports casing for
every vocabulary id. A restriction under an OR counts only inside its own operand, and a class filter on another
concept read (a reused alias in another scope, a concept reached through concept_ancestor) does not count.
"""

from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import _query_branches, parse_sql, unwrap_cast
from fastssv.core.references import BOOK_SQL, DQD_FIELD_THRESHOLDS, cdm_table, section
from fastssv.core.registry import register

from .._ds1_cdm_facts import FK_CONCEPT_CLASS
from .._ds1_polymorphic_keys import EXCLUDED, PATTERN, VALUES, Restriction, like_matches, restrictions
from .._ds1_scope import (
    Path,
    column_origins_with_paths,
    holds_wherever,
    or_branches,
    outer_join_of,
    predicate_root,
    same_route,
)

CONCEPT = "concept"
CONCEPT_ID = "concept_id"
CONCEPT_CLASS_ID = "concept_class_id"

# A concept read matched with a class-constrained field: (link node, routes to the concept read, table, field, class)
Link = Tuple[exp.Expression, FrozenSet[Path], str, str, str]


def _origins(node: Optional[exp.Expression]):
    node = unwrap_cast(node) if node is not None else None
    return column_origins_with_paths(node) if isinstance(node, exp.Column) else set()


def _first_projection_origins(query: exp.Expression):
    query = query.this if isinstance(query, exp.Subquery) else query
    out = set()
    for branch in _query_branches(query):
        if branch.expressions:
            first = branch.expressions[0]
            out |= _origins(first.this if isinstance(first, exp.Alias) else first)
    return out


def _links(tree: exp.Expression) -> List[Link]:
    links: List[Link] = []
    for node in tree.find_all(exp.EQ, exp.In):
        if predicate_root(node) is None:
            continue
        if isinstance(node, exp.In):
            if node.args.get("query") is None:
                continue
            pairs = [(_origins(node.this), _first_projection_origins(node.args["query"]))]
        else:
            left, right = _origins(node.this), _origins(node.expression)
            pairs = [(left, right), (right, left)]
        for constrained, concept_side in pairs:
            concept_paths = frozenset(p for p, t, c in concept_side if (t, c) == (CONCEPT, CONCEPT_ID))
            if not concept_paths:
                continue
            for _, table, field in constrained:
                required = FK_CONCEPT_CLASS.get((table, field))
                if required:
                    links.append((node, concept_paths, table, field, required))
    return links


_SUBQUERY_PREDICATES = (exp.In, exp.Exists, exp.Any, exp.All, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def _only_through_or(link_node: exp.Expression) -> bool:
    """Whether the match with the class-constrained field sits in an OR branch, directly or through the predicate
    that holds its subquery: the other branches still return rows."""
    node = link_node
    while True:
        if or_branches(node):
            return True
        select = node.find_ancestor(exp.Select)
        holder = select.parent if select is not None else None
        while isinstance(holder, (exp.Subquery, exp.Paren, exp.SetOperation)):
            holder = holder.parent
        if not isinstance(holder, _SUBQUERY_PREDICATES):
            return False
        node = holder


def _admits(r: Restriction, required: str) -> bool:
    """Whether a concept of class ``required`` passes ``r``, ignoring case (casing is the canonical-string rule's)."""
    if r.kind == PATTERN:
        return like_matches(r.literals[0][0], required, ignore_case=True)
    return any(is_string and text.lower() == required.lower() for text, is_string in r.literals)


def _shown(r: Restriction) -> str:
    return ", ".join(f"'{t}'" if s else t for t, s in r.literals)


def _judge(r: Restriction, link: Link) -> Optional[Tuple[str, Severity]]:
    _, _, table, field, required = link
    text = r.node.sql()
    rule = f"{table}.{field} holds only {required} concepts on a conformant CDM (fkClass check, threshold 0)"
    if r.kind == EXCLUDED:
        if not any(is_string and t.lower() == required.lower() for t, is_string in r.literals):
            return None
        return (
            f"`{text}` keeps only {table} rows whose concept is not an {required}. {rule}, so this returns nothing "
            f"unless the CDM fails that check; it is useful only as a conformance check.",
            Severity.WARNING,
        )
    if r.kind not in (VALUES, PATTERN) or _admits(r, required):
        return None
    if outer_join_of(r.node) is not None:
        return (
            f"`{text}` restricts the concept of {table}.{field} to class {_shown(r)} in an outer join's ON. {rule}, "
            f"so no concept is ever attached and its columns are always NULL.",
            Severity.WARNING,
        )
    if _only_through_or(link[0]):
        return (
            f"`{text}` restricts the concept of {table}.{field} to class {_shown(r)}, but the concept reaches "
            f"{table} only through one branch of an OR. {rule}, so that branch selects no {table} row; the other "
            "branches still return rows.",
            Severity.WARNING,
        )
    return (
        f"`{text}` restricts the concept of {table}.{field} to class {_shown(r)}. {rule}, so the query returns no "
        f"{table} row.",
        Severity.ERROR,
    )


@register
class DrugEraConceptClassValidationRule(Rule):
    """A product-level concept-class filter on the concept of drug_era (or dose_era, drug_strength's ingredient)."""

    rule_id = "domain_specific.drug_era_concept_class_validation"
    name = "Drug Era Concept Class Validation"
    description = (
        "drug_era.drug_concept_id (like dose_era.drug_concept_id) holds only Ingredient concepts. A "
        "concept_class_id filter on that concept that admits no Ingredient returns no row (ERROR); one that "
        "excludes Ingredient selects only non-conformant rows, and one reached through an OR branch empties only "
        "that branch (WARNING)."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REWRITE: drug_era holds ingredient concepts only. For product-level classes (Clinical Drug, Branded Drug, Clinical "
        "Drug Form, …) query drug_exposure instead; to keep the eras of a product's ingredients, map the product to "
        "its ingredient through concept_ancestor (ancestor_concept_id = drug_era.drug_concept_id). Drop a "
        "`concept_class_id <> 'Ingredient'` filter unless the query is a conformance check."
    )
    example_bad = (
        "SELECT de.person_id FROM drug_era de\n"
        "JOIN concept c ON de.drug_concept_id = c.concept_id\n"
        "WHERE c.concept_class_id = 'Branded Drug';"
    )
    example_good = (
        "SELECT de.person_id FROM drug_era de\n"
        "JOIN concept c ON de.drug_concept_id = c.concept_id\n"
        "WHERE c.concept_class_id = 'Ingredient';"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT de.person_id FROM drug_era de\n"
                "JOIN concept c ON de.drug_concept_id = c.concept_id\n"
                "WHERE c.concept_class_id <> 'Ingredient';"
            ),
            severity=Severity.WARNING,
            note="excluding Ingredient selects only rows that fail the conformance check",
        ),
    )
    references = (
        cdm_table("drug_era"),
        section(BOOK_SQL, "exposure-cohort", "Exposure cohort"),
        DQD_FIELD_THRESHOLDS,
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = (sql or "").lower()
        if CONCEPT_CLASS_ID not in lowered or not any(t in lowered for t, _ in FK_CONCEPT_CLASS):
            return []
        trees, error = parse_sql(sql, dialect)
        if error:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            links = _links(tree)
            if not links:
                continue
            reported: Set[int] = set()
            verdicts: Dict[int, Tuple[str, Severity]] = {}
            for r in restrictions(tree, CONCEPT, CONCEPT_CLASS_ID):
                for link in links:
                    if not any(same_route(a, b) for a in r.paths for b in link[1]):
                        continue
                    if not holds_wherever(r.node, link[0]):
                        continue
                    verdict = _judge(r, link)
                    if verdict and id(r.node) not in reported:
                        verdicts[id(r.node)] = verdict
                        reported.add(id(r.node))
            for message, severity in verdicts.values():
                violations.append(self.create_violation(message=message, severity=severity))
        return violations


__all__ = ["DrugEraConceptClassValidationRule"]

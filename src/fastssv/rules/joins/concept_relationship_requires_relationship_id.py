"""Concept Relationship Requires Relationship ID Rule.

CONCEPT_RELATIONSHIP holds every kind of link between two concepts ('Maps to', 'Is a', 'Subsumes', 'Has finding
site', 'RxNorm has ing', ...) in the same two columns; the type is named only by relationship_id (Book of OHDSI 5.3).
A lookup that constrains concept_id_1 / concept_id_2 without restricting relationship_id returns every type at once.

Whether that changes the result depends on the concept looked up, which the SQL does not show: a non-standard source
code often has a single standard target, while a standard concept also links to its parents, children and attribute
concepts. The finding is therefore a WARNING. When the unrestricted ids reach a predicate on a clinical
``*_concept_id`` column the message says the selected rows can include concepts that are not the intended mapping.

A lookup is an instance of concept_relationship whose concept_id_1 / concept_id_2 values are compared anywhere in the
statement (directly, through CTEs, derived tables and IN-subqueries). It is not reported when:

* relationship_id is restricted for that instance, in its own SELECT or downstream on its projected column, by any
  non-negated predicate except ``<>``, ``IS [NOT] NULL`` and a LIKE pattern that matches every value;
* the instance's relationship_id reaches the statement's output (projected by ``SELECT cr.relationship_id``, ``*`` or
  ``cr.*``, or grouped on in the output SELECT), so every row shows its relationship type;
* the lookup is the body of NOT EXISTS: it asks whether a concept has any relationship at all.
"""

from typing import List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    CONCEPT_RELATIONSHIP,
    RELATIONSHIP_ID,
    InstanceKey,
    _instance_key,
    _is_filter_position,
    _query_branches,
    _reads,
    _reads_through,
    _select_sources,
    _side_reads,
    _source_query,
    _star_qualifiers,
    comparison_pairs,
    normalize_name,
    parse_sql,
    relationship_filters,
)
from fastssv.core.patch import add as patch_add, locate
from fastssv.core.references import BOOK_VOCABULARIES, CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register
from fastssv.schemas import SOURCE_CONCEPT_FIELDS, STANDARD_CONCEPT_FIELDS

CONCEPT_ID_COLUMNS = ("concept_id_1", "concept_id_2")

_CLINICAL_CONCEPT_FIELDS = {
    (normalize_name(t), normalize_name(c)) for t, c in (*STANDARD_CONCEPT_FIELDS, *SOURCE_CONCEPT_FIELDS)
}


def _matches_everything(node: exp.Expression) -> bool:
    """``x LIKE '%'`` (and ILIKE): true for every non-null value, so it restricts nothing."""
    if not isinstance(node, (exp.Like, exp.ILike)):
        return False
    pattern = node.expression
    return isinstance(pattern, exp.Literal) and pattern.is_string and set(pattern.this) <= {"%"} and pattern.this != ""


def _drop_non_restricting(tree: exp.Expression) -> exp.Expression:
    return tree.transform(lambda node: exp.true() if _matches_everything(node) else node)


def _instance_reads(nodes, table_column_ok) -> Set[InstanceKey]:
    out: Set[InstanceKey] = set()
    for select, alias, table, column in nodes:
        if table == CONCEPT_RELATIONSHIP and table_column_ok(column):
            out.add(_instance_key(select, alias))
    return out


def _constrained_instances(tree: exp.Expression) -> Set[InstanceKey]:
    """Instances whose concept_id_1 / concept_id_2 are compared somewhere: a filter-position column, or either side
    of an equality / IN-subquery."""
    out: Set[InstanceKey] = set()
    for col in tree.find_all(exp.Column):
        if _is_filter_position(col):
            out |= _instance_reads(_reads(col), lambda c: c in CONCEPT_ID_COLUMNS)
    for _, left, right in comparison_pairs(tree):
        for side in (left, right):
            out |= _instance_reads(_side_reads(side, relay=False), lambda c: c in CONCEPT_ID_COLUMNS)
    return out


def _clinical_reach(tree: exp.Expression) -> Set[InstanceKey]:
    """Instances whose concept ids are compared with a clinical *_concept_id column."""
    out: Set[InstanceKey] = set()
    for _, left, right in comparison_pairs(tree):
        left_reads, right_reads = _side_reads(left), _side_reads(right)
        for a, b in ((left_reads, right_reads), (right_reads, left_reads)):
            if any((t, c) in _CLINICAL_CONCEPT_FIELDS for _, _, t, c in a):
                out |= _instance_reads(b, lambda c: c in CONCEPT_ID_COLUMNS)
    return out


def _output_query(tree: exp.Expression) -> Optional[exp.Expression]:
    """The query whose rows the statement returns or writes."""
    if isinstance(tree, (exp.Select, exp.SetOperation)):
        return tree
    if isinstance(tree, (exp.Insert, exp.Create)):
        query = tree.expression
        return query if isinstance(query, (exp.Select, exp.SetOperation)) else None
    return None


def _shown_instances(tree: exp.Expression) -> Set[InstanceKey]:
    """Instances whose relationship_id reaches the statement output: projected (explicitly or through ``*`` /
    ``t.*``) or grouped on by an output SELECT."""
    query = _output_query(tree)
    if query is None:
        return set()
    out: Set[InstanceKey] = set()
    for branch in _query_branches(query):
        nodes = list(branch.expressions)
        if branch.args.get("group") is not None:
            nodes.append(branch.args["group"])
        for node in nodes:
            for col in node.find_all(exp.Column):
                if not isinstance(col.this, exp.Star) and col.find_ancestor(exp.Select) is branch:
                    out |= _instance_reads(_reads(col), lambda c: c == RELATIONSHIP_ID)
        stars = _star_qualifiers(branch)
        for src in _select_sources(branch):
            if None in stars or normalize_name(src.alias_or_name) in stars:
                reads = _reads_through(branch, src, RELATIONSHIP_ID, 0, set(), relay=False)
                out |= _instance_reads(reads, lambda c: c == RELATIONSHIP_ID)
    return out


def _in_not_exists(select: exp.Select) -> bool:
    node = select.parent
    if isinstance(node, exp.Subquery):
        node = node.parent
    return isinstance(node, exp.Exists) and isinstance(node.parent, exp.Not)


def _join_on_sql(select: exp.Select, alias: str) -> Optional[str]:
    for join in select.args.get("joins") or []:
        table = join.this
        if isinstance(table, exp.Table) and normalize_name(table.alias_or_name) == alias:
            on = join.args.get("on")
            return on.sql() if on is not None else None
    return None


@register
class ConceptRelationshipRequiresRelationshipIdRule(Rule):
    """Flag concept_relationship lookups that do not restrict relationship_id."""

    rule_id = "joins.concept_relationship_requires_relationship_id"
    name = "Concept Relationship Requires Relationship ID Filter"
    description = (
        "A concept_relationship lookup without a relationship_id restriction returns every relationship type at "
        "once ('Maps to', 'Is a', 'Subsumes', attribute links). Whether that changes the result depends on the "
        "concept looked up, so the finding is a WARNING."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: `AND cr.relationship_id = '<relationship>'` to the concept_relationship lookup, typically "
        "'Maps to' for source-to-standard mapping. For hierarchy use concept_ancestor instead. If every "
        "relationship type is wanted, project relationship_id."
    )
    long_description = (
        "concept_relationship stores every link between two concepts: 'Maps to', 'Is a', 'Subsumes', attribute "
        "links such as 'Has finding site', drug links such as 'RxNorm has ing', and their reverses. Reading "
        "concept_id_2 for a concept without restricting relationship_id returns all of them. For a non-standard "
        "source code that may be just its standard target, but for a standard concept it adds parents, children "
        "and attribute concepts (Type 2 diabetes mellitus, 201826, has 133 standard condition concepts among its "
        "links and one 'Maps to' target in Athena v5.0 27-AUG-25). Because the effect depends on the concept, the "
        "rule warns. Lookups that show relationship_id in the output, never constrain concept_id_1 / "
        "concept_id_2, or sit in a NOT EXISTS orphan check are not reported."
    )
    example_bad = (
        "WITH related AS (\n"
        "  SELECT cr.concept_id_2 AS concept_id\n"
        "  FROM concept_relationship cr\n"
        "  WHERE cr.concept_id_1 = 201826\n"
        ")\n"
        "SELECT COUNT(DISTINCT co.person_id)\n"
        "FROM condition_occurrence co\n"
        "JOIN related r ON co.condition_concept_id = r.concept_id;"
    )
    example_good = (
        "WITH related AS (\n"
        "  SELECT cr.concept_id_2 AS concept_id\n"
        "  FROM concept_relationship cr\n"
        "  WHERE cr.concept_id_1 = 201826\n"
        "    AND cr.relationship_id = 'Maps to'\n"
        ")\n"
        "SELECT COUNT(DISTINCT co.person_id)\n"
        "FROM condition_occurrence co\n"
        "JOIN related r ON co.condition_concept_id = r.concept_id;"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT c.concept_id, c.concept_name\n"
                "FROM concept_relationship cr\n"
                "JOIN concept c ON cr.concept_id_2 = c.concept_id\n"
                "WHERE cr.concept_id_1 = 201826;"
            ),
            severity=Severity.WARNING,
            note="A vocabulary-only lookup: the result mixes relationship types and does not show which.",
        ),
    )
    references = (
        cdm_table("concept_relationship"),
        section(CDM_CONVENTIONS, "concept-relationships", "Concept Relationships"),
        section(BOOK_VOCABULARIES, "relationships", "Relationships"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if CONCEPT_RELATIONSHIP not in sql.lower():
            return []
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for parsed in trees:
            if not parsed:
                continue
            tree = _drop_non_restricting(parsed)
            filtered = relationship_filters(tree)
            constrained = _constrained_instances(tree)
            shown = _shown_instances(tree)
            reaching: Optional[Set[InstanceKey]] = None

            for select in tree.find_all(exp.Select):
                for src in _select_sources(select):
                    if not isinstance(src, exp.Table) or normalize_name(src.name) != CONCEPT_RELATIONSHIP:
                        continue
                    if _source_query(select, src) is not None:
                        continue
                    alias = normalize_name(src.alias_or_name)
                    key = _instance_key(select, alias)
                    if key in filtered or key in shown or key not in constrained or _in_not_exists(select):
                        continue
                    if reaching is None:
                        reaching = _clinical_reach(tree)
                    reaches = key in reaching
                    if reaches:
                        message = (
                            f"concept_relationship '{alias}' has no relationship_id restriction, and its concept ids "
                            "select clinical rows. Every relationship type ('Maps to', 'Is a', 'Subsumes', attribute "
                            "links) is included, so depending on the concept looked up the rows can include concepts "
                            "that are not the intended mapping."
                        )
                    else:
                        message = (
                            f"concept_relationship '{alias}' is looked up without a relationship_id restriction, "
                            "so the result can mix relationship types without showing which. Restrict "
                            "relationship_id, or project it if all relationships are wanted."
                        )
                    patch = None
                    on_sql = _join_on_sql(select, alias)
                    span = locate(sql, on_sql) if on_sql else None
                    if span is not None:
                        patch = patch_add(span[1], f" AND {alias}.relationship_id = '<relationship>'")
                    violations.append(
                        self.create_violation(
                            message=message,
                            details={"alias": alias, "reaches_clinical_predicate": reaches},
                            suggested_fix_patch=patch,
                        )
                    )
        return violations


__all__ = ["ConceptRelationshipRequiresRelationshipIdRule"]

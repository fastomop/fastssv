"""Join Key Validation Rule.

The CDM conventions separate two id namespaces. "_ID: Unique identifiers for key entities, which can serve as
foreign keys to establish relationships across entities" (person_id, visit_occurrence_id, provider_id, ...), and
"_CONCEPT_ID: Foreign key into the Standardized Vocabularies". An equality between an entity key and a concept id
compares numbers from unrelated sequences: both are integers, so the comparison runs, but every match is a numeric
coincidence.

    SELECT * FROM person p JOIN concept c ON p.person_id = c.concept_id
    -- returns the concept whose id happens to equal each person_id

Entity keys are the primary keys of the CDM's non-vocabulary tables and the foreign keys that refer to them;
concept ids are concept.concept_id and every column that is a foreign key to CONCEPT. Both lists come from the CDM
v5.4 field-level specification. Both sides are traced through CTEs, derived tables, casts and renaming aliases to
the base column they read, and ``entity_key IN (SELECT concept_id ...)`` is judged like an equality. Columns of
tables outside the CDM are not judged.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.deduplication import predicate_key
from fastssv.core.helpers import DERIVED, extract_aliases, normalize_name, parse_sql, resolve_column_source
from fastssv.core.references import BOOK_CDM, CDM_CONVENTIONS, section
from fastssv.core.registry import register
from fastssv.rules.anti_patterns._ap_cdm_fields import CDM_TABLES, CONCEPT_ID_COLUMNS, ENTITY_KEY_COLUMNS

_UNWRAP = (exp.Cast, exp.TryCast, exp.Paren)


def _base_column(node: Optional[exp.Expression], aliases: Dict[str, str]) -> Optional[Tuple[str, str]]:
    """(table, column) of the CDM column ``node`` reads, through casts and lineage; None otherwise."""
    while isinstance(node, _UNWRAP):
        node = node.this
    if not isinstance(node, exp.Column):
        return None
    table, column = resolve_column_source(node, aliases)
    table, column = normalize_name(table or ""), normalize_name(column or "")
    if table == DERIVED or table not in CDM_TABLES:
        return None
    return table, column


def _subquery_column(query: exp.Expression) -> Optional[exp.Expression]:
    while isinstance(query, exp.Subquery):
        query = query.this
    if isinstance(query, exp.Select) and len(query.expressions) == 1:
        proj = query.expressions[0]
        return proj.this if isinstance(proj, exp.Alias) else proj
    return None


def _kind(col: Tuple[str, str]) -> Optional[str]:
    if col[1] in CONCEPT_ID_COLUMNS:
        return "concept"
    if col[1] in ENTITY_KEY_COLUMNS:
        return "entity"
    return None


@register
class JoinKeyValidationRule(Rule):
    """Entity key compared with a concept id."""

    rule_id = "anti_patterns.join_key_validation"
    name = "Join Key Validation"

    description = (
        "Detects an equality between an OMOP entity key (person_id, visit_occurrence_id, provider_id, "
        "condition_occurrence_id, ...) and a concept id. These are different id namespaces, so any match is "
        "coincidental and the join returns meaningless rows."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "REPLACE: the join keys with a pair the CDM defines: person_id = person_id across clinical tables; "
        "<event>.<x>_concept_id = concept.concept_id; <clinical>.visit_occurrence_id = "
        "visit_occurrence.visit_occurrence_id."
    )
    long_description = (
        "The CDM conventions use distinct id namespaces: _ID columns identify entity rows (person_id, "
        "visit_occurrence_id, provider_id, care_site_id, location_id, the event-table keys), _CONCEPT_ID columns "
        "are foreign keys into the Standardized Vocabularies. Comparing an entity key with a concept id rarely "
        "errors, because both are integers, but every match is a numeric coincidence. Both sides are traced "
        "through CTEs, derived tables, casts and renaming aliases to the base column they read, and IN "
        "(SELECT ...) is judged like an equality. The key lists come from the CDM v5.4 field-level specification."
    )
    example_bad = "SELECT *\nFROM person p\nJOIN concept c ON p.person_id = c.concept_id;"
    example_good = (
        "SELECT p.person_id, c.concept_name AS gender\n"
        "FROM person p\n"
        "JOIN concept c ON p.gender_concept_id = c.concept_id;"
    )
    references = (
        section(CDM_CONVENTIONS, "fields", "Fields"),
        section(BOOK_CDM, "general-naming-conventions-of-fields", "General naming conventions of fields"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees or []:
            if not tree:
                continue
            aliases = extract_aliases(tree)
            for node in tree.find_all(exp.EQ, exp.In):
                if isinstance(node, exp.EQ):
                    left, right = node.this, node.expression
                else:
                    if node.args.get("query") is None:
                        continue
                    left, right = node.this, _subquery_column(node.args["query"])
                a, b = _base_column(left, aliases), _base_column(right, aliases)
                if a is None or b is None:
                    continue
                kinds = {_kind(a), _kind(b)}
                if kinds != {"entity", "concept"}:
                    continue
                entity, concept = (a, b) if _kind(a) == "entity" else (b, a)
                context = node.sql()
                if context in seen:
                    continue
                seen.add(context)
                violations.append(
                    self.create_violation(
                        message=(
                            f"Incompatible JOIN keys: {context} compares entity key '{entity[1]}' with concept id "
                            f"'{concept[0]}.{concept[1]}'. Entity keys and concept ids are different id "
                            "namespaces, so any match is a coincidence."
                        ),
                        details={
                            "type": "incompatible_join",
                            "left_column": a[1],
                            "right_column": b[1],
                            "context": context,
                            "predicate": predicate_key(node),
                        },
                    )
                )
        return violations


__all__ = ["JoinKeyValidationRule"]

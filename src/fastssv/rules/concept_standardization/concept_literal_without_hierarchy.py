"""Clinical concept literal filtered without hierarchy expansion.

Filtering an event table on a literal concept id — ``condition_concept_id = 201826`` or
``drug_concept_id IN (1127433, 19078106)`` — matches only records coded at exactly that concept. OMOP standard
vocabularies are hierarchical: descendants (subtypes, clinical drugs of an ingredient, branded forms) are coded
with their own concept ids, and an ingredient- or class-level literal matches almost nothing. The idiomatic
pattern expands the literal through ``concept_ancestor`` (``JOIN concept_ancestor ca ON e.x_concept_id =
ca.descendant_concept_id WHERE ca.ancestor_concept_id = 201826``).

Evidence: silent-wrong queries returning 0 patients because an ingredient literal was filtered directly.
This is advisory — a query may deliberately target one exact concept — so the
rule is a WARNING and stays silent when the query uses ``concept_ancestor`` anywhere or filters on a
``*_source_concept_id``.
"""

from typing import List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register

# Standard-concept columns whose vocabularies are hierarchical in practice.
_HIERARCHICAL_COLUMNS = {
    "condition_concept_id",
    "drug_concept_id",
    "procedure_concept_id",
    "measurement_concept_id",
    "observation_concept_id",
    "device_concept_id",
}


def _int_literals(node: exp.Expression) -> List[int]:
    if isinstance(node, exp.Literal) and node.is_int:
        return [int(node.this)]
    if isinstance(node, (exp.Tuple, exp.Array)):
        out: List[int] = []
        for e in node.expressions:
            out.extend(_int_literals(e))
        return out
    return []


@register
class ConceptLiteralWithoutHierarchyRule(Rule):
    rule_id = "concept_standardization.concept_literal_without_hierarchy"
    name = "Concept literal without hierarchy expansion"
    description = (
        "A clinical *_concept_id is filtered on literal concept ids while the query never uses concept_ancestor; "
        "descendant concepts (subtypes, clinical drugs of an ingredient) will be missed."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: `e.<x>_concept_id = <id>` WITH a hierarchy expansion: `JOIN concept_ancestor ca ON "
        "e.<x>_concept_id = ca.descendant_concept_id WHERE ca.ancestor_concept_id = <id>` (or a codeset built the "
        "same way), unless exactly that one concept is intended."
    )
    example_bad = "SELECT COUNT(DISTINCT person_id) FROM drug_exposure\nWHERE drug_concept_id = 1125315;  -- acetaminophen ingredient: products are descendants"
    example_good = "SELECT COUNT(DISTINCT de.person_id) FROM drug_exposure de\nJOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id\nWHERE ca.ancestor_concept_id = 1125315;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            tables = {normalize_name(t.name) for t in tree.find_all(exp.Table)}
            if "concept_ancestor" in tables:
                continue  # hierarchy is expanded somewhere in the query
            hits = []
            for node in tree.find_all(exp.EQ, exp.In):
                col = node.this
                if not isinstance(col, exp.Column):
                    continue
                cname = normalize_name(col.name) or ""
                if cname not in _HIERARCHICAL_COLUMNS:
                    continue
                target = node.expression if isinstance(node, exp.EQ) else node
                lits = (
                    _int_literals(target)
                    if isinstance(node, exp.EQ)
                    else [v for e in node.expressions for v in _int_literals(e)]
                )
                if lits:
                    hits.append((cname, lits))
            if not hits:
                continue
            cols = sorted({c for c, _ in hits})
            ids = sorted({v for _, ls in hits for v in ls})[:6]
            violations.append(
                self.create_violation(
                    message=(
                        f"{', '.join(cols)} filtered on literal concept id(s) {', '.join(map(str, ids))} without "
                        "concept_ancestor expansion; records coded at descendant concepts will be missed."
                    ),
                    severity=self.severity,
                )
            )
        return violations


__all__ = ["ConceptLiteralWithoutHierarchyRule"]

"""Negative Concept ID Validation Rule.

The Standardized Vocabularies assign concept_ids from 0 upwards ("reserved from 0 to 2,000,000,000"; above that
range for local use), so no CDM column that holds a concept_id can contain a negative value. A predicate that only a
negative concept_id would satisfy matches no row.

Checked columns: CONCEPT.concept_id and every CDM v5.4 column with a foreign key to CONCEPT (generated from the
field-level CSV, ``_dq_concept_columns.py``), when the column's lineage is that CDM column. A value recomputed on the
way (``COALESCE(unit_concept_id, -1)``) and columns of non-CDM tables are not judged.

Flagged predicates (literals may be parenthesised or cast):
    * ``= -n``, ``IN (...)`` whose values are all negative, ``BETWEEN`` with a negative upper bound, ``< 0``,
      ``<= -n``, ``< -n``.
    * ERROR when the predicate restricts the rows the statement returns: an AND-conjunct of WHERE, HAVING or an
      inner join's ON, and, inside a subquery, only when that subquery's IN / EXISTS / comparison restricts the
      enclosing query in turn. The query returns nothing from that source. WARNING otherwise: in an outer join's ON
      it attaches no row of the joined side (the outer rows are kept), inside NOT EXISTS / NOT IN the empty subquery
      removes no row, and under OR, CASE or in the select list the branch is dead.
    * WARNING for a negative value inside an IN list that also holds valid ids: that member matches nothing.
Not flagged: predicates that every row satisfies (``> -1``, ``<> -1``, ``NOT IN (-1)``), and anything under NOT.
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import DERIVED, column_origins, parse_sql
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.references import CDM_CONVENTIONS, section
from fastssv.core.registry import register

from fastssv.rules.domain_specific._rows import in_negated_scope, restricting_clause, restricts_rows

from ._dq_concept_columns import CONCEPT_ID_COLUMNS

_WRAPPERS = (exp.Paren, exp.Cast)


def _peel(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, _WRAPPERS) and not isinstance(node, exp.TryCast):
        node = node.this
    return node


def _int(node: Optional[exp.Expression]) -> Optional[int]:
    node = _peel(node)
    if isinstance(node, exp.Neg):
        inner = _peel(node.this)
        if isinstance(inner, exp.Literal) and inner.is_int:
            return -int(inner.this)
        return None
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    return None


def _concept_column(node: Optional[exp.Expression]) -> Optional[exp.Column]:
    node = _peel(node)
    if not isinstance(node, exp.Column):
        return None
    origins = column_origins(node)
    if not origins or any(t == DERIVED or (t, c) not in CONCEPT_ID_COLUMNS for t, c in origins):
        return None
    return node


def _negated(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None and not isinstance(parent, (exp.Select, exp.Where, exp.Join, exp.Having)):
        if isinstance(parent, exp.Not):
            return True
        parent = parent.parent
    return False


_SUBQUERY_PREDICATES = (exp.In, exp.Exists, exp.Any, exp.All, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def _restricts_result(node: exp.Expression) -> bool:
    """``node`` must hold for every row the statement returns: it restricts its own SELECT (not in an outer join's
    ON, not under NOT), and a subquery holding it sits in a predicate that restricts the enclosing SELECT in turn.
    Under NOT EXISTS / NOT IN an empty subquery removes no outer row."""
    if not restricts_rows(node) or in_negated_scope(node):
        return False
    select = node.find_ancestor(exp.Select)
    while select is not None:
        holder = select.parent
        while isinstance(holder, (exp.Subquery, exp.Paren, exp.SetOperation)):
            holder = holder.parent
        if not isinstance(holder, _SUBQUERY_PREDICATES):
            return True
        if not restricts_rows(holder):
            return False
        select = holder.find_ancestor(exp.Select)
    return True


def _in_outer_join_on(node: exp.Expression) -> bool:
    clause = restricting_clause(node)
    return isinstance(clause, exp.Join) and not restricts_rows(node)


def _never_true(node: exp.Expression) -> Optional[Tuple[exp.Column, str]]:
    """(column, kind) when ``node`` compares a concept_id column so that no valid id satisfies it.

    kind: "dead" (the whole predicate matches nothing) or "member" (one IN member matches nothing)."""
    if isinstance(node, exp.In):
        column = _concept_column(node.this)
        values = [_int(v) for v in node.expressions or []]
        if column is None or not values or any(v is None for v in values):
            return None
        negatives = [v for v in values if v < 0]
        if not negatives:
            return None
        return column, "dead" if len(negatives) == len(values) else "member"
    if isinstance(node, exp.Between):
        column = _concept_column(node.this)
        high = _int(node.args.get("high"))
        return (column, "dead") if column is not None and high is not None and high < 0 else None
    for col_side, val_side, flipped in ((node.this, node.expression, False), (node.expression, node.this, True)):
        column = _concept_column(col_side)
        value = _int(val_side)
        if column is None or value is None:
            continue
        op = type(node)
        if flipped:
            op = {exp.LT: exp.GT, exp.GT: exp.LT, exp.LTE: exp.GTE, exp.GTE: exp.LTE}.get(op, op)
        if (op is exp.EQ and value < 0) or (op is exp.LT and value <= 0) or (op is exp.LTE and value < 0):
            return column, "dead"
    return None


@register
class NegativeConceptIdValidationRule(Rule):
    """Predicates on a concept_id column that only a negative value would satisfy."""

    rule_id = "data_quality.negative_concept_id_validation"
    name = "Negative Concept ID Validation"
    description = (
        "Concept ids are non-negative (0 for 'no matching concept', positive for real concepts), so a filter that "
        "only a negative concept_id satisfies (= -1, IN (-1, -2), < 0) matches no row."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: the negative literal WITH the intended concept_id (0 for unmapped records). If -1 stands for a "
        "missing value, test `IS NULL` or `= 0` instead."
    )
    long_description = (
        "The Standardized Vocabularies assign concept_ids from 0 upwards; 0 means 'no matching concept' and ids "
        "above 2,000,000,000 are reserved for local use. No CDM column that holds a concept_id contains a negative "
        "value, so a filter such as condition_concept_id = -1 or unit_concept_id < 0 matches nothing: the query "
        "returns no rows from that table (ERROR). In an outer join's ON clause the comparison attaches no row and "
        "the outer rows are kept; inside NOT EXISTS / NOT IN the empty subquery removes no row; under OR, in a CASE "
        "or in the select list the comparison is a dead branch; a negative id inside an IN list with valid ids is a "
        "dead member (all WARNING). Predicates "
        "that every row satisfies (> -1, <> -1, NOT IN (-1)) and sentinel values computed in the query "
        "(COALESCE(unit_concept_id, -1)) are not flagged."
    )
    example_bad = "SELECT *\nFROM condition_occurrence\nWHERE condition_concept_id = -1;"
    example_good = "SELECT *\nFROM condition_occurrence\nWHERE condition_concept_id = 201820;"
    extra_examples = (
        RuleExample(
            sql="SELECT *\nFROM condition_occurrence\nWHERE condition_concept_id IN (201820, -1);",
            severity=Severity.WARNING,
            note="A negative id among valid ones: that member of the IN list matches nothing.",
        ),
    )
    references = (section(CDM_CONVENTIONS, "concepts", "Concepts"),)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not any(ch in sql for ch in "-<>"):
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            seen: Set[str] = set()
            for node in tree.find_all(exp.EQ, exp.LT, exp.LTE, exp.GT, exp.GTE, exp.In, exp.Between):
                if isinstance(node, exp.In) and isinstance(node.parent, exp.Not) or _negated(node):
                    continue
                found = _never_true(node)
                if found is None:
                    continue
                column, kind = found
                fragment = node.sql(dialect=dialect)
                if fragment in seen:
                    continue
                seen.add(fragment)
                if kind == "member":
                    severity = Severity.WARNING
                    message = (
                        f"Negative value in `{fragment}`: concept ids are non-negative, so that member of the IN "
                        f"list matches nothing."
                    )
                elif _restricts_result(node):
                    severity = Severity.ERROR
                    message = (
                        f"`{fragment}` matches no row: concept ids are non-negative (0 = no matching concept), so "
                        f"the query returns nothing from {column.table or 'this table'}."
                    )
                elif _in_outer_join_on(node):
                    severity = Severity.WARNING
                    message = (
                        f"`{fragment}` is never true: concept ids are non-negative (0 = no matching concept), so this "
                        "outer join's ON clause attaches no row; the joined columns are NULL on every row and the "
                        "other side's rows are kept."
                    )
                elif in_negated_scope(node):
                    severity = Severity.WARNING
                    message = (
                        f"`{fragment}` is never true: concept ids are non-negative (0 = no matching concept). It sits "
                        "in a NOT EXISTS / NOT IN subquery (or under NOT), so the negation around it removes no row "
                        "from the outer query."
                    )
                else:
                    severity = Severity.WARNING
                    message = (
                        f"`{fragment}` is never true: concept ids are non-negative (0 = no matching concept), so "
                        f"this branch is dead."
                    )
                patch = None
                if isinstance(node, exp.EQ):
                    span = locate(sql, node.sql())
                    if span is not None:
                        patch = patch_replace(span, f"{column.sql()} = <expected_concept_id>")
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=severity,
                        suggested_fix_patch=patch,
                        details={"column": column.name, "kind": kind},
                    )
                )
        return violations


__all__ = ["NegativeConceptIdValidationRule"]

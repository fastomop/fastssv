"""Left Join Then Where On Right Table Rule.

OMOP semantic rule OMOP_149:
A LEFT JOIN keeps every left-side row; rows without a match get NULL in every
right-side column. A WHERE conjunct that can never be true when a right-side
column is NULL (a "null-rejecting" predicate) removes exactly those rows, so the
LEFT JOIN behaves like an INNER JOIN.

Null-rejecting conjuncts that are flagged (``r`` is the LEFT JOIN's right side):
    r.col = 'x', r.col <> 'x', r.col > 5, r.col IN (...), r.col LIKE '...',
    r.col BETWEEN a AND b, r.col = l.col, LOWER(r.col) = 'x', r.col IS NOT NULL

Predicates that keep NULL-extended rows are not flagged:
    r.col IS NULL                         -- selects the unmatched rows on purpose
    COALESCE(r.col, 0) = 0, CASE ... END  -- explicit NULL handling
    NOT (r.col IS NULL AND l.col IS NULL)
    r.col = 'x' OR l.col = 'y'            -- another branch can keep the row

Violation patterns:
    SELECT co.* FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_concept_id = 9201

    SELECT co.* FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_occurrence_id IS NOT NULL   -- same rows as an INNER JOIN

Correct patterns:
    -- INNER JOIN when only matched rows are wanted
    SELECT co.* FROM condition_occurrence co
    JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id
    WHERE vo.visit_concept_id = 9201

    -- Right-side filter in the ON clause keeps unmatched left rows
    SELECT co.* FROM condition_occurrence co
    LEFT JOIN visit_occurrence vo
      ON co.visit_occurrence_id = vo.visit_occurrence_id
      AND vo.visit_concept_id = 9201

Only columns that the SELECT reads from a LEFT JOIN's right-side source are
considered (a qualified alias, or an unqualified name that only that source
supplies); another alias of the same base table (self-joins, an inner-joined
second copy of ``concept``) is not. Date functions that return NULL for a NULL
argument (``DATE_TRUNC``, ``DATEDIFF``, ``EXTRACT``, ``YEAR``) are followed, and
``(predicate) IS TRUE`` is as null-rejecting as the predicate.
"""

import logging
from typing import List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.registry import register

from ._lineage import owner_alias


logger = logging.getLogger(__name__)


# --- Helpers -----------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_left_join(join: exp.Join) -> bool:
    kind = join.args.get("kind")
    side = join.args.get("side")
    return bool((kind and _norm(kind) == "left") or (side and _norm(side) == "left"))


def _unparen(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _right_aliases(select: exp.Select) -> Set[str]:
    """Aliases of the right-hand sources of LEFT JOINs declared directly on ``select``."""
    out: Set[str] = set()
    for join in select.args.get("joins") or []:
        if not _is_left_join(join):
            continue
        src = join.this
        if isinstance(src, (exp.Table, exp.Subquery)) and src.alias_or_name:
            out.add(_norm(src.alias_or_name))
    return out


def _flatten_and(node: exp.Expression) -> List[exp.Expression]:
    node = _unparen(node)
    if isinstance(node, exp.And):
        return _flatten_and(node.this) + _flatten_and(node.expression)
    return [node]


# Functions/operators that return NULL whenever their argument is NULL.
_STRICT_WRAPPERS = (
    exp.Paren,
    exp.Cast,
    exp.TryCast,
    exp.Lower,
    exp.Upper,
    exp.Trim,
    exp.Abs,
    exp.Length,
    exp.Substring,
    exp.Extract,
    exp.Add,
    exp.Sub,
    exp.Mul,
    exp.Div,
    exp.Mod,
    exp.DateTrunc,
    exp.TimestampTrunc,
    exp.DateDiff,
    exp.DateAdd,
    exp.DateSub,
    exp.Year,
    exp.Month,
    exp.Day,
    exp.TimeStrToTime,
    exp.TsOrDsToDate,
)

_COMPARISONS = (
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.ILike,
    exp.Between,
    exp.In,
    exp.SimilarTo,
    exp.RegexpLike,
)


def _strict_columns(node: Optional[exp.Expression]) -> List[exp.Column]:
    """Columns whose NULL makes ``node`` NULL (reached only through strict wrappers)."""
    if node is None:
        return []
    if isinstance(node, exp.Column):
        return [node]
    if isinstance(node, _STRICT_WRAPPERS):
        out: List[exp.Column] = []
        for child in node.iter_expressions():
            out.extend(_strict_columns(child))
        return out
    return []


def _null_rejected_columns(cond: exp.Expression) -> List[exp.Column]:
    """Columns for which ``cond`` is never TRUE when the column is NULL."""
    cond = _unparen(cond)
    if isinstance(cond, exp.Is) and isinstance(cond.expression, exp.Boolean) and cond.expression.this is True:
        return _null_rejected_columns(cond.this)  # (NULL) IS TRUE is FALSE
    if isinstance(cond, exp.Not):
        inner = _unparen(cond.this)
        if isinstance(inner, exp.Is):
            return _strict_columns(inner.this)  # col IS NOT NULL
        if isinstance(inner, exp.In) and inner.args.get("query"):
            return []  # NULL NOT IN (<empty subquery>) is TRUE
        if isinstance(inner, _COMPARISONS):
            return _null_rejected_columns(inner)  # NOT (NULL) is NULL
        return []
    if not isinstance(cond, _COMPARISONS):
        return []
    if isinstance(cond, exp.Between):
        operands = [cond.this]
    elif isinstance(cond, exp.In):
        operands = [cond.this]
    else:
        operands = [cond.this, cond.expression]
    out: List[exp.Column] = []
    for operand in operands:
        out.extend(_strict_columns(operand))
    return out


def _find_violations(tree: exp.Expression) -> List[str]:
    issues: List[str] = []

    for select in tree.find_all(exp.Select):
        right = _right_aliases(select)
        if not right:
            continue
        where = select.args.get("where")
        if where is None:
            continue

        cited: List[str] = []
        for cond in _flatten_and(where.this):
            for col in _null_rejected_columns(cond):
                if col.find_ancestor(exp.Select) is not select:
                    continue
                qualifier = _norm(col.table) if col.table else owner_alias(col, select)
                if qualifier in right:
                    cited.append(f"{qualifier}.{_norm(col.name)}")

        if cited:
            cols = ", ".join(sorted(set(cited)))
            issues.append(
                f"LEFT JOIN followed by WHERE filtering right table column(s): {cols}. "
                f"These predicates are never true for the NULL-extended rows of unmatched left rows, "
                f"so the LEFT JOIN behaves as an INNER JOIN. Use INNER JOIN or move the condition "
                f"into JOIN ON."
            )

    return list(dict.fromkeys(issues))


# --- Rule --------------------------------------------------------------------


@register
class LeftJoinThenWhereOnRightTableRule(Rule):
    """
    OMOP_149: Warn when LEFT JOIN is followed by WHERE filtering on right table.
    """

    rule_id = "joins.left_join_then_where_on_right_table"
    name = "Left Join Then Where On Right Table"

    description = (
        "A WHERE predicate that is never true when a LEFT JOIN's right-side column is NULL "
        "(e.g. right.col = 'x' or right.col IS NOT NULL) drops the unmatched rows, so the LEFT JOIN "
        "behaves as an INNER JOIN."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `WHERE <right>.<col> <op> <val>` (after LEFT JOIN) WITH either: (a) move the predicate into the JOIN ON clause (`LEFT JOIN ... ON ... AND <right>.<col> <op> <val>`) to preserve left-outer semantics, OR (b) accept that the LEFT JOIN is effectively INNER and rewrite as INNER JOIN."
    example_bad = (
        "SELECT p.person_id FROM person p\n"
        "LEFT JOIN visit_occurrence vo ON p.person_id = vo.person_id\n"
        "WHERE vo.visit_start_date >= DATE '2023-01-01';"
    )
    example_good = (
        "SELECT p.person_id FROM person p\n"
        "LEFT JOIN visit_occurrence vo\n"
        "  ON p.person_id = vo.person_id\n"
        "  AND vo.visit_start_date >= DATE '2023-01-01';"
    )
    extra_examples = (
        RuleExample(
            "SELECT co.condition_occurrence_id FROM condition_occurrence co\n"
            "LEFT JOIN visit_occurrence vo ON co.visit_occurrence_id = vo.visit_occurrence_id\n"
            "WHERE vo.visit_occurrence_id IS NOT NULL;",
            Severity.WARNING,
            "An IS NOT NULL test on the right table keeps exactly the rows an INNER JOIN would.",
        ),
    )
    references = (
        Reference(
            "PostgreSQL documentation: Joined Tables",
            "https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN",
        ),
        Reference(
            "PostgreSQL documentation: The WHERE Clause",
            "https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-WHERE",
        ),
        Reference(
            "PostgreSQL documentation: Comparison Functions and Operators",
            "https://www.postgresql.org/docs/current/functions-comparison.html",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        # Fast pre-filters
        if "left" not in sql_lower or "join" not in sql_lower:
            return []

        if "where" not in sql_lower:
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_149",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            issues = _find_violations(tree)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["LeftJoinThenWhereOnRightTableRule"]

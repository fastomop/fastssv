"""Concept Code Requires Vocabulary ID Rule.

OMOP vocabulary rule:
concept_code is unique only within a vocabulary. Any filter on concept_code
must also include a vocabulary_id filter in the same scope, otherwise the
query may silently match unintended concepts from other vocabularies.
"""

from typing import Dict, List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register

STRING_MATCH_EXP_TYPES = (exp.Like, exp.ILike, exp.RegexpLike)


def _alias_matches(col: exp.Column, target_alias: Optional[str]) -> bool:
    """Check if a column's table qualifier matches the target alias.

    If either side is unqualified, accept (handles single-table cases).
    If both are qualified, aliases must match exactly.
    """
    col_alias = normalize_name(col.table) if col.table else None
    if col_alias is None or target_alias is None:
        return True
    return col_alias == target_alias


def _has_vocabulary_id_filter(select: exp.Select, target_alias: Optional[str]) -> bool:
    """Check if vocabulary_id is filtered in this SELECT's direct scope.

    Searches WHERE and JOIN ON clauses but skips filters inside nested subqueries.
    """
    filter_nodes: List[exp.Expression] = []

    where = select.args.get("where")
    if where:
        filter_nodes.append(where)

    for join in select.args.get("joins") or []:
        on_expr = join.args.get("on")
        if on_expr:
            filter_nodes.append(on_expr)

    for filter_node in filter_nodes:
        for eq in filter_node.find_all(exp.EQ):
            # Skip EQs that belong to a nested subquery
            eq_select = eq.find_ancestor(exp.Select)
            if eq_select is not None and eq_select is not select:
                continue

            left, right = eq.left, eq.right
            if isinstance(right, exp.Column) and is_string_literal(left):
                left, right = right, left
            if isinstance(left, exp.Column) and is_string_literal(right):
                if normalize_name(left.name) == "vocabulary_id" and _alias_matches(left, target_alias):
                    return True

        for in_expr in filter_node.find_all(exp.In):
            in_select = in_expr.find_ancestor(exp.Select)
            if in_select is not None and in_select is not select:
                continue

            col = in_expr.this
            if isinstance(col, exp.Column) and normalize_name(col.name) == "vocabulary_id":
                if _alias_matches(col, target_alias):
                    if any(is_string_literal(v) for v in in_expr.expressions or []):
                        return True

    return False


def _check_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[RuleViolation]:
    """Find all concept_code filter usages missing a corresponding vocabulary_id filter."""
    violations: List[RuleViolation] = []
    seen: set = set()  # (id(select), alias) — one violation per scope per alias

    def _resolve(col: exp.Column):
        """Return (table, alias, select) or (None, None, None) if not on concept table."""
        table, _ = resolve_table_col(col, aliases)
        if table and table != "concept":
            return None, None, None
        alias = normalize_name(col.table) if col.table else None
        select = col.find_ancestor(exp.Select)
        return table, alias, select

    def _maybe_add(col: exp.Column, message: str):
        table, alias, select = _resolve(col)
        if select is None:
            return
        key = (id(select), alias)
        if key in seen:
            return
        seen.add(key)
        if not _has_vocabulary_id_filter(select, alias):
            violations.append(RuleViolation(
                rule_id="vocabulary.concept_code_requires_vocabulary_id",
                severity=Severity.ERROR,
                message=message,
                suggested_fix="Add a vocabulary_id filter in the same scope, e.g.: AND <alias>.vocabulary_id = '<vocab>'",
                details={"column": f"{table}.concept_code" if table else "concept_code"},
            ))

    # --- concept_code = 'value' ---
    for eq in tree.find_all(exp.EQ):
        left, right = eq.left, eq.right
        if isinstance(right, exp.Column) and is_string_literal(left):
            left, right = right, left
        if not (isinstance(left, exp.Column) and is_string_literal(right)):
            continue
        if normalize_name(left.name) != "concept_code":
            continue
        _maybe_add(left, f"concept_code filtered without vocabulary_id: {left.sql()} = {right.sql()}")

    # --- concept_code IN ('value', ...) ---
    for in_expr in tree.find_all(exp.In):
        col = in_expr.this
        if not isinstance(col, exp.Column) or normalize_name(col.name) != "concept_code":
            continue
        string_vals = [v for v in (in_expr.expressions or []) if is_string_literal(v)]
        if not string_vals:
            continue
        vals_str = ", ".join(v.sql() for v in string_vals[:3])
        if len(string_vals) > 3:
            vals_str += ", ..."
        _maybe_add(col, f"concept_code IN clause without vocabulary_id: {col.sql()} IN ({vals_str})")

    # --- concept_code LIKE 'pattern' (and ILIKE / NOT variants) ---
    for node in tree.walk():
        is_not = False
        check_node = node

        if isinstance(node, exp.Not):
            inner = node.this
            if isinstance(inner, STRING_MATCH_EXP_TYPES):
                is_not = True
                check_node = inner
            else:
                continue
        elif not isinstance(node, STRING_MATCH_EXP_TYPES):
            continue

        left = check_node.this
        if not isinstance(left, exp.Column) or normalize_name(left.name) != "concept_code":
            continue

        right = check_node.expression
        not_prefix = "NOT " if is_not else ""
        op_name = check_node.key.upper() if hasattr(check_node, "key") else "LIKE"
        _maybe_add(left, f"concept_code {not_prefix}{op_name} without vocabulary_id: {left.sql()} {not_prefix}{op_name} {right.sql()}")

    return violations


@register
class ConceptCodeRequiresVocabularyIdRule(Rule):
    """Ensures concept_code filters are always accompanied by vocabulary_id."""

    rule_id = "vocabulary.concept_code_requires_vocabulary_id"
    name = "Concept Code Requires Vocabulary ID"
    description = (
        "concept_code is unique only within a vocabulary. "
        "Any filter on concept_code must include a vocabulary_id filter "
        "in the same scope to avoid ambiguous cross-vocabulary matches."
    )
    severity = Severity.ERROR
    suggested_fix = "Add a vocabulary_id filter alongside concept_code"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        all_violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            aliases = extract_aliases(tree)
            all_violations.extend(_check_violations(tree, aliases))

        return all_violations


__all__ = ["ConceptCodeRequiresVocabularyIdRule"]

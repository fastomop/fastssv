"""Concept Ancestor Self-Include Redundancy Validation Rule.

OMOP semantic rule VOCAB_017:
concept_ancestor records every Standard and Classification concept as its own
ancestor (CDM v5.4 conventions: "Each concept is also recorded as an ancestor of
itself"; "Only valid and Standard Concepts participate in the CONCEPT_ANCESTOR
table"), so a descendant expansion of such a concept already returns the anchor.

Only ``UNION ALL`` turns that into a defect: the anchor row is produced by
the explicit branch *and* by the concept_ancestor branch, and ``UNION ALL``
keeps both. A plain ``UNION`` "eliminates duplicate rows from its result, in
the same way as DISTINCT, unless UNION ALL is used" (PostgreSQL, Combining
Queries), ``OR`` predicates select rows rather than emit them, and ``IN (...)``
is set membership — none of those can duplicate the anchor, so the rule stays
silent on them. The explicit anchor may be a concept row, a literal-only SELECT
or a VALUES list.

Violation pattern:
    -- WRONG: UNION ALL keeps the anchor twice
    SELECT concept_id FROM concept WHERE concept_id = 201820
    UNION ALL
    SELECT descendant_concept_id FROM concept_ancestor
    WHERE ancestor_concept_id = 201820

Correct patterns:
    -- UNION deduplicates
    SELECT concept_id FROM concept WHERE concept_id = 201820
    UNION
    SELECT descendant_concept_id FROM concept_ancestor
    WHERE ancestor_concept_id = 201820

    -- concept_ancestor alone already includes the anchor
    SELECT descendant_concept_id FROM concept_ancestor
    WHERE ancestor_concept_id = 201820

    -- Exclude the self row if the explicit branch must stay
    SELECT descendant_concept_id FROM concept_ancestor
    WHERE ancestor_concept_id = 201820 AND descendant_concept_id <> ancestor_concept_id

    (A level predicate such as ``min_levels_of_separation > 0`` also removes the self row, so it is not reported,
    but it removes more: descendants reached only through relationships that do not count as a level step have
    min_levels_of_separation = 0 too.)

    -- Intentional duplication, distinguished by a marker column
    SELECT concept_id, 'anchor' AS source FROM concept WHERE concept_id = 201820
    UNION ALL
    SELECT descendant_concept_id, 'hierarchy' AS source
    FROM concept_ancestor WHERE ancestor_concept_id = 201820
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleViolation, Severity
from fastssv.core.references import BOOK_VOCABULARIES, CDM_CONVENTIONS, section
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register

from ._cs_scope import int_value, peel_cast

CONCEPT_ANCESTOR = "concept_ancestor"
CONCEPT_TABLE = "concept"

ANCESTOR_CONCEPT_ID = "ancestor_concept_id"
DESCENDANT_CONCEPT_ID = "descendant_concept_id"
CONCEPT_ID = "concept_id"
MIN_LEVELS = "min_levels_of_separation"
MAX_LEVELS = "max_levels_of_separation"


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _get_aliases_by_table(aliases: Dict[str, str], table_name: str) -> Set[str]:
    return {alias for alias, table in aliases.items() if _norm(table) == table_name}


def _extract_literal_int(node: exp.Expression) -> Optional[int]:
    return int_value(node)


def _is_column(
    col: exp.Column,
    aliases: Dict[str, str],
    target_table: str,
    target_column: str,
    valid_aliases: Set[str],
) -> bool:
    table, col_name = resolve_table_col(col, aliases)
    if _norm(col_name) != target_column:
        return False
    if table:
        return _norm(table) == target_table
    # Unqualified column: accept only when exactly one candidate table is in scope.
    return len(valid_aliases) == 1


def _required(node: exp.Expression) -> bool:
    """Joined to its WHERE / JOIN ON / HAVING only through AND, so every row the branch emits satisfies it."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, (exp.Where, exp.Join, exp.Having)):
            return True
        if not isinstance(parent, (exp.And, exp.Paren)):
            return False
        parent = parent.parent
    return False


def _ca_column(node: exp.Expression, aliases: Dict[str, str], names: Set[str]) -> Optional[str]:
    if not isinstance(node, exp.Column):
        return None
    table, col_name = resolve_table_col(node, aliases)
    if _norm(table) and _norm(table) != CONCEPT_ANCESTOR:
        return None
    name = _norm(col_name)
    return name if name in names else None


_FLIPPED = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE, exp.EQ: exp.EQ, exp.NEQ: exp.NEQ}
_LEVELS = {MIN_LEVELS, MAX_LEVELS}


def _level_predicate_excludes_zero(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    """A required comparison on min/max_levels_of_separation that the self row (both levels 0) fails."""
    if isinstance(node, exp.Between):
        if not _ca_column(node.this, aliases, _LEVELS):
            return False
        low, high = _extract_literal_int(node.args.get("low")), _extract_literal_int(node.args.get("high"))
        return (low is not None and low > 0) or (high is not None and high < 0)
    if isinstance(node, exp.In):
        if not _ca_column(node.this, aliases, _LEVELS) or node.args.get("query") is not None:
            return False
        values = [_extract_literal_int(v) for v in node.expressions]
        return bool(values) and all(v is not None and v != 0 for v in values)
    op = type(node)
    if op not in _FLIPPED:
        return False
    if _ca_column(node.this, aliases, _LEVELS):
        val = _extract_literal_int(node.expression)
    elif _ca_column(node.expression, aliases, _LEVELS):
        val, op = _extract_literal_int(node.this), _FLIPPED[op]
    else:
        return False
    if val is None:
        return False
    holds_at_zero = {
        exp.GT: 0 > val,
        exp.GTE: 0 >= val,
        exp.LT: 0 < val,
        exp.LTE: 0 <= val,
        exp.EQ: val == 0,
        exp.NEQ: val != 0,
    }[op]
    return not holds_at_zero


def _self_exclusion(branch: exp.Expression, aliases: Dict[str, str]) -> Tuple[bool, Set[int]]:
    """(every self row dropped, anchors whose self row is dropped by ``descendant_concept_id <> <id>``)."""
    excluded_ids: Set[int] = set()
    pair = {ANCESTOR_CONCEPT_ID, DESCENDANT_CONCEPT_ID}
    for node in branch.find_all(exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ, exp.Between, exp.In):
        outer = node.parent
        while isinstance(outer, exp.Paren):
            outer = outer.parent
        negated = isinstance(outer, exp.Not)
        target = outer if negated else node
        if not _required(target):
            continue
        if not negated and _level_predicate_excludes_zero(node, aliases):
            return True, excluded_ids
        is_neq = (isinstance(node, exp.NEQ) and not negated) or (isinstance(node, exp.EQ) and negated)
        if is_neq:
            left = _ca_column(node.this, aliases, pair)
            right = _ca_column(node.expression, aliases, pair)
            if left and right and left != right:
                return True, excluded_ids
            for col_side, val_side in ((node.this, node.expression), (node.expression, node.this)):
                if _ca_column(col_side, aliases, {DESCENDANT_CONCEPT_ID}):
                    val = _extract_literal_int(val_side)
                    if val is not None:
                        excluded_ids.add(val)
        if isinstance(node, exp.In) and negated and _ca_column(node.this, aliases, {DESCENDANT_CONCEPT_ID}):
            excluded_ids.update(v for v in (_extract_literal_int(e) for e in node.expressions) if v is not None)
    return False, excluded_ids


def _extract_ids_from_branch(
    node: exp.Expression,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
    ancestor_aliases: Set[str],
) -> Tuple[Set[int], Set[int]]:
    """Literal ids compared against concept.concept_id and against concept_ancestor's id columns."""
    concept_ids: Set[int] = set()
    ancestor_ids: Set[int] = set()

    def classify(col: exp.Column, val: int) -> None:
        if _is_column(col, aliases, CONCEPT_TABLE, CONCEPT_ID, concept_aliases):
            concept_ids.add(val)
        elif _is_column(col, aliases, CONCEPT_ANCESTOR, ANCESTOR_CONCEPT_ID, ancestor_aliases) or _is_column(
            col, aliases, CONCEPT_ANCESTOR, DESCENDANT_CONCEPT_ID, ancestor_aliases
        ):
            ancestor_ids.add(val)

    for eq in node.find_all(exp.EQ):
        for col_node, val_node in ((eq.this, eq.expression), (eq.expression, eq.this)):
            if not isinstance(col_node, exp.Column):
                continue
            val = _extract_literal_int(val_node)
            if val is not None:
                classify(col_node, val)

    for in_node in node.find_all(exp.In):
        if not isinstance(in_node.this, exp.Column):
            continue
        for expr in in_node.args.get("expressions") or []:
            val = _extract_literal_int(expr)
            if val is not None:
                classify(in_node.this, val)

    return concept_ids, ancestor_ids


def _top_selects(branch: exp.Expression) -> List[exp.Select]:
    """The SELECTs whose projection *is* the branch's output (through nested UNIONs and parentheses)."""
    if isinstance(branch, exp.Select):
        return [branch]
    if isinstance(branch, (exp.Subquery, exp.Paren)):
        return _top_selects(branch.this) if branch.this is not None else []
    if isinstance(branch, exp.SetOperation):
        return _top_selects(branch.this) + _top_selects(branch.expression)
    return []


def _output_constants(branch: exp.Expression) -> List[Dict[int, str]]:
    """Per top-level SELECT of the branch: output position -> constant written there (``'anchor' AS source``)."""
    out: List[Dict[int, str]] = []
    for sel in _top_selects(branch):
        consts: Dict[int, str] = {}
        for i, e in enumerate(sel.expressions or []):
            e = peel_cast(e.this if isinstance(e, exp.Alias) else e)
            if isinstance(e, exp.Literal):
                consts[i] = str(e.this)
        out.append(consts)
    return out


def _rows_distinguishable(left: exp.Expression, right: exp.Expression) -> bool:
    """Every pair of output tuples from the two branches differs in some column that both write as a different
    constant, so the anchor rows are not duplicates."""
    lefts, rights = _output_constants(left), _output_constants(right)
    if not lefts or not rights:
        return False
    return all(any(i in r and r[i] != v for i, v in lc.items()) for lc in lefts for r in rights)


def _literal_anchor_ids(branch: exp.Expression) -> Set[int]:
    """Ids a branch emits without reading any table: ``SELECT 201820`` or ``VALUES (201820)``."""
    ids: Set[int] = set()
    for sel in _top_selects(branch):
        if sel.args.get("from_") or sel.args.get("from"):
            source = (sel.args.get("from_") or sel.args.get("from")).this
            values = source.this if isinstance(source, exp.Subquery) else source
            if isinstance(values, exp.Values):
                for row in values.expressions:
                    first = row.expressions[0] if isinstance(row, exp.Tuple) and row.expressions else row
                    if int_value(first) is not None:
                        ids.add(int_value(first))
            continue
        if sel.expressions:
            first = sel.expressions[0]
            value = int_value(first.this if isinstance(first, exp.Alias) else first)
            if value is not None:
                ids.add(value)
    if isinstance(branch, exp.Values):
        for row in branch.expressions:
            first = row.expressions[0] if isinstance(row, exp.Tuple) and row.expressions else row
            if int_value(first) is not None:
                ids.add(int_value(first))
    return ids


def _select_deduplicates(sel: exp.Select) -> bool:
    """The SELECT's own output does not depend on a duplicated input row: DISTINCT, or aggregates that ignore
    duplicates (COUNT(DISTINCT ...), MIN, MAX), with or without GROUP BY."""
    if sel.args.get("distinct"):
        return True
    aggs = [a for e in sel.expressions for a in e.find_all(exp.AggFunc)]
    insensitive = all(isinstance(a.this, exp.Distinct) or isinstance(a, (exp.Min, exp.Max)) for a in aggs)
    if sel.args.get("group") is not None:
        # grouping collapses duplicate rows only if no aggregate counts or sums them
        return insensitive
    return bool(aggs) and insensitive


def _absorbed_downstream(union: exp.Union) -> bool:
    """True when every consumer of the UNION ALL's rows deduplicates them or reads them as a set (IN / EXISTS),
    followed through plain SELECTs and the CTEs that pass the rows on."""
    root = union
    while root.parent is not None:
        root = root.parent

    def cte_absorbs(cte: exp.CTE, depth: int) -> bool:
        name = normalize_name(cte.alias)
        uses = [
            t
            for t in root.find_all(exp.Table)
            if normalize_name(t.name) == name and t.find_ancestor(exp.CTE) is not cte
        ]
        return bool(uses) and all(consumer_absorbs(t, depth + 1) for t in uses)

    def consumer_absorbs(node: exp.Expression, depth: int = 0) -> bool:
        if depth > 12:
            return False
        parent = node.parent
        while isinstance(parent, (exp.Subquery, exp.Paren)):
            node, parent = parent, parent.parent
        if isinstance(parent, (exp.In, exp.Exists)):
            return True
        if isinstance(parent, exp.CTE):
            return cte_absorbs(parent, depth)
        if isinstance(parent, (exp.From, exp.Join)):
            sel = parent.parent
            if not isinstance(sel, exp.Select):
                return False
            # A plain SELECT passes the duplicate on; what matters is what *its* rows feed into.
            return _select_deduplicates(sel) or consumer_absorbs(sel, depth + 1)
        return False

    return consumer_absorbs(union)


def _detect_union_all(tree: exp.Expression) -> List[Dict]:
    violations = []
    for union in tree.find_all(exp.Union):
        if union.args.get("distinct") is not False:
            continue  # plain UNION deduplicates the anchor row
        left, right = union.this, union.expression
        if not left or not right:
            continue

        left_aliases = extract_aliases(left)
        right_aliases = extract_aliases(right)
        left_drops_all, left_dropped = _self_exclusion(left, left_aliases)
        right_drops_all, right_dropped = _self_exclusion(right, right_aliases)
        if left_drops_all or right_drops_all:
            continue
        if _rows_distinguishable(left, right) or _absorbed_downstream(union):
            continue

        l_concept = _get_aliases_by_table(left_aliases, CONCEPT_TABLE)
        l_ancestor = _get_aliases_by_table(left_aliases, CONCEPT_ANCESTOR)
        r_concept = _get_aliases_by_table(right_aliases, CONCEPT_TABLE)
        r_ancestor = _get_aliases_by_table(right_aliases, CONCEPT_ANCESTOR)
        l_literal, r_literal = _literal_anchor_ids(left), _literal_anchor_ids(right)
        if not ((l_concept or l_literal) and r_ancestor) and not ((r_concept or r_literal) and l_ancestor):
            continue

        l_c, l_a = _extract_ids_from_branch(left, left_aliases, l_concept, l_ancestor)
        r_c, r_a = _extract_ids_from_branch(right, right_aliases, r_concept, r_ancestor)
        l_c, r_c = l_c | l_literal, r_c | r_literal
        # Only an id emitted explicitly by one branch and via the self row by the *other* branch is
        # duplicated by this UNION ALL; an overlap inside a single branch is that branch's own business.
        for cid in sorted((l_c & (r_a - right_dropped)) | (r_c & (l_a - left_dropped))):
            violations.append({"type": "union_all", "concept_id": cid, "context": union.sql()})
    return violations


@register
class ConceptAncestorSelfIncludeRedundancyRule(Rule):
    """Detect an anchor concept emitted twice by UNION ALL with its own concept_ancestor expansion."""

    rule_id = "concept_standardization.concept_ancestor_self_include_redundancy"
    name = "Concept Ancestor Self-Include Redundancy"

    description = (
        "concept_ancestor already returns the anchor concept as its own descendant "
        "(min_levels_of_separation = 0). UNION ALL of an explicit anchor branch with "
        "that expansion emits the anchor row twice."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "REPLACE: `UNION ALL` WITH `UNION`, OR REMOVE the explicit anchor branch, OR ADD "
        "`AND descendant_concept_id <> ancestor_concept_id` to the concept_ancestor branch."
    )
    long_description = (
        "concept_ancestor records every Standard and Classification concept as its own "
        "descendant (both levels of separation 0), so a descendant expansion of such a "
        "concept already contains the anchor. Adding the anchor again through UNION ALL keeps both copies; "
        "a codeset built that way multiplies rows when it is joined to clinical "
        "events. A plain UNION, an OR predicate, or an IN list cannot duplicate "
        "the anchor and are not flagged. Nor is a UNION ALL whose branches "
        "emit tuples that differ in a constant column, or whose every "
        "consumer deduplicates (DISTINCT, GROUP BY on the concept, COUNT(DISTINCT ...)) "
        "or reads the rows as a set (IN, EXISTS): no duplicate reaches the result."
    )
    example_bad = (
        "SELECT descendant_concept_id AS concept_id\n"
        "FROM concept_ancestor\n"
        "WHERE ancestor_concept_id = 201820\n"
        "UNION ALL\n"
        "SELECT concept_id\n"
        "FROM concept\n"
        "WHERE concept_id = 201820;"
    )
    example_good = (
        "SELECT descendant_concept_id AS concept_id\nFROM concept_ancestor\nWHERE ancestor_concept_id = 201820;"
    )
    references = (
        section(CDM_CONVENTIONS, "concept-ancestor", "Concept ancestor"),
        section(BOOK_VOCABULARIES, "conceptAncestor", "Concept ancestor"),
        Reference(
            "PostgreSQL documentation: Combining Queries (UNION, INTERSECT, EXCEPT)",
            "https://www.postgresql.org/docs/current/queries-union.html",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "concept_ancestor" not in sql.lower() or "union" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        seen: Set[str] = set()
        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree or not has_table_reference(tree, CONCEPT_ANCESTOR):
                continue
            for v in _detect_union_all(tree):
                key = f"{v['concept_id']}_{v['context']}"
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    self.create_violation(
                        message=(
                            f"Concept {v['concept_id']} is selected explicitly and again as its own "
                            "descendant by concept_ancestor (the self row, levels of separation 0); "
                            "UNION ALL keeps both rows, so the anchor appears twice if it is a Standard or "
                            "Classification concept (only those have concept_ancestor rows)."
                        ),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details=v,
                    )
                )

        return violations


__all__ = ["ConceptAncestorSelfIncludeRedundancyRule"]

"""Note NLP Offset is Character Position Rule.

OMOP semantic rule GAP_012.

CDM v5.4 stores ``note_nlp.offset`` ("Character offset of the extracted term in
the input note") as ``varchar(50)``. Its content is a number, but every ordering
operation on the raw column is a string ordering: ``'9' > '100'`` is true,
``ORDER BY offset`` puts 100 before 9, and ``MAX(offset)`` returns '99' over
'100'. These run without error and silently return wrong rows.

Flagged on an uncast ``note_nlp.offset``:
    * ``<``, ``<=``, ``>``, ``>=`` against a string literal or against another
      uncast offset (e.g. "term A appears before term B in the same note");
    * ``BETWEEN`` with string-literal bounds;
    * ``ORDER BY offset`` (including window ``ORDER BY``);
    * ``MIN(offset)`` / ``MAX(offset)``, ``GREATEST`` / ``LEAST`` over offsets.

The column is recognised by lineage (``column_origins``): through CTEs, derived tables and renames, qualified or
not; a CTE that casts it (``CAST("offset" AS INTEGER) AS "offset"``) yields an integer and is not flagged.

Not flagged:
    * equality and IN (string equality of positions is exact);
    * comparison with a numeric literal or arithmetic: PostgreSQL has no
      ``varchar > integer`` operator and casts from string types are
      explicit-only, so the query fails loudly rather than returning wrong rows;
    * any use inside CAST / TRY_CAST / CONVERT or ``::int``.

Violation:
    SELECT a.note_id FROM note_nlp a
    JOIN note_nlp b ON a.note_id = b.note_id
    WHERE a."offset" < b."offset"

Correct:
    ... WHERE CAST(a."offset" AS INTEGER) < CAST(b."offset" AS INTEGER)
"""

from typing import List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import BOOK_SQL, cdm_table, section
from fastssv.core.helpers import (
    is_string_literal,
    normalize_name,
    parse_sql,
    _explicit_projection,
    _pick_source,
    _query_branches,
    _select_sources,
    _source_by_qualifier,
    _source_query,
    column_origins,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


NOTE_NLP = "note_nlp"
OFFSET_COL = "offset"
_SYMBOL = {exp.LT: "<", exp.LTE: "<=", exp.GT: ">", exp.GTE: ">="}


def _is_casted(node: exp.Expression) -> bool:
    parent = node.parent
    while isinstance(parent, (exp.Paren, exp.Cast, exp.TryCast, exp.Anonymous)):
        if isinstance(parent, (exp.Cast, exp.TryCast)):
            return True
        if isinstance(parent, exp.Anonymous) and normalize_name(str(parent.this)) == "convert":
            return True
        parent = parent.parent
    return False


def _recast(col: exp.Column, depth: int = 0) -> bool:
    """A CTE / derived-table projection on ``col``'s lineage casts the value (``column_origins`` looks through casts)."""
    if depth > 12:
        return False
    select = col.find_ancestor(exp.Select)
    if select is None:
        return False
    name = normalize_name(col.name)
    if col.table:
        scope, src = _source_by_qualifier(select, normalize_name(col.table))
    else:
        scope, src = select, _pick_source(select, _select_sources(select), name, depth)
    if scope is None or src is None:
        return False
    query = _source_query(scope, src)
    if query is None:
        return False
    for branch in _query_branches(query):
        proj = _explicit_projection(branch, name)
        if proj is None:
            continue
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        while isinstance(expr, exp.Paren):
            expr = expr.this
        if isinstance(expr, (exp.Cast, exp.TryCast)) or (
            isinstance(expr, exp.Anonymous) and normalize_name(str(expr.this)) == "convert"
        ):
            return True
        if isinstance(expr, exp.Column) and _recast(expr, depth + 1):
            return True
    return False


def _is_offset(node: exp.Expression) -> bool:
    """True for an uncast value whose lineage is note_nlp.offset, through CTEs, derived tables and renames."""
    while isinstance(node, exp.Paren):
        node = node.this
    if not isinstance(node, exp.Column) or _is_casted(node):
        return False
    origins = column_origins(node)
    return origins == {(NOTE_NLP, OFFSET_COL)} and not _recast(node)


def _column(node: exp.Expression) -> exp.Column:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _order_target(ordered: exp.Ordered) -> exp.Expression:
    """What an ORDER BY item sorts on. PostgreSQL resolves a bare name in a query-level ORDER BY to an
    output column first, so ``CAST(nn."offset" AS INTEGER) AS "offset" ... ORDER BY "offset"`` sorts integers."""
    key = _column(ordered.this)
    order = ordered.parent
    select = order.parent if isinstance(order, exp.Order) else None
    if not isinstance(key, exp.Column) or key.table or not isinstance(select, exp.Select):
        return key
    for proj in select.expressions:
        if isinstance(proj, exp.Alias) and normalize_name(proj.alias) == normalize_name(key.name):
            return proj.this
    return key


def _findings(tree: exp.Expression) -> List[Tuple[str, str, exp.Column]]:
    """(kind, detail, column) for every string-ordering use of an uncast offset."""
    out: List[Tuple[str, str, exp.Column]] = []

    for cmp in tree.find_all(exp.LT, exp.LTE, exp.GT, exp.GTE):
        left, right = cmp.this, cmp.expression
        if _is_offset(left) and _is_offset(right):
            out.append(("offset_pair", _SYMBOL[type(cmp)], _column(left)))
        elif _is_offset(left) and is_string_literal(right):
            out.append(("comparison", _SYMBOL[type(cmp)], _column(left)))
        elif _is_offset(right) and is_string_literal(left):
            out.append(("comparison", _SYMBOL[type(cmp)], _column(right)))

    for between in tree.find_all(exp.Between):
        bounds = (between.args.get("low"), between.args.get("high"))
        if _is_offset(between.this) and any(b is not None and is_string_literal(b) for b in bounds):
            out.append(("between", "BETWEEN", _column(between.this)))

    for ordered in tree.find_all(exp.Ordered):
        target = _order_target(ordered)
        if _is_offset(target):
            out.append(("order", "ORDER BY", _column(target)))

    for agg in tree.find_all(exp.Min, exp.Max):
        if _is_offset(agg.this):
            out.append(("minmax", type(agg).__name__.upper(), _column(agg.this)))

    for fn in tree.find_all(exp.Greatest, exp.Least):
        args = [fn.this, *(fn.expressions or [])]
        offsets = [a for a in args if _is_offset(a)]
        if offsets and all(_is_offset(a) or is_string_literal(a) for a in args):
            out.append(("minmax", type(fn).__name__.upper(), _column(offsets[0])))

    return out


_MESSAGES = {
    "comparison": "note_nlp.offset used in a `{detail}` comparison as a string: offset is varchar, so '9' > '100'.",
    "offset_pair": "Two note_nlp.offset values compared with `{detail}` as strings: offset is varchar, so '9' > '100'.",
    "between": "note_nlp.offset used in BETWEEN with string bounds: offset is varchar, so the range is lexicographic.",
    "order": "ORDER BY note_nlp.offset sorts character positions as strings (100 before 9).",
    "minmax": "{detail}(note_nlp.offset) picks the lexicographic extreme of a varchar column ('99' over '100').",
}


@register
class NoteNlpOffsetIsCharacterPositionRule(Rule):
    """Flag string-ordered uses of note_nlp.offset, a varchar character position."""

    rule_id = "data_quality.note_nlp_offset_is_character_position"
    name = "Note NLP Offset is Character Position"

    description = (
        "note_nlp.offset is a varchar character position. Range comparisons with string literals or another "
        "offset, BETWEEN with string bounds, ORDER BY, and MIN/MAX on the uncast column order it as text."
    )

    severity = Severity.WARNING

    suggested_fix = "WRAP: note_nlp.offset in `CAST(offset AS INTEGER)` wherever it is ordered, ranged or aggregated."
    long_description = (
        "note_nlp.offset holds the character position of the extracted term but is declared varchar(50) in "
        "CDM v5.4. Ordering it without a cast compares strings, so position 100 sorts before 9 and a range "
        "filter such as offset > '100' keeps position 9. Cast it to an integer first."
    )
    example_bad = "SELECT note_nlp_id\nFROM note_nlp\nWHERE \"offset\" > '100';"
    example_good = 'SELECT note_nlp_id\nFROM note_nlp\nWHERE CAST("offset" AS INTEGER) > 100;'
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT a.note_id\n"
                "FROM note_nlp a\n"
                "JOIN note_nlp b ON a.note_id = b.note_id\n"
                'WHERE a."offset" < b."offset";'
            ),
            severity=Severity.WARNING,
            note="Two offsets compared as text ('term A before term B'); cast both sides.",
        ),
        RuleExample(
            sql='SELECT note_id, lexical_variant\nFROM note_nlp\nORDER BY "offset";',
            severity=Severity.WARNING,
            note="ORDER BY on the uncast offset sorts position 100 before 9.",
        ),
    )
    references = (cdm_table("note_nlp"), section(BOOK_SQL, "implicit-casts", "Implicit casts"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if NOTE_NLP not in lowered or OFFSET_COL not in lowered:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, str, str]] = set()
        for tree in trees:
            if not tree:
                continue
            for kind, detail, col in _findings(tree):
                col_sql = col.sql(dialect=dialect)
                key = (kind, detail, col_sql)
                if key in seen:
                    continue
                seen.add(key)
                # Casting one side of an offset-vs-offset comparison would leave integer vs varchar, which
                # PostgreSQL rejects; that fix needs both sides and is left to the author.
                span = locate(sql, col_sql) if kind != "offset_pair" else None
                patch = patch_replace(span, f"CAST({col_sql} AS INTEGER)") if span is not None else None
                violations.append(
                    self.create_violation(
                        message=_MESSAGES[kind].format(detail=detail),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        suggested_fix_patch=patch,
                        details={"type": kind, "operator": detail, "column": col_sql},
                    )
                )
        return violations


__all__ = ["NoteNlpOffsetIsCharacterPositionRule"]

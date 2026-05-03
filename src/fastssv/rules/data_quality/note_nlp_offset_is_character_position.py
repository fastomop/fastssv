"""Note NLP Offset is Character Position Rule.

OMOP semantic rule GAP_012:
note_nlp.offset is a VARCHAR field storing the character position of the NLP
extraction within the note text. It is not an integer ID and should not be used
in JOINs or numeric comparisons without explicit CAST.

The Problem:
    The note_nlp.offset column stores character positions as VARCHAR, not INTEGER.
    Developers often mistakenly treat it as a numeric column because:
    1. Positions are conceptually numeric
    2. Most OMOP CDM position/ID fields are integers
    3. The name "offset" suggests a numeric value

    This leads to:
    - String vs numeric comparison semantics (e.g., '9' > '100' is true)
    - Using it in JOINs (semantically incorrect - it's a position, not a key)
    - Arithmetic operations without proper casting

Common mistakes:
    1. Direct numeric comparisons: WHERE offset > 100
    2. Using in JOIN conditions: JOIN ... ON nn.offset = ...
    3. BETWEEN without CAST: WHERE offset BETWEEN 50 AND 200
    4. Arithmetic: WHERE offset + 10 < 500

Violation pattern:
    SELECT *
    FROM note_nlp
    WHERE offset > 100
    -- WRONG: VARCHAR compared as string, not number!

Correct pattern:
    SELECT *
    FROM note_nlp
    WHERE CAST(offset AS INT) > 100
    -- or
    WHERE CONVERT(INT, offset) > 100
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

NOTE_NLP = "note_nlp"
OFFSET_COL = "offset"


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    return _norm(name.split(".")[-1]) if name else None


def _is_note_nlp(table: Optional[str]) -> bool:
    return _normalize_table(table) == NOTE_NLP


def _is_offset_col(col: Optional[str]) -> bool:
    return _norm(col) == OFFSET_COL


def _has_note_nlp(tree: exp.Expression) -> bool:
    return any(_normalize_table(t.name) == NOTE_NLP for t in tree.find_all(exp.Table))


def _is_casted(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, (exp.Cast, exp.TryCast)):
            return True
        if isinstance(parent, exp.Anonymous):
            func_name = str(parent.this).lower()
            if func_name == "convert":
                return True
        parent = parent.parent
    return False


def _is_in_join(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Join):
            return True
        if isinstance(parent, (exp.Where, exp.Select)):
            return False
        parent = parent.parent
    return False


def _is_offset_reference(
    col: exp.Column,
    aliases: Dict[str, str],
    has_note_nlp: bool,
) -> Tuple[bool, Optional[str], Optional[str]]:
    table, column = resolve_table_col(col, aliases)

    is_offset = _is_offset_col(column)

    if table and _is_note_nlp(table):
        return is_offset, table, column

    if not table and has_note_nlp and is_offset:
        return True, NOTE_NLP, column

    return False, None, None


# --- Detection -------------------------------------------------------------


def _detect_join_usage(tree, aliases, has_note_nlp):
    results = []

    for eq in tree.find_all(exp.EQ):
        if not _is_in_join(eq):
            continue

        for node in (eq.this, eq.expression):
            if not isinstance(node, exp.Column):
                continue
            if _is_casted(node):
                continue

            is_offset, table, col = _is_offset_reference(node, aliases, has_note_nlp)

            if is_offset:
                results.append(("join", table, col, None, node.sql()))

    return results


def _detect_numeric_comparisons(tree, aliases, has_note_nlp):
    results = []

    comparison_types = (exp.GT, exp.GTE, exp.LT, exp.LTE)

    for node in tree.find_all(*comparison_types):
        if _is_in_join(node):
            continue

        left, right = node.this, node.expression

        for col_node in (left, right):
            if not isinstance(col_node, exp.Column):
                continue
            if _is_casted(col_node):
                continue

            is_offset, table, col = _is_offset_reference(col_node, aliases, has_note_nlp)

            if is_offset:
                op = type(node).__name__
                results.append(("numeric", table, col, op, col_node.sql()))

    return results


def _detect_between(tree, aliases, has_note_nlp):
    results = []

    for node in tree.find_all(exp.Between):
        col_node = node.this

        if not isinstance(col_node, exp.Column):
            continue
        if _is_casted(col_node):
            continue

        is_offset, table, col = _is_offset_reference(col_node, aliases, has_note_nlp)

        if is_offset:
            results.append(("between", table, col, None, col_node.sql()))

    return results


def _detect_arithmetic(tree, aliases, has_note_nlp):
    results = []

    for node in tree.find_all(exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod):
        for col in node.find_all(exp.Column):
            if _is_casted(col):
                continue

            is_offset, table, column = _is_offset_reference(col, aliases, has_note_nlp)

            if is_offset:
                op = type(node).__name__
                results.append(("arithmetic", table, column, op, col.sql()))

    return results


# --- Rule ------------------------------------------------------------------


@register
class NoteNlpOffsetIsCharacterPositionRule(Rule):
    """Ensure note_nlp.offset is not misused as numeric."""

    rule_id = "data_quality.note_nlp_offset_is_character_position"
    name = "Note NLP Offset is Character Position"

    description = (
        "note_nlp.offset is VARCHAR storing character positions and must not "
        "be used in joins or numeric operations without explicit casting."
    )

    severity = Severity.WARNING

    suggested_fix = "WRAP: note_nlp.offset in CAST when used numerically — `CAST(offset AS INTEGER)`. Or treat it as VARCHAR (string compare, LIKE). Do not JOIN on it."
    long_description = (
        "note_nlp.offset is a VARCHAR column that stores the character "
        "position of the NLP-extracted term within the note text. Despite "
        "the numeric content, the column type is string — using it in a "
        "numeric comparison or JOIN without an explicit CAST leads to "
        "implicit-cast errors on strict dialects and silent mis-sorts on "
        "lenient ones. CAST to INT when you need numeric semantics."
    )
    example_bad = "SELECT *\nFROM note_nlp\nWHERE offset > 100;"
    example_good = "SELECT *\nFROM note_nlp\nWHERE CAST(offset AS INT) > 100;"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "note_nlp" not in sql.lower() or "offset" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            has_note_nlp = _has_note_nlp(tree)

            findings = []
            findings += _detect_join_usage(tree, aliases, has_note_nlp)
            findings += _detect_numeric_comparisons(tree, aliases, has_note_nlp)
            findings += _detect_between(tree, aliases, has_note_nlp)
            findings += _detect_arithmetic(tree, aliases, has_note_nlp)

            for kind, table, col, op, col_sql in findings:
                key = f"{kind}|{table}|{col}|{op}|{col_sql}"
                if key in seen:
                    continue
                seen.add(key)

                if kind == "join":
                    message = f"{table}.{col} used in JOIN condition."
                    fix = "REMOVE: `note_nlp.offset` from the JOIN ON clause. offset is a VARCHAR character position, not a relational key — JOIN on `note_nlp.note_id = note.note_id` instead."

                elif kind == "numeric":
                    message = f"{table}.{col} used in numeric comparison ({op}) without CAST."
                    fix = self.suggested_fix

                elif kind == "between":
                    message = f"{table}.{col} used in BETWEEN without CAST."
                    fix = self.suggested_fix

                else:
                    message = f"{table}.{col} used in arithmetic operation ({op}) without CAST."
                    fix = self.suggested_fix

                # Structured patch: wrap the offset reference in
                # CAST(<col> AS INTEGER). This is the canonical fix for
                # numeric comparison / BETWEEN / arithmetic uses. JOIN
                # uses are *not* mechanical (the right fix is to remove
                # the predicate, not cast it), so leave those FREEFORM.
                patch = None
                if kind in {"numeric", "between", "arithmetic"} and col_sql:
                    span = locate(sql, col_sql)
                    if span is not None:
                        patch = patch_replace(span, f"CAST({col_sql} AS INTEGER)")

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        suggested_fix=fix,
                        suggested_fix_patch=patch,
                        details={
                            "type": kind,
                            "table": table,
                            "column": col,
                            "operator": op,
                        },
                    )
                )

        return violations


__all__ = ["NoteNlpOffsetIsCharacterPositionRule"]

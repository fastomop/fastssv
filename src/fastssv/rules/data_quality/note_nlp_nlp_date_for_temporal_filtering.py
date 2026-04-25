"""Note NLP nlp_date for Temporal Filtering Rule.

OMOP semantic rule GAP_015:
note_nlp.nlp_date is the date the NLP processing was performed, NOT the clinical
date of the extracted concept. To get the clinical date, join to note.note_date
via note_id. Filtering nlp_date as if it were the event date is incorrect.

The Problem:
    The note_nlp.nlp_date column stores when the NLP processing was performed,
    NOT when the clinical event occurred. This is a critical semantic distinction:

    - nlp_date: Processing timestamp (e.g., when cTAKES ran on the note)
    - note.note_date: Actual clinical date of the note/event

    Using nlp_date for temporal filtering produces incorrect results because:
    1. NLP processing often happens in batches, long after the clinical event
    2. Re-running NLP changes nlp_date but not the clinical date
    3. The same note processed twice would have different nlp_date values
    4. Cohorts defined by nlp_date are non-reproducible across NLP runs

Common mistakes:
    1. WHERE nlp_date BETWEEN '2023-01-01' AND '2023-12-31'
    2. WHERE nlp_date > '2023-01-01'
    3. Using nlp_date for cohort entry date ranges
    4. Temporal filtering without joining to note.note_date

Violation pattern:
    SELECT *
    FROM note_nlp
    WHERE note_nlp_concept_id = 201826
      AND nlp_date BETWEEN '2023-01-01' AND '2023-12-31'
    -- WRONG: Finds concepts extracted during 2023, not from notes written in 2023!

Correct pattern:
    SELECT nn.*
    FROM note_nlp nn
    JOIN note n ON nn.note_id = n.note_id
    WHERE nn.note_nlp_concept_id = 201826
      AND n.note_date BETWEEN '2023-01-01' AND '2023-12-31'
    -- Correct: Finds concepts from notes written in 2023
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
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

NOTE_NLP = "note_nlp"
NOTE = "note"
NLP_DATE = "nlp_date"
NOTE_DATE = "note_date"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    return _norm(name.split(".")[-1]) if name else None


def _is_note_nlp(table: Optional[str]) -> bool:
    return _normalize_table(table) == NOTE_NLP


def _is_note(table: Optional[str]) -> bool:
    return _normalize_table(table) == NOTE


def _is_nlp_date(col: Optional[str]) -> bool:
    return _norm(col) == NLP_DATE


def _is_note_date(col: Optional[str]) -> bool:
    return _norm(col) == NOTE_DATE


def _has_note_nlp(tree: exp.Expression) -> bool:
    return any(_normalize_table(t.name) == NOTE_NLP for t in tree.find_all(exp.Table))


def _is_nlp_date_ref(
    col: exp.Column,
    aliases: Dict[str, str],
    has_note_nlp: bool,
) -> Tuple[bool, Optional[str], Optional[str]]:
    table, column = resolve_table_col(col, aliases)

    if table and _is_note_nlp(table) and _is_nlp_date(column):
        return True, table, column

    # Only allow unqualified if clearly safe
    if not table and has_note_nlp and _is_nlp_date(column):
        return True, NOTE_NLP, column

    return False, None, None


def _contains_note_date(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if expression contains note.note_date."""
    for col in node.find_all(exp.Column):
        table, column = resolve_table_col(col, aliases)

        if table and _is_note(table) and _is_note_date(column):
            return True

    return False


def _has_valid_note_date_filter(tree: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Detect proper filtering using note.note_date (WHERE or JOIN)."""

    comparison_types = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.EQ)

    # WHERE
    for where in tree.find_all(exp.Where):
        for node in where.find_all(*comparison_types):
            if _contains_note_date(node, aliases):
                return True

    # JOIN ON
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if not on:
            continue

        for node in on.find_all(*comparison_types):
            if _contains_note_date(node, aliases):
                return True

    return False


def _is_in_where(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Where):
            return True
        if isinstance(parent, (exp.Select, exp.Having, exp.Join)):
            return False
        parent = parent.parent
    return False


# --- Detection -------------------------------------------------------------

def _detect_nlp_date_usage(tree, aliases, has_note_nlp):
    results = []

    comparison_types = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)

    for node in tree.find_all(*comparison_types):
        if not _is_in_where(node):
            continue

        for col in node.find_all(exp.Column):
            is_nlp, table, col_name = _is_nlp_date_ref(col, aliases, has_note_nlp)

            if is_nlp:
                op = type(node).__name__
                results.append(("comparison", table, col_name, op))

    return results


def _detect_between(tree, aliases, has_note_nlp):
    results = []

    for node in tree.find_all(exp.Between):
        if not _is_in_where(node):
            continue

        for col in node.this.find_all(exp.Column):
            is_nlp, table, col_name = _is_nlp_date_ref(col, aliases, has_note_nlp)

            if is_nlp:
                results.append(("between", table, col_name, None))

    return results


# --- Rule ------------------------------------------------------------------

@register
class NoteNlpNlpDateForTemporalFilteringRule(Rule):
    """Prevent misuse of nlp_date for clinical filtering."""

    rule_id = "data_quality.note_nlp_nlp_date_for_temporal_filtering"
    name = "Note NLP nlp_date for Temporal Filtering"

    description = (
        "note_nlp.nlp_date is the NLP processing time, not the clinical event date. "
        "Use note.note_date for temporal filtering."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `WHERE note_nlp.nlp_date <op> <date>` WITH `WHERE note.note_date <op> <date>` (after JOIN note_nlp ON note_id = note.note_id). nlp_date is processing time, not clinical event time."
    long_description = (
        "note_nlp.nlp_date records when the NLP pipeline *processed* the "
        "source note, which can be months or years after the clinical "
        "event itself. For temporal cohort logic you need the clinical "
        "date, not the processing date — that lives on note.note_date. "
        "Join note_nlp back to note and filter on note_date instead."
    )
    example_bad = (
        "SELECT note_nlp_id\n"
        "FROM note_nlp\n"
        "WHERE nlp_date >= DATE '2023-01-01';"
    )
    example_good = (
        "SELECT nnlp.note_nlp_id\n"
        "FROM note_nlp nnlp\n"
        "JOIN note n ON nnlp.note_id = n.note_id\n"
        "WHERE n.note_date >= DATE '2023-01-01';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "note_nlp" not in sql.lower() or "nlp_date" not in sql.lower():
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

            # Skip if correct pattern exists
            if _has_valid_note_date_filter(tree, aliases):
                continue

            findings = []
            findings += _detect_nlp_date_usage(tree, aliases, has_note_nlp)
            findings += _detect_between(tree, aliases, has_note_nlp)

            for kind, table, col, op in findings:
                key = f"{kind}|{table}|{col}|{op}"
                if key in seen:
                    continue
                seen.add(key)

                if kind == "comparison":
                    message = (
                        f"{table}.{col} used in temporal comparison ({op}). "
                        "nlp_date is processing time, not clinical event time."
                    )
                else:
                    message = (
                        f"{table}.{col} used in BETWEEN for temporal filtering. "
                        "nlp_date is processing time, not clinical event time."
                    )

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": kind,
                            "table": table,
                            "column": col,
                            "operator": op,
                        },
                    )
                )

        return violations


__all__ = ["NoteNlpNlpDateForTemporalFilteringRule"]

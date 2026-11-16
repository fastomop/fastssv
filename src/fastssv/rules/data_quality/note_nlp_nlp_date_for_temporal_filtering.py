"""Note NLP nlp_date for Temporal Filtering Rule.

CDM v5.4 defines ``note_nlp.nlp_date`` / ``nlp_datetime`` as "the date (and time) of the note processing": when
the NLP pipeline ran, not when the note was written. The clinical date lives on ``note.note_date`` /
``note.note_datetime`` ("the date the note was recorded"), reached through ``note_nlp.note_id = note.note_id``.
Filtering NLP findings to a time window with nlp_date selects them by processing batch, so re-running the
pipeline changes the result.

Fires on a time window over nlp_date / nlp_datetime in WHERE, JOIN ... ON or HAVING: a range comparison
(``<``, ``<=``, ``>``, ``>=``, BETWEEN) on it, equality on it, or equality on a truncated value
(``EXTRACT(YEAR FROM nlp_date) = 2023``), through casts, arithmetic, CTEs and derived tables. Not flagged:
    * a SELECT that also filters ``note.note_date`` / ``note_datetime`` (the clinical window is applied; an extra
      nlp_date filter picks a processing run on purpose);
    * equality on the date in a SELECT that also restricts nlp_system (``nlp_system = 'x' AND nlp_date = DATE
      '2024-03-01'`` picks one processing run), and aggregates over nlp_date (``HAVING MAX(nlp_date) > ...``);
    * a comparison whose other side reads nlp_date / note_date or is a subquery: the latest run
      (``nlp_date = (SELECT MAX(nlp_date) ...)``), a processing-lag audit (``nlp_date >= n.note_date``) or an
      incremental-load watermark.

Violation:
    SELECT note_nlp_id FROM note_nlp
    WHERE note_nlp_concept_id = 201826 AND nlp_date BETWEEN '2023-01-01' AND '2023-12-31'

Correct:
    SELECT nn.note_nlp_id FROM note_nlp nn JOIN note n ON nn.note_id = n.note_id
    WHERE nn.note_nlp_concept_id = 201826 AND n.note_date BETWEEN '2023-01-01' AND '2023-12-31'
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.references import cdm_table
from fastssv.core.helpers import (
    normalize_name,
    parse_sql,
    column_origins,
)
from fastssv.core.registry import register


NOTE_NLP = "note_nlp"
NOTE = "note"
NLP_DATE_COLS = {"nlp_date", "nlp_datetime"}
NOTE_DATE_COLS = {"note_date", "note_datetime"}
_RANGES = (exp.GT, exp.GTE, exp.LT, exp.LTE)
_COMPARISONS = (*_RANGES, exp.EQ, exp.Between)
# A date reduced to a period (year, month, ...): equality on it is a time window.
_TRUNCATIONS = (exp.Extract, exp.Year, exp.Month, exp.DateTrunc, exp.TimestampTrunc)


def _reads(node: Optional[exp.Expression], table: str, columns: Set[str]) -> bool:
    """Whether ``node`` contains, outside any subquery, a column whose lineage is ``table``.<one of columns>."""
    if node is None:
        return False
    for col in node.find_all(exp.Column):
        if col.find_ancestor(exp.Select) is not node.find_ancestor(exp.Select) and not isinstance(node, exp.Column):
            continue
        origins = column_origins(col)
        if origins and all(t == table and c in columns for t, c in origins):
            return True
    return False


def _reads_any_date(node: Optional[exp.Expression]) -> bool:
    return node is not None and any(
        normalize_name(c.name) in NLP_DATE_COLS | NOTE_DATE_COLS for c in node.find_all(exp.Column)
    )


def _filter_clauses(select: exp.Select) -> List[exp.Expression]:
    clauses = [select.args.get("where"), select.args.get("having")]
    clauses += [j.args.get("on") for j in select.args.get("joins") or []]
    return [c for c in clauses if c is not None]


def _own(node: exp.Expression, select: exp.Select) -> bool:
    return node.find_ancestor(exp.Select) is select


def _filters_note_date(select: exp.Select) -> bool:
    for clause in _filter_clauses(select):
        for cmp in clause.find_all(*_COMPARISONS):
            if _own(cmp, select) and _reads(cmp, NOTE, NOTE_DATE_COLS):
                return True
    return False


def _picks_processing_run(select: exp.Select) -> bool:
    """The SELECT restricts nlp_system: together with an nlp_date equality it selects one processing run."""
    for clause in _filter_clauses(select):
        for cmp in clause.find_all(exp.EQ, exp.In):
            if _own(cmp, select) and _reads(cmp, NOTE_NLP, {"nlp_system"}):
                return True
    return False


def _is_window_side(side: exp.Expression, cmp: exp.Expression, select: exp.Select) -> bool:
    """``side`` reads nlp_date as a point in time: a range comparison on it, equality on a truncated date, or
    equality on the date itself unless the SELECT also picks an NLP system (a processing run)."""
    if side.find(exp.AggFunc) is not None or not _reads(side, NOTE_NLP, NLP_DATE_COLS):
        return False
    if isinstance(cmp, (exp.Between, *_RANGES)):
        return True
    if not isinstance(cmp, exp.EQ):
        return False
    return side.find(*_TRUNCATIONS) is not None or not _picks_processing_run(select)


def _find_nlp_date_filters(tree: exp.Expression) -> List[Tuple[str, str, Optional[str]]]:
    """(column name, "comparison" | "between", operator class name or None) per time-window filter on nlp_date."""
    found: List[Tuple[str, str, Optional[str]]] = []
    for select in tree.find_all(exp.Select):
        if _filters_note_date(select):
            continue
        for clause in _filter_clauses(select):
            for cmp in clause.find_all(*_COMPARISONS):
                if not _own(cmp, select):
                    continue
                if isinstance(cmp, exp.Between):
                    operand_pairs = [(cmp.this, [cmp.args.get("low"), cmp.args.get("high")])]
                else:
                    operand_pairs = [(cmp.this, [cmp.expression]), (cmp.expression, [cmp.this])]
                for side, others in operand_pairs:
                    if not _is_window_side(side, cmp, select):
                        continue
                    # Another processing or note date (latest run, processing lag) or a subquery (a watermark kept
                    # elsewhere) is not a clinical time window.
                    if any(o is not None and (_reads_any_date(o) or o.find(exp.Select) is not None) for o in others):
                        continue
                    name = next(
                        normalize_name(c.name)
                        for c in side.find_all(exp.Column)
                        if normalize_name(c.name) in NLP_DATE_COLS
                    )
                    found.append(
                        (name, "between" if isinstance(cmp, exp.Between) else "comparison", type(cmp).__name__)
                    )
    return found


@register
class NoteNlpNlpDateForTemporalFilteringRule(Rule):
    """Flag time-window filters on note_nlp's processing date."""

    rule_id = "data_quality.note_nlp_nlp_date_for_temporal_filtering"
    name = "Note NLP nlp_date for Temporal Filtering"

    description = (
        "note_nlp.nlp_date / nlp_datetime record when the NLP processing ran, not the clinical event date. "
        "Filter on note.note_date for temporal windows."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "REPLACE: `WHERE note_nlp.nlp_date <op> <date>` WITH `WHERE note.note_date <op> <date>` "
        "(after JOIN note ON note_nlp.note_id = note.note_id). nlp_date is processing time, not clinical event time."
    )
    long_description = (
        "note_nlp.nlp_date records when the NLP pipeline processed the source note, which can be months or "
        "years after the clinical event itself. For temporal cohort logic you need the clinical date, which "
        "lives on note.note_date. Join note_nlp back to note and filter on note_date instead."
    )
    example_bad = "SELECT note_nlp_id\nFROM note_nlp\nWHERE nlp_date >= DATE '2023-01-01';"
    example_good = (
        "SELECT nnlp.note_nlp_id\n"
        "FROM note_nlp nnlp\n"
        "JOIN note n ON nnlp.note_id = n.note_id\n"
        "WHERE n.note_date >= DATE '2023-01-01';"
    )
    references = (cdm_table("note_nlp"), cdm_table("note"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if NOTE_NLP not in lowered or "nlp_date" not in lowered:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()
        for tree in trees:
            if not tree:
                continue
            for name, kind, op in _find_nlp_date_filters(tree):
                key = f"{kind}|{name}|{op}"
                if key in seen:
                    continue
                seen.add(key)
                how = "in BETWEEN" if kind == "between" else f"in a {op} comparison"
                violations.append(
                    self.create_violation(
                        message=(
                            f"note_nlp.{name} used {how} to filter by date. {name} is when the NLP processing ran "
                            "(CDM v5.4), not when the note was written; filter note.note_date instead."
                        ),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details={"type": kind, "table": NOTE_NLP, "column": name, "operator": op},
                    )
                )
        return violations


__all__ = ["NoteNlpNlpDateForTemporalFilteringRule"]

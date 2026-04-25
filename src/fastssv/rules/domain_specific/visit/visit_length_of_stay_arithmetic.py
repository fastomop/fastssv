"""Visit Length-of-Stay Arithmetic Rule.

Detects ``visit_end_date - visit_start_date`` (or ``DATEDIFF`` / ``DATE_DIFF``
on those columns) used for length-of-stay computation **without** an
inpatient ``visit_concept_id`` filter.

Length-of-stay is meaningful only for visit types where end_date can
exceed start_date — primarily inpatient visits (`visit_concept_id = 9201`),
non-hospital institution stays (`9203`, `262`), and emergency-room visits
that admit (`9203`). For outpatient visits (`9202`), `visit_end_date`
equals `visit_start_date` by spec, so LOS is always 0 and aggregating it
across mixed visit types averages in zeros.

Detection patterns (any of these in the query, on visit_occurrence
columns, without an accompanying inpatient filter):
    visit_end_date - visit_start_date
    visit_end_datetime - visit_start_datetime
    DATEDIFF(<unit>, visit_start_date, visit_end_date)
    DATE_DIFF(visit_end_date, visit_start_date, <unit>)
"""

from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    has_table_reference,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


VISIT_OCCURRENCE = "visit_occurrence"
VISIT_START_DATE_COLS = {"visit_start_date", "visit_start_datetime"}
VISIT_END_DATE_COLS = {"visit_end_date", "visit_end_datetime"}
VISIT_DATE_COLS = VISIT_START_DATE_COLS | VISIT_END_DATE_COLS

# OMOP visit_concept_id values where end_date can legitimately differ
# from start_date (i.e. multi-day stays):
INPATIENT_LIKE_CONCEPT_IDS = {9201, 9203, 32037, 32760, 38004279, 581379}


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_visit_date_column(col: exp.Column, aliases: dict, names: set) -> bool:
    table, col_name = resolve_table_col(col, aliases)
    if _norm(col_name) not in names:
        return False
    if table:
        return _norm(table) == VISIT_OCCURRENCE
    real_tables = {_norm(t) for t in aliases.values()}
    return VISIT_OCCURRENCE in real_tables


def _has_inpatient_filter(tree: exp.Expression, aliases: dict) -> bool:
    """True if the query restricts ``visit_concept_id`` to an inpatient-like
    value via ``= <id>`` or ``IN (...)`` in WHERE / JOIN ON.
    """
    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.In)):
            continue
        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        if not isinstance(left, exp.Column):
            continue
        if _norm(left.name) != "visit_concept_id":
            continue
        # accept on visit_occurrence or unqualified-with-vo-in-scope
        table, _ = resolve_table_col(left, aliases)
        real_tables = {_norm(t) for t in aliases.values()}
        if table:
            if _norm(table) != VISIT_OCCURRENCE:
                continue
        elif VISIT_OCCURRENCE not in real_tables:
            continue

        if isinstance(node, exp.EQ):
            right = node.expression
            if isinstance(right, exp.Literal) and not right.is_string:
                try:
                    if int(right.this) in INPATIENT_LIKE_CONCEPT_IDS:
                        return True
                except (ValueError, TypeError):
                    pass
        else:  # exp.In
            for val in node.expressions or []:
                if isinstance(val, exp.Literal) and not val.is_string:
                    try:
                        if int(val.this) in INPATIENT_LIKE_CONCEPT_IDS:
                            return True
                    except (ValueError, TypeError):
                        pass

    return False


def _detect_los_expressions(tree: exp.Expression, aliases: dict) -> List[str]:
    """Find LOS-shaped expressions involving visit_occurrence date columns."""
    found: List[str] = []

    # Pattern A: visit_end_date - visit_start_date
    for sub in tree.find_all(exp.Sub):
        left, right = sub.this, sub.expression
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue
        if (
            _is_visit_date_column(left, aliases, VISIT_END_DATE_COLS)
            and _is_visit_date_column(right, aliases, VISIT_START_DATE_COLS)
        ):
            found.append(sub.sql())

    # Pattern B: DATEDIFF / DATE_DIFF mentioning both a visit_start_* and a
    # visit_end_* column. sqlglot parses DATEDIFF inconsistently across
    # dialects (e.g. postgres puts the second column into a ``unit`` Var
    # slot), so we use a textual check on the rendered SQL of the function
    # call rather than walking node children.
    def _mentions_both_visit_date_cols(text: str) -> bool:
        lower = text.lower()
        has_start = any(c in lower for c in VISIT_START_DATE_COLS)
        has_end = any(c in lower for c in VISIT_END_DATE_COLS)
        return has_start and has_end

    for func in tree.find_all(exp.Anonymous):
        name = _norm(func.this) if hasattr(func, "this") else None
        if name not in {"datediff", "date_diff"}:
            continue
        if _mentions_both_visit_date_cols(func.sql()):
            found.append(func.sql())

    for diff in tree.find_all(exp.DateDiff):
        if _mentions_both_visit_date_cols(diff.sql()):
            found.append(diff.sql())

    return found


@register
class VisitLengthOfStayArithmeticRule(Rule):
    """Warn when visit length-of-stay is computed without an inpatient filter."""

    rule_id = "domain_specific.visit_length_of_stay_arithmetic"
    name = "Visit Length-of-Stay Arithmetic"

    description = (
        "Computing visit length-of-stay (visit_end_date - visit_start_date or DATEDIFF) "
        "without restricting to inpatient-like visit_concept_ids mixes outpatient "
        "same-day visits (LOS = 0) into the calculation."
    )

    severity = Severity.WARNING

    suggested_fix = "ADD: `WHERE vo.visit_concept_id IN (9201, 9203, 262, 32037, 32760, 38004279, 581379)` (inpatient-like) before computing LOS, AND `AND vo.visit_end_date IS NOT NULL` (or COALESCE(visit_end_date, CURRENT_DATE)) to handle ongoing stays."
    long_description = (
        "Length-of-stay arithmetic (`visit_end_date - visit_start_date` or "
        "`DATEDIFF(day, visit_start_date, visit_end_date)`) is meaningful only "
        "for inpatient-like visit types where the end-date can exceed the "
        "start-date. Outpatient visits (`visit_concept_id = 9202`) by spec "
        "have `visit_end_date = visit_start_date`, so a mixed query averages "
        "in a sea of zeros and understates inpatient LOS. Restrict to "
        "inpatient concept ids (9201, 9203, 262, 32037, …) before computing. "
        "Also note that `visit_end_date` is nullable for ongoing stays — "
        "consider `COALESCE(visit_end_date, CURRENT_DATE)` if the query is "
        "computing on live data."
    )

    example_bad = (
        "SELECT AVG(visit_end_date - visit_start_date) AS avg_los\n"
        "FROM visit_occurrence;"
    )
    example_good = (
        "SELECT AVG(visit_end_date - visit_start_date) AS avg_los\n"
        "FROM visit_occurrence\n"
        "WHERE visit_concept_id = 9201\n"
        "  AND visit_end_date IS NOT NULL;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if VISIT_OCCURRENCE not in sql_lower:
            return []
        if not any(c in sql_lower for c in VISIT_DATE_COLS):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: set = set()

        for tree in trees:
            if not tree:
                continue
            if not has_table_reference(tree, VISIT_OCCURRENCE):
                continue

            aliases = extract_aliases(tree)
            los_exprs = _detect_los_expressions(tree, aliases)
            if not los_exprs:
                continue
            if _has_inpatient_filter(tree, aliases):
                continue

            for expr_sql in los_exprs:
                if expr_sql in seen:
                    continue
                seen.add(expr_sql)
                violations.append(
                    self.create_violation(
                        message=(
                            f"Length-of-stay computation `{expr_sql}` "
                            f"without an inpatient visit_concept_id filter. "
                            f"Outpatient visits (9202) have visit_end_date = "
                            f"visit_start_date, which dilutes LOS averages."
                        ),
                        details={
                            "expression": expr_sql,
                            "table": VISIT_OCCURRENCE,
                        },
                    )
                )

        return violations


__all__ = ["VisitLengthOfStayArithmeticRule"]

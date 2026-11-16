"""Nullable Date Column Rule.

CDM v5.4 makes one date per clinical table mandatory (``condition_start_date``, ``drug_exposure_start_date``,
``visit_end_date``, ``drug_exposure_end_date``, ...) and leaves the rest optional (Required = No): the
``*_datetime`` columns (including ``person.birth_datetime`` and ``note_nlp.nlp_datetime``), ``measurement_time``,
``drug_exposure.verbatim_end_date``, and the end dates of condition_occurrence, procedure_occurrence,
device_exposure and episode. A NULL in an optional column makes a comparison unknown and an arithmetic result
NULL, so the row silently drops out of the filter, the join, or the aggregate.

Flagged uses of an optional date column, unless the column is wrapped in COALESCE / NULLIF / IFNULL / ISNULL /
NVL / CASE or the SELECT that uses it tests it with ``IS [NOT] NULL`` in its WHERE / JOIN ON / HAVING (or in the
aggregate's FILTER clause):

* an operand of a comparison, the tested value or a bound of ``BETWEEN``, or the tested value of ``IN``, in
  WHERE / JOIN ON / HAVING;
* an operand of date arithmetic (``end - start``, ``+``) or of a date-difference / date-add function.

Calendar-part extraction passes NULL through, so ``EXTRACT(YEAR FROM p.birth_datetime)`` counts as a use of
``birth_datetime``: an age computed from it drops every person without a birth datetime, although
``year_of_birth`` is required.

The end-before-start plausibility check on one row (``condition_end_date < condition_start_date``, for the start/end
pairs of the Data Quality Dashboard) is not flagged: under three-valued logic a NULL end is not before its start, so
excluding it is the intended answer. Other same-row comparisons
(``condition_end_date > condition_start_date``) drop ongoing records and are flagged.
"""

from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import CDM_FIELD_LEVEL, cdm_table
from fastssv.core.helpers import (
    DERIVED,
    column_origins,
    extract_aliases,
    has_table_reference,
    normalize_name,
    parse_sql,
    resolve_column_source,
)
from fastssv.core.registry import register
from fastssv.rules.temporal._cdm_dates import OPTIONAL_DATE_COLUMNS as _GENERATED_OPTIONAL
from fastssv.rules.temporal._cdm_dates import START_BEFORE_END_PAIRS


# Optional (Required = No) date columns of CDM v5.4, mapped to the required date that can stand in for them; generated
# from the CDM v5.4 field-level CSV (see _cdm_dates.py).
OPTIONAL_DATE_COLUMNS: Dict[str, Dict[str, str]] = _GENERATED_OPTIONAL

# end column -> start column of the same row, for the Data Quality Dashboard plausibleStartBeforeEnd pairs.
_START_OF_END = {(table, end): start for table, start, end in START_BEFORE_END_PAIRS}

_OWNER = {col: table for table, cols in OPTIONAL_DATE_COLUMNS.items() for col in cols}

_PEEL = (exp.Cast, exp.TryCast, exp.Paren, exp.TsOrDsToDate, exp.TimeStrToTime, exp.TimeStrToDate)
_NULL_SAFE_FUNCS = {"isnull", "ifnull", "nvl", "nvl2", "zeroifnull"}
_DATE_FUNC_NAMES = {"datediff", "date_diff", "timestampdiff", "dateadd", "date_add", "adddate", "date_sub", "age"}
_DATE_FUNC_TYPES = (exp.DateDiff, exp.DateAdd, exp.DateSub, exp.TsOrDsAdd, exp.TimestampDiff)
_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def _peel(node: exp.Expression) -> exp.Expression:
    while isinstance(node, _PEEL):
        node = node.this
    return node


def _optional_column(col: exp.Column, aliases: Dict[str, str]) -> Optional[Tuple[str, str]]:
    """(table, column) when ``col`` reads an optional CDM date column, followed through renames, CTEs and derived
    tables; a value computed from it (COALESCE, CASE) is not the column any more."""
    origins = column_origins(col)
    if origins is not None:
        if len(origins) != 1:
            return None
        origin = next(iter(origins))
        return origin if origin[1] in OPTIONAL_DATE_COLUMNS.get(origin[0], {}) else None
    table = _OWNER.get(normalize_name(col.name))
    if table is None:
        return None
    src_table, src_col = resolve_column_source(col, aliases)
    if src_table == DERIVED:
        return None
    if src_table and src_table != table:
        return None
    if not src_table and table not in aliases.values():
        return None
    return table, src_col


def _null_safe_wrapped(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, (exp.Coalesce, exp.Nullif, exp.Case, exp.If)):
            return True
        if isinstance(parent, exp.Anonymous) and normalize_name(parent.name) in _NULL_SAFE_FUNCS:
            return True
        parent = parent.parent
    return False


def _null_tested(use: exp.Column, table: str, column: str, aliases: Dict[str, str]) -> bool:
    """An ``IS [NOT] NULL`` test of the same column in the WHERE / JOIN ON / HAVING of the SELECT that uses it, or in
    the FILTER clause of the aggregate around the use. A test in another SELECT, or in the projection list, does not
    change which rows the use sees."""
    select = use.find_ancestor(exp.Select)
    scopes = []
    if select is not None:
        scopes += [select.args.get("where"), select.args.get("having")]
        scopes += [j.args.get("on") for j in select.args.get("joins") or []]
    flt = use.find_ancestor(exp.Filter)
    if flt is not None and flt.find_ancestor(exp.Select) is select:
        scopes.append(flt.expression)
    for scope in scopes:
        if scope is None:
            continue
        for is_node in scope.find_all(exp.Is):
            if not isinstance(is_node.expression, exp.Null) or is_node.find_ancestor(exp.Select) is not select:
                continue
            target = _peel(is_node.this)
            if isinstance(target, exp.Column) and _optional_column(target, aliases) == (table, column):
                return True
    return False


def _in_predicate_clause(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, (exp.Where, exp.Join, exp.Having)):
            return True
        parent = parent.parent
    return False


def _is_part_extract(node: exp.Expression, child: exp.Expression) -> bool:
    """True when ``node`` extracts a calendar part (year, month, ...) from ``child``; NULL passes straight through."""
    if isinstance(node, exp.Extract):
        return node.expression is child
    if isinstance(node, (exp.Year, exp.Month, exp.Day, exp.DateTrunc, exp.TimestampTrunc)):
        return node.this is child
    if isinstance(node, exp.Anonymous) and normalize_name(node.name) in {"date_part", "datepart", "date_trunc"}:
        return any(arg is child for arg in node.expressions[1:])
    return False


def _end_before_start_check(less: exp.Expression, greater: exp.Expression, aliases: Dict[str, str]) -> bool:
    """``<end> < <start>`` on one row, for a Data Quality Dashboard start/end pair. Under three-valued logic a NULL end
    date is not before its start, so dropping it is the answer the check wants; ``temporal.end_before_start_validation``
    leaves this comparison alone for the same reason."""
    less, greater = _peel(less), _peel(greater)
    if not (isinstance(less, exp.Column) and isinstance(greater, exp.Column)):
        return False
    if normalize_name(less.table) != normalize_name(greater.table):
        return False
    end, start = resolve_column_source(less, aliases), resolve_column_source(greater, aliases)
    return end[0] == start[0] and _START_OF_END.get(end) == start[1]


def _use_of(col: exp.Column, table: str, aliases: Dict[str, str]) -> Optional[Tuple[str, exp.Expression]]:
    """(context, node) describing how the column is used, or None for an unflagged use."""
    top: exp.Expression = col
    while isinstance(top.parent, _PEEL) or (top.parent is not None and _is_part_extract(top.parent, top)):
        top = top.parent
    parent = top.parent
    if isinstance(parent, (exp.Add, exp.Sub)):
        return "date arithmetic", parent
    if isinstance(parent, _DATE_FUNC_TYPES) or (
        isinstance(parent, exp.Anonymous) and normalize_name(parent.name) in _DATE_FUNC_NAMES
    ):
        return "a date function", parent
    if isinstance(parent, _COMPARISONS) and _in_predicate_clause(parent):
        other = parent.expression if parent.this is top else parent.this
        if isinstance(_peel(other), exp.Null):
            return None
        col_is_less = isinstance(parent, (exp.LT, exp.LTE)) == (parent.this is top)
        less, greater = (top, other) if col_is_less else (other, top)
        if isinstance(parent, (exp.LT, exp.LTE, exp.GT, exp.GTE)) and _end_before_start_check(less, greater, aliases):
            return None
        return "a comparison", parent
    if isinstance(parent, exp.Between) and _in_predicate_clause(parent):
        return "a BETWEEN filter", parent
    if isinstance(parent, exp.In) and parent.this is top and _in_predicate_clause(parent):
        return "an IN filter", parent
    return None


@register
class NullableDateColumnRule(Rule):
    """Flag optional (Required = No) CDM date columns used in filters or arithmetic without NULL handling."""

    rule_id = "temporal.nullable_date_column"
    name = "Nullable Date Column Without NULL Handling"
    description = (
        "Flags optional CDM v5.4 date columns (*_datetime incl. birth_datetime, measurement_time, "
        "verbatim_end_date, condition/procedure/device/episode end dates) used in comparisons or date arithmetic "
        "without COALESCE or an IS NULL test, which silently drops rows where the column is NULL."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the optional column WITH its required counterpart (e.g. condition_start_date for "
        "condition_start_datetime), OR WRAP it as `COALESCE(<optional_col>, <fallback>)`, OR ADD an explicit "
        "`<optional_col> IS NOT NULL` so the exclusion is deliberate."
    )
    long_description = (
        "In CDM v5.4 each clinical table has a required date (Required = Yes) and optional companions: the "
        "*_datetime columns (including person.birth_datetime and note_nlp.nlp_datetime), measurement_time, "
        "drug_exposure.verbatim_end_date, and the end dates of condition_occurrence, procedure_occurrence, "
        "device_exposure and episode. drug_exposure_end_date, visit_end_date and visit_detail_end_date are "
        "required and are not flagged. Comparing or subtracting an optional column yields NULL for rows where it "
        "is missing, so those rows disappear from the filter, join or aggregate without any error. Use the "
        "required column, COALESCE to a sensible fallback, or state the exclusion with IS NOT NULL. Calendar-part "
        "extraction (EXTRACT(YEAR FROM birth_datetime)) passes NULL through and counts as a use. The "
        "end-before-start plausibility check on one row is not flagged."
    )
    example_bad = "SELECT person_id\nFROM condition_occurrence\nWHERE condition_end_date >= DATE '2023-01-01';"
    example_good = (
        "SELECT person_id\nFROM condition_occurrence\n"
        "WHERE COALESCE(condition_end_date, condition_start_date) >= DATE '2023-01-01';"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT co.person_id,\n"
                "       EXTRACT(YEAR FROM co.condition_start_date) - EXTRACT(YEAR FROM p.birth_datetime) AS age\n"
                "FROM condition_occurrence co\n"
                "JOIN person p ON co.person_id = p.person_id;"
            ),
            severity=Severity.WARNING,
            note="Date arithmetic on optional birth_datetime: age is NULL wherever it is missing (year_of_birth is required)",
        ),
    )
    references = (
        CDM_FIELD_LEVEL,
        cdm_table("condition_occurrence"),
        Reference(
            "PostgreSQL documentation: Comparison Functions and Operators",
            "https://www.postgresql.org/docs/current/functions-comparison.html",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if not any(col in lowered for col in _OWNER):
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None or not any(has_table_reference(tree, t) for t in OPTIONAL_DATE_COLUMNS):
                continue
            aliases = extract_aliases(tree)
            seen = set()
            for col in tree.find_all(exp.Column):
                found = _optional_column(col, aliases)
                if found is None:
                    continue
                table, column = found
                use = _use_of(col, table, aliases)
                if use is None or _null_safe_wrapped(col) or _null_tested(col, table, column, aliases):
                    continue
                context, node = use
                key = (column, node.sql())
                if key in seen:
                    continue
                seen.add(key)
                fallback = OPTIONAL_DATE_COLUMNS[table][column]
                violations.append(
                    self.create_violation(
                        message=(
                            f"{table}.{column} is optional in CDM v5.4 and is used in {context} without NULL "
                            f"handling (`{node.sql()[:120]}`); rows where it is NULL drop out silently. Use the "
                            f"required {fallback}, COALESCE({column}, <fallback>), or an explicit "
                            f"`{column} IS NOT NULL`."
                        ),
                        details={"table": table, "column": column, "required_alternative": fallback},
                    )
                )
        return violations


__all__ = ["NullableDateColumnRule"]

"""Having Without Group By Rule.

A query with HAVING and no GROUP BY is a grouped query whose rows form one single group (PostgreSQL: "The presence
of HAVING turns a query into a grouped query even if there is no GROUP BY clause"). Its SELECT list and HAVING
clause may therefore read table columns only inside aggregate functions. A column read outside an aggregate is
rejected by the engine (PostgreSQL: ``column "t.x" must appear in the GROUP BY clause or be used in an aggregate
function``; DuckDB raises the same binder error).

    SELECT person_id, COUNT(*) FROM drug_exposure HAVING COUNT(*) > 3
    -- rejected: person_id is not grouped

The aggregate-only form is valid SQL and is not flagged: it returns one row when the condition holds and none
otherwise.

    SELECT COUNT(*) FROM drug_exposure HAVING COUNT(*) > 3

The finding is an ERROR for PostgreSQL and DuckDB, where the rejection was verified, and a WARNING for other
dialects, because some engines accept an ungrouped column under a permissive SQL mode.
"""

import re
from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleViolation, Severity
from fastssv.core.helpers import _provides, _select_sources, normalize_name, parse_sql
from fastssv.core.patch import add as patch_add
from fastssv.core.registry import register

_REJECTING_DIALECTS = {"postgres", "postgresql", "duckdb"}

# Aggregate function names each engine defines, from its documentation (PostgreSQL "Aggregate Functions", tables of
# general-purpose, statistical, ordered-set and hypothetical-set aggregates; DuckDB "Aggregate Functions"). sqlglot
# parses some of them into classes that are not AggFunc (DuckDB list(x) becomes exp.List).
_POSTGRES_AGGREGATES = frozenset(
    """any_value array_agg avg bit_and bit_or bit_xor bool_and bool_or count every json_agg jsonb_agg json_agg_strict
    jsonb_agg_strict json_arrayagg json_objectagg json_object_agg jsonb_object_agg json_object_agg_strict
    jsonb_object_agg_strict json_object_agg_unique jsonb_object_agg_unique json_object_agg_unique_strict
    jsonb_object_agg_unique_strict max min range_agg range_intersect_agg string_agg sum xmlagg corr covar_pop
    covar_samp regr_avgx regr_avgy regr_count regr_intercept regr_r2 regr_slope regr_sxx regr_sxy regr_syy stddev
    stddev_pop stddev_samp variance var_pop var_samp mode percentile_cont percentile_disc rank dense_rank
    percent_rank cume_dist""".split()
)
_DUCKDB_AGGREGATES = frozenset(
    """any_value arg_max argmax max_by arg_max_null arg_max_nulls_last arg_min argmin min_by arg_min_null
    arg_min_nulls_last avg mean bit_and bit_or bit_xor bitstring_agg bool_and bool_or count countif
    decimal_average favg first arbitrary fsum sumkahan kahan_sum geometric_mean geomean histogram histogram_exact
    histogram_values last list array_agg lttb max min product string_agg group_concat listagg sum weighted_avg
    wavg approx_count_distinct approx_quantile approx_top_k reservoir_quantile corr covar_pop covar_samp regr_sxy
    entropy kurtosis_pop kurtosis mad median mode quantile_cont quantile quantile_disc regr_avgx regr_avgy
    regr_count regr_intercept regr_r2 regr_slope regr_sxx regr_syy skewness sem stddev_pop stddev_samp stddev
    var_pop var_samp variance percentile_cont percentile_disc""".split()
)


def _aggregate_names(dialect: str) -> frozenset:
    if dialect in ("postgres", "postgresql"):
        return _POSTGRES_AGGREGATES
    if dialect == "duckdb":
        return _DUCKDB_AGGREGATES
    return _POSTGRES_AGGREGATES | _DUCKDB_AGGREGATES


def _function_name(node: exp.Func) -> str:
    return normalize_name(node.name if isinstance(node, exp.Anonymous) else node.sql_name())


def _owning_select(node: exp.Expression) -> Optional[exp.Expression]:
    return node.find_ancestor(exp.Select)


def _inside_plain_aggregate(col: exp.Column, select: exp.Select, dialect: str = "postgres") -> Optional[bool]:
    """True when ``col`` is an argument of an aggregate of ``select``; None when a function of unknown kind wraps
    it (a user-defined aggregate cannot be told from a scalar function)."""
    unknown = False
    aggregates = _aggregate_names(dialect)
    node = col.parent
    while node is not None and node is not select:
        if isinstance(node, exp.Window):
            # A window function is evaluated per row after grouping, so its arguments must themselves be grouped.
            return False
        # FILTER (WHERE ...) and WITHIN GROUP (ORDER BY ...) are evaluated per input row of their aggregate.
        if isinstance(node, (exp.AggFunc, exp.Filter, exp.WithinGroup)):
            return True
        if isinstance(node, exp.Func) and _function_name(node) in aggregates:
            return True
        if isinstance(node, exp.Anonymous):
            unknown = True
        node = node.parent
    return None if unknown else False


def _is_local_column(col: exp.Column, select: exp.Select) -> bool:
    """Whether ``col`` certainly reads a FROM/JOIN source of ``select`` and not an enclosing query."""
    sources = _select_sources(select)
    if col.table:
        qualifier = normalize_name(col.table)
        return any(normalize_name(s.alias_or_name) == qualifier for s in sources)
    if select.find_ancestor(exp.Select) is None:
        return bool(sources)
    name = normalize_name(col.name)
    return any(_provides(select, s, name, 0) is True for s in sources)


def _ungrouped_columns(select: exp.Select, dialect: str) -> List[str]:
    """Columns the SELECT list or HAVING clause of ``select`` reads outside an aggregate, as written."""
    found: List[str] = []
    seen = set()
    clauses = list(select.expressions)
    having = select.args.get("having")
    if having is not None:
        clauses.append(having)
    # PostgreSQL resolves HAVING names against input columns only; other engines (DuckDB, MySQL, ...) also accept
    # an output alias there, and the alias then names an aggregate.
    alias_names = (
        {normalize_name(e.alias) for e in select.expressions if isinstance(e, exp.Alias)}
        if dialect not in ("postgres", "postgresql")
        else set()
    )
    for clause in clauses:
        if isinstance(clause, exp.Star) or (isinstance(clause, exp.Column) and isinstance(clause.this, exp.Star)):
            if "*" not in seen:
                seen.add("*")
                found.append(clause.sql())
            continue
        for col in clause.find_all(exp.Column):
            if _owning_select(col) is not select or isinstance(col.this, exp.Star):
                continue
            if _inside_plain_aggregate(col, select, dialect) is not False or not _is_local_column(col, select):
                continue
            if clause is having and not col.table and normalize_name(col.name) in alias_names:
                continue
            key = col.sql().lower()
            if key not in seen:
                seen.add(key)
                found.append(col.sql())
    return found


def _having_offset(sql: str, n_selects_with_having: int) -> Optional[int]:
    """Offset of the HAVING keyword when the statement has exactly one; None when the target is ambiguous."""
    matches = list(re.finditer(r"\bhaving\b", sql, re.IGNORECASE))
    if len(matches) == 1 and n_selects_with_having == 1:
        return matches[0].start()
    return None


@register
class HavingWithoutGroupByRule(Rule):
    """Ungrouped column in a query that has HAVING but no GROUP BY."""

    rule_id = "anti_patterns.having_without_group_by"
    name = "Having Without Group By"

    description = (
        "A query with HAVING and no GROUP BY is one single group, so its SELECT list and HAVING clause may read "
        "columns only inside aggregates. A column read outside an aggregate makes the engine reject the query."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "ADD: GROUP BY <the ungrouped columns> before HAVING, OR wrap the column in an aggregate, OR move a "
        "row-level condition from HAVING to WHERE."
    )
    long_description = (
        "HAVING without GROUP BY turns the query into a grouped query whose rows form a single group. That is "
        "valid SQL when every column is read inside an aggregate: the query returns one row if the HAVING "
        "condition holds and none otherwise, and the rule stays silent. When the SELECT list or the HAVING clause "
        "reads a column outside an aggregate, PostgreSQL and DuckDB reject the query ('must appear in the GROUP BY "
        "clause or be used in an aggregate function'); that is the case reported. For other dialects the finding "
        "is a WARNING, because some engines accept an ungrouped column under a permissive SQL mode and return an "
        "arbitrary value for it."
    )
    example_bad = "SELECT person_id, COUNT(*) AS n_exposures\nFROM drug_exposure\nHAVING COUNT(*) > 3;"
    example_good = (
        "SELECT person_id, COUNT(*) AS n_exposures\nFROM drug_exposure\nGROUP BY person_id\nHAVING COUNT(*) > 3;"
    )
    references = (
        Reference(
            "PostgreSQL documentation: SELECT, HAVING clause",
            "https://www.postgresql.org/docs/current/sql-select.html#SQL-HAVING",
        ),
        Reference(
            "PostgreSQL documentation: Aggregate Functions",
            "https://www.postgresql.org/docs/current/functions-aggregate.html",
        ),
        Reference(
            "DuckDB documentation: Aggregate Functions", "https://duckdb.org/docs/stable/sql/functions/aggregates"
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql or "having" not in sql.lower():
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        dialect_name = (dialect or "postgres").lower()
        rejected = dialect_name in _REJECTING_DIALECTS
        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue
            candidates = [
                s for s in tree.find_all(exp.Select) if s.args.get("having") is not None and s.args.get("group") is None
            ]
            n_having = sum(1 for s in tree.find_all(exp.Select) if s.args.get("having") is not None)
            for select in candidates:
                columns = _ungrouped_columns(select, dialect_name)
                if not columns:
                    continue
                shown = ", ".join(columns[:4]) + (", ..." if len(columns) > 4 else "")
                groupable = [c for c in columns if "*" not in c]
                if rejected:
                    consequence = (
                        "the engine rejects the query (PostgreSQL: 'must appear in the GROUP BY clause or be used "
                        "in an aggregate function')"
                    )
                else:
                    consequence = (
                        "standard SQL rejects the query, and an engine that accepts it returns an arbitrary "
                        "value for the column"
                    )
                patch = None
                offset = _having_offset(sql, n_having)
                if offset is not None and groupable and len(groupable) == len(columns):
                    patch = patch_add(offset, f"GROUP BY {', '.join(groupable)}\n")
                violations.append(
                    self.create_violation(
                        message=(
                            f"HAVING without GROUP BY makes the whole result one group, but {shown} is read "
                            f"outside an aggregate, so {consequence}. Add GROUP BY for it, aggregate it, or use "
                            "WHERE for a row-level condition."
                        ),
                        severity=Severity.ERROR if rejected else Severity.WARNING,
                        suggested_fix_patch=patch,
                        details={"ungrouped_columns": columns},
                    )
                )

        return violations


__all__ = ["HavingWithoutGroupByRule"]

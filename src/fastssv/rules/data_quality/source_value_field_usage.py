"""Source Value Field Usage Warning Rule.

The CDM v5.4 conventions describe ``*_SOURCE_VALUE`` fields as "verbatim information from the source data,
typically used in ETL to map to CONCEPT_ID, and not to be used by any standard analytics"; "the standard
*_CONCEPT_ID fields are strongly suggested to be used in all standardized analytics". Grouping an analysis by a
source value therefore produces site-specific buckets that do not compare across data sources.

Flagged: a SELECT that groups by a CDM ``*_source_value`` column that has a standardized counterpart in its table
(``condition_source_value`` -> ``condition_concept_id``, ``value_source_value`` -> ``value_as_concept_id``).
Grouping covers GROUP BY (by expression, ordinal or output alias), SELECT DISTINCT and window PARTITION BY; the
column may be wrapped in CAST, UPPER / LOWER / TRIM or COALESCE and is followed through CTEs and derived tables.

Not flagged: mapping reviews, which the Book of OHDSI (15.3.1, "Checking mappings") recommends: "We can review
both the source codes that map to the concepts used in the study, as well as those that don't."
    * the same SELECT also groups by the paired ``*_concept_id`` or ``*_source_concept_id``;
    * the table instance the source value is read from has its paired ``*_concept_id`` restricted (``= 0``,
      ``IN (...)``, ``IS NULL``, a join to a concept set), in the SELECT that reads it or in the grouping SELECT.
Source values without a standardized counterpart (``person_source_value``, ``dose_unit_source_value``, ...) and
columns of non-CDM tables are never flagged.
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    DERIVED,
    _is_filter_position,
    _predicate_atom,
    _reads,
    column_origins,
    normalize_name,
    parse_sql,
)
from fastssv.core.references import BOOK_CDM, BOOK_DATA_QUALITY, CDM_CONVENTIONS, section
from fastssv.core.registry import register
from fastssv.schemas.cdm_column_types import get_table_columns

_SUFFIX = "_source_value"
_PAIR_OVERRIDES = {"value_source_value": "value_as_concept_id"}
_WRAPPERS = (exp.Cast, exp.TryCast, exp.Paren, exp.Upper, exp.Lower, exp.Trim, exp.Coalesce)
_NON_RESTRICTING = (exp.NEQ, exp.NullSafeNEQ)


def _paired_concept_column(table: str, column: str) -> Optional[str]:
    """The standardized ``*_concept_id`` column paired with ``table.column``, if the CDM defines one."""
    columns = get_table_columns(table) or set()
    if column not in columns or not column.endswith(_SUFFIX):
        return None
    pair = _PAIR_OVERRIDES.get(column, column[: -len(_SUFFIX)] + "_concept_id")
    return pair if pair in columns else None


def _peel(node: exp.Expression) -> exp.Expression:
    while isinstance(node, _WRAPPERS):
        node = node.this
    return node


def _grouping(select: exp.Select) -> List[exp.Expression]:
    """Expressions ``select`` groups its rows by: GROUP BY (ordinals and output aliases resolved), SELECT DISTINCT
    projections, and PARTITION BY of its window functions."""
    projections = list(select.expressions)
    by_alias = {normalize_name(p.alias): p.this for p in projections if isinstance(p, exp.Alias)}
    out: List[exp.Expression] = []
    group = select.args.get("group")
    for key in group.expressions if group is not None else []:
        if isinstance(key, exp.Literal) and key.is_int and 0 < int(key.this) <= len(projections):
            proj = projections[int(key.this) - 1]
            out.append(proj.this if isinstance(proj, exp.Alias) else proj)
        elif isinstance(key, exp.Column) and not key.table and normalize_name(key.name) in by_alias:
            out.append(by_alias[normalize_name(key.name)])
        else:
            out.append(key)
    if select.args.get("distinct") is not None:
        out.extend(p.this if isinstance(p, exp.Alias) else p for p in projections)
    for window in select.find_all(exp.Window):
        if window.find_ancestor(exp.Select) is select:
            out.extend(window.args.get("partition_by") or [])
    return out


def _origin(node: exp.Expression) -> Optional[Tuple[str, str]]:
    node = _peel(node)
    if not isinstance(node, exp.Column):
        return None
    origins = column_origins(node)
    if not origins or len(origins) != 1:
        return None
    table, column = next(iter(origins))
    return None if table == DERIVED else (table, column)


def _restricted(select: exp.Select, alias: Optional[str], table: str, column: str) -> bool:
    """Whether a predicate of ``select`` restricts ``table``.``column``: of the instance ``alias`` read by ``select``
    when given, else of any instance whose values reach ``select``."""
    for col in select.find_all(exp.Column):
        if col.find_ancestor(exp.Select) is not select or not _is_filter_position(col):
            continue
        reads = _reads(col, relay=False)
        if alias is None:
            hit = any(t == table and c == column for _, _, t, c in reads)
        else:
            hit = any((s is select and a == alias and t == table and c == column) for s, a, t, c in reads)
        if not hit:
            continue
        atom, negated = _predicate_atom(col)
        if negated or atom is col or isinstance(atom, _NON_RESTRICTING):
            continue
        return True
    return False


def _findings(select: exp.Select) -> List[Tuple[str, str, str, str]]:
    keys = _grouping(select)
    if not keys:
        return []
    grouped = {o for o in (_origin(k) for k in keys) if o is not None}
    out = []
    seen: Set[Tuple[str, str]] = set()
    for key in keys:
        node = _peel(key)
        origin = _origin(key)
        if origin is None or not origin[1].endswith(_SUFFIX) or origin in seen:
            continue
        table, column = origin
        pair = _paired_concept_column(table, column)
        if pair is None:
            continue
        source_pair = column[: -len(_SUFFIX)] + "_source_concept_id"
        if (table, pair) in grouped or (table, source_pair) in grouped:
            continue
        if _restricted(select, None, table, pair):
            continue
        reads = _reads(node, relay=False) if isinstance(node, exp.Column) else set()
        if any(_restricted(s, a, table, pair) for s, a, t, c in reads if t == table and c == column):
            continue
        seen.add(origin)
        out.append((key.sql(), table, column, pair))
    return out


@register
class SourceValueFieldUsageRule(Rule):
    """Warns about grouping analyses by *_source_value instead of the standardized concept field."""

    rule_id = "data_quality.source_value_field_usage"
    name = "Source Value Field Usage"
    description = (
        "Warns when an analysis groups by a CDM *_source_value column (verbatim, unstandardized source code) "
        "instead of its standardized *_concept_id. Mapping reviews, which also group by the concept id or restrict "
        "it (to unmapped rows or to the study's concepts), are not flagged."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the grouping on `<col>_source_value` WITH `<col>_concept_id` and JOIN concept on that id to "
        "recover the standardized name. If the query reviews mappings, restrict `<col>_concept_id` (= 0 for "
        "unmapped codes) or group by it as well."
    )
    long_description = (
        "*_source_value columns store the verbatim source code or text and, per the CDM conventions, are not to be "
        "used by standardized analytics; their contents depend on each site's source system and ETL. Grouping an "
        "analysis by them (GROUP BY, SELECT DISTINCT or PARTITION BY) produces buckets that are not comparable "
        "across sites and splits one clinical concept into many source strings. Group by the paired *_concept_id "
        "instead. Reviewing mappings is the exception the Book of OHDSI recommends, so the rule stays quiet when "
        "the same SELECT also groups by the paired concept id or source_concept_id, or when the table the source "
        "value comes from has its concept id restricted (unmapped rows, = 0, or the study's concepts)."
    )
    example_bad = (
        "SELECT condition_source_value, COUNT(*) AS n\nFROM condition_occurrence\nGROUP BY condition_source_value;"
    )
    example_good = (
        "SELECT co.condition_concept_id, c.concept_name, COUNT(*) AS n\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON c.concept_id = co.condition_concept_id\n"
        "GROUP BY co.condition_concept_id, c.concept_name;"
    )
    references = (
        section(CDM_CONVENTIONS, "fields", "Fields"),
        section(
            CDM_CONVENTIONS,
            "source-values-source-concept-ids-and-standard-concept-ids",
            "Source values, source concept ids, and standard concept ids",
        ),
        section(BOOK_CDM, "concepts-Sources", "Difference between concepts and source values"),
        section(BOOK_DATA_QUALITY, "checking-mappings", "Checking mappings"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if _SUFFIX not in sql.lower():
            return []
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            for select in tree.find_all(exp.Select):
                for col_sql, table, column, pair in _findings(select):
                    violations.append(
                        self.create_violation(
                            message=(
                                f"Grouping by '{col_sql}' ({table}.{column}, verbatim source value) gives "
                                f"site-specific groups that do not compare across data sources. Group by "
                                f"'{pair}' (standardized) and join concept for its name."
                            ),
                            suggested_fix=(
                                f"REPLACE: the grouping on `{col_sql}` WITH `{pair}`, and JOIN concept ON "
                                f"{pair} = concept.concept_id to recover the standardized name. If this reviews "
                                f"mappings, restrict `{pair}` (e.g. `{pair} = 0` for unmapped codes) or group by it too."
                            ),
                            details={
                                "source_value_field": column,
                                "table": table,
                                "suggested_standard_field": pair,
                            },
                        )
                    )
        return violations


__all__ = ["SourceValueFieldUsageRule"]

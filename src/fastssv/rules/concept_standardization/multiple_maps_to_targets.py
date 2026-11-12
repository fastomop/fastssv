"""Multiple Maps To Targets Rule.

OMOP semantic rule VOCAB_013:
``concept_relationship`` with ``relationship_id = 'Maps to'`` is one-to-many.
One ICD-10 code can split into several SNOMED concepts and one NDC can fan
out to several RxNorm ingredients. SQL that *assumes* a single target is
wrong whenever such a fan-out exists:

* a **scalar subquery** returning the target fails at execution on
  PostgreSQL, SQL Server, DuckDB and most other engines ("more than one
  row returned by a subquery used as an expression");
* ``LIMIT 1`` / ``TOP 1`` on the mapping lookup silently keeps an
  arbitrary target and drops the rest.

Retaining every target through a JOIN or ``IN (SELECT …)`` is correct: it
is the only way to keep the mapping complete; the duplicate intermediate
rows it creates are collapsed by ``COUNT(DISTINCT …)`` or by the set-valued
consumer. A scalar subquery that aggregates without GROUP BY
(``SELECT MIN(concept_id_2) …``) returns one row by construction and makes
the choice deterministic, so it is not flagged.

Violation patterns:
    -- WRONG: scalar subquery assumes one mapping
    SELECT c.concept_id,
           (SELECT cr.concept_id_2 FROM concept_relationship cr
            WHERE cr.concept_id_1 = c.concept_id
              AND cr.relationship_id = 'Maps to') AS standard_id
    FROM concept c

    -- WRONG: LIMIT 1 picks an arbitrary target
    SELECT concept_id_2 FROM concept_relationship
    WHERE concept_id_1 = 12345 AND relationship_id = 'Maps to'
    LIMIT 1

Correct patterns:
    -- JOIN keeps every target
    SELECT c.concept_id, cr.concept_id_2
    FROM concept c
    JOIN concept_relationship cr ON cr.concept_id_1 = c.concept_id
    WHERE cr.relationship_id = 'Maps to'

    -- IN (…) is set-valued
    SELECT * FROM condition_occurrence
    WHERE condition_concept_id IN (
        SELECT concept_id_2 FROM concept_relationship
        WHERE concept_id_1 = 12345 AND relationship_id = 'Maps to')
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.deduplication import scope_key
from fastssv.core.references import BOOK_VOCABULARIES, section
from fastssv.core.helpers import has_table_reference, normalize_name, parse_sql, relationship_filters, side_reads
from fastssv.core.registry import register

from ._cs_scope import int_value

CONCEPT_RELATIONSHIP = "concept_relationship"
RELATIONSHIP_ID = "relationship_id"
MAPS_TO_VALUES = {"maps to", "mapped from"}

# Positions where a subquery is set-valued: membership and existence tests.
_SET_VALUED_PARENTS = (exp.In, exp.Exists, exp.Any, exp.All)
_MAPPING_TARGETS = {"concept_id_1", "concept_id_2"}


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _projects_mapping_target(select: exp.Expression, maps_to: Dict[Tuple[int, str], Set[Optional[str]]]) -> bool:
    """Some output column of ``select`` carries a concept_relationship id from an instance restricted to 'Maps to' /
    'Mapped from' (followed through CTEs and derived tables by lineage, and through a concept row joined in the same
    SELECT)."""
    if not isinstance(select, exp.Select):
        return False

    def is_target(read) -> bool:
        reads_select, alias, table, column = read
        if table != CONCEPT_RELATIONSHIP or column not in _MAPPING_TARGETS:
            return False
        return bool(maps_to.get((id(reads_select), alias), set()) & MAPS_TO_VALUES)

    for projection in select.expressions or []:
        expr = projection.this if isinstance(projection, exp.Alias) else projection
        if expr.find(exp.AggFunc) is not None:
            continue
        for col in expr.find_all(exp.Column):
            if any(is_target(r) for r in side_reads(col, relay=False)):
                return True
            # a concept row joined to cr.concept_id_2 in this SELECT carries the target; a concept row looked up by
            # a scalar subquery does not (that subquery is judged on its own)
            if any(is_target(r) and r[0] is select for r in side_reads(col, relay=True)):
                return True
    return False


def _is_scalar_position(subquery: exp.Subquery) -> bool:
    """True when the subquery's value is consumed as a single scalar (SELECT list, comparison, arithmetic)."""
    if isinstance(subquery.parent, (exp.From, exp.Join)):
        return False  # a row source, not a value
    parent = subquery.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, _SET_VALUED_PARENTS):
            return False
        parent = parent.parent
    return True


def _single_row_aggregate(query: exp.Expression) -> bool:
    """An aggregate without GROUP BY (``SELECT MIN(concept_id_2) ...``) returns exactly one row."""
    return (
        isinstance(query, exp.Select)
        and query.args.get("group") is None
        and bool(query.expressions)
        and all(e.find(exp.AggFunc) is not None for e in query.expressions)
    )


def _row_cap_is_one(select: exp.Select) -> bool:
    """``LIMIT 1``, ``TOP 1`` or ``FETCH FIRST 1 ROW ONLY``."""
    limit = select.args.get("limit")
    if isinstance(limit, exp.Limit):
        value = int_value(limit.expression)
    elif isinstance(limit, exp.Fetch):
        value = int_value(limit.args.get("count"))
    else:
        value = None
    return value == 1


def _distinct_on_without_order(select: exp.Select) -> bool:
    distinct = select.args.get("distinct")
    return (
        isinstance(distinct, exp.Distinct) and distinct.args.get("on") is not None and select.args.get("order") is None
    )


@register
class MultipleMapsToTargetsRule(Rule):
    """Detect SQL that assumes a 'Maps to' lookup returns exactly one target."""

    rule_id = "concept_standardization.multiple_maps_to_targets"
    name = "Multiple Maps To Targets Not Handled"
    description = (
        "A source concept can map to several standard concepts via 'Maps to'. "
        "A scalar subquery or LIMIT 1 on the mapping lookup assumes exactly one target: "
        "the scalar form fails at execution on a fan-out, LIMIT 1 silently drops targets."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the scalar subquery or `LIMIT 1` WITH a `JOIN concept_relationship cr ON … "
        "AND cr.relationship_id = 'Maps to'` (or `IN (SELECT concept_id_2 …)`) so every target is kept."
    )
    long_description = (
        "Not every source concept maps 1:1 to a standard concept: one ICD-10 "
        "code can split into multiple SNOMED concepts, and one NDC can fan out "
        "to several RxNorm ingredients. A scalar subquery that returns "
        "concept_id_2 raises 'more than one row returned' on most engines as "
        "soon as such a code is encountered; LIMIT 1 keeps an arbitrary target "
        "and silently discards the others. Joins and IN-subqueries retain every "
        "target and are the correct shape; duplicate intermediate rows they "
        "create are harmless under COUNT(DISTINCT …) or set-valued consumers "
        "and are not flagged. Neither is a scalar subquery that aggregates "
        "without GROUP BY (e.g. MIN(concept_id_2)): it returns one row and "
        "picks the target deterministically."
    )
    example_bad = (
        "SELECT c.concept_id,\n"
        "       (SELECT cr.concept_id_2 FROM concept_relationship cr\n"
        "        WHERE cr.concept_id_1 = c.concept_id\n"
        "          AND cr.relationship_id = 'Maps to') AS standard_id\n"
        "FROM concept c;"
    )
    example_good = (
        "SELECT c.concept_id, cr.concept_id_2 AS standard_id\n"
        "FROM concept c\n"
        "JOIN concept_relationship cr ON cr.concept_id_1 = c.concept_id\n"
        "WHERE cr.relationship_id = 'Maps to';"
    )
    references = (
        section(BOOK_VOCABULARIES, "conceptMapping", "Mapping relationships"),
        Reference(
            "PostgreSQL documentation: Value Expressions, Scalar Subqueries",
            "https://www.postgresql.org/docs/current/sql-expressions.html#SQL-SYNTAX-SCALAR-SUBQUERIES",
        ),
        Reference(
            "PostgreSQL documentation: SELECT, LIMIT clause",
            "https://www.postgresql.org/docs/current/sql-select.html#SQL-LIMIT",
        ),
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT cr.concept_id_2 AS standard_concept_id\n"
                "FROM concept c\n"
                "JOIN concept_relationship cr ON cr.concept_id_1 = c.concept_id\n"
                "WHERE c.vocabulary_id = 'ICD9CM'\n"
                "  AND c.concept_code = '070.43'\n"
                "  AND cr.relationship_id = 'Maps to'\n"
                "LIMIT 1;"
            ),
            severity=Severity.WARNING,
            note="LIMIT 1 keeps one arbitrary target (ICD9CM 070.43 maps to two SNOMED concepts)",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "concept_relationship" not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree or not has_table_reference(tree, CONCEPT_RELATIONSHIP):
                continue
            maps_to = relationship_filters(tree)
            scalar_bodies: Set[int] = set()

            for subquery in tree.find_all(exp.Subquery):
                sub_tree = subquery.this
                if not sub_tree or not _is_scalar_position(subquery):
                    continue
                branches = [sub_tree] if isinstance(sub_tree, exp.Select) else list(sub_tree.find_all(exp.Select))
                # a LIMIT 1 body returns at most one row, so it cannot fail; the LIMIT finding below covers it
                if not any(
                    _projects_mapping_target(b, maps_to) and not _single_row_aggregate(b) and not _row_cap_is_one(b)
                    for b in branches
                ):
                    continue
                key = f"scalar_{subquery.sql()}"
                if key in seen:
                    continue
                seen.add(key)
                scalar_bodies.update(id(b) for b in branches)
                violations.append(
                    self.create_violation(
                        message=(
                            "Scalar subquery returns a 'Maps to' target as a single value. A source "
                            "concept can map to several standard concepts, and the subquery fails at "
                            "execution ('more than one row returned') whenever one does."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=(
                            "REWRITE: the scalar subquery as `JOIN concept_relationship cr ON … AND "
                            "cr.relationship_id = 'Maps to'` so every target is kept."
                        ),
                        details={"context": subquery.sql()},
                    )
                )

            for select in tree.find_all(exp.Select):
                if id(select) in scalar_bodies or not _projects_mapping_target(select, maps_to):
                    continue
                capped = _row_cap_is_one(select)
                if not capped and not _distinct_on_without_order(select):
                    continue
                key = f"one_target_{select.sql()}"
                if key in seen:
                    continue
                seen.add(key)
                what = "LIMIT 1" if capped else "DISTINCT ON without ORDER BY"
                holder = select.parent
                while isinstance(holder, (exp.Paren, exp.SetOperation)):
                    holder = holder.parent
                where = (
                    " in a scalar subquery" if isinstance(holder, exp.Subquery) and _is_scalar_position(holder) else ""
                )
                violations.append(
                    self.create_violation(
                        message=(
                            f"{what} on a 'Maps to' lookup{where} keeps one arbitrary target and silently "
                            "drops the others when a source concept maps to several standard concepts."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=(
                            f"REMOVE: `{what.split(' without')[0]}` and keep every target, OR REPLACE WITH explicit "
                            "aggregation (e.g. `MIN(concept_id_2)`) so the choice is deterministic."
                        ),
                        details={"context": select.sql(), **({"limit_scope": scope_key(select)} if capped else {})},
                    )
                )

        return violations


__all__ = ["MultipleMapsToTargetsRule"]

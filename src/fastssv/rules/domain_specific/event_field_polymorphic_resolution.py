"""Event-Field Polymorphic Resolution Rule.

OMOP CDM v5.4 has four ``*_event_id`` polymorphic foreign keys whose
target table is identified by a sibling ``*_event_field_concept_id`` on
the same row:

    note.note_event_id              → identified by note.note_event_field_concept_id
    observation.observation_event_id → identified by observation.obs_event_field_concept_id
    measurement.measurement_event_id → identified by measurement.meas_event_field_concept_id
    episode_event.event_id          → identified by episode_event.episode_event_field_concept_id

Each ``*_event_id`` is an INTEGER that points into a *different* clinical
table depending on the field-concept value. Joining or filtering on the
event_id without restricting the field_concept_id mixes IDs from disjoint
sequences — the join either matches nothing (the value lives in another
table's PK sequence) or matches by coincidence.

This is the same anti-pattern that ``cost.cost_event_id`` and
``location_history.entity_id`` rules enforce. One parameterized rule
covers all four v5.4 cases.
"""

from typing import Dict, List, Optional, Tuple

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


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


# (table, polymorphic_event_id_column, field_concept_id_column)
TARGETS: List[Tuple[str, str, str]] = [
    ("note", "note_event_id", "note_event_field_concept_id"),
    ("observation", "observation_event_id", "obs_event_field_concept_id"),
    ("measurement", "measurement_event_id", "meas_event_field_concept_id"),
    ("episode_event", "event_id", "episode_event_field_concept_id"),
]


def _is_target_column(
    col: exp.Column,
    aliases: Dict[str, str],
    target_table: str,
    target_col: str,
) -> bool:
    """True if ``col`` resolves to ``target_table.target_col`` (qualified or
    unqualified-with-target-as-sole-table-in-scope).
    """
    table, col_name = resolve_table_col(col, aliases)
    if _norm(col_name) != _norm(target_col):
        return False
    if table:
        return _norm(table) == _norm(target_table)
    real_tables = {_norm(t) for t in aliases.values()}
    return real_tables == {_norm(target_table)}


def _references_event_id(
    tree: exp.Expression,
    aliases: Dict[str, str],
    table: str,
    event_id_col: str,
) -> List[str]:
    """Return SQL fragments where ``table.event_id_col`` is used inside a
    JOIN ON or WHERE clause. Empty list means it isn't being used to link
    or filter, so the rule shouldn't fire.
    """
    found: List[str] = []
    for col in tree.find_all(exp.Column):
        if not _is_target_column(col, aliases, table, event_id_col):
            continue
        if not is_in_where_or_join_clause(col):
            continue
        found.append(col.sql())
    return found


def _has_field_concept_filter(
    tree: exp.Expression,
    aliases: Dict[str, str],
    table: str,
    field_concept_col: str,
) -> bool:
    """True if the query restricts ``table.field_concept_col`` via ``=``,
    ``IN (...)``, or ``IS NOT NULL`` in a WHERE / JOIN ON clause.
    """
    for node in tree.walk():
        # x.field_concept_id = <int> / IS NOT NULL
        if isinstance(node, exp.EQ):
            if not is_in_where_or_join_clause(node):
                continue
            left = node.this
            if isinstance(left, exp.Column) and _is_target_column(
                left, aliases, table, field_concept_col
            ):
                right = node.expression
                if isinstance(right, exp.Literal) and not right.is_string:
                    return True
        elif isinstance(node, exp.In):
            if not is_in_where_or_join_clause(node):
                continue
            left = node.this
            if isinstance(left, exp.Column) and _is_target_column(
                left, aliases, table, field_concept_col
            ):
                vals = node.expressions or []
                if any(isinstance(v, exp.Literal) and not v.is_string for v in vals):
                    return True
        elif isinstance(node, exp.Is):
            if not is_in_where_or_join_clause(node):
                continue
            left = node.this
            right = node.expression
            if (
                isinstance(left, exp.Column)
                and _is_target_column(left, aliases, table, field_concept_col)
                and isinstance(right, (exp.Null, exp.Not))
            ):
                # `x IS NOT NULL` or `x IS NULL` — both narrow polymorphic
                # resolution (NULL means "no event link"; non-null means
                # there is one and the analyst is opting in).
                return True
    return False


@register
class EventFieldPolymorphicResolutionRule(Rule):
    """Require ``*_event_field_concept_id`` filter when ``*_event_id`` is joined."""

    rule_id = "domain_specific.event_field_polymorphic_resolution"
    name = "Event-Field Polymorphic Resolution"

    description = (
        "OMOP v5.4 polymorphic FKs (note.note_event_id, observation.observation_event_id, "
        "measurement.measurement_event_id, episode_event.event_id) require their sibling "
        "*_event_field_concept_id to be filtered. Without the filter the join mixes IDs "
        "from disjoint sequences."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Restrict the *_event_field_concept_id column to a specific concept "
        "(or `IS NOT NULL`) before using the *_event_id column in a JOIN or WHERE. "
        "Example: `WHERE n.note_event_field_concept_id IS NOT NULL` for note, or "
        "filter to the specific field concept that identifies the target table."
    )

    long_description = (
        "Each ``*_event_id`` column in OMOP v5.4 is a polymorphic foreign key — "
        "an INTEGER that points at a row in a different clinical table "
        "depending on the value of the sibling ``*_event_field_concept_id`` "
        "column. With no filter on the field-concept side, the analyst "
        "joins integer IDs from disjoint sequences: a "
        "``measurement_event_id`` of 1234 might happen to equal a "
        "``visit_occurrence_id`` of 1234 in the joined table, but the row "
        "doesn't refer to that visit at all. Filter the field-concept "
        "column first to identify which target table the event_id "
        "addresses, then join. The same discipline is enforced for "
        "``cost.cost_event_id`` and ``location_history.entity_id``."
    )

    example_bad = (
        "SELECT m.measurement_id, vo.visit_concept_id\n"
        "FROM measurement m\n"
        "JOIN visit_occurrence vo ON m.measurement_event_id = vo.visit_occurrence_id;"
    )
    example_good = (
        "SELECT m.measurement_id, vo.visit_concept_id\n"
        "FROM measurement m\n"
        "JOIN visit_occurrence vo ON m.measurement_event_id = vo.visit_occurrence_id\n"
        "WHERE m.meas_event_field_concept_id IS NOT NULL;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if not any(t in sql_lower for t, _, _ in TARGETS):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: set = set()

        for tree in trees:
            if not tree:
                continue
            aliases = extract_aliases(tree)

            for table, event_id_col, field_concept_col in TARGETS:
                if not has_table_reference(tree, table):
                    continue

                refs = _references_event_id(tree, aliases, table, event_id_col)
                if not refs:
                    continue
                if _has_field_concept_filter(tree, aliases, table, field_concept_col):
                    continue

                for ref in refs:
                    key = (table, event_id_col, ref)
                    if key in seen:
                        continue
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                f"`{ref}` (in {table}) used in a JOIN/WHERE without "
                                f"a {field_concept_col} filter. {table}.{event_id_col} "
                                f"is a polymorphic FK; without restricting "
                                f"{field_concept_col}, the join either matches "
                                f"nothing or matches by coincidence."
                            ),
                            details={
                                "table": table,
                                "event_id_column": event_id_col,
                                "field_concept_column": field_concept_col,
                            },
                        )
                    )

        return violations


__all__ = ["EventFieldPolymorphicResolutionRule"]

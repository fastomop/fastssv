"""Event-Field Polymorphic Resolution Rule.

CDM v5.4 has four keys that hold "the primary key of the linked record" in whichever table a sibling field concept
names ("the CONCEPT_ID that identifies which table the primary key of the linked record came from"):

    measurement.measurement_event_id   qualified by meas_event_field_concept_id
    observation.observation_event_id   qualified by obs_event_field_concept_id
    note.note_event_id                 qualified by note_event_field_concept_id
    episode_event.event_id             qualified by episode_event_field_concept_id

The field concepts are the CDM vocabulary's 'Field' concepts named ``<table>.<primary key>``
(``condition_occurrence.condition_occurrence_id`` is 1147127 and 1147663).

For every comparison that matches such a key with a column, an ``IN`` subquery or a literal, the rule collects the
restrictions of the field concept that hold for the same row (traced through CTEs, derived tables and casts; scoped
to their OR operand and to their outer join; never taken from another read of the table). It uses the restriction
parser shared with ``domain_specific.cost_event_id_polymorphic_resolution``.

- ERROR: the key is matched with a table's primary key while the field concept is restricted to the field concept
  of another table's primary key, or excludes every field concept of the matched one. Every returned row then pairs
  a record with an unrelated row that happens to share its id.
- WARNING: no restriction (wrong only where the column links records of several tables), a restriction to ids that
  are not CDM field concepts, or a list that also admits other tables.

A restriction whose value the SQL does not show (parameter, scalar subquery, a field concept chosen by name through a
join to ``concept``) is accepted; NULL tests on the key are not reported.
"""

from typing import List, Optional, Set, Tuple

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import cdm_table
from fastssv.core.registry import register

from ._ds1_cdm_facts import EVENT_FIELD_KEYS, PRIMARY_KEY_FIELD_CONCEPTS
from ._ds1_polymorphic_keys import EXCLUDED, OPAQUE, VALUES, KeyUse, Restriction, key_uses

_PK_FIELD_CONCEPTS = {cid: table for table, (_, ids) in PRIMARY_KEY_FIELD_CONCEPTS.items() for cid in ids}


def _ids(r: Restriction) -> Optional[Set[int]]:
    out = set()
    for text, _ in r.literals:
        try:
            out.add(int(text.strip()))
        except ValueError:
            return None
    return out


def _target(use: KeyUse) -> Optional[Tuple[str, str, Tuple[int, ...]]]:
    for table, column in sorted(use.targets):
        info = PRIMARY_KEY_FIELD_CONCEPTS.get(table)
        if info and info[0] == column:
            return table, column, info[1]
    return None


def _judge(use: KeyUse, table: str, field: str) -> Optional[Tuple[str, Severity]]:
    if any(r.kind == OPAQUE for r in use.restrictions):
        return None
    positive = [r for r in use.restrictions if r.kind != EXCLUDED]
    excluded = [r for r in use.restrictions if r.kind == EXCLUDED]
    target = _target(use)
    if any(r.kind != VALUES or _ids(r) is None for r in positive):
        return None  # a pattern or a non-numeric literal: no field concept the rule can read
    if target is None:
        if positive:
            return None
        return (
            f"`{use.text}` (in {table}) is used without restricting {field}. The key holds the primary key of a row "
            f"in whichever table {field} names, so where {table} links records of several tables the same id can "
            f"denote unrelated records.",
            Severity.WARNING,
        )
    other, column, expected = target
    pair = f"`{use.text}` (in {table}) is matched with {other}.{column}"
    wrong = (
        f"The field concept of {other}.{column} is {' or '.join(map(str, expected))}, so every row this returns pairs "
        f"a {table} record with an unrelated {other} row that shares the linked record's id."
    )
    for r in excluded:
        ids = _ids(r) or set()
        if set(expected) <= ids:
            shown = ", ".join(map(str, sorted(ids)))
            return f"{pair}, but {field} is restricted to exclude {shown}. {wrong}", Severity.ERROR
    if not positive:
        return (
            f"{pair} without restricting {field} to {' or '.join(map(str, expected))}. The key holds the primary key "
            f"of a row in whichever table {field} names; where {table} links records of several tables, records "
            f"linked to other tables whose id equals an {other} id are joined too.",
            Severity.WARNING,
        )
    for r in positive:
        ids = _ids(r)
        if ids & set(expected):
            continue
        named = sorted({_PK_FIELD_CONCEPTS[i] for i in ids if i in _PK_FIELD_CONCEPTS})
        shown = ", ".join(map(str, sorted(ids)))
        if named and all(i in _PK_FIELD_CONCEPTS for i in ids):
            return (
                f"{pair}, but {field} is restricted to {shown}, the field concept of {', '.join(named)}. {wrong}",
                Severity.ERROR,
            )
        return (
            f"{pair}, but {field} is restricted to {shown}, which is not the CDM field concept of "
            f"{other}.{column} ({' or '.join(map(str, expected))}). Unless the CDM uses its own concept for that "
            f"field, the join returns only unrelated rows.",
            Severity.WARNING,
        )
    for r in positive:
        extra = sorted(i for i in _ids(r) if i not in expected)
        if extra and not any(_ids(q) <= set(expected) for q in positive):
            return (
                f"{field} is restricted to {', '.join(map(str, sorted(_ids(r))))} while {pair}; records linked to "
                f"the other listed tables whose id equals an {other} id are joined too.",
                Severity.WARNING,
            )
    return None


@register
class EventFieldPolymorphicResolutionRule(Rule):
    """A ``*_event_id`` key matched without, or against, the field concept of the table it is matched with."""

    rule_id = "domain_specific.event_field_polymorphic_resolution"
    name = "Event-Field Polymorphic Resolution"
    description = (
        "measurement.measurement_event_id, observation.observation_event_id, note.note_event_id and "
        "episode_event.event_id hold the primary key of a row in whichever table their *_event_field_concept_id "
        "names. Matching one with a table while the field concept names another table pairs unrelated rows (ERROR); "
        "matching it without a field-concept restriction is wrong wherever the column links several tables "
        "(WARNING)."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "ADD: a restriction on the same row (or correct the existing one): `<x>_event_field_concept_id = <id>`, where <id> is the CDM "
        "'Field' concept named `<table>.<primary key>` of the table matched with the key (condition_occurrence: "
        "1147127, visit_occurrence: 1147070, drug_exposure: 1147094, procedure_occurrence: 1147082, measurement: "
        "1147138). Look it up with `SELECT concept_id FROM concept WHERE vocabulary_id = 'CDM' AND concept_class_id "
        "= 'Field' AND concept_name = '<table>.<primary key>'`."
    )
    long_description = (
        "Each *_event_id key holds the primary key of the linked record, and the sibling *_event_field_concept_id "
        "names the table that key came from. A measurement linked to visit 5 and a condition_occurrence with id 5 "
        "have nothing to do with each other, so joining measurement_event_id with condition_occurrence_id while the "
        "field concept names visit_occurrence returns only coincidental pairs. Without any field-concept "
        "restriction the join is right only while every linked record lives in one table, so that case is a "
        "warning. `IS NOT NULL` on the field concept does not say which table the key points into."
    )
    example_bad = (
        "SELECT m.measurement_concept_id, m.value_as_number, co.condition_concept_id\n"
        "FROM measurement m\n"
        "JOIN condition_occurrence co\n"
        "  ON m.measurement_event_id = co.condition_occurrence_id\n"
        " AND m.meas_event_field_concept_id = 1147070  -- visit_occurrence.visit_occurrence_id\n;"
    )
    example_good = (
        "SELECT m.measurement_concept_id, m.value_as_number, co.condition_concept_id\n"
        "FROM measurement m\n"
        "JOIN condition_occurrence co\n"
        "  ON m.measurement_event_id = co.condition_occurrence_id\n"
        " AND m.meas_event_field_concept_id = 1147127  -- condition_occurrence.condition_occurrence_id\n;"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT e.episode_id, co.condition_concept_id\n"
                "FROM episode_event ee\n"
                "JOIN episode e ON ee.episode_id = e.episode_id\n"
                "JOIN condition_occurrence co ON ee.event_id = co.condition_occurrence_id;"
            ),
            severity=Severity.WARNING,
            note="episode_event.event_id joined without an episode_event_field_concept_id restriction",
        ),
    )
    references = (
        cdm_table("measurement"),
        cdm_table("observation"),
        cdm_table("note"),
        cdm_table("episode_event"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = (sql or "").lower()
        if not any(t in lowered and key in lowered for t, (key, _) in EVENT_FIELD_KEYS.items()):
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        seen = set()
        for tree in trees:
            if tree is None:
                continue
            for table, (key, field) in sorted(EVENT_FIELD_KEYS.items()):
                if table not in lowered:
                    continue
                for use in key_uses(tree, table, key, field):
                    verdict = _judge(use, table, field)
                    if verdict is None or verdict[0] in seen:
                        continue
                    seen.add(verdict[0])
                    message, severity = verdict
                    violations.append(
                        self.create_violation(
                            message=message,
                            severity=severity,
                            details={"table": table, "event_id_column": key, "field_concept_column": field},
                        )
                    )
        return violations


__all__ = ["EventFieldPolymorphicResolutionRule"]

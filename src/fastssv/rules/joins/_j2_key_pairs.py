"""Findings for predicates that equate keys of two different CDM entities.

Each CDM v5.4 key column identifies one entity: a primary key identifies its own table's rows, and a foreign key the
rows of the table it references (field-level specification: isPrimaryKey, isForeignKey, fkTableName). Two columns
that identify different entities hold values from unrelated id sequences, so equating them matches rows that have
nothing to do with each other on every conformant CDM. Polymorphic keys (``fact_id_1``, ``cost_event_id``,
``cohort.subject_id``, ...) have no declared target and are never judged.

Several joins rules report such predicates; ``reporting_rule`` gives each predicate to exactly one of them.
"""

from typing import Callable, List, Optional, Tuple, Union

from fastssv.core.base import Rule, RuleViolation
from fastssv.core.deduplication import predicate_key
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import freeform, replace

from ._cdm_keys import COLUMNS, ENTITY, PRIMARY_KEY
from ._lineage import (
    KeyMismatch,
    described,
    key_mismatches,
    locate_equality,
    origin,
    reads_base_table_directly,
    reporting_rule,
    with_column,
    written,
)


def _entity_name(entity: Tuple[str, str]) -> str:
    return f"{entity[0]}.{entity[1]}"


def _column_referencing(table: str, target: Tuple[str, str]) -> Optional[str]:
    """The column of ``table`` that identifies ``target``: its primary key when that is the target, else the one
    foreign key to it (None when there are several, e.g. parent_ and preceding_visit_detail_id)."""
    if PRIMARY_KEY.get(table) and ENTITY.get((table, PRIMARY_KEY[table])) == target:
        return PRIMARY_KEY[table]
    found = [c for c in COLUMNS.get(table, ()) if ENTITY.get((table, c)) == target]
    return found[0] if len(found) == 1 else None


Entity = Tuple[str, str]
KeepEntity = Union[None, Entity, Callable[[KeyMismatch], Optional[Entity]]]


def _patch(sql: str, m: KeyMismatch, keep_entity: KeepEntity, fallback: str) -> dict:
    """REPLACE one side with the column of the same row source that holds the other side's key. The side kept by
    preference is a primary key facing a foreign key (``co.visit_occurrence_id = vd.visit_detail_id`` keeps
    ``vd.visit_detail_id``), else the side holding ``keep_entity`` (an entity, or a function of the predicate); when
    the other row source has no column for that key, the kept side is rewritten instead (``p.person_id =
    l.location_id`` becomes ``p.location_id = l.location_id``). Only a column read straight from a base table is
    rewritten, so the replacement column is known to exist; anything else gets advice."""
    sides = [(m.left, m.left_origin, m.left_entity), (m.right, m.right_origin, m.right_entity)]

    def is_pk(source: Tuple[str, str]) -> bool:
        return PRIMARY_KEY.get(source[0]) == source[1]

    if callable(keep_entity):
        keep_entity = keep_entity(m)
    pk_flags = [is_pk(src) for _, src, _ in sides]
    if pk_flags[0] != pk_flags[1]:
        keep = 0 if pk_flags[0] else 1
    elif keep_entity is not None and keep_entity in (m.left_entity, m.right_entity):
        keep = 0 if m.left_entity == keep_entity else 1
    else:
        return freeform(fallback)
    located = locate_equality(sql, m.left, m.right)
    if located is None:
        return freeform(fallback)
    for change in (1 - keep, keep):
        col, src, _ = sides[change]
        new_name = _column_referencing(src[0], sides[1 - change][2])
        if new_name is None or not reads_base_table_directly(col):
            continue
        span, swapped = located
        new_side = with_column(col, new_name)
        left_text = new_side if change == 0 else written(m.left)
        right_text = new_side if change == 1 else written(m.right)
        text = f"{right_text} = {left_text}" if swapped else f"{left_text} = {right_text}"
        return replace(span, text)
    return freeform(fallback)


def key_pair_violations(
    rule: Rule,
    sql: str,
    dialect: str,
    keep_entity: KeepEntity,
    explain: Callable[[KeyMismatch], str],
    prefilter: str,
) -> List[RuleViolation]:
    """One finding per predicate that equates keys of different entities and that ``reporting_rule`` assigns to
    ``rule``."""
    if prefilter not in sql.lower():
        return []
    trees, err = parse_sql(sql, dialect)
    if err:
        return []
    violations: List[RuleViolation] = []
    seen = set()
    for tree in trees:
        if tree is None:
            continue
        for m in key_mismatches(tree):
            if reporting_rule(m) != rule.rule_id or id(m.node) in seen:
                continue
            seen.add(id(m.node))
            left, right = described(m.left, m.left_origin), described(m.right, m.right_origin)
            fallback = (
                f"REPLACE: `{written(m.left)} = {written(m.right)}` WITH an equality between two columns that "
                f"identify the same entity (e.g. both {_entity_name(m.left_entity)}, or both "
                f"{_entity_name(m.right_entity)})."
            )
            message = (
                f"{left} = {right} equates a {_entity_name(m.left_entity)} key with a "
                f"{_entity_name(m.right_entity)} key. The two come from unrelated id sequences, so the predicate "
                f"matches unrelated rows. {explain(m)}"
            )
            violations.append(
                rule.create_violation(
                    message=message,
                    suggested_fix_patch=_patch(sql, m, keep_entity, fallback),
                    details={
                        "type": "key_entity_mismatch",
                        "left_column": f"{m.left_origin[0]}.{m.left_origin[1]}",
                        "right_column": f"{m.right_origin[0]}.{m.right_origin[1]}",
                        "left_entity": _entity_name(m.left_entity),
                        "right_entity": _entity_name(m.right_entity),
                        "predicate": predicate_key(m.node),
                    },
                )
            )
    return violations


__all__ = ["key_pair_violations", "origin"]

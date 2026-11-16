"""Concept Ancestor Max Levels Misuse Rule.

concept_ancestor has one row per (ancestor, descendant) pair. ``min_levels_of_separation`` is the shortest path between
them and ``max_levels_of_separation`` the longest (Book of OHDSI 5.4: "defining the shortest or longest possible
connection"). A concept with several parents can be a direct child of an ancestor (min = 1) and also reach it through a
longer path (max > 1).

Restricting ``max_levels_of_separation`` to exactly 1 therefore keeps only the children that have *no* longer path,
and drops direct children that also descend through another branch. In Athena v5.0 27-AUG-25, ancestor 201820
'Diabetes mellitus' has 24 descendants with min_levels_of_separation = 1 and 21 with max_levels_of_separation = 1.
"Direct children" is normally written ``min_levels_of_separation = 1``.

The rule is a WARNING: "children with no longer path" is a legitimate question, and the query runs. It recognises the
restriction however it is written (``= 1``, ``IN (1)``, ``BETWEEN 1 AND 1``, ``> 0 AND < 2``, casts, quoted
literals), in a filter or a CASE label, and stays silent when the same concept_ancestor row is also restricted on
min_levels_of_separation (``min = 1 AND max = 1`` asks deliberately for single-path children). Other values of
max_levels_of_separation are not reported: equality on a longest-path length is well defined.
"""

import math
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.references import BOOK_VOCABULARIES, cdm_table, section
from fastssv.core.registry import register

from ._cs_scope import ColumnFilter, InstanceKey, instance_filters

CONCEPT_ANCESTOR = "concept_ancestor"
MAX_LEVELS = "max_levels_of_separation"
MIN_LEVELS = "min_levels_of_separation"


def _num(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _integer_range(filters: List[ColumnFilter]) -> Optional[Tuple[float, float]]:
    """The integer interval the predicates of one AND-chain allow, or None when one of them is not a bound."""
    low, high = -math.inf, math.inf
    allowed: Optional[Set[float]] = None
    for flt in filters:
        values = [_num(v) for v in flt.values]
        if not flt.complete or any(v is None for v in values):
            return None
        if flt.op == "eq":
            allowed = {values[0]} if allowed is None else allowed & {values[0]}
        elif flt.op == "in":
            allowed = set(values) if allowed is None else allowed & set(values)
        elif flt.op == "between":
            low, high = max(low, math.ceil(values[0])), min(high, math.floor(values[1]))
        elif flt.op == "gt":
            low = max(low, math.floor(values[0]) + 1)
        elif flt.op == "gte":
            low = max(low, math.ceil(values[0]))
        elif flt.op == "lt":
            high = min(high, math.ceil(values[0]) - 1)
        elif flt.op == "lte":
            high = min(high, math.floor(values[0]))
        else:
            return None
    if allowed is not None:
        allowed = {v for v in allowed if low <= v <= high}
        if len(allowed) != 1:
            return None
        only = next(iter(allowed))
        return only, only
    return low, high


def _equality_patch(sql: str, flt: ColumnFilter) -> Optional[dict]:
    """REPLACE the column in a plain ``max_levels_of_separation = 1`` with min_levels_of_separation."""
    if flt.op != "eq" or not isinstance(flt.node, exp.EQ):
        return None
    span = locate(sql, flt.node.sql())
    if span is None:
        return None
    swapped = flt.node.copy()
    for col in swapped.find_all(exp.Column):
        if col.name.lower() == MAX_LEVELS:
            col.set("this", exp.to_identifier(MIN_LEVELS))
    return patch_replace(span, swapped.sql())


@register
class ConceptAncestorMaxLevelsMisuseRule(Rule):
    """max_levels_of_separation restricted to exactly 1, which drops direct children that also have a longer path."""

    rule_id = "concept_standardization.concept_ancestor_max_levels_misuse"
    name = "Concept Ancestor Max Levels Misuse"
    description = (
        "max_levels_of_separation is restricted to exactly 1. It is the longest path, so direct children that are "
        "also reachable through a longer path are dropped; min_levels_of_separation = 1 is the usual way to ask for "
        "direct children (WARNING: the narrower set may be intended)."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: `max_levels_of_separation = 1` WITH `min_levels_of_separation = 1` for direct children, unless you "
        "want only children with no longer path."
    )
    long_description = (
        "concept_ancestor has one row per ancestor/descendant pair, with the shortest path in "
        "min_levels_of_separation and the longest in max_levels_of_separation. A concept with several parents can be "
        "a direct child (min = 1) and also descend through a longer branch (max > 1). Restricting "
        "max_levels_of_separation to 1 keeps only children with no longer path: for 'Diabetes mellitus' (201820) in "
        "the Athena v5.0 27-AUG-25 release, 21 rows instead of the 24 with min_levels_of_separation = 1. The rule is a "
        "WARNING because that narrower set can be intended; it is silent when min_levels_of_separation is restricted "
        "on the same row, and does not report other max_levels_of_separation values."
    )
    example_bad = (
        "SELECT descendant_concept_id\n"
        "FROM concept_ancestor\n"
        "WHERE ancestor_concept_id = 201820\n"
        "  AND max_levels_of_separation = 1;"
    )
    example_good = (
        "SELECT descendant_concept_id\n"
        "FROM concept_ancestor\n"
        "WHERE ancestor_concept_id = 201820\n"
        "  AND min_levels_of_separation = 1;"
    )
    references = (
        cdm_table("concept_ancestor"),
        section(BOOK_VOCABULARIES, "conceptAncestor", "Concept ancestor"),
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT descendant_concept_id\n"
                "FROM concept_ancestor\n"
                "WHERE ancestor_concept_id = 201820\n"
                "  AND max_levels_of_separation > 0\n"
                "  AND max_levels_of_separation < 2;"
            ),
            severity=Severity.WARNING,
            note="The same restriction written as a range",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if MAX_LEVELS not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            max_filters = instance_filters(tree, CONCEPT_ANCESTOR, MAX_LEVELS)
            if not max_filters:
                continue
            min_restricted: Set[InstanceKey] = {
                key for key, flts in instance_filters(tree, CONCEPT_ANCESTOR, MIN_LEVELS).items() if flts
            }
            reported: Set[int] = set()
            for key, flts in max_filters.items():
                if key in min_restricted:
                    continue
                chains: Dict[int, List[ColumnFilter]] = {}
                for flt in flts:
                    chains.setdefault(flt.conjunction, []).append(flt)
                for chain in chains.values():
                    if id(chain[0].node) in reported or _integer_range(chain) != (1, 1):
                        continue
                    reported.update(id(f.node) for f in chain)
                    shown = " AND ".join(f.sql for f in chain)
                    violations.append(
                        self.create_violation(
                            message=(
                                f"`{shown}` keeps only descendants whose longest path to the ancestor is one step. "
                                "Direct children that are also reachable through a longer path are dropped. Use "
                                "min_levels_of_separation = 1 for all direct children, unless only single-path "
                                "children are intended."
                            ),
                            severity=Severity.WARNING,
                            suggested_fix_patch=_equality_patch(sql, chain[0]) if len(chain) == 1 else None,
                            details={"predicate": shown},
                        )
                    )
        return violations


__all__ = ["ConceptAncestorMaxLevelsMisuseRule"]

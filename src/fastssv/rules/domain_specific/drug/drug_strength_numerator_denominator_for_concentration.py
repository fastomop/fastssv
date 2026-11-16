"""Drug Strength Numerator/Denominator for Concentration Rule.

CDM v5.4 conventions, Drug Strength: "Ingredient strength information is provided either as absolute amount (usually
for solid formulations) or as concentration (usually for liquid formulations)." An absolute amount is stored in
``amount_value`` / ``amount_unit_concept_id``; a concentration in ``numerator_value`` / ``numerator_unit_concept_id``
over ``denominator_unit_concept_id``, with ``amount_value`` empty. The drug-dose page adds that inhalers may put the
strength in the numerator, and the vocabulary stores rates (transdermal systems, extended-release forms) the same way.

For every read of ``drug_strength`` (one FROM / JOIN entry, followed through CTEs, derived tables and casts) the rule
reports

- ``amount_value`` used (projected, computed with, aggregated, grouped or ordered on) while ``numerator_value`` of
  the same read is never used: the value is NULL for concentration and rate rows;
- ``amount_value`` filtered (a comparison, ``IN``, ``BETWEEN``, ``LIKE`` or ``IS NOT NULL`` in WHERE, JOIN ON or
  HAVING) while no condition on the same read uses ``numerator_value``: the filter drops those rows, whatever is
  projected.

It is a WARNING: an analysis may be meant for fixed-dose forms only. A read restricted to the amount model
(``numerator_value IS NULL``, ``numerator_unit_concept_id IS NULL`` or ``denominator_unit_concept_id IS NULL``) states
that intent and is not reported, nor is ``amount_value IS NULL``, which selects the concentration rows.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import CDM_CONVENTIONS, CDM_DRUG_DOSE, cdm_table, section
from fastssv.core.registry import register

from .._ds1_scope import Hop, column_origins_with_paths, is_negated, predicate_root

DRUG_STRENGTH = "drug_strength"
AMOUNT_VALUE = "amount_value"
NUMERATOR_VALUE = "numerator_value"
AMOUNT_MODEL_MARKERS = {"numerator_value", "numerator_unit_concept_id", "denominator_unit_concept_id"}

_COMPARISONS = (
    exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.In, exp.Between, exp.Like, exp.ILike, exp.Is,
)  # fmt: skip

_CONCENTRATION_ROWS = (
    "drugs whose strength is a concentration or rate (liquids, injectables, inhalers, transdermal systems, some "
    "extended-release forms), stored in numerator_value / denominator_unit_concept_id"
)


def _comparison(col: exp.Column) -> Optional[exp.Expression]:
    node = col.parent
    while node is not None and not isinstance(node, (exp.Select, exp.Where, exp.Join, exp.Having)):
        if isinstance(node, _COMPARISONS):
            return node
        node = node.parent
    return None


def _passes_through(col: exp.Column) -> bool:
    """``col`` is projected unchanged by a CTE or derived table: its use is decided where it is read from there."""
    proj = col
    while isinstance(proj.parent, (exp.Cast, exp.TryCast, exp.Paren, exp.Alias)):
        proj = proj.parent
    select = proj.parent
    if not isinstance(select, exp.Select) or proj not in select.expressions:
        return False
    holder = select.parent
    while isinstance(holder, exp.SetOperation):
        holder = holder.parent
    return isinstance(holder, (exp.Subquery, exp.CTE))


def _reads(tree: exp.Expression) -> Dict[Hop, Dict[str, bool]]:
    """Per base read of drug_strength: which roles its columns play anywhere in the statement."""
    out: Dict[Hop, Dict[str, bool]] = {}
    for col in tree.find_all(exp.Column):
        for path, table, column in column_origins_with_paths(col):
            if table != DRUG_STRENGTH or not path:
                continue
            roles = out.setdefault(
                path[-1],
                {
                    "use": False,
                    "filter": False,
                    "numerator": False,
                    "numerator_filter": False,
                    "amount_model": False,
                    "passed_through": False,
                    "read_outside": False,
                },
            )
            in_predicate = predicate_root(col) is not None
            if column == NUMERATOR_VALUE:
                roles["numerator"] = True
                roles["numerator_filter"] |= in_predicate
            comparison = _comparison(col) if in_predicate else None
            if isinstance(comparison, exp.Is) and isinstance(comparison.expression, exp.Null):
                if not is_negated(comparison):
                    if column in AMOUNT_MODEL_MARKERS:
                        roles["amount_model"] = True
                    continue  # amount_value IS NULL keeps the concentration rows
                if column == AMOUNT_VALUE:
                    roles["filter"] = True
                continue
            if column != AMOUNT_VALUE:
                continue
            if len(path) > 1:
                roles["read_outside"] = True
            if comparison is not None:
                roles["filter"] = True
            elif not in_predicate:
                if _passes_through(col):
                    roles["passed_through"] = True
                else:
                    roles["use"] = True
    for roles in out.values():
        # Projected by a CTE / derived table and never read from it by name: the outer SELECT * returns it as is.
        if roles["passed_through"] and not roles["read_outside"]:
            roles["use"] = True
    return out


@register
class DrugStrengthNumeratorDenominatorForConcentrationRule(Rule):
    """amount_value read without numerator_value, which holds the strength of concentrations and rates."""

    rule_id = "domain_specific.drug_strength_numerator_denominator_for_concentration"
    name = "Drug Strength Completeness (Amount vs Concentration)"

    description = (
        "drug_strength stores an absolute amount in amount_value and a concentration or rate in numerator_value over "
        "denominator_unit_concept_id, with amount_value empty. Filtering only on amount_value drops the "
        "concentration rows; using only amount_value yields NULL for them."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "REPLACE: `amount_value` WITH `COALESCE(amount_value, numerator_value)` and carry its unit, "
        "`COALESCE(amount_unit_concept_id, numerator_unit_concept_id)`, together with denominator_value / "
        "denominator_unit_concept_id: a concentration's numerator is per denominator unit (e.g. 125 mg per 5 mL), "
        "so compare or compute with the units in view. If only fixed-dose forms are meant, say so with "
        "`numerator_value IS NULL`."
    )
    example_bad = "SELECT drug_concept_id, amount_value FROM drug_strength\nWHERE amount_value > 500;"
    example_good = (
        "SELECT drug_concept_id,\n"
        "       COALESCE(amount_value, numerator_value) AS strength_value,\n"
        "       COALESCE(amount_unit_concept_id, numerator_unit_concept_id) AS strength_unit_concept_id,\n"
        "       denominator_value, denominator_unit_concept_id\n"
        "FROM drug_strength;"
    )
    references = (
        section(CDM_CONVENTIONS, "drug-strength", "Drug strength"),
        CDM_DRUG_DOSE,
        cdm_table("drug_strength"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = (sql or "").lower()
        if DRUG_STRENGTH not in lowered or AMOUNT_VALUE not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, ...]] = set()
        for tree in trees:
            if tree is None:
                continue
            for roles in _reads(tree).values():
                if roles["amount_model"]:
                    continue
                if roles["use"] and not roles["numerator"] and ("use",) not in seen:
                    seen.add(("use",))
                    violations.append(
                        self.create_violation(
                            message=(
                                f"amount_value is used without numerator_value. It is NULL for {_CONCENTRATION_ROWS}."
                            ),
                            details={"type": "select"},
                        )
                    )
                if roles["filter"] and not roles["numerator_filter"] and ("filter",) not in seen:
                    seen.add(("filter",))
                    violations.append(
                        self.create_violation(
                            message=(
                                f"amount_value filtered without numerator_value alternative. This excludes "
                                f"{_CONCENTRATION_ROWS}."
                            ),
                            details={"type": "where"},
                        )
                    )
        return violations


__all__ = ["DrugStrengthNumeratorDenominatorForConcentrationRule"]

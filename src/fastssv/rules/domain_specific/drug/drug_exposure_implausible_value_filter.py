"""Drug Exposure Implausible Value Filter Rule.

CDM v5.4, DRUG_EXPOSURE.days_supply: "Negative values are not allowed" and "High values (>365 days) should be
investigated." The OHDSI Data Quality Dashboard thresholds in the CDM v5.4 field-level specification go further and
flag as implausible a ``days_supply`` outside 1 to 365 and a ``quantity`` below 0.0000001. The bounds are generated
from that specification.

A WHERE, HAVING or inner JOIN ON condition that admits *no* plausible value for one of these columns
(``days_supply = 0``, ``days_supply > 365``, ``quantity <= 0``, ``days_supply < 0 OR days_supply > 365``) keeps only
rows the thresholds flag: records the conventions forbid (negative days_supply) or ask the ETL to investigate. The
result is typically empty or made of data-quality outliers, which is what a deliberate data-quality query wants, so
the finding is a WARNING. A condition that still admits some plausible value (``days_supply > 0``,
``quantity >= 1``, ``NOT (quantity <= 0)``) is not reported, and neither is one that removes rows instead of
selecting them: under ``NOT EXISTS`` / ``NOT IN``, in the right branch of EXCEPT, or in an outer join's ON.

The condition is evaluated as a whole, so NOT, AND and OR are honoured; the column may carry a cast or a constant
offset (``days_supply - 1 < 0``). Other predicates (other columns, NULL tests, subqueries) are unknown, which can only
make the rule fire less.
"""

from typing import Callable, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_column_source,
    unwrap_cast,
)
from fastssv.core.references import DQD_FIELD_THRESHOLDS, cdm_table
from fastssv.core.registry import register

from .._ds1_cdm_facts import DRUG_EXPOSURE_PLAUSIBLE_RANGE
from .._ds1_scope import is_negated, outer_join_of

DRUG_EXPOSURE = "drug_exposure"

# (description, plausibility test, integer-valued column)


def _plausible(low: Optional[float], high: Optional[float]) -> Callable[[float], bool]:
    return lambda v: (low is None or v >= low) and (high is None or v <= high)


def _describe(low: Optional[float], high: Optional[float]) -> str:
    if low is not None and high is not None:
        return f"{low:g} to {high:g}"
    return f"at least {low:g}" if low is not None else f"at most {high:g}"


# (description, plausibility test, integer-valued column)
PLAUSIBLE: Dict[str, Tuple[str, Callable[[float], bool], bool]] = {
    column: (_describe(low, high), _plausible(low, high), integer)
    for column, (low, high, integer) in DRUG_EXPOSURE_PLAUSIBLE_RANGE.items()
}

# Kleene three-valued logic: True, False, or None (unknown).
Truth = Optional[bool]


def _and(a: Truth, b: Truth) -> Truth:
    if a is False or b is False:
        return False
    if a is True and b is True:
        return True
    return None


def _or(a: Truth, b: Truth) -> Truth:
    if a is True or b is True:
        return True
    if a is False and b is False:
        return False
    return None


def _number(node: Optional[exp.Expression]) -> Optional[float]:
    if node is None:
        return None
    node = unwrap_cast(node)
    sign = 1.0
    if isinstance(node, exp.Neg):
        sign, node = -1.0, unwrap_cast(node.this)
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return sign * float(node.this)
        except ValueError:
            return None
    return None


_COMPARE = {
    exp.EQ: lambda v, k: v == k,
    exp.NEQ: lambda v, k: v != k,
    exp.GT: lambda v, k: v > k,
    exp.GTE: lambda v, k: v >= k,
    exp.LT: lambda v, k: v < k,
    exp.LTE: lambda v, k: v <= k,
}
_MIRROR = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE, exp.EQ: exp.EQ, exp.NEQ: exp.NEQ}


class _Evaluator:
    """Evaluates a boolean condition for one target column at a candidate value."""

    def __init__(self, column: str, aliases: Dict[str, str]):
        self.column = column
        self.aliases = aliases
        self.literals: List[float] = []

    def is_target(self, node: exp.Expression) -> bool:
        return self.offset(node) == 0.0

    def offset(self, node: exp.Expression) -> Optional[float]:
        """``k`` when ``node`` is the target column plus the constant ``k`` (``days_supply - 1`` gives -1)."""
        node = unwrap_cast(node)
        if isinstance(node, exp.Paren):
            return self.offset(node.this)
        if isinstance(node, (exp.Add, exp.Sub)):
            right = _number(node.expression)
            if right is not None:
                inner = self.offset(node.this)
                return None if inner is None else inner + (right if isinstance(node, exp.Add) else -right)
            left = _number(node.this)
            if left is not None and isinstance(node, exp.Add):
                inner = self.offset(node.expression)
                return None if inner is None else inner + left
            return None
        if not isinstance(node, exp.Column):
            return None
        table, name = resolve_column_source(node, self.aliases)
        if normalize_name(name) == self.column and normalize_name(table or "") == DRUG_EXPOSURE:
            return 0.0
        return None

    def atom(self, node: exp.Expression) -> Optional[Callable[[float], bool]]:
        """A test ``value -> bool`` when ``node`` compares the target column (plus a constant) with a number."""
        if type(node) in _COMPARE:
            left, right = self.offset(node.this), self.offset(node.expression)
            if left is not None and _number(node.expression) is not None:
                k, op = _number(node.expression) - left, type(node)
            elif right is not None and _number(node.this) is not None:
                k, op = _number(node.this) - right, _MIRROR[type(node)]
            else:
                return None
            self.literals.append(k)
            return lambda v, f=_COMPARE[op], k=k: f(v, k)
        if isinstance(node, exp.Between) and self.is_target(node.this):
            low, high = _number(node.args.get("low")), _number(node.args.get("high"))
            if low is None or high is None:
                return None
            self.literals += [low, high]
            return lambda v: low <= v <= high
        if isinstance(node, exp.In) and self.is_target(node.this) and node.args.get("query") is None:
            values = [_number(e) for e in node.expressions or []]
            if not values or any(x is None for x in values):
                return None
            self.literals += values
            return lambda v: v in values
        return None

    def evaluate(self, node: exp.Expression, value: float) -> Truth:
        if isinstance(node, exp.Paren):
            return self.evaluate(node.this, value)
        if isinstance(node, exp.Not):
            inner = self.evaluate(node.this, value)
            return None if inner is None else not inner
        if isinstance(node, exp.And):
            return _and(self.evaluate(node.this, value), self.evaluate(node.expression, value))
        if isinstance(node, exp.Or):
            return _or(self.evaluate(node.this, value), self.evaluate(node.expression, value))
        test = self.atom(node)
        return None if test is None else test(value)

    def mentions_target(self, node: exp.Expression) -> bool:
        return any(self.atom(n) is not None for n in _own_scope_nodes(node))


def _own_scope_nodes(node: exp.Expression):
    """Nodes of ``node`` without descending into nested queries."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in current.iter_expressions():
            if not isinstance(child, (exp.Select, exp.Subquery, exp.SetOperation)):
                stack.append(child)


def _candidates(column: str, literals: List[float]) -> List[float]:
    """Points covering every region the literals carve out inside the plausible range."""
    _, plausible, integer = PLAUSIBLE[column]
    if integer:
        return [float(v) for v in range(1, 366)]
    points: Set[float] = {1e-7, 1.0}
    ordered = sorted({k for k in literals if k > 0})
    points.update(ordered)
    points.update((a + b) / 2 for a, b in zip(ordered, ordered[1:]))
    if ordered:
        points.add(ordered[0] / 2)
        points.add(ordered[-1] + 1)
    return sorted(p for p in points if plausible(p))


def _removes_rows(clause: exp.Expression) -> bool:
    """The clause's rows are taken away from the result: NOT EXISTS / NOT IN, the right branch of EXCEPT."""
    if is_negated(clause):
        return True
    child, parent = clause, clause.parent
    while parent is not None:
        if isinstance(parent, exp.Except) and parent.expression is child:
            return True
        child, parent = parent, parent.parent
    return False


def _conditions(tree: exp.Expression) -> List[exp.Expression]:
    """Conditions that select the rows a SELECT returns: WHERE, HAVING and inner join ON clauses."""
    out: List[exp.Expression] = []
    for clause in tree.find_all(exp.Where, exp.Having):
        if not _removes_rows(clause):
            out.append(clause.this)
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None and outer_join_of(on) is None and not _removes_rows(join):
            out.append(on)
    return out


@register
class DrugExposureImplausibleValueFilterRule(Rule):
    """A filter on drug_exposure.days_supply / quantity that admits only implausible values."""

    rule_id = "domain_specific.drug_exposure_implausible_value_filter"
    name = "Drug Exposure Implausible Value Filter"
    description = (
        "A WHERE / JOIN condition on drug_exposure.days_supply or drug_exposure.quantity that admits no "
        "plausible value (days_supply 1-365, quantity at least 0.0000001, per the OHDSI Data Quality Dashboard "
        "thresholds) keeps only data-quality outliers. Deliberate data-quality queries can ignore it."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: a filter such as `days_supply = 0`, `days_supply > 365` or `quantity <= 0` WITH one that keeps "
        "plausible rows (`days_supply BETWEEN 1 AND 365`, `quantity > 0`). If the query is a deliberate "
        "data-quality check for out-of-range values, this warning can be ignored."
    )
    example_bad = "SELECT person_id FROM drug_exposure\nWHERE days_supply = 0;"
    example_good = "SELECT person_id FROM drug_exposure\nWHERE days_supply > 0;"
    extra_examples = (
        RuleExample(
            sql="SELECT person_id FROM drug_exposure\nWHERE quantity <= 0;",
            severity=Severity.WARNING,
            note="quantity filter that admits no value above 0",
        ),
    )
    references = (DQD_FIELD_THRESHOLDS, cdm_table("drug_exposure"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if DRUG_EXPOSURE not in lowered or not any(c in lowered for c in PLAUSIBLE):
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            aliases = extract_aliases(tree)
            seen: Set[Tuple[str, str]] = set()
            for condition in _conditions(tree):
                for column, (plausible_text, _, _) in PLAUSIBLE.items():
                    evaluator = _Evaluator(column, aliases)
                    if not evaluator.mentions_target(condition):
                        continue
                    candidates = _candidates(column, evaluator.literals)
                    # Unknown (None) means the condition might hold, so only a definite False everywhere fires.
                    if any(evaluator.evaluate(condition, v) is not False for v in candidates):
                        continue
                    text = condition.sql()
                    if (column, text) in seen:
                        continue
                    seen.add((column, text))
                    violations.append(
                        self.create_violation(
                            message=(
                                f"Filter on drug_exposure.{column} admits only values outside the plausible range of "
                                f"the OHDSI Data Quality Dashboard thresholds ({plausible_text}). It keeps only rows "
                                f"those checks flag, so the result is typically empty or made of data-quality outliers "
                                f"rather than ordinary exposures. A deliberate data-quality query can ignore this."
                            ),
                            details={"column": f"drug_exposure.{column}", "condition": text},
                        )
                    )
        return violations


__all__ = ["DrugExposureImplausibleValueFilterRule"]

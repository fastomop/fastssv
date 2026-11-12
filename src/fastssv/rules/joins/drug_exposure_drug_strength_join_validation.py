"""Drug Exposure to Drug Strength Join Validation Rule.

DRUG_STRENGTH holds, for each active Standard Drug Concept, one row per active ingredient (OMOP CDM v5.4
conventions, Drug Strength); its ``drug_concept_id`` is "the Branded Drug or Clinical Drug Product". DRUG_EXPOSURE
records the standard drug in ``drug_concept_id``. The two relate on ``drug_concept_id``, directly or through a table
or CTE that carries it (CONCEPT, CONCEPT_ANCESTOR, a CTE projecting ``drug_strength.drug_concept_id``).

Reported, one SELECT at a time:

- ERROR: both tables are read in one FROM list and no predicate relates their rows, so every exposure is paired with
  every strength row.
- ERROR: the only relation equates columns that hold different kinds of value (``drug_exposure_id = drug_concept_id``,
  ``route_concept_id = drug_concept_id``, ``quantity = denominator_value``): concept ids are unique across domains and
  row ids are not concept ids, so no conformant CDM gives the intended pairing.
- WARNING: the only relation is ``drug_source_concept_id`` = a drug_strength concept, or ``drug_concept_id`` =
  ``ingredient_concept_id``. Whether that pairs an exposure with its strength depends on the data: a source concept is
  standard only when the source was coded in RxNorm ("This field is discouraged from use in analysis because it is
  not required to contain Standard Concepts"), and an exposure recorded at ingredient level matches the strength of
  every product that contains the ingredient.

Once a SELECT relates the tables on ``drug_concept_id``, further predicates between them (``de.quantity =
ds.denominator_value``, an OR alternative) are filters and are not reported.

Operands are followed through casts, CTEs, derived tables and ``IN (SELECT …)``: a CTE or derived table that projects
``drug_strength.drug_concept_id`` unchanged carries the same values, and an IN subquery is a semi-join on the column
it projects.

Violation pattern:
    SELECT de.drug_exposure_id, ds.amount_value
    FROM drug_exposure de
    JOIN drug_strength ds ON de.drug_exposure_id = ds.drug_concept_id

Correct pattern:
    SELECT de.drug_exposure_id, de.quantity * ds.amount_value AS ingredient_dose
    FROM drug_exposure de
    JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql, resolve_column_source, unwrap_cast
from fastssv.core.patch import build_join_replace_patch
from fastssv.core.references import CDM_CONVENTIONS, CDM_DRUG_DOSE, CDM_FIELD_LEVEL, cdm_table, section
from fastssv.core.registry import register
from fastssv.schemas.cdm_column_types import CDM_COLUMNS

from ._lineage import column_ref, equated_columns, scope_tables, source_of, written

DRUG_EXPOSURE = "drug_exposure"
DRUG_STRENGTH = "drug_strength"
DRUG_CONCEPT_ID = "drug_concept_id"

# Pairs whose values can coincide on a conformant CDM, so whether they pair an exposure with its strength depends on
# the data (see the module docstring).
_DATA_DEPENDENT_PAIRS = {
    ("drug_source_concept_id", "drug_concept_id"),
    ("drug_source_concept_id", "ingredient_concept_id"),
    ("drug_concept_id", "ingredient_concept_id"),
}


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


_LINKING_PREDICATES = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE, exp.Between, exp.In, exp.Like)


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[tuple, tuple] = {}

    def find(self, x: tuple) -> tuple:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: tuple, b: tuple) -> None:
        self.parent[self.find(a)] = self.find(b)


def _column_key(col: exp.Column) -> Optional[Tuple[int, str, str]]:
    """(SELECT, alias, column) that ``col`` reads, through its own SELECT or an enclosing one (a correlated reference).
    For a source that passes a base table's rows through, the column is the base table's (a rename is undone)."""
    scope, src = source_of(col)
    if scope is None or src is None or not src.alias_or_name:
        return None
    ref = column_ref(col, scope, scope_tables(scope))
    return (id(scope), ref[0], ref[1]) if ref is not None else None


def _first_projection(query: Optional[exp.Expression]) -> Optional[exp.Column]:
    while isinstance(query, exp.Subquery):
        query = query.this
    if not isinstance(query, exp.Select) or len(query.expressions) != 1:
        return None
    proj = query.expressions[0]
    proj = unwrap_cast(proj.this if isinstance(proj, exp.Alias) else proj)
    return proj if isinstance(proj, exp.Column) else None


def _negated_within(node: exp.Expression, select: exp.Select) -> bool:
    parent = node.parent
    while parent is not None and parent is not select:
        if isinstance(parent, exp.Not):
            return True
        parent = parent.parent
    return False


def _scope_link(select: exp.Select) -> Optional[Tuple[bool, bool]]:
    """(drug_exposure and drug_strength rows are related, related on drug_concept_id) within ``select``'s own
    FROM/JOIN scope, or None when the scope does not read both tables.

    Every FROM/JOIN source is a node, whatever it is (a CTE or derived table built with JOIN, GROUP BY or DISTINCT
    relays the value as well as a base table does), and so is every source of a subquery in WHERE / ON. Columns
    equated there (through CAST, IN subqueries and correlated subqueries such as EXISTS) form equivalence classes,
    as do USING and NATURAL joins; any predicate over columns of two sources relates them. A predicate under NOT
    relates rows but does not carry the key.
    """
    local = scope_tables(select)
    de_aliases = [a for a, t in local.items() if t == DRUG_EXPOSURE]
    ds_aliases = [a for a, t in local.items() if t == DRUG_STRENGTH]
    if not de_aliases or not ds_aliases:
        return None

    sid = id(select)
    columns = _UnionFind()
    tables = _UnionFind()

    clauses = [select.args.get("where")] + [j.args.get("on") for j in select.args.get("joins") or []]
    for clause in clauses:
        if clause is None:
            continue
        for pred in clause.find_all(*_LINKING_PREDICATES):
            keys = [k for c in pred.find_all(exp.Column) if (k := _column_key(c))]
            nodes = sorted({k[:2] for k in keys})
            for other in nodes[1:]:
                tables.union(nodes[0], other)
            if _negated_within(pred, select):
                continue
            if isinstance(pred, exp.EQ):
                left, right = unwrap_cast(pred.this), unwrap_cast(pred.expression)
            elif isinstance(pred, exp.In) and pred.args.get("query") is not None:
                left, right = unwrap_cast(pred.this), _first_projection(pred.args["query"])
            else:
                continue
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                lk, rk = _column_key(left), _column_key(right)
                if lk and rk:
                    columns.union(lk, rk)
                    tables.union(lk[:2], rk[:2])

    earlier: List[str] = []
    from_node = select.args.get("from_") or select.args.get("from")
    for scope_node in [from_node, *(select.args.get("joins") or [])]:
        if scope_node is None or not isinstance(scope_node.this, (exp.Table, exp.Subquery)):
            continue
        alias = _norm(scope_node.this.alias_or_name)
        if isinstance(scope_node, exp.Join):
            shared = {_norm(u.name) for u in scope_node.args.get("using") or []}
            if _norm(scope_node.args.get("method") or "") == "natural":
                mine = CDM_COLUMNS.get(local.get(alias, ""), frozenset())
                shared |= {
                    c for prior in earlier for c in CDM_COLUMNS.get(local.get(prior, ""), frozenset()) if c in mine
                }
            for prior in earlier:
                prior_cols = CDM_COLUMNS.get(local.get(prior, ""), frozenset())
                for col in shared:
                    if col in prior_cols or not prior_cols:
                        columns.union((sid, alias, col), (sid, prior, col))
                        tables.union((sid, alias), (sid, prior))
        earlier.append(alias)

    related = on_key = False
    for de in de_aliases:
        for ds in ds_aliases:
            if tables.find((sid, de)) == tables.find((sid, ds)):
                related = True
            if columns.find((sid, de, DRUG_CONCEPT_ID)) == columns.find((sid, ds, DRUG_CONCEPT_ID)):
                related = on_key = True
    return related, on_key


# --- Detection -------------------------------------------------------------


class _Pair:
    __slots__ = ("node", "de_col", "ds_col", "de_expr", "ds_expr")

    def __init__(self, node, de_col, ds_col, de_expr, ds_expr):
        self.node, self.de_col, self.ds_col, self.de_expr, self.ds_expr = node, de_col, ds_col, de_expr, ds_expr


def _pairs_by_select(tree: exp.Expression) -> Dict[int, List[_Pair]]:
    """Predicates between a drug_exposure column and a drug_strength column, grouped by the SELECT that holds them."""
    out: Dict[int, List[_Pair]] = {}
    for pair in equated_columns(tree):
        lt, lc = resolve_column_source(pair.left, {})
        rt, rc = resolve_column_source(pair.right, {})
        if (lt, rt) == (DRUG_EXPOSURE, DRUG_STRENGTH):
            found = _Pair(pair.node, _norm(lc), _norm(rc), pair.left, pair.right)
        elif (lt, rt) == (DRUG_STRENGTH, DRUG_EXPOSURE):
            found = _Pair(pair.node, _norm(rc), _norm(lc), pair.right, pair.left)
        else:
            continue
        select = pair.node.find_ancestor(exp.Select)
        out.setdefault(id(select), []).append(found)
    return out


def _detect(tree: exp.Expression) -> List[Tuple[str, Optional[_Pair]]]:
    """("wrong_key" | "data_dependent_key", pair) for each SELECT whose only relation is a non-key pair, and
    ("unrelated", None) for a SELECT that reads both tables without relating them."""
    findings: List[Tuple[str, Optional[_Pair]]] = []
    pairs = _pairs_by_select(tree)
    links = {id(select): link for select in tree.find_all(exp.Select) if (link := _scope_link(select)) is not None}
    for select_id, found in pairs.items():
        if any(p.de_col == DRUG_CONCEPT_ID and p.ds_col == DRUG_CONCEPT_ID for p in found):
            continue
        if links.get(select_id, (False, False))[1]:
            continue
        seen: Set[Tuple[str, str]] = set()
        for p in found:
            if (p.de_col, p.ds_col) in seen:
                continue
            seen.add((p.de_col, p.ds_col))
            kind = "data_dependent_key" if (p.de_col, p.ds_col) in _DATA_DEPENDENT_PAIRS else "wrong_key"
            findings.append((kind, p))
    if any(not related for related, _ in links.values()):
        findings.append(("unrelated", None))
    return findings


# --- Rule ------------------------------------------------------------------


@register
class DrugExposureDrugStrengthJoinValidationRule(Rule):
    """drug_exposure and drug_strength must be related on drug_concept_id."""

    rule_id = "joins.drug_exposure_drug_strength_join_validation"
    name = "Drug Exposure to Drug Strength Join Validation"
    description = (
        "drug_exposure relates to drug_strength on drug_concept_id. ERROR when a SELECT reads both without relating "
        "them, or relates them only on columns that hold different kinds of value; WARNING when it relates "
        "drug_source_concept_id, or drug_concept_id to ingredient_concept_id, which pairs the right rows only for some "
        "data."
    )
    severity = Severity.ERROR
    suggested_fix = "REPLACE: the join condition WITH `drug_exposure.drug_concept_id = drug_strength.drug_concept_id`."
    example_bad = (
        "SELECT de.drug_exposure_id, ds.amount_value\n"
        "FROM drug_exposure de\n"
        "JOIN drug_strength ds ON de.drug_exposure_id = ds.drug_concept_id;"
    )
    example_good = (
        "SELECT de.drug_exposure_id, de.quantity * ds.amount_value AS ingredient_dose\n"
        "FROM drug_exposure de\n"
        "JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id;"
    )
    extra_examples = (
        RuleExample(
            "SELECT de.drug_exposure_id, ds.amount_value\n"
            "FROM drug_exposure de, drug_strength ds\n"
            "WHERE ds.ingredient_concept_id = 1125315;",
            Severity.ERROR,
            "Both tables are read but nothing relates their rows, so every exposure pairs with every strength row.",
        ),
        RuleExample(
            "SELECT de.drug_exposure_id, de.quantity * ds.amount_value AS ingredient_dose\n"
            "FROM drug_exposure de\n"
            "JOIN drug_strength ds ON de.drug_source_concept_id = ds.drug_concept_id;",
            Severity.WARNING,
            "drug_source_concept_id matches drug_strength only where the source was coded in a standard drug "
            "vocabulary.",
        ),
    )
    references = (
        cdm_table("drug_strength"),
        section(CDM_CONVENTIONS, "drug-strength", "Drug strength"),
        CDM_DRUG_DOSE,
        CDM_FIELD_LEVEL,
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if DRUG_EXPOSURE not in sql_lower or DRUG_STRENGTH not in sql_lower:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            for kind, pair in _detect(tree):
                if kind == "unrelated":
                    violations.append(
                        self.create_violation(
                            message=(
                                "drug_exposure and drug_strength are read in one FROM list but no predicate relates "
                                "their rows, so every exposure is paired with every strength row. Relate them on "
                                "drug_concept_id."
                            ),
                            details={"type": "unrelated"},
                        )
                    )
                    continue
                de_text, ds_text = written(pair.de_expr), written(pair.ds_expr)
                fix_text = (
                    f"REPLACE: `{de_text} = {ds_text}` WITH the drug_concept_id columns of the same two row sources."
                )
                patch = build_join_replace_patch(
                    sql,
                    DRUG_EXPOSURE,
                    pair.de_col,
                    DRUG_STRENGTH,
                    pair.ds_col,
                    DRUG_CONCEPT_ID,
                    DRUG_CONCEPT_ID,
                    fix_text,
                    aliases=_aliases_of(tree),
                )
                if kind == "data_dependent_key":
                    severity = Severity.WARNING
                    reason = (
                        "drug_source_concept_id holds a standard drug only when the source was coded in RxNorm"
                        if pair.de_col == "drug_source_concept_id"
                        else "an exposure recorded at ingredient level matches the strength of every product that "
                        "contains the ingredient"
                    )
                    message = (
                        f"drug_exposure.{pair.de_col} = drug_strength.{pair.ds_col} relates the tables on a "
                        f"different key than drug_concept_id: {reason}, so whether each exposure gets its own "
                        "strength depends on the data. Use drug_exposure.drug_concept_id = "
                        "drug_strength.drug_concept_id."
                    )
                else:
                    severity = Severity.ERROR
                    message = (
                        f"drug_exposure.{pair.de_col} = drug_strength.{pair.ds_col} compares different kinds of "
                        "value, so it never pairs an exposure with its strength. Use drug_exposure.drug_concept_id = "
                        "drug_strength.drug_concept_id."
                    )
                violations.append(
                    self.create_violation(
                        message=message,
                        severity=severity,
                        suggested_fix_patch=patch,
                        details={
                            "type": kind,
                            "drug_exposure_column": pair.de_col,
                            "drug_strength_column": pair.ds_col,
                        },
                    )
                )
        return violations


def _aliases_of(tree: exp.Expression) -> Dict[str, str]:
    """alias -> base table for every SELECT of ``tree`` (used only to locate the predicate text for the patch)."""
    out: Dict[str, str] = {}
    for select in tree.find_all(exp.Select):
        out.update(scope_tables(select))
    return out


__all__ = ["DrugExposureDrugStrengthJoinValidationRule"]

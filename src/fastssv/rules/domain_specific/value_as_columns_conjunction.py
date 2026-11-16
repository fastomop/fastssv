"""Value-As Columns Conjunction Rule.

``measurement`` and ``observation`` store a result in whichever ``value_as_*`` column matches its type:
``value_as_number`` for numeric results, ``value_as_concept_id`` for coded results and, on observation,
``value_as_string``. CDM v5.4 populates each "if applicable and available"; it also provides for both being
present ("If both a continuous and categorical result are given in the source data such that both VALUE_AS_NUMBER
and VALUE_AS_CONCEPT_ID are both included ..."), but many ETLs store one value per result.

Requiring value conditions on two *different* ``value_as_*`` columns of the *same row* with AND
(``value_as_number > 140 AND value_as_concept_id = 4126681``) therefore keeps only the rows where the ETL populated
both, which on many databases is few or none. Whether it is intended depends on the data, so this is a WARNING.

Conditions are combined per table row across WHERE, HAVING and every JOIN ON of the SELECT, and through CTEs and
derived tables (a filter inside a CTE holds for every row read from it). Two rows of a self-join stay apart. Not
value conditions: a conjunct that tests several value columns (an OR, a CASE), a conjunct that also accepts the
column being absent (``value_as_concept_id IS NULL OR value_as_concept_id = 0``), NULL tests, conditions under NOT,
and joins of ``value_as_concept_id`` to ``concept.concept_id`` (decoding the value for display).
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql, scope_nodes
from fastssv.core.references import cdm_table
from fastssv.core.registry import register
from fastssv.rules.domain_specific._rows import RowPath, group_by_row, in_negated_scope, peel, read_of

VALUE_COLUMNS: Dict[str, Set[str]] = {
    "measurement": {"value_as_number", "value_as_concept_id"},
    "observation": {"value_as_number", "value_as_concept_id", "value_as_string"},
}

_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.In, exp.Like, exp.ILike)


def _flatten_and(node: exp.Expression) -> List[exp.Expression]:
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        return _flatten_and(node.this) + _flatten_and(node.expression)
    return [node]


def _value_column(node: Optional[exp.Expression]) -> Optional[Tuple[RowPath, str, str]]:
    read = read_of(node)
    if read is None or read[2] not in VALUE_COLUMNS.get(read[1], set()):
        return None
    return read


def _is_label_lookup(node: Optional[exp.Expression]) -> bool:
    """``concept.concept_id`` on the other side: a join that decodes the value, not a condition on it."""
    read = read_of(node)
    return read is not None and read[1:] == ("concept", "concept_id")


def _accepts_absence(conjunct: exp.Expression, column: Tuple[RowPath, str, str]) -> bool:
    """An OR branch that lets the column be NULL: the conjunct does not require the column to be populated."""
    if not isinstance(peel(conjunct), exp.Or):
        return False
    for node in scope_nodes(conjunct):
        if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null) and not isinstance(node.parent, exp.Not):
            if read_of(node.this) == column:
                return True
    return False


def _value_conditions(conjunct: exp.Expression) -> Set[Tuple[RowPath, str, str]]:
    found: Set[Tuple[RowPath, str, str]] = set()
    for node in scope_nodes(conjunct):
        if not isinstance(node, _COMPARISONS):
            continue
        if isinstance(node, exp.Between):
            pairs = [(node.this, [node.args.get("low"), node.args.get("high")])]
        elif isinstance(node, exp.In):
            query = node.args.get("query")
            pairs = [(node.this, [query] if query is not None else list(node.expressions))]
        else:
            pairs = [(node.this, [node.expression]), (node.expression, [node.this])]
        for side, others in pairs:
            column = _value_column(side)
            if column is None or not others or any(o is None or _is_label_lookup(o) for o in others):
                continue
            found.add(column)
    return found


def _conditions(select: exp.Select) -> List[exp.Expression]:
    out: List[exp.Expression] = []
    for key in ("where", "having"):
        if select.args.get(key) is not None:
            out.append(select.args[key].this)
    out += [j.args["on"] for j in select.args.get("joins") or [] if j.args.get("on") is not None]
    return out


@register
class ValueAsColumnsConjunctionRule(Rule):
    """AND-combined value conditions on two different value_as_* columns of the same row."""

    rule_id = "domain_specific.value_as_columns_conjunction"
    name = "Value-As Columns Combined With AND"
    description = (
        "AND-ing value conditions on two different value_as_* columns of the same measurement or observation row "
        "keeps only rows where both columns are populated; many ETLs store one value per result, so this can return "
        "few or no rows."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: `value_as_number <op> <n> AND value_as_concept_id = <id>` WITH the single value_as_* column "
        "that matches how the result is recorded, OR use `OR` (in parentheses) to accept either representation."
    )
    long_description = (
        "CDM v5.4 stores a measurement or observation result in the value_as_* column matching its type and allows "
        "both value_as_number and value_as_concept_id to be filled when the source gives both. A filter that requires "
        "conditions on two of these columns of the same row keeps only rows where both are populated, which depends on "
        "the ETL. Conditions are combined per row across WHERE, JOIN ON, CTEs and derived tables; an OR, a NULL "
        "alternative or a concept lookup join is not a value condition."
    )
    example_bad = "SELECT person_id FROM measurement\nWHERE value_as_number > 140 AND value_as_concept_id = 4126681;"
    example_good = "SELECT person_id FROM measurement\nWHERE value_as_number > 140 OR value_as_concept_id = 4126681;"
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT m.person_id FROM person p\n"
                "JOIN measurement m ON m.person_id = p.person_id AND m.value_as_number > 140\n"
                "WHERE m.value_as_concept_id = 4126681;"
            ),
            severity=Severity.WARNING,
            note="the two conditions split between JOIN ON and WHERE",
        ),
    )
    references = (cdm_table("measurement"), cdm_table("observation"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "value_as_" not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            per_row: List[Tuple[RowPath, Tuple[str, str]]] = []
            for select in tree.find_all(exp.Select):
                for condition in _conditions(select):
                    for conjunct in _flatten_and(condition):
                        if in_negated_scope(conjunct) or isinstance(peel(conjunct), exp.Not):
                            continue
                        by_row: Dict[RowPath, Set[Tuple[RowPath, str, str]]] = {}
                        for column in _value_conditions(conjunct):
                            by_row.setdefault(column[0], set()).add(column)
                        for columns in by_row.values():
                            # One conjunct touching several value columns of a row (an OR, a CASE) is not a
                            # conjunction of them.
                            if len(columns) != 1:
                                continue
                            column = next(iter(columns))
                            if not _accepts_absence(conjunct, column):
                                per_row.append((column[0], (column[1], column[2])))
            seen: Set[Tuple[str, Tuple[str, ...]]] = set()
            for row, items in group_by_row(per_row):
                tables = {t for t, _ in items}
                for table in sorted(tables):
                    cols = tuple(sorted({c for t, c in items if t == table}))
                    if len(cols) < 2 or (table, cols) in seen:
                        continue
                    seen.add((table, cols))
                    violations.append(
                        self.create_violation(
                            message=(
                                f"Value conditions on {', '.join(cols)} of the same {table} row are combined with "
                                "AND, so only rows where both columns are populated can match. Many ETLs store one "
                                "value per result; use the column that holds this result, or OR."
                            ),
                            details={"table": table, "alias": normalize_name(row[0][1]), "columns": list(cols)},
                        )
                    )
        return violations


__all__ = ["ValueAsColumnsConjunctionRule"]

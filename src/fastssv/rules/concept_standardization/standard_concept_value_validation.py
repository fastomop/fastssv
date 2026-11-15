"""Standard Concept Value Validation Rule.

CDM v5.4 field-level specification, concept.standard_concept: "The allowable values are 'S' (Standard Concept) and
'C' (Classification Concept), otherwise the content is NULL." A comparison with any other value matches nothing.

* ERROR: a required filter (AND-joined to WHERE / HAVING / an inner join's ON) that admits only values the column
  never holds: ``= 'Y'``, ``= 'N'``, ``= ''`` (non-standard is NULL, not an empty string), ``IN ('N')``, and a
  lowercase ``'s'`` / ``'c'`` (the CDM values are uppercase), on every dialect.
* WARNING: the same comparison where it does not restrict the result (under OR, in a CASE label, in an outer join's
  ON clause), a list that mixes valid and impossible values (``IN ('S', 'Y')``: the extra value is dead), and a
  negated impossible value (``<> 'N'``, ``NOT IN ('')``), which excludes nothing and equals ``IS NOT NULL``.

``standard_concept = '' OR standard_concept IS NULL`` is accepted: it is a superset of ``IS NULL`` and returns the
right rows. Number and boolean operands (``= 1``, ``= TRUE``) are type errors on this varchar column and are left to
data_quality.column_type_validation; only values written as strings are judged. The column is identified by lineage, so a CTE or derived column that reuses the name for a computed value
(``CASE ... END AS standard_concept``) is not the CDM column.
"""

import re
from typing import List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.references import BOOK_VOCABULARIES, CDM_FIELD_LEVEL, cdm_table, section
from fastssv.core.registry import register

from ._cs_scope import ColumnFilter, dead_scalar_column, instance_filters

CONCEPT_TABLE = "concept"
STANDARD_CONCEPT_COLUMN = "standard_concept"
VALID_VALUES = {"S", "C"}

_TRUE_LIKE = {"Y", "YES", "TRUE", "T", "1"}
_FALSE_LIKE = {"N", "NO", "FALSE", "F", "0"}

# Unrendered template parameters (`'{std}'`, `'@std'`, `'${std}'`) are filled in before the SQL runs.
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{\w+\}|\$\{\w+\}|@\w+")


def _matches_nothing(raw: str, case_sensitive: bool) -> bool:
    if _TEMPLATE_PLACEHOLDER_RE.fullmatch(raw):
        return False
    if raw in VALID_VALUES:
        return False
    return case_sensitive or raw.upper() not in VALID_VALUES


def _note(raw: str) -> str:
    if raw == "":
        return "'' is not a standard_concept value; non-standard concepts carry NULL (use IS NULL)."
    if raw.upper() in VALID_VALUES:
        return f"'{raw}' is lowercase; the CDM values are the uppercase 'S' / 'C'."
    if raw.upper() in _FALSE_LIKE:
        return f"'{raw}' is not a standard_concept value; non-standard concepts carry NULL (use IS NULL)."
    return f"'{raw}' is not a standard_concept value."


def _is_typed_literal(node: Optional[exp.Expression]) -> bool:
    """A number or boolean operand: on this varchar column that is a type error, which
    data_quality.column_type_validation reports."""
    while isinstance(node, (exp.Paren, exp.Neg)):
        node = node.this
    return isinstance(node, exp.Boolean) or (isinstance(node, exp.Literal) and not node.is_string)


def _string_values(flt: ColumnFilter) -> List[str]:
    """The values of ``flt`` written as strings; numbers and booleans are left out."""
    atom = flt.node
    while isinstance(atom, (exp.Not, exp.Paren)):
        atom = atom.this
    if isinstance(atom, exp.In):
        operands = list(atom.expressions or [])
    elif isinstance(atom, exp.Between):
        operands = [atom.args.get("low"), atom.args.get("high")]
    else:
        side = atom.this
        operands = [atom.expression if side is flt.column or flt.column in side.find_all(exp.Column) else side]
    if len(operands) != len(flt.values):
        return list(flt.values)
    return [v for v, o in zip(flt.values, operands) if not _is_typed_literal(o)]


def _or_with_is_null(flt: ColumnFilter) -> bool:
    """``standard_concept = '' OR standard_concept IS NULL``: the empty string is a guard for loaders that wrote ''."""
    parent = flt.node.parent
    while parent is not None and not isinstance(parent, (exp.Where, exp.Join, exp.Having, exp.Select)):
        if isinstance(parent, exp.Or):
            for is_node in parent.find_all(exp.Is):
                col = is_node.this
                if (
                    isinstance(col, exp.Column)
                    and normalize_name(col.name) == STANDARD_CONCEPT_COLUMN
                    and isinstance(is_node.expression, exp.Null)
                ):
                    return True
        parent = parent.parent
    return False


def _eq_patch(sql: str, flt: ColumnFilter, raw: str) -> Optional[dict]:
    """Mechanical fix for `standard_concept = '<impossible>'` when the intent is unambiguous; otherwise None."""
    node = flt.node
    if not isinstance(node, exp.EQ):
        return None
    literal = node.expression if node.this is flt.column or flt.column in node.this.find_all(exp.Column) else node.this
    upper = raw.upper()
    if upper in VALID_VALUES:
        replacement, target = f"'{upper}'", literal
    elif upper in _TRUE_LIKE:
        replacement, target = "'S'", literal
    elif upper in _FALSE_LIKE or raw == "":
        replacement, target = f"{flt.column.sql()} IS NULL", node
    else:
        return None
    span = locate(sql, target.sql())
    if span is None and target is node:
        # The rendered predicate drops the author's spacing (`standard_concept='N'`); match it loosely instead.
        col = flt.column
        qualifier = rf"{re.escape(col.table)}\s*\.\s*" if col.table else ""
        pattern = rf"{qualifier}{re.escape(col.name)}\s*=\s*'{re.escape(raw)}'"
        matches = list(re.finditer(pattern, sql, flags=re.IGNORECASE))
        if len(matches) == 1:
            span = (matches[0].start(), matches[0].end())
    return patch_replace(span, replacement) if span is not None else None


@register
class StandardConceptValueValidationRule(Rule):
    """standard_concept compared with a value it never holds."""

    rule_id = "concept_standardization.standard_concept_value_validation"
    name = "Standard Concept Value Validation"
    description = (
        "standard_concept holds only 'S', 'C' or NULL. A required filter on any other string ('N', 'Y', '', a "
        "lowercase 's') returns no rows (ERROR); a dead value next to valid ones, a negated impossible value or a "
        "comparison that does not restrict the rows is a WARNING."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: `standard_concept = '<other>'` WITH one of: `= 'S'` (standard), `= 'C'` (classification), "
        "`IS NULL` (non-standard). Those are the only values the CDM allows."
    )
    long_description = (
        "The CDM v5.4 specification allows exactly 'S' (Standard), 'C' (Classification) and NULL in "
        "concept.standard_concept. A required filter on any other string ('N', 'Y', an empty string) matches no "
        "concept row, so the query returns nothing (ERROR). A lowercase 's' / 'c' is not a CDM value either and is "
        "an ERROR on every dialect, whatever collation an engine would compare it under. A list that also contains "
        "a valid value (IN ('S', 'Y')) still returns "
        "rows and is a WARNING about the dead value; so is a comparison under OR, in a CASE label or in an outer "
        "join's ON clause. A negated impossible value (<> 'N', NOT IN ('')) excludes nothing and equals IS NOT NULL "
        "(WARNING). `standard_concept = '' OR standard_concept IS NULL` is accepted."
    )
    example_bad = "SELECT concept_id\nFROM concept\nWHERE standard_concept = 'Y';"
    example_good = "SELECT concept_id\nFROM concept\nWHERE standard_concept = 'S';"
    references = (
        cdm_table("concept"),
        section(BOOK_VOCABULARIES, "non-standard-concepts", "Non-standard concepts"),
        CDM_FIELD_LEVEL,
    )
    extra_examples = (
        RuleExample(
            sql="SELECT concept_id\nFROM concept\nWHERE standard_concept IN ('S', 'Y');",
            severity=Severity.WARNING,
            note="A valid value next to an impossible one: rows still come back, 'Y' is dead",
        ),
        RuleExample(
            sql="SELECT concept_id\nFROM concept\nWHERE standard_concept <> 'N';",
            severity=Severity.WARNING,
            note="Negated impossible value: excludes nothing, same as IS NOT NULL",
        ),
        RuleExample(
            sql="SELECT concept_id\nFROM concept\nWHERE standard_concept = '';",
            severity=Severity.ERROR,
            note="Empty string: non-standard concepts carry NULL, so nothing matches",
        ),
        RuleExample(
            sql="SELECT concept_id\nFROM concept\nWHERE standard_concept = 's';",
            severity=Severity.ERROR,
            note="Lowercase: the CDM values are the uppercase 'S' and 'C', on every dialect",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if STANDARD_CONCEPT_COLUMN not in sql.lower():
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            seen: Set[int] = set()
            for flts in instance_filters(tree, CONCEPT_TABLE, STANDARD_CONCEPT_COLUMN).values():
                for flt in flts:
                    if id(flt.node) in seen or not flt.complete:
                        continue
                    seen.add(id(flt.node))
                    finding = self._judge(flt)
                    if finding is not None:
                        severity, message, raw = finding
                        patch = _eq_patch(sql, flt, raw) if severity == Severity.ERROR and flt.op == "eq" else None
                        fix = (
                            "REPLACE: the negated comparison WITH `standard_concept IS NOT NULL` (or "
                            "`standard_concept = 'S'` for standard concepts only)."
                            if flt.op in ("neq", "not_in")
                            else None
                        )
                        violations.append(
                            self.create_violation(
                                message=message, severity=severity, suggested_fix=fix, suggested_fix_patch=patch
                            )
                        )
        return violations

    def _judge(self, flt: ColumnFilter):
        case_sensitive = not flt.case_folded
        values = _string_values(flt)
        if not values:
            return None
        if flt.op == "like":
            if any(ch in values[0] for ch in "%_"):
                return None
            op = "eq"
        else:
            op = flt.op
        if op not in ("eq", "in", "neq", "not_in"):
            return None
        impossible = [v for v in values if _matches_nothing(v, case_sensitive)]
        if not impossible:
            return None
        if op == "eq" and values == [""] and _or_with_is_null(flt):
            return None
        notes = " ".join(dict.fromkeys(_note(v) for v in impossible))
        if op in ("neq", "not_in"):
            return (
                Severity.WARNING,
                f"{notes} A value that never occurs excludes nothing, so `{flt.sql}` keeps every 'S' and 'C' row: it "
                "is the same as standard_concept IS NOT NULL. Write that, or `standard_concept = 'S'` if only "
                "standard concepts are wanted.",
                impossible[0],
            )
        all_dead = len(impossible) == len(values)
        restricts = flt.required and not flt.outer_join_on
        if all_dead and restricts:
            dead = dead_scalar_column(flt.node)
            consequence = dead or "the query returns no rows"
            return (
                Severity.ERROR,
                f"Invalid standard_concept {'values ' + str(impossible) if len(impossible) > 1 else 'value ' + repr(impossible[0])} "
                f"in `{flt.sql}`: no concept row matches, so {consequence}. "
                f"Valid values are 'S', 'C', or NULL. {notes}",
                impossible[0],
            )
        if not all_dead:
            reason = "the other values still return rows, but these never match"
        else:
            reason = "it never matches; here it does not restrict the result, but the branch is dead"
        return (
            Severity.WARNING,
            f"`{flt.sql}`: {', '.join(repr(v) for v in impossible)} is not a standard_concept value; {reason}. "
            f"Valid values are 'S', 'C', or NULL. {notes}",
            impossible[0],
        )


__all__ = ["StandardConceptValueValidationRule"]

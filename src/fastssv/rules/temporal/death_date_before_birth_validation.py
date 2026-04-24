"""Death Date Before Birth Validation Rule.

OMOP semantic rule CLIN_050: death_date_not_before_birth

death.death_date must logically be on or after the person's birth date. Queries
that filter for death_date < birth date indicate impossible temporal logic, data
quality issues, or join errors.

The Problem:
    A person cannot die before they are born. Queries filtering for death_date
    before birth_datetime (or death year before year_of_birth) represent:
    - Data quality issues (incorrect dates)
    - Logic errors in the query
    - Incorrect join conditions

Violation patterns:
    -- Direct comparison: death_date < birth_datetime
    SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
    WHERE d.death_date < p.birth_datetime

    -- Year comparison: YEAR(death_date) < year_of_birth
    SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
    WHERE YEAR(d.death_date) < p.year_of_birth

Correct patterns:
    -- Valid temporal constraint: death_date >= birth_datetime
    SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
    WHERE d.death_date >= p.birth_datetime

    -- Or simply no impossible constraint
    SELECT * FROM death d
    JOIN person p ON d.person_id = p.person_id
"""

from typing import List, Dict, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
    is_in_where_or_join_clause,
)
from fastssv.core.registry import register


DEATH_TABLE = "death"
PERSON_TABLE = "person"

DEATH_DATE = "death_date"
BIRTH_DATETIME = "birth_datetime"
YEAR_OF_BIRTH = "year_of_birth"


def _norm(x: str) -> str:
    return normalize_name(x) if x else ""


def _is_in_or(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Or):
            return True
        parent = parent.parent
    return False


def _is_year_function(node: exp.Expression) -> bool:
    if isinstance(node, exp.Extract):
        return _norm(node.args.get("this")) == "year"
    if isinstance(node, exp.Anonymous):
        return _norm(node.name) == "year"
    if isinstance(node, exp.Year):
        return True
    return False


def _extract_year_column(node: exp.Expression, aliases: Dict[str, str]):
    if not _is_year_function(node):
        return None

    for col in node.find_all(exp.Column):
        table, column = resolve_table_col(col, aliases)
        if _norm(table) == DEATH_TABLE and _norm(column) == DEATH_DATE:
            return True

    return False


def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    violations: List[str] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_or(node):
            continue

        if not isinstance(node, (exp.LT, exp.LTE, exp.GT, exp.GTE)):
            continue

        left = node.this
        right = node.expression

        # --- Direct comparison: death_date < birth_datetime ---
        if isinstance(left, exp.Column) and isinstance(right, exp.Column):
            lt, lc = resolve_table_col(left, aliases)
            rt, rc = resolve_table_col(right, aliases)

            # death_date < birth_datetime (or <=)
            if (isinstance(node, (exp.LT, exp.LTE)) and
                _norm(lt) == DEATH_TABLE and _norm(lc) == DEATH_DATE and
                _norm(rt) == PERSON_TABLE and _norm(rc) == BIRTH_DATETIME):
                key = "death_before_birth"
                if key not in seen:
                    seen.add(key)
                    violations.append(
                        "death_date occurs before birth_datetime, indicating impossible temporal logic."
                    )

            # birth_datetime > death_date (or >=) - reversed comparison
            elif (isinstance(node, (exp.GT, exp.GTE)) and
                  _norm(lt) == PERSON_TABLE and _norm(lc) == BIRTH_DATETIME and
                  _norm(rt) == DEATH_TABLE and _norm(rc) == DEATH_DATE):
                key = "death_before_birth"
                if key not in seen:
                    seen.add(key)
                    violations.append(
                        "death_date occurs before birth_datetime, indicating impossible temporal logic."
                    )

        # --- YEAR(death_date) < year_of_birth ---
        if isinstance(left, exp.Expression) and isinstance(right, exp.Column):
            # YEAR(death_date) < year_of_birth (or <=)
            if isinstance(node, (exp.LT, exp.LTE)) and _extract_year_column(left, aliases):
                rt, rc = resolve_table_col(right, aliases)
                if _norm(rt) == PERSON_TABLE and _norm(rc) == YEAR_OF_BIRTH:
                    key = "year_death_before_birth"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            "YEAR(death_date) is earlier than year_of_birth, indicating impossible temporal logic."
                        )

        # year_of_birth > YEAR(death_date) (or >=) - reversed comparison
        if isinstance(left, exp.Column) and isinstance(right, exp.Expression):
            if isinstance(node, (exp.GT, exp.GTE)) and _extract_year_column(right, aliases):
                lt, lc = resolve_table_col(left, aliases)
                if _norm(lt) == PERSON_TABLE and _norm(lc) == YEAR_OF_BIRTH:
                    key = "year_death_before_birth"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            "YEAR(death_date) is earlier than year_of_birth, indicating impossible temporal logic."
                        )

    return violations


@register
class DeathDateBeforeBirthValidationRule(Rule):
    rule_id = "temporal.death_date_before_birth_validation"
    name = "Death Date Before Birth Validation"

    description = (
        "Detects impossible temporal conditions where death occurs before birth."
    )

    severity = Severity.ERROR
    suggested_fix = "Ensure death_date >= birth_datetime"
    long_description = (
        "A predicate that allows death_date < birth_datetime to evaluate "
        "TRUE encodes an impossibility, no patient can die before they are "
        "born. When this condition appears in a WHERE or JOIN clause, it is "
        "almost always a column-swap bug: the tables on either side of the "
        "comparison have been reversed, or start/end columns from a "
        "template have been mixed up. The rule flags the impossible "
        "comparison so the author can restore the intended temporal "
        "ordering before the query silently returns zero rows."
    )
    example_bad = (
        "SELECT p.person_id\n"
        "FROM person p\n"
        "JOIN death d ON p.person_id = d.person_id\n"
        "WHERE d.death_date < p.birth_datetime;"
    )
    example_good = (
        "SELECT p.person_id\n"
        "FROM person p\n"
        "JOIN death d ON p.person_id = d.person_id\n"
        "WHERE d.death_date > p.birth_datetime;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, DEATH_TABLE):
                continue

            if not has_table_reference(tree, PERSON_TABLE):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                    )
                )

        return violations


__all__ = ["DeathDateBeforeBirthValidationRule"]

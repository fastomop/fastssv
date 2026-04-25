"""Person Year-of-Birth Age Arithmetic Rule.

Detects the common OMOP cohort-authoring pattern of computing patient age
by subtracting ``person.year_of_birth`` from a year expression:

    EXTRACT(YEAR FROM co.condition_start_date) - p.year_of_birth >= 65
    YEAR(visit_start_date) - year_of_birth
    2024 - person.year_of_birth

This rounds aggressively (a person born 1959-12-31 and an event on
2024-01-01 yields 2024-1959 = 65, even though the person is barely 64)
and silently ignores ``birth_datetime`` (or month/day) when present. The
correct pattern computes age from the full date, falling back to
year_of_birth only when birth_datetime is NULL.
"""

from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.patch import freeform
from fastssv.core.registry import register


YEAR_OF_BIRTH = "year_of_birth"
PERSON = "person"


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_year_of_birth_column(col: exp.Column, aliases: dict) -> bool:
    """True if ``col`` resolves to ``person.year_of_birth`` (qualified or
    unqualified-with-person-in-scope)."""
    table, col_name = resolve_table_col(col, aliases)
    if _norm(col_name) != YEAR_OF_BIRTH:
        return False
    if table:
        return _norm(table) == PERSON
    # Unqualified — accept only if person is the sole table in scope
    real_tables = {_norm(t) for t in aliases.values()}
    return real_tables == {PERSON}


def _is_year_extracting_expr(node: exp.Expression) -> bool:
    """True if ``node`` is a YEAR-returning expression: EXTRACT(YEAR FROM ...),
    YEAR(...), DATE_PART('year', ...), or a 4-digit year literal.
    """
    # Integer literal that looks like a year (1900–2099)
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            val = int(node.this)
            if 1900 <= val <= 2099:
                return True
        except (ValueError, TypeError):
            pass

    # YEAR(...) function
    if isinstance(node, exp.Year):
        return True

    # EXTRACT(YEAR FROM ...)
    if isinstance(node, exp.Extract):
        unit = node.this
        if isinstance(unit, exp.Var) and _norm(unit.name) == "year":
            return True
        if isinstance(unit, exp.Literal) and _norm(str(unit.this)) == "year":
            return True

    # DATE_PART('year', ...) — sqlglot wraps as Anonymous in many dialects
    if isinstance(node, exp.Anonymous):
        name = _norm(node.this) if hasattr(node, "this") else None
        if name == "date_part" and node.expressions:
            first = node.expressions[0]
            if isinstance(first, exp.Literal) and _norm(str(first.this)) == "year":
                return True

    return False


@register
class PersonYearOfBirthAgeArithmeticRule(Rule):
    """Flag age computed from ``year_of_birth`` alone."""

    rule_id = "domain_specific.year_of_birth_age_arithmetic"
    name = "Person Year-of-Birth Age Arithmetic"

    description = (
        "Computing age as `<year_expression> - person.year_of_birth` rounds the "
        "result by up to a year and silently ignores birth_datetime / month_of_birth / "
        "day_of_birth. Prefer full-date arithmetic when the data carries it."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `<year_expr> - p.year_of_birth` WITH full-date arithmetic: `FLOOR((event_date - p.birth_datetime) / 365.25)` when birth_datetime is populated, OR `FLOOR((event_date - MAKE_DATE(year_of_birth, COALESCE(month_of_birth, 7), COALESCE(day_of_birth, 1))) / 365.25)` as fallback."
    long_description = (
        "Many OMOP cohort definitions compute age at index by subtracting "
        "`person.year_of_birth` from the event year: "
        "`EXTRACT(YEAR FROM co.condition_start_date) - p.year_of_birth`. "
        "This drops the month and day entirely, so a person born 1959-12-31 "
        "with an event on 2024-01-01 evaluates as 65 even though they are "
        "barely 64. For age cutoffs (`>= 65`) this introduces systematic "
        "off-by-one errors. The OMOP person table also carries "
        "`birth_datetime` (and `month_of_birth` / `day_of_birth`) for sites "
        "that captured them; prefer full-date arithmetic when available."
    )

    example_bad = (
        "SELECT co.person_id\n"
        "FROM person p\n"
        "JOIN condition_occurrence co ON p.person_id = co.person_id\n"
        "WHERE EXTRACT(YEAR FROM co.condition_start_date) - p.year_of_birth >= 65;"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM person p\n"
        "JOIN condition_occurrence co ON p.person_id = co.person_id\n"
        "WHERE FLOOR((co.condition_start_date - p.birth_datetime) / 365.25) >= 65;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if YEAR_OF_BIRTH not in sql.lower():
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
            if PERSON not in {_norm(t) for t in aliases.values()}:
                continue

            # Look at every Sub (a - b). Fire when one side is year_of_birth and
            # the other side is a year-returning expression or another column.
            for sub in tree.find_all(exp.Sub):
                left, right = sub.this, sub.expression

                left_is_yob = isinstance(left, exp.Column) and _is_year_of_birth_column(left, aliases)
                right_is_yob = isinstance(right, exp.Column) and _is_year_of_birth_column(right, aliases)

                if not (left_is_yob or right_is_yob):
                    continue

                # Year - YOB pattern (canonical age calc)
                other_side = right if left_is_yob else left

                # Fire on year-returning expressions, integer literals (1900-2099),
                # or a column that's NOT another year_of_birth (rules out yob - yob).
                fires = (
                    _is_year_extracting_expr(other_side)
                    or (isinstance(other_side, exp.Column)
                        and not _is_year_of_birth_column(other_side, aliases))
                )
                if not fires:
                    continue

                key = sub.sql()
                if key in seen:
                    continue
                seen.add(key)

                # Structured patch: WRAP the `<year_expr> - year_of_birth`
                # subtraction in `FLOOR((... - p.birth_datetime) / 365.25)`
                # form. Since the right transformation depends on which
                # date the `year_expr` was extracted from (we'd need to
                # recover it), we emit FREEFORM with the prose fix —
                # WRAP isn't structurally accurate here and would yield
                # invalid SQL.
                patch = freeform(self.suggested_fix)

                violations.append(
                    self.create_violation(
                        message=(
                            f"Age computed as `{sub.sql()}`. Subtracting "
                            f"year_of_birth from a year value rounds up to a "
                            f"year and ignores month_of_birth / day_of_birth / "
                            f"birth_datetime — for cutoff comparisons "
                            f"(e.g. `>= 65`) this produces off-by-one errors."
                        ),
                        details={
                            "expression": sub.sql(),
                            "table": PERSON,
                            "column": YEAR_OF_BIRTH,
                        },
                        suggested_fix_patch=patch,
                    )
                )

        return violations


__all__ = ["PersonYearOfBirthAgeArithmeticRule"]

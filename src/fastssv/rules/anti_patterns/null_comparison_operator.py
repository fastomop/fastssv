"""NULL Comparison Operator Rule.

PostgreSQL: "Ordinary comparison operators yield null (signifying 'unknown'), not true or false, when either input
is null." A predicate that compares with the NULL keyword therefore never holds, so a WHERE / ON / HAVING filter
built on it removes every row, and a CASE branch built on it is never taken. ``IS NULL`` / ``IS NOT NULL`` are the
predicates that test for null.

Reported:
- ``x = NULL``, ``x <> NULL`` / ``!=``, ``x < NULL`` (and the other orderings), with NULL on either side, bare,
  parenthesised or cast;
- ``x IN (NULL)`` (every member NULL) and ``x NOT IN (..., NULL, ...)`` (a NULL member makes NOT IN never true);
- ``x BETWEEN NULL AND y`` and ``x BETWEEN y AND NULL``. Negated (``x NOT BETWEEN NULL AND y``), the predicate is
  true where the non-null bound fails (``x > y``) and unknown elsewhere, so it is not "never holds": it is a
  WARNING that states that result;
- ``CASE x WHEN NULL THEN ...``, which compares x = NULL.

Severity: ERROR for PostgreSQL and DuckDB, where the outcome was verified. One exception: PostgreSQL's
``transform_null_equals`` setting rewrites a bare ``x = NULL`` to ``x IS NULL``, so that form is a WARNING there.
Other dialects get a WARNING, because some engines change NULL comparison semantics by configuration (SQL Server
``ANSI_NULLS OFF``).

Not reported: assignments (``UPDATE ... SET col = NULL``, MERGE / ON CONFLICT updates, ``SET @var = NULL``, T-SQL
``SELECT @var = NULL``), named arguments of table functions (DuckDB ``read_csv(..., nullstr = NULL)``), the string
``'NULL'``, and ``IN`` lists that still hold a non-null member.
"""

from typing import List, Optional

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import freeform, locate, replace
from fastssv.core.registry import register

_VERIFIED_DIALECTS = {"postgres", "postgresql", "duckdb"}

# `col = NULL` directly under these is an assignment, not a comparison: UPDATE ... SET (also MERGE's
# WHEN ... THEN UPDATE SET), INSERT ... ON CONFLICT / ON DUPLICATE KEY UPDATE, and SET @var = NULL.
_ASSIGNMENT_PARENTS = {
    exp.Update: "expressions",
    exp.OnConflict: "expressions",
    exp.SetItem: "this",
}

_COMPARISONS = {exp.EQ: "=", exp.NEQ: "<>", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}


def _null_literal(node: Optional[exp.Expression]) -> Optional[exp.Null]:
    """The NULL keyword under parentheses or casts; the string 'NULL' is an ordinary value."""
    while isinstance(node, (exp.Paren, exp.Cast, exp.TryCast)):
        node = node.this
    return node if isinstance(node, exp.Null) else None


def _is_assignment(node: exp.Expression) -> bool:
    expected = _ASSIGNMENT_PARENTS.get(type(node.parent))
    if expected is not None and node.arg_key == expected:
        return True
    # T-SQL `SELECT @var = NULL [FROM ...]` assigns the variable; it is not a projected comparison.
    return (
        isinstance(node, exp.EQ)
        and isinstance(node.parent, exp.Select)
        and node.arg_key == "expressions"
        and isinstance(node.this, exp.Parameter)
    )


def _is_table_function_argument(node: exp.Expression) -> bool:
    """``name = value`` passed to a function in FROM is a named argument (DuckDB ``read_csv(..., nullstr = NULL)``)."""
    parent = node.parent
    if not isinstance(parent, (exp.Func, exp.Anonymous)) or isinstance(parent, (exp.Connector, exp.Not)):
        return False
    return isinstance(parent.parent, (exp.Table, exp.From, exp.Join))


def _negated(node: exp.Expression) -> bool:
    parent = node.parent
    while isinstance(parent, exp.Paren):
        parent = parent.parent
    return isinstance(parent, exp.Not)


def _context(node: exp.Expression) -> str:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Where):
            return "WHERE clause"
        if isinstance(parent, (exp.Case, exp.If)):
            return "CASE expression"
        if isinstance(parent, exp.Join):
            return "JOIN ON clause"
        if isinstance(parent, exp.Having):
            return "HAVING clause"
        parent = parent.parent
    return "expression"


@register
class NullComparisonOperatorRule(Rule):
    """Comparison with the NULL keyword, which never holds."""

    rule_id = "anti_patterns.null_comparison_operator"
    name = "NULL Comparison Must Use IS NULL / IS NOT NULL"

    description = (
        "A comparison with NULL (=, <>, !=, <, >, <=, >=, IN (NULL), NOT IN with a NULL member, BETWEEN NULL, "
        "CASE x WHEN NULL) yields unknown, so it never holds. Use IS NULL or IS NOT NULL instead."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "REPLACE: `<col> = NULL` WITH `<col> IS NULL`, and `<col> <> NULL` / `<col> != NULL` WITH `<col> IS NOT NULL`. "
        "Remove NULL from a NOT IN list, and write `CASE WHEN <col> IS NULL THEN ...` instead of `CASE <col> WHEN NULL`."
    )
    long_description = (
        "Ordinary comparison operators yield unknown, not true or false, when either input is null. A predicate "
        "like `WHERE death_date = NULL` therefore returns no rows, `x NOT IN (1, NULL)` is never true, and "
        "`CASE x WHEN NULL` never takes its branch. IS NULL and IS NOT NULL are the predicates that test for null. "
        "The finding is an ERROR for PostgreSQL and DuckDB, except a bare `x = NULL` on PostgreSQL, which the "
        "transform_null_equals setting rewrites to `x IS NULL`; that form, and every form in other dialects, is a "
        "WARNING because engine configuration can change NULL comparison semantics."
    )
    example_bad = "SELECT person_id\nFROM death\nWHERE death_date <> NULL;"
    example_good = "SELECT person_id\nFROM death\nWHERE death_date IS NOT NULL;"
    extra_examples = (
        RuleExample(
            sql="SELECT person_id\nFROM death\nWHERE death_date = NULL;",
            severity=Severity.WARNING,
            note="Bare `= NULL` on PostgreSQL: transform_null_equals would rewrite it to IS NULL",
        ),
    )
    references = (
        Reference(
            "PostgreSQL documentation: Comparison Functions and Operators",
            "https://www.postgresql.org/docs/current/functions-comparison.html",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "null" not in sql.lower():
            return []
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        name = (dialect or "postgres").lower()
        verified = name in _VERIFIED_DIALECTS
        postgres = name in ("postgres", "postgresql")
        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            for node in tree.walk():
                found = self._inspect(sql, node, verified, postgres)
                if found is not None:
                    violations.append(found)
        return violations

    def _inspect(self, sql: str, node: exp.Expression, verified: bool, postgres: bool) -> Optional[RuleViolation]:
        if isinstance(node, tuple(_COMPARISONS)):
            if _is_assignment(node) or _is_table_function_argument(node):
                return None
            left_null, right_null = _null_literal(node.this), _null_literal(node.expression)
            if not (left_null or right_null):
                return None
            operator = _COMPARISONS[type(node)]
            other = node.expression if left_null else node.this
            span = locate(sql, node.sql())
            if span is None and isinstance(node, exp.NEQ):
                span = locate(sql, node.sql().replace("<>", "!="))
            if span and isinstance(node, exp.NEQ) and "!=" in sql[span[0] : span[1]]:
                operator = "!="
            bare = isinstance(node.this if left_null else node.expression, exp.Null)
            transformable = postgres and isinstance(node, exp.EQ) and bare
            patch = None
            if span and isinstance(node, exp.EQ):
                patch = replace(span, f"{other.sql()} IS NULL")
            elif span and isinstance(node, exp.NEQ):
                patch = replace(span, f"{other.sql()} IS NOT NULL")
            return self._violation(
                f"'{operator}' with NULL", node, verified and not transformable, patch, transformable
            )
        if isinstance(node, exp.In) and not node.args.get("query"):
            members = list(node.expressions or [])
            nulls = [m for m in members if _null_literal(m)]
            if not nulls:
                return None
            negated = isinstance(node.parent, exp.Not)
            if negated:
                return self._violation("NOT IN with a NULL member", node, verified, None, False)
            if len(nulls) == len(members):
                return self._violation("IN (NULL)", node, verified, None, False)
            return None
        if isinstance(node, exp.Between):
            low, high = node.args.get("low"), node.args.get("high")
            low_null, high_null = _null_literal(low), _null_literal(high)
            if not (low_null or high_null):
                return None
            if low_null and high_null or not _negated(node):
                return self._violation("BETWEEN with a NULL bound", node, verified, None, False)
            return self._negated_between(node, low if high_null else high, ">" if low_null else "<")
        if isinstance(node, exp.Case) and node.this is not None:
            for branch in node.args.get("ifs") or []:
                if _null_literal(branch.this):
                    return self._violation("CASE <expr> WHEN NULL", node, verified, None, False)
        return None

    def _negated_between(self, node: exp.Between, bound: exp.Expression, operator: str) -> RuleViolation:
        # x BETWEEN NULL AND y is false where x > y and unknown elsewhere, so its negation is true exactly there.
        subject = node.this.sql()
        holds = f"{subject} {operator} {bound.sql()}"
        message = (
            f"NOT BETWEEN with a NULL bound in {_context(node)}: the NULL bound makes the BETWEEN unknown wherever "
            f"the other bound holds, so the negation is true only where {holds} and unknown for every other row, "
            f"including rows where {subject} is NULL. As a filter it keeps exactly the rows with `{holds}`; write "
            "that comparison if it is what was meant, or use IS NULL / IS NOT NULL to test for null."
        )
        return self.create_violation(
            message=message,
            severity=Severity.WARNING,
            suggested_fix_patch=freeform(self.suggested_fix),
            details={"form": "NOT BETWEEN with a NULL bound", "context": _context(node), "holds_where": holds},
        )

    def _violation(self, form, node, certain, patch, transformable) -> RuleViolation:
        consequence = (
            "never holds: a filter on it removes every row and a CASE branch on it is never taken"
            if form != "CASE <expr> WHEN NULL"
            else "never takes that branch, because it compares <expr> = NULL"
        )
        message = f"Incorrect NULL comparison using {form} in {_context(node)}: under SQL semantics it {consequence}."
        if transformable:
            message += " (PostgreSQL rewrites `x = NULL` to `x IS NULL` only when transform_null_equals is on.)"
        elif not certain:
            message += " (Engine settings such as SQL Server ANSI_NULLS OFF can change this.)"
        message += " Use IS NULL or IS NOT NULL instead."
        return self.create_violation(
            message=message,
            severity=Severity.ERROR if certain else Severity.WARNING,
            suggested_fix_patch=patch or freeform(self.suggested_fix),
            details={"form": form, "context": _context(node)},
        )


__all__ = ["NullComparisonOperatorRule"]

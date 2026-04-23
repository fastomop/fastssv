"""Tests for `fastssv.core.helpers.parse_sql` and the top-level
`validate_sql_structured` entry point's rejection of non-SQL text.

sqlglot is a lenient parser — it will happily return a tree for `select`
alone (empty Select) or `hello world` (an Alias expression). These tests
lock in the structural validity checks that reject such inputs so the
validator surfaces a clean `parse.syntax_error` violation instead of
silently reporting "valid".
"""

from __future__ import annotations

import pytest

from fastssv import PARSE_ERROR_RULE_ID, validate_sql_structured
from fastssv.core.helpers import parse_sql


# ---- parse_sql: structural validity -----------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "select",
        "SELECT",
        "   select   ",
    ],
)
def test_bare_select_keyword_rejected(sql: str) -> None:
    trees, err = parse_sql(sql)
    assert trees is None
    assert err is not None
    assert "Incomplete SELECT" in err


@pytest.mark.parametrize(
    "sql",
    [
        "hello world",
        "not sql at all",
        "x",
    ],
)
def test_non_sql_text_rejected(sql: str) -> None:
    """Some non-SQL inputs raise sqlglot's ParseError; others tokenize into a
    stray Alias/Column that our new structural check rejects. Both outcomes
    are acceptable — both surface as a parse-error violation downstream.
    """
    trees, err = parse_sql(sql)
    assert trees is None
    assert err is not None
    assert (
        "did not parse as a SQL statement" in err
        or "SQL parse error" in err
    )


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "\n\t",
    ],
)
def test_empty_and_whitespace_rejected(sql: str) -> None:
    trees, err = parse_sql(sql)
    assert trees is None
    assert "Empty or whitespace-only" in err


def test_comment_only_rejected() -> None:
    trees, err = parse_sql("/* just a comment */")
    assert trees is None
    assert err is not None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT person_id FROM person",
        "SELECT person_id FROM person WHERE year_of_birth > 1980",
        "WITH cc AS (SELECT 1) SELECT * FROM cc",
        "INSERT INTO person (person_id) VALUES (1)",
        "UPDATE person SET year_of_birth = 1990 WHERE person_id = 1",
        "DELETE FROM person WHERE person_id = 1",
    ],
)
def test_real_sql_parses_cleanly(sql: str) -> None:
    trees, err = parse_sql(sql)
    assert err is None
    assert trees is not None
    assert all(t is not None for t in trees)


# ---- validate_sql_structured surfaces as PARSE_ERROR_RULE_ID ----------------


@pytest.mark.parametrize(
    "sql",
    ["select", "hello world", ""],
)
def test_rejection_surfaces_as_parse_error_violation(sql: str) -> None:
    violations = validate_sql_structured(sql)
    assert len(violations) == 1
    assert violations[0].rule_id == PARSE_ERROR_RULE_ID
    assert violations[0].severity.value == "error"


def test_valid_sql_does_not_raise_parse_error() -> None:
    violations = validate_sql_structured("SELECT person_id FROM person")
    parse_errors = [v for v in violations if v.rule_id == PARSE_ERROR_RULE_ID]
    assert parse_errors == []

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

from fastssv import NOT_SQL_RULE_ID, PARSE_ERROR_RULE_ID, validate_sql_structured
from fastssv.core.helpers import looks_like_prose, parse_sql


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
    assert "did not parse as a SQL statement" in err or "SQL parse error" in err


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
    # Prose inputs now route to NOT_SQL_RULE_ID; bare/empty SQL stays on
    # PARSE_ERROR_RULE_ID. Both indicate the validator declined to run rules.
    violations = validate_sql_structured(sql)
    assert len(violations) == 1
    assert violations[0].rule_id in {PARSE_ERROR_RULE_ID, NOT_SQL_RULE_ID}
    assert violations[0].severity.value == "error"


def test_valid_sql_does_not_raise_parse_error() -> None:
    violations = validate_sql_structured("SELECT person_id FROM person")
    parse_errors = [v for v in violations if v.rule_id == PARSE_ERROR_RULE_ID]
    assert parse_errors == []


# ---- looks_like_prose: heuristic for distinguishing prose from SQL ----------


@pytest.mark.parametrize(
    "text",
    [
        "It appears that direct table relationships between `base.condition_occurrence` and `base.drug_exposure` are unavailable.",
        "I cannot generate this query because the schema is missing.",
        "Sorry, but that is not feasible with the current schema.",
        "The query is infeasible.",
        "hello world",
    ],
)
def test_prose_detected(text: str) -> None:
    assert looks_like_prose(text) is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "  select person_id from person",
        "WITH cc AS (SELECT 1) SELECT * FROM cc",
        "(SELECT 1)",
        "((SELECT 1))",
        "-- a comment\nSELECT 1",
        "/* block */ SELECT 1",
        "EXPLAIN SELECT 1",
        "INSERT INTO t VALUES (1)",
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET x = 1",
    ],
)
def test_real_sql_not_flagged_as_prose(sql: str) -> None:
    assert looks_like_prose(sql) is False


@pytest.mark.parametrize("text", ["", "   ", "\n\t", "/* only comment */"])
def test_empty_and_comment_only_not_flagged_as_prose(text: str) -> None:
    # Empty/comment-only inputs are handled by parse_sql's other checks; the
    # prose heuristic should not claim them as prose.
    assert looks_like_prose(text) is False


# ---- validate_sql_structured surfaces NOT_SQL_RULE_ID for prose -------------


def test_prose_input_surfaces_as_not_sql_violation() -> None:
    prose = (
        "It appears that direct table relationships between "
        "`base.condition_occurrence` and `base.drug_exposure` are unavailable, "
        "making the query infeasible with the current schema information."
    )
    violations = validate_sql_structured(prose)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == NOT_SQL_RULE_ID
    assert v.severity.value == "error"
    # Suggested fix must NOT recommend a dialect retry — that was the bug.
    # (It may *mention* dialect to explicitly tell callers not to retry, but
    # it must not advise switching to tsql/postgres/etc.)
    fix_lower = v.suggested_fix.lower()
    assert "try dialect=" not in fix_lower
    assert "tsql" not in fix_lower
    assert "re-prompt" in fix_lower or "not sql" in fix_lower


def test_genuine_syntax_error_still_uses_parse_error_rule() -> None:
    # Starts with SELECT so the prose heuristic shouldn't match — this is a
    # real malformed-SQL case where the dialect-retry hint is appropriate.
    violations = validate_sql_structured("SELECT FROM WHERE")
    assert len(violations) == 1
    assert violations[0].rule_id == PARSE_ERROR_RULE_ID

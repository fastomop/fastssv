"""CLI integration tests covering batch path, stdin, and comment-aware splitting.

The existing `tests/api/test_cli_serve.py` covers the `serve` subcommand and
basic dispatch. This file covers the validation CLI itself — especially the
multi-query batch path and the `_split_queries` state machine.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from fastssv import cli as cli_module
from fastssv.cli import _clean_llm_output, _split_queries, main


# ---- main(...) end-to-end ---------------------------------------------------


def test_main_batch_multiple_queries_writes_grouped_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sql = """
    SELECT person_id FROM person;
    SELECT * FROM no_such_table;
    SELECT person_id FROM person WHERE year_of_birth > 1980;
    """
    sql_file = tmp_path / "batch.sql"
    sql_file.write_text(sql)
    out = tmp_path / "out.json"
    monkeypatch.chdir(tmp_path)

    rc = main([str(sql_file), "--output", str(out), "--log-level", "WARNING"])
    report = json.loads(out.read_text())

    # One invalid query in the batch → non-zero exit code.
    assert rc == 1
    assert report["total_queries"] == 3
    assert report["valid_queries"] == 2
    assert report["invalid_queries"] == 1
    assert len(report["results"]) == 3


def test_main_reads_from_stdin_when_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "stdin_out.json"
    monkeypatch.chdir(tmp_path)

    # Fake a piped stdin so `_read_sql`'s isatty() path returns False.
    fake_stdin = io.StringIO("SELECT person_id FROM person;")
    fake_stdin.isatty = lambda: False  # type: ignore[assignment]
    monkeypatch.setattr("sys.stdin", fake_stdin)

    rc = main(["--output", str(out), "--log-level", "WARNING"])
    assert rc == 0
    report = json.loads(out.read_text())
    # Single-query path uses the non-batch branch.
    assert "results" in report or "is_valid" in report


def test_main_no_file_and_tty_stdin_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_stdin = io.StringIO("")
    fake_stdin.isatty = lambda: True  # type: ignore[assignment]
    monkeypatch.setattr("sys.stdin", fake_stdin)
    with pytest.raises(SystemExit):
        main(["--log-level", "WARNING"])


# ---- _split_queries state machine -------------------------------------------


def test_split_queries_ignores_semicolons_in_line_comments() -> None:
    sql = "SELECT 1; -- comment with ; in it\nSELECT 2;"
    queries = _split_queries(sql)
    assert len(queries) == 2


def test_split_queries_ignores_semicolons_in_block_comments() -> None:
    sql = "SELECT 1; /* block ; comment */ SELECT 2;"
    queries = _split_queries(sql)
    assert len(queries) == 2


def test_split_queries_ignores_semicolons_inside_strings() -> None:
    sql = "SELECT 'a;b;c'; SELECT 2;"
    queries = _split_queries(sql)
    assert len(queries) == 2


def test_split_queries_single_statement_no_trailing_semicolon() -> None:
    queries = _split_queries("SELECT 1")
    assert len(queries) == 1


# ---- _clean_llm_output ------------------------------------------------------


def test_clean_llm_output_strips_fenced_sql_block() -> None:
    raw = "Sure, here's the query:\n```sql\nSELECT 1;\n```\nHope this helps!"
    cleaned = _clean_llm_output(raw)
    assert cleaned.startswith("SELECT")
    assert "Hope this helps" not in cleaned
    assert "```" not in cleaned


def test_clean_llm_output_handles_unfenced_text_trails() -> None:
    raw = "SELECT 1;\nThis selects the number one."
    cleaned = _clean_llm_output(raw)
    # Everything after the last `;` is discarded.
    assert cleaned.endswith(";")
    assert "selects" not in cleaned


# ---- build_validation_result ------------------------------------------------


def test_build_validation_result_shape() -> None:
    from fastssv.core.base import RuleViolation, Severity

    violations = [
        RuleViolation(
            rule_id="a.b",
            severity=Severity.ERROR,
            message="bad",
            suggested_fix="fix it",
            details={},
        ),
        RuleViolation(
            rule_id="x.y",
            severity=Severity.WARNING,
            message="eh",
            suggested_fix="mhm",
            details={},
        ),
    ]
    result = cli_module.build_validation_result("SELECT 1;", violations, "postgres")
    assert result["is_valid"] is False
    assert result["error_count"] == 1
    assert result["warning_count"] == 1
    assert "errors" in result and "warnings" in result

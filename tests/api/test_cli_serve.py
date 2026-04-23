"""Tests for the `fastssv serve` CLI subcommand.

These tests exercise argument parsing and the `--prod`-path fallback messages;
they do NOT boot an actual HTTP server.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("fastapi")

from fastssv.cli import _serve_command, main  # noqa: E402


def test_serve_help_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        _serve_command(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--reload" in out
    assert "--prod" in out
    assert "--workers" in out


def test_serve_invokes_uvicorn_with_expected_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)

    rc = _serve_command(["--host", "0.0.0.0", "--port", "9000", "--reload"])
    assert rc == 0
    assert captured["app"] == "fastssv.api.app:app"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000
    assert captured["reload"] is True


def test_serve_prod_missing_gunicorn_reports_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)

    rc = _serve_command(["--prod"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "gunicorn not found" in err
    assert "pip install 'fastssv[api]'" in err


def test_serve_missing_uvicorn_reports_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Simulate missing uvicorn by booby-trapping the import.
    real_uvicorn = sys.modules.pop("uvicorn", None)
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    try:
        rc = _serve_command([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "fastssv[api]" in err
    finally:
        if real_uvicorn is not None:
            sys.modules["uvicorn"] = real_uvicorn


def test_main_dispatches_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []

    def fake_serve(argv):
        calls.append(list(argv))
        return 0

    monkeypatch.setattr("fastssv.cli._serve_command", fake_serve)

    rc = main(["serve", "--port", "9090"])
    assert rc == 0
    assert calls == [["--port", "9090"]]


def test_main_without_serve_still_validates(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: existing `fastssv <sqlfile>` behavior is unchanged."""
    sql_file = tmp_path / "q.sql"
    sql_file.write_text("SELECT person_id FROM person;\n")
    out = tmp_path / "out.json"
    monkeypatch.chdir(tmp_path)

    rc = main([str(sql_file), "--output", str(out), "--log-level", "WARNING"])
    assert rc == 0
    assert out.exists()

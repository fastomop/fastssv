"""Unit tests for the JSON log formatter.

The formatter must serialize *every* field passed via ``extra=`` — the API
attaches request_id/sql_hash/dialect/... and dropping them (the old
fixed-allowlist behaviour) made request-ID correlation impossible.
"""

from __future__ import annotations

import json
import logging

from fastssv.core.logging import JSONFormatter


def _format(msg: str = "hello", **extra) -> dict:
    record = logging.LogRecord(
        name="fastssv.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(JSONFormatter().format(record))


def test_base_fields_present() -> None:
    data = _format("validation_complete")
    assert data["message"] == "validation_complete"
    assert data["level"] == "INFO"
    assert data["logger"] == "fastssv.test"
    assert "timestamp" in data


def test_all_extra_fields_serialized() -> None:
    data = _format(
        "validation_complete",
        request_id="abc123",
        sql_hash="deadbeef",
        dialect="postgres",
        strict=False,
        query_count=3,
        duration_ms=12.5,
        rule_id="joins.person_id_join_validation",
        violation_count=2,
    )
    assert data["request_id"] == "abc123"
    assert data["sql_hash"] == "deadbeef"
    assert data["dialect"] == "postgres"
    assert data["strict"] is False
    assert data["query_count"] == 3
    assert data["duration_ms"] == 12.5
    assert data["rule_id"] == "joins.person_id_join_validation"
    assert data["violation_count"] == 2


def test_reserved_logrecord_attrs_not_leaked() -> None:
    data = _format("x", request_id="abc")
    for noise in ("args", "levelno", "pathname", "process", "thread", "msecs"):
        assert noise not in data


def test_non_serializable_extra_falls_back_to_repr() -> None:
    class Opaque:
        def __repr__(self) -> str:
            return "<opaque thing>"

    data = _format("x", weird=Opaque())
    assert data["weird"] == "<opaque thing>"


def test_exception_info_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="fastssv.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    data = json.loads(JSONFormatter().format(record))
    assert "ValueError: boom" in data["exception"]

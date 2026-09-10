"""Tests for the FastSSV HTTP API."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("slowapi")

from fastapi.testclient import TestClient  # noqa: E402

from fastssv.api.app import create_app  # noqa: E402
from fastssv.api.config import Settings  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    settings = Settings(
        max_sql_bytes=1024,
        parse_timeout_seconds=2.0,
        rate_limit="1000/minute",
        cors_origins=[],
        log_level="WARNING",
        # Explicit so the test is deterministic regardless of any local
        # `.env` that might enable MCP for dev convenience.
        mcp_enabled=False,
    )
    app = create_app(settings)
    return TestClient(app)


def test_health_returns_ok_and_rules_count(client: TestClient):
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["rules_loaded"] > 0
    assert "version" in body
    # The default `client` fixture leaves MCP off; this guards against the
    # field disappearing or flipping its default in HealthResponse.
    assert body["mcp_mounted"] is False


def test_validate_valid_query_returns_no_errors(client: TestClient):
    resp = client.post(
        "/v1/validate",
        json={"sql": "SELECT person_id FROM person;", "dialect": "postgres"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is True
    assert body["error_count"] == 0
    assert body["dialect"] == "postgres"
    assert "duration_ms" in body


def test_validate_unknown_table_returns_schema_error(client: TestClient):
    resp = client.post(
        "/v1/validate",
        json={"sql": "SELECT * FROM nonexistent_table;", "dialect": "postgres"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is False
    assert body["error_count"] >= 1
    rule_ids = {e["rule_id"] for e in body["errors"]}
    assert "data_quality.schema_validation" in rule_ids


def test_validate_empty_sql_rejected(client: TestClient):
    resp = client.post("/v1/validate", json={"sql": "", "dialect": "postgres"})
    assert resp.status_code == 422


# A vocabulary join without a standard-concept filter (and without concept_ancestor) — still a warning
# under the narrowed rule; escalates to an error in strict mode.
_STRICT_ESCALATION_SQL = """
SELECT co.person_id FROM condition_occurrence co
JOIN concept c ON c.concept_id = co.condition_concept_id
WHERE c.concept_name LIKE '%diabetes%'
"""


def test_validate_default_is_non_strict(client: TestClient):
    resp = client.post(
        "/v1/validate",
        json={"sql": _STRICT_ESCALATION_SQL, "dialect": "postgres"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strict"] is False
    # Best-practice rule stays a WARNING; query is still is_valid=true.
    assert body["is_valid"] is True
    assert any(w["rule_id"] == "concept_standardization.standard_concept_enforcement" for w in body["warnings"])


def test_validate_single_statement_has_one_result(client: TestClient):
    resp = client.post(
        "/v1/validate",
        json={"sql": "SELECT person_id FROM person", "dialect": "postgres"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_count"] == 1
    assert len(body["results"]) == 1
    first = body["results"][0]
    assert first["query_index"] == 1
    assert first["is_valid"] is True


def test_validate_multi_statement_attributes_per_query(client: TestClient):
    resp = client.post(
        "/v1/validate",
        json={
            "sql": ("SELECT person_id FROM person; SELECT * FROM bogus_table_alpha; SELECT * FROM bogus_table_beta;"),
            "dialect": "postgres",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query_count"] == 3
    assert body["error_count"] == 2
    assert len(body["results"]) == 3
    assert body["results"][0]["is_valid"] is True
    assert body["results"][0]["query_index"] == 1
    assert body["results"][1]["is_valid"] is False
    assert body["results"][1]["query_index"] == 2
    assert any("bogus_table_alpha" in e["issue"] for e in body["results"][1]["errors"])
    assert body["results"][2]["query_index"] == 3
    assert any("bogus_table_beta" in e["issue"] for e in body["results"][2]["errors"])


def test_validate_strict_escalates_warning_to_error(client: TestClient):
    resp = client.post(
        "/v1/validate",
        json={"sql": _STRICT_ESCALATION_SQL, "dialect": "postgres", "strict": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strict"] is True
    assert body["is_valid"] is False
    # Standard-concept rule now reports as an ERROR.
    assert any(e["rule_id"] == "concept_standardization.standard_concept_enforcement" for e in body["errors"])


def test_validate_bad_dialect_rejected(client: TestClient):
    resp = client.post(
        "/v1/validate",
        json={"sql": "SELECT 1;", "dialect": "mysql"},
    )
    assert resp.status_code == 422


def test_validate_oversized_body_rejected_by_middleware(client: TestClient):
    big = "SELECT 1; " + ("-- pad" * 500)  # > 1024 bytes
    resp = client.post(
        "/v1/validate",
        json={"sql": big, "dialect": "postgres"},
    )
    assert resp.status_code == 413
    assert resp.json()["error"] == "payload_too_large"


def test_rules_endpoint_lists_registered_rules(client: TestClient):
    resp = client.get("/v1/rules")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0
    assert len(body["rules"]) == body["total"]
    first = body["rules"][0]
    for key in ("rule_id", "name", "description", "severity", "category"):
        assert key in first


def test_security_headers_present(client: TestClient):
    resp = client.get("/v1/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in resp.headers
    assert "Strict-Transport-Security" in resp.headers
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_validation_capacity_returns_503(client: TestClient):
    """With the per-worker limiter saturated, /v1/validate fails fast with
    503 instead of queueing onto (potentially pinned) worker threads."""
    limiter = client.app.state.validation_limiter
    held = 0
    while limiter.try_acquire():
        held += 1
    try:
        resp = client.post(
            "/v1/validate",
            json={"sql": "SELECT person_id FROM person;", "dialect": "postgres"},
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "service_unavailable"
        assert resp.headers["Retry-After"] == "1"
    finally:
        for _ in range(held):
            limiter.release()

    # Released → requests flow again.
    resp = client.post(
        "/v1/validate",
        json={"sql": "SELECT person_id FROM person;", "dialect": "postgres"},
    )
    assert resp.status_code == 200


def test_timeout_does_not_free_capacity_while_thread_runs(monkeypatch):
    """A 408 must NOT release the limiter permit: the abandoned parse still
    occupies its worker thread, so capacity frees only when the thread ends.
    Guards against the `async with semaphore` regression where wait_for's
    timeout exit released the permit while the thread kept running."""
    import threading
    import time as _time

    settings = Settings(
        max_sql_bytes=4096,
        parse_timeout_seconds=0.05,
        rate_limit="1000/minute",
        cors_origins=[],
        log_level="WARNING",
        mcp_enabled=False,
        max_concurrent_validations=1,
    )
    app = create_app(settings)
    finish_worker = threading.Event()

    def fake_validate_each(statements, dialect):
        finish_worker.wait(timeout=10)
        return []

    monkeypatch.setattr("fastssv.api._validation._validate_each", fake_validate_each)

    payload = {"sql": "SELECT person_id FROM person;", "dialect": "postgres"}
    # Context-managed client keeps one event loop alive across requests so
    # the worker future's done callback (which releases the permit) runs.
    with TestClient(app) as client:
        limiter = app.state.validation_limiter

        resp = client.post("/v1/validate", json=payload)
        assert resp.status_code == 408
        # Client got 408, but the worker thread is still parked → the
        # permit must still be held...
        assert limiter.active == 1
        # ...so the next request is refused rather than starting a second
        # thread the pool can't afford.
        resp = client.post("/v1/validate", json=payload)
        assert resp.status_code == 503

        # Let the worker finish; the done callback releases the permit.
        finish_worker.set()
        deadline = _time.time() + 5
        while limiter.active and _time.time() < deadline:
            _time.sleep(0.01)
            client.get("/v1/health")
        assert limiter.active == 0

        assert client.post("/v1/validate", json=payload).status_code == 200


def test_health_exempt_from_rate_limit():
    """LB/kubelet probes must never be throttled into 429s by client traffic
    from the same source IP."""
    settings = Settings(
        max_sql_bytes=1024,
        rate_limit="2/minute",
        cors_origins=[],
        log_level="WARNING",
        mcp_enabled=False,
    )
    limited = TestClient(create_app(settings))
    for _ in range(6):
        assert limited.get("/v1/health").status_code == 200

    # The default limit does apply to everything else.
    codes = [
        limited.post(
            "/v1/validate",
            json={"sql": "SELECT person_id FROM person;", "dialect": "postgres"},
        ).status_code
        for _ in range(3)
    ]
    assert 429 in codes


def test_request_id_echoed(client: TestClient):
    rid = "test-abc-123"
    resp = client.get("/v1/health", headers={"x-request-id": rid})
    assert resp.headers["x-request-id"] == rid


def test_error_response_includes_request_id(client: TestClient):
    resp = client.post("/v1/validate", json={"sql": "x" * 2048})
    assert resp.status_code == 413
    body = resp.json()
    assert body["error"] == "payload_too_large"
    assert "request_id" in body


def test_auto_dialect_resolves_cross_statement_scope(client: TestClient):
    """dialect="auto" (the API default) must not break cross-statement
    local-table scoping: the collectors can't parse with a pseudo-dialect,
    so "auto" is resolved via detect_dialect before they run."""
    sql = "CREATE TABLE tempresults AS SELECT person_id FROM person; SELECT person_id FROM tempresults;"
    resp = client.post("/v1/validate", json={"sql": sql})  # dialect defaults to "auto"
    assert resp.status_code == 200
    body = resp.json()
    # The scratch table created in statement 1 is in scope for statement 2.
    assert not any(
        v["rule_id"] == "data_quality.schema_validation" and "tempresults" in v["issue"] for v in body["errors"]
    ), body["errors"]
    # The response reports the dialect actually used, not the "auto" sentinel.
    assert body["dialect"] != "auto"

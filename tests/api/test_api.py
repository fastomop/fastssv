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


_STRICT_ESCALATION_SQL = """
WITH cc AS (
    SELECT descendant_concept_id AS concept_id FROM concept_ancestor
    WHERE ancestor_concept_id IN (320128)
)
SELECT person_id FROM condition_occurrence co
WHERE co.condition_concept_id IN (SELECT concept_id FROM cc)
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

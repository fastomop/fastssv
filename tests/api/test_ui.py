"""Tests for the FastSSV HTMX frontend."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient  # noqa: E402

from fastssv.api.app import create_app  # noqa: E402
from fastssv.api.config import Settings  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    settings = Settings(
        max_sql_bytes=2048,
        parse_timeout_seconds=2.0,
        rate_limit="1000/minute",
        cors_origins=[],
        log_level="WARNING",
    )
    app = create_app(settings)
    return TestClient(app)


def test_index_renders_html(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "<textarea" in body
    assert 'hx-post="/ui/validate"' in body
    assert "/static/htmx.min.js" in body
    assert "/static/style.css" in body


def test_rules_page_renders_all_rules(client: TestClient):
    resp = client.get("/rules")
    assert resp.status_code == 200
    body = resp.text
    assert body.count('class="rule-card"') > 100  # 157 in practice
    assert "rule-filter" in body


def test_static_css_served(client: TestClient):
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


def test_static_htmx_served(client: TestClient):
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_ui_validate_valid_query_renders_ok_banner(client: TestClient):
    resp = client.post(
        "/ui/validate",
        data={"sql": "SELECT person_id FROM person;", "dialect": "postgres"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "banner-ok" in body
    assert "Valid" in body


def test_ui_validate_invalid_table_renders_error_card(client: TestClient):
    resp = client.post(
        "/ui/validate",
        data={"sql": "SELECT * FROM no_such_table;", "dialect": "postgres"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "sev-error" in body
    assert "data_quality.schema_validation" in body
    assert "no_such_table" in body


def test_ui_validate_empty_sql_returns_error_fragment(client: TestClient):
    resp = client.post(
        "/ui/validate",
        data={"sql": "   ", "dialect": "postgres"},
    )
    assert resp.status_code == 422
    body = resp.text
    assert "banner-error" in body
    assert "empty" in body.lower()


def test_ui_validate_bad_dialect_returns_error_fragment(client: TestClient):
    resp = client.post(
        "/ui/validate",
        data={"sql": "SELECT 1;", "dialect": "mysql"},
    )
    assert resp.status_code == 422
    body = resp.text
    assert "Invalid dialect" in body


def test_ui_validate_oversized_rejected_by_middleware(client: TestClient):
    # The body-size middleware kicks in before the route.
    big = "a" * 4096
    resp = client.post(
        "/ui/validate",
        data={"sql": big, "dialect": "postgres"},
    )
    assert resp.status_code == 413


def test_ui_security_headers_present_on_html(client: TestClient):
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in resp.headers


def test_v1_json_api_still_reachable_alongside_ui(client: TestClient):
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"

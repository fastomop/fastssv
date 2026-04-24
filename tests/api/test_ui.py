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
    # Each rule renders as a <details class="rule-block ..."> accordion.
    assert body.count('class="rule-block ') > 100  # 157 in practice
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
    # Valid query: pastel mint block + "passed" in the stat strip
    assert "block-ok" in body
    assert "status-ok" in body
    assert "Valid" in body
    assert "passed" in body


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


def test_ui_has_no_strict_toggle(client: TestClient):
    """Strict mode is intentionally API/CLI-only; the UI no longer exposes
    the toggle since the primary use case (CI gating) is automation."""
    resp = client.get("/")
    body = resp.text
    assert 'name="strict"' not in body
    assert "Strict mode" not in body


def test_ui_validate_result_includes_json_view_toggle(client: TestClient):
    """Each per-query panel ships both a formatted and a JSON view, with a
    toggle button and a Copy JSON button. No more 'Copy fix' per violation."""
    resp = client.post(
        "/ui/validate",
        data={"sql": "SELECT * FROM no_such_table;", "dialect": "postgres"},
    )
    assert resp.status_code == 200
    body = resp.text
    # Default view is formatted.
    assert 'data-view="formatted"' in body
    # Both views are rendered (CSS toggles visibility).
    assert "query-view-formatted" in body
    assert "query-view-json" in body
    # Toggle + floating copy-JSON icon button present; Copy fix removed.
    assert 'data-action="toggle-view"' in body
    assert 'aria-label="Copy JSON' in body
    assert "editor-copy-btn" in body
    assert "Copy fix" not in body
    # JSON content embedded (HTML-escaped quotes become &#34; — the browser
    # decodes them back for both <pre> display and data-text reads).
    assert "&#34;query_index&#34;" in body
    assert "&#34;errors&#34;" in body


def test_ui_validate_multi_query_renders_one_panel_per_query(client: TestClient):
    sql = (
        "SELECT person_id FROM person; "
        "SELECT * FROM bogus_table_alpha; "
        "SELECT * FROM bogus_table_beta;"
    )
    resp = client.post("/ui/validate", data={"sql": sql, "dialect": "postgres"})
    assert resp.status_code == 200
    body = resp.text
    # Three per-query panels.
    assert body.count("Query 1") == 1
    assert body.count("Query 2") == 1
    assert body.count("Query 3") == 1
    # Each bad-table error appears in a panel — per-query attribution.
    assert "bogus_table_alpha" in body
    assert "bogus_table_beta" in body
    # First block is valid (mint pastel), others are flagged (red pastel).
    assert body.count("block-ok") == 1
    assert body.count("block-bad") == 2


def test_ui_ignores_strict_form_param(client: TestClient):
    """If a client still sends `strict=on`, the UI route ignores it (the
    toggle was removed). Escalation stays API/CLI-only."""
    sql = (
        "WITH cc AS ( "
        "SELECT descendant_concept_id AS concept_id FROM concept_ancestor "
        "WHERE ancestor_concept_id IN (320128) "
        ") "
        "SELECT person_id FROM condition_occurrence co "
        "WHERE co.condition_concept_id IN (SELECT concept_id FROM cc)"
    )
    resp = client.post(
        "/ui/validate",
        data={"sql": sql, "dialect": "postgres", "strict": "on"},
    )
    assert resp.status_code == 200
    body = resp.text
    # Non-strict: the rule still fires as a warning, not an error.
    # Whole block is valid-styled (block-ok) and warnings appear in the violation list.
    assert "block-ok" in body
    assert "sev-warning" in body

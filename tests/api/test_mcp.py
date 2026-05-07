"""Tests for the MCP Streamable HTTP endpoint mounted at /mcp."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from fastssv.api.app import create_app  # noqa: E402
from fastssv.api.config import Settings  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
ACCEPT = "application/json, text/event-stream"


@pytest.fixture()
def client():
    settings = Settings(
        max_sql_bytes=4096,
        parse_timeout_seconds=5.0,
        rate_limit="1000/minute",
        cors_origins=[],
        log_level="WARNING",
        mcp_enabled=True,
        mcp_allowed_origins=["https://allowed.example.com"],
    )
    app = create_app(settings)
    # Use the TestClient as a context manager so the FastAPI lifespan runs;
    # FastMCP's session manager is initialised in startup and torn down on
    # shutdown.
    with TestClient(app) as c:
        yield c


def _post(
    client: TestClient,
    body: Dict[str, Any],
    *,
    session_id: str | None = None,
    origin: str | None = None,
):
    headers = {"Accept": ACCEPT, "Content-Type": "application/json"}
    if session_id:
        headers["MCP-Session-Id"] = session_id
    if origin:
        headers["Origin"] = origin
    return client.post("/mcp/", json=body, headers=headers)


def _parse_sse_or_json(resp) -> Dict[str, Any]:
    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        return resp.json()
    # SSE: each event line starts with "data: "; pluck the last data payload
    payload = None
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
    assert payload is not None, f"no SSE data event in response: {resp.text!r}"
    return payload


def _initialize(client: TestClient) -> str | None:
    """Run the MCP initialize handshake and return the session id (if any)."""
    resp = _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0.0.1"},
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = _parse_sse_or_json(resp)
    assert body.get("result", {}).get("protocolVersion") == PROTOCOL_VERSION
    sid = resp.headers.get("mcp-session-id")
    if sid:
        # Send the required initialized notification before any tool call.
        ack = _post(
            client,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=sid,
        )
        assert ack.status_code in (200, 202), ack.text
    return sid


def test_initialize_advertises_tools_capability(client: TestClient):
    resp = _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = _parse_sse_or_json(resp)
    assert "result" in body
    caps = body["result"]["capabilities"]
    assert "tools" in caps


def test_tools_list_exposes_validate_sql(client: TestClient):
    sid = _initialize(client)
    resp = _post(
        client,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        session_id=sid,
    )
    assert resp.status_code == 200, resp.text
    body = _parse_sse_or_json(resp)
    names = {t["name"] for t in body["result"]["tools"]}
    assert names == {"validate_sql"}


def test_validate_sql_tool_matches_direct_validator(client: TestClient):
    """The MCP tool result must agree with validate_sql_structured for the same input."""
    from fastssv import validate_sql_structured

    bad_sql = "SELECT * FROM nonexistent_table;"
    sid = _initialize(client)
    resp = _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "validate_sql",
                "arguments": {"sql": bad_sql, "dialect": "postgres", "strict": False},
            },
        },
        session_id=sid,
    )
    assert resp.status_code == 200, resp.text
    body = _parse_sse_or_json(resp)
    result = body["result"]
    assert result.get("isError") is not True

    # FastMCP returns structured tool output under structuredContent for tools
    # with a return-type hint; the body is the dict our tool returned.
    structured = result.get("structuredContent")
    assert structured is not None, f"no structuredContent in {result!r}"
    # Single-statement submission, so query_count is 1 and rule_ids on the
    # MCP side match the direct call.
    assert structured["query_count"] == 1
    direct = validate_sql_structured(bad_sql, dialect="postgres")
    direct_ids = sorted(v.rule_id for v in direct)
    mcp_ids = sorted(v["rule_id"] for v in structured["errors"] + structured["warnings"])
    assert mcp_ids == direct_ids


def test_origin_disallowed_returns_403(client: TestClient):
    resp = _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
        },
        origin="https://evil.example.com",
    )
    assert resp.status_code == 403
    body = resp.json()
    # Per the spec, the body MAY be a JSON-RPC error response with no id.
    assert body["error"]["code"] == -32000
    assert "id" not in body
    # Security-headers + request-id middlewares must wrap the rejection
    # response — regression guard against accidentally re-ordering them
    # inside MCPOriginMiddleware. Without these, the 403 leaks audit-grade
    # security headers and breaks request correlation.
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert "strict-transport-security" in resp.headers
    assert "x-request-id" in resp.headers


def test_origin_allowlisted_passes(client: TestClient):
    resp = _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
        },
        origin="https://allowed.example.com",
    )
    assert resp.status_code == 200, resp.text


def test_mcp_disabled_returns_404():
    """When mcp_enabled=False, /mcp should not be mounted."""
    settings = Settings(
        max_sql_bytes=1024,
        parse_timeout_seconds=2.0,
        rate_limit="1000/minute",
        cors_origins=[],
        log_level="WARNING",
        mcp_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/mcp/")
        assert resp.status_code == 404
        # /v1/health should reflect the disabled state too.
        health = c.get("/v1/health").json()
        assert health["mcp_mounted"] is False


def test_health_reflects_mcp_mounted(client: TestClient):
    """When the /mcp mount succeeded, /v1/health reports mcp_mounted=true."""
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json()["mcp_mounted"] is True


def test_mcp_enabled_but_extra_missing_raises(monkeypatch):
    """Fail loudly at startup when MCP_ENABLED=true but the [mcp] extra is missing.

    Silent skip in non-interactive deployments (CI, docker) would let
    misconfigured containers boot with /mcp absent and surface the problem
    only when a client first tries to use it. The startup-time RuntimeError
    forces the operator to either install the extra or flip MCP_ENABLED off.
    """
    import sys

    # Force the `from fastssv.mcp import build_mcp_server` line in
    # _maybe_build_mcp_app to raise ImportError, simulating a deploy that
    # set FASTSSV_API_MCP_ENABLED=true without installing the [mcp] extra.
    monkeypatch.setitem(sys.modules, "fastssv.mcp", None)

    settings = Settings(
        max_sql_bytes=1024,
        parse_timeout_seconds=2.0,
        rate_limit="1000/minute",
        cors_origins=[],
        log_level="WARNING",
        mcp_enabled=True,
    )
    with pytest.raises(RuntimeError, match="MCP_ENABLED=true but the 'mcp' extra is not installed"):
        create_app(settings)

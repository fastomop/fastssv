# MCP server

FastSSV exposes its validator as a [Model Context Protocol](https://modelcontextprotocol.io/) server, mounted at `/mcp` on the same FastAPI app as the HTTP API. The transport is [Streamable HTTP (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#streamable-http) in stateless mode (`stateless_http=True`, `json_response=True`) — no session store and no SSE resumption layer, since the rule registry is process-wide and every call is self-contained.

## Install

The endpoint is gated by the optional `[mcp]` extra. The Docker image bundled in `deploy/` already includes it.

```sh
uv add "fastssv[api,mcp]"
uv run fastssv serve  # MCP at http://localhost:8000/mcp/
```

**The MCP endpoint is opt-in.** `FASTSSV_API_MCP_ENABLED` defaults to `false` everywhere — in-code, in `deploy/docker-compose.yml`, and in `deploy/.env.example`. A deployment that doesn't think about MCP gets no MCP endpoint, by design (it's unauthenticated at the application layer; defaulting it on would be a footgun). To enable it, set `FASTSSV_API_MCP_ENABLED=true` in your env.

If `FASTSSV_API_MCP_ENABLED=true` but the `mcp` extra is missing, the app raises `RuntimeError` at startup with a clear install hint — the misconfiguration fails loudly so non-interactive deployments (CI, docker) can't silently boot without `/mcp`.

## Tool

### `validate_sql(sql, dialect="auto", strict=False)`

Static validation of an OMOP CDM SQL submission. Wraps [`fastssv.validate_sql_structured`](api.md) with the same statement-split, strict-mode and parse-timeout behaviour as `POST /v1/validate`. Returns a structured payload (aggregate `is_valid`/`error_count`/`warning_count`, flattened `errors` and `warnings`, and a per-statement `results` array).

There is intentionally no `list_rules` tool: a static rule catalog is a poor fit for the tool primitive (the JSON-RPC `tools/list` already advertises `validate_sql`, and every violation comes back with its `rule_id`). For an enumerable catalog use the existing HTTP endpoint `GET /v1/rules` or [`docs/rules_reference.md`](rules_reference.md).

## Configuration

All knobs are env-driven; see [`deploy/.env.example`](../deploy/.env.example) for the canonical list.

| Variable | Default | Notes |
| --- | --- | --- |
| `FASTSSV_API_MCP_ENABLED` | `false` | Mount toggle (opt-in). Set to `true` to enable the endpoint. |
| `FASTSSV_API_MCP_ALLOWED_ORIGINS` | _(empty)_ | CSV or JSON list of `Origin` values permitted from a browser. Requests with no `Origin` header (curl, Claude Desktop, MCP Inspector outside a browser) always pass through; requests with a present-but-unlisted `Origin` are rejected with HTTP 403 + a JSON-RPC error body that has no `id`, per the spec's [DNS rebinding mitigation](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#security-warning). |
| `FASTSSV_API_MCP_AUTH_MODE` | `none` | Reserved knob for future OAuth 2.1 conformance. Today the Literal is pinned to `"none"`. |

## Authentication and the deployment expectation

[The MCP authorization spec is OPTIONAL.](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) FastSSV does not implement application-layer auth on `/mcp`. There are two reasons:

1. **No per-user data.** The validator is stateless and deterministic — every caller gets identical results for identical input. Tokens scope nothing.
2. **OAuth 2.1 is the only spec-compliant option.** The MCP spec does not allow simple shared-bearer auth; you would have to be a full OAuth 2.1 Resource Server (RFC 9728 metadata, RFC 8707 audience binding, an Authorization Server, etc.). That is significant scope for a public-domain validation utility.

Operators are expected to gate `/mcp` at the reverse proxy: oauth2-proxy, Cloudflare Access, mTLS, network ACLs, or whatever their environment uses. The `behind_proxy` setting (see [`docs/api.md`](api.md)) is wired and trusts `X-Forwarded-*` headers from the upstream.

## Connecting a client

The endpoint is at `http://<host>/mcp/` (note the trailing slash; the spec says servers MUST provide a single endpoint that handles POST and GET).

### MCP Inspector (recommended for first checks)

The official [MCP Inspector](https://github.com/modelcontextprotocol/inspector) is an npm package that gives you a browser UI for `initialize`, `tools/list`, and `tools/call`:

```sh
npx @modelcontextprotocol/inspector
```

In the UI: Transport = `Streamable HTTP`, URL = `http://localhost:8000/mcp/`, click *Connect*, then call `validate_sql` from the Tools tab.

### Claude Desktop (via the `mcp-remote` stdio bridge)

Claude Desktop's `claude_desktop_config.json` natively supports stdio only; the standard workaround for HTTP MCP servers is the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) npm package, which presents a Streamable HTTP server as a stdio process to the desktop app. Edit:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "fastssv": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:8000/mcp/"]
    }
  }
}
```

Restart Claude Desktop, then prompt: *"Use fastssv to validate this SQL: SELECT * FROM nonexistent;"*

### Cursor / Claude Pro+ Custom Connectors

Other MCP clients (Cursor, the Claude web app's Custom Connectors UI on Pro/Max/Team/Enterprise) accept a Streamable HTTP URL directly — point them at `https://<your-public-host>/mcp/`. Those clients typically support an `Authorization` header that you can wire to whatever your reverse proxy enforces. Note that Anthropic's Custom Connectors fetch the URL from Anthropic's servers, not the user's machine, so `localhost` is unreachable; tunnel via `cloudflared` / `ngrok` if you need to test the connector path against a local instance.

## Spec conformance summary

| Requirement | Status |
| --- | --- |
| Single endpoint supporting POST and GET | ✓ (FastMCP SDK) |
| `Origin` validation | ✓ (`MCPOriginMiddleware`) |
| `Accept: application/json, text/event-stream` on POST | ✓ (FastMCP SDK) |
| `MCP-Protocol-Version` handling | ✓ (FastMCP SDK) |
| Stateless server (no session store, no SSE resumption) | ✓ (`stateless_http=True`, `json_response=True`) |
| Authorization (OAuth 2.1 + RFC 9728 + RFC 8707) | ✗ — OPTIONAL per spec; deferred to the reverse proxy |
| stdio transport | ✗ — out of scope for this iteration |

## References

- [Streamable HTTP transport (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#streamable-http)
- [Authorization (2025-11-25, OPTIONAL)](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [`modelcontextprotocol/python-sdk`](https://github.com/modelcontextprotocol/python-sdk)

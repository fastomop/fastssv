# FastSSV HTTP API

The `fastssv.api` subpackage exposes the validator as a FastAPI service. It is
optional — the core library and CLI work without it. Install with the `api`
extra:

```bash
pip install "fastssv[api]"
```

This pulls in `fastapi`, `uvicorn[standard]`, `gunicorn`, `slowapi`, and
`pydantic-settings`.

---

## Running

There are two supported "one command" paths. Both launch the JSON API **and**
the HTMX web UI from the same process (they're mounted on the same app).

### `fastssv serve` — host Python

Works for local dev, demos, and single-VM production. No Docker required.

```bash
fastssv serve                       # dev: uvicorn, host 127.0.0.1, port 8000
fastssv serve --reload              # + auto-reload on code changes
fastssv serve --host 0.0.0.0 --port 9000
fastssv serve --prod                # gunicorn + 2 uvicorn workers
fastssv serve --prod --workers 4    # tune worker count
```

Under the hood: dev mode invokes `uvicorn.run(...)` in-process; `--prod` execs
`gunicorn -k uvicorn.workers.UvicornWorker ...`. Each worker loads the full
rule registry once at startup (~157 rules, sub-second).

### `docker compose up` — containerized

Use this for servers, CI, or when you want container isolation to match
production. The compose file wraps the existing `deploy/Dockerfile`.

```bash
docker compose -f deploy/docker-compose.yml up --build
docker compose -f deploy/docker-compose.yml down
```

Environment variables set in `deploy/docker-compose.yml` override the defaults
(log level, rate limit, body-size cap, parse timeout, CORS origins, worker
count). See the comments in that file or the Configuration section below.

The container runs the same gunicorn command as `fastssv serve --prod`,
uses a non-root user, mounts a read-only root filesystem, and ships a
healthcheck against `/v1/health`.

---

## Configuration

All configuration is via environment variables with the `FASTSSV_API_`
prefix. Defaults are production-sane.

| Variable | Default | Description |
|----------|---------|-------------|
| `FASTSSV_API_MAX_SQL_BYTES` | `100000` | Maximum SQL body size. Requests exceeding this return `413`. |
| `FASTSSV_API_PARSE_TIMEOUT_SECONDS` | `5.0` | Hard ceiling per validation call. Exceeded → `408`. |
| `FASTSSV_API_RATE_LIMIT` | `60/minute` | `slowapi`-format limit applied per client IP. |
| `FASTSSV_API_CORS_ORIGINS` | `[]` | Comma-separated list of allowed origins. Empty = CORS disabled. |
| `FASTSSV_API_LOG_LEVEL` | `INFO` | Root logger level (`DEBUG`/`INFO`/`WARNING`/`ERROR`). |

A `.env` file in the working directory is loaded automatically.

---

## Web UI

In addition to the JSON API, the service ships with a minimal HTMX-based web
interface for ad-hoc validation:

- `GET /` — paste SQL, pick a dialect, submit; violations render inline via
  HTMX fragment swap. No JS framework, no build step.
- `GET /rules` — browsable list of every registered rule with a client-side
  filter by id/name/description and category.
- `GET /static/*` — vendored HTMX (`htmx.min.js`) and `style.css`.

UI form submissions go through `POST /ui/validate`, which returns an HTML
fragment (not JSON). It shares the same middleware stack as the JSON API —
body-size limit, parse timeout, rate limiting, security headers.

---

## Endpoints

All JSON endpoints are versioned under `/v1`. Error responses use a uniform
schema (`error`, `message`, `request_id`).

### `POST /v1/validate`

Validate a single SQL query.

**Request:**
```json
{
  "sql": "SELECT * FROM person;",
  "dialect": "auto"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `sql` | string | yes | Non-empty. Subject to `MAX_SQL_BYTES`. |
| `dialect` | `"auto" \| "postgres" \| "tsql"` | no | Default `"auto"`. |

**Response (200):**
```json
{
  "is_valid": false,
  "error_count": 1,
  "warning_count": 0,
  "errors": [
    {
      "rule_id": "data_quality.schema_validation",
      "severity": "error",
      "issue": "Table 'no_such_table' does not exist in OMOP CDM 5.4 schema.",
      "suggested_fix": "Ensure all table and column names match the OMOP CDM 5.4 schema",
      "location": null,
      "details": {"layer": "schema", "type": "invalid_table", "table": "no_such_table"}
    }
  ],
  "warnings": [],
  "dialect": "postgres",
  "duration_ms": 8.7
}
```

**Error status codes:**
- `400` — malformed body
- `408` — validation exceeded `PARSE_TIMEOUT_SECONDS`
- `413` — body larger than `MAX_SQL_BYTES`
- `422` — request failed schema validation (missing SQL, bad dialect enum)
- `429` — rate limit exceeded

### `GET /v1/rules`

List every registered rule. Useful for a frontend that wants to render rule
metadata, filter by category, etc.

**Response (200):**
```json
{
  "total": 157,
  "rules": [
    {
      "rule_id": "anti_patterns.ambiguous_column_reference",
      "name": "Ambiguous Column Reference",
      "description": "Detects unqualified column references ...",
      "severity": "warning",
      "category": "anti_patterns"
    }
  ]
}
```

### `GET /v1/health`

Liveness probe. Always `200 OK` unless the process cannot service requests.

**Response (200):**
```json
{
  "status": "ok",
  "version": "0.2.0",
  "rules_loaded": 157
}
```

---

## Production guardrails

The service is designed to be exposed to untrusted clients.

- **Body-size enforcement** runs as ASGI middleware: oversized requests are
  rejected by `Content-Length` before the body is read.
- **Parse-timeout** uses `asyncio.wait_for` around `asyncio.to_thread`, so the
  CPU-bound validator runs off the event loop and cannot wedge the server.
- **Rate limiting** via `slowapi`, keyed by client IP. Stored in memory by
  default (fine for single-instance deployments). Swap for Redis when you
  scale horizontally.
- **CORS** is strict — an explicit whitelist is required to enable it. No
  wildcards.
- **Security headers** set on every response: `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, `Strict-Transport-Security`,
  `Permissions-Policy`.
- **Request IDs** are generated per request and returned in `x-request-id`.
  Clients can override by sending the same header.
- **Structured JSON logging** reuses the core `JSONFormatter`. Every
  validation logs `sql_hash`, `dialect`, counts, and timing — **never the
  SQL body**, to avoid accidentally persisting sensitive data.
- **Exception handlers** return the uniform `ErrorResponse` schema. Stack
  traces are logged but never sent to the client.
- **Versioned routes** (`/v1/...`) so the API can evolve without breaking
  deployed clients.

---

## Deployment notes

- **Reverse proxy:** put the service behind nginx / Cloudflare / a cloud load
  balancer that terminates TLS. The `Strict-Transport-Security` header the
  service sets assumes HTTPS is handled upstream.
- **Scaling:** the service is stateless, so horizontal scaling is trivial.
  When you run more than one instance, switch `slowapi` storage to Redis so
  rate limits are shared across workers.
- **Observability:** the JSON log lines are designed to be shipped as-is to a
  log aggregator (Datadog, Grafana Loki, CloudWatch). Key fields:
  `sql_hash` (never the SQL itself), `dialect`, `errors`, `warnings`,
  `duration_ms`, `client`, `request_id`.
- **Health checks:** point your orchestrator at `/v1/health`. The Dockerfile
  already does this via `HEALTHCHECK`.

---

## Extending

- **Auth:** the MVP has none — the service is intended to run behind an API
  gateway that handles authentication. If you want in-process auth, add a
  FastAPI dependency on every route in `routes.py`.
- **Metrics:** Prometheus is intentionally not bundled. Add `prometheus-client`
  and a `/metrics` route if your platform doesn't already scrape request
  metrics.
- **Redis rate limiting:** pass a `storage_uri` to `Limiter(...)` in
  `fastssv/api/app.py` to swap the in-memory backend for Redis when you scale
  out.

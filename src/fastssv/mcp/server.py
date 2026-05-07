"""FastMCP server exposing FastSSV's validator over Streamable HTTP.

Stateless mode (`stateless_http=True, json_response=True`) is the spec's
"recommended" production mode for stateless servers — there are no
per-session resources, so we don't need session storage or SSE
resumption. The mount path is set to "/" because the parent FastAPI app
mounts this sub-app at "/mcp"; setting it here would double the prefix.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from fastssv.api._validation import run_validation
from fastssv.api.config import Settings

logger = logging.getLogger("fastssv.mcp")


def build_mcp_server(settings: Settings) -> FastMCP:
    """Construct the FastMCP server with FastSSV's tool surface."""
    # Disable the SDK's built-in DNS rebinding protection: it auto-enables a
    # localhost-only Host allowlist when host="127.0.0.1" (FastMCP's default),
    # which doesn't make sense for a reverse-proxied deployment where the
    # public Host varies. The transport spec's Origin check (a separate
    # requirement) is enforced by MCPOriginMiddleware in api/app.py against
    # FASTSSV_API_MCP_ALLOWED_ORIGINS, and the upstream proxy is expected to
    # validate the Host.
    transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    mcp = FastMCP(
        name="fastssv",
        instructions=(
            "Static, semantic validator for SQL written against the OMOP CDM v5.4. "
            "Use validate_sql to check a query for schema/vocabulary/modelling "
            "errors that would otherwise pass syntax check but produce "
            "silently-wrong analytics."
        ),
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
    )
    mcp.settings.streamable_http_path = "/"

    @mcp.tool(
        name="validate_sql",
        description=(
            "Validate an OMOP CDM SQL query. Returns per-statement and "
            "aggregate violations (errors and warnings) without executing the "
            "SQL — purely static analysis."
        ),
    )
    async def validate_sql(
        sql: str,
        dialect: str = "auto",
        strict: bool = False,
    ) -> dict[str, Any]:
        """Run FastSSV's full rule registry against ``sql``.

        Args:
            sql: SQL query to validate (1+ statements, ``;``-delimited).
            dialect: ``auto`` (default), ``postgres``, ``tsql``, ``oracle``,
                ``redshift``, ``bigquery``, ``snowflake``, ``databricks``,
                or ``duckdb``.
            strict: Escalate best-practice warnings to errors.

        Returns:
            Structured result: aggregate ``is_valid`` / ``error_count`` /
            ``warning_count``, flattened ``errors`` and ``warnings``, plus
            per-statement breakdown under ``results``.
        """
        try:
            response = await run_validation(sql, dialect, strict, settings, client=None)
        except HTTPException as exc:
            # Surface size/timeout limits as a tool error per MCP semantics.
            raise ValueError(exc.detail) from exc
        return response.model_dump()

    return mcp

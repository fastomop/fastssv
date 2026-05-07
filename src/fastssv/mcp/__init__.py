"""MCP (Model Context Protocol) Streamable HTTP server for FastSSV.

The Streamable HTTP endpoint is mounted at ``/mcp`` by
``fastssv.api.app.create_app`` when the optional ``[mcp]`` extra is
installed and ``FASTSSV_API_MCP_ENABLED`` is true.

See ``docs/mcp.md`` and the spec at
https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#streamable-http
"""

from fastssv.mcp.server import build_mcp_server

__all__ = ["build_mcp_server"]

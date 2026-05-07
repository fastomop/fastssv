"""FastAPI application factory for FastSSV."""

from __future__ import annotations

import logging
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Iterable

from fastapi import FastAPI, HTTPException, Request, status
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from fastssv.api.config import Settings, get_settings
from fastssv.api.models import ErrorResponse
from fastssv.api.routes import router
from fastssv.api.ui import mount_static, router as ui_router
from fastssv.core.logging import JSONFormatter

logger = logging.getLogger("fastssv.api")

MCP_MOUNT_PATH = "/mcp"


def _configure_logging(level: str) -> None:
    root = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JSONFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("Permissions-Policy", "interest-cohort=()")
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > self.max_bytes:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content=ErrorResponse(
                            error="payload_too_large",
                            message=f"Request body exceeds {self.max_bytes} bytes.",
                            request_id=getattr(request.state, "request_id", None),
                        ).model_dump(),
                    )
            except ValueError:
                pass
        return await call_next(request)


class MCPOriginMiddleware(BaseHTTPMiddleware):
    """Block requests to /mcp/* whose Origin isn't allow-listed.

    Per the Streamable HTTP spec's Security Warning: servers MUST validate
    the Origin header to prevent DNS rebinding attacks. Requests with no
    Origin header (non-browser clients like Claude Desktop, curl) are
    allowed through; requests with a present-but-unlisted Origin are
    rejected with 403 and a JSON-RPC error response that has no ``id``.
    """

    def __init__(self, app: ASGIApp, mount_path: str, allowed: Iterable[str]) -> None:
        super().__init__(app)
        self._mount_path = mount_path.rstrip("/")
        self._allowed = {o.rstrip("/") for o in allowed}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not (path == self._mount_path or path.startswith(self._mount_path + "/")):
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") not in self._allowed:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32000,
                        "message": f"Origin '{origin}' is not allowed.",
                    },
                },
            )
        return await call_next(request)


def _build_mcp_lifespan(mcp_apps):
    """Compose lifespans of mounted MCP sub-apps into the parent lifecycle.

    FastMCP's session manager runs as a long-lived task group that needs
    to start before any request hits and stop cleanly on shutdown.
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            for sub_app in mcp_apps:
                lifespan_cm = sub_app.router.lifespan_context(sub_app)
                await stack.enter_async_context(lifespan_cm)
            logger.info(
                "api_startup",
                extra={"rules_loaded": _rules_count(), "mcp_mounted": bool(mcp_apps)},
            )
            yield
            logger.info("api_shutdown")

    return _lifespan


def _rules_count() -> int:
    try:
        from fastssv.core.registry import get_all_rules

        return len(get_all_rules())
    except Exception:
        return 0


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def on_http_exception(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=_http_error_slug(exc.status_code),
                message=str(exc.detail),
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error="invalid_request",
                message="Request body failed validation.",
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception):
        logger.exception("unhandled_error", extra={"request_id": getattr(request.state, "request_id", None)})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error="internal_error",
                message="Internal server error.",
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(),
        )


def _http_error_slug(code: int) -> str:
    return {
        400: "bad_request",
        404: "not_found",
        408: "request_timeout",
        413: "payload_too_large",
        422: "invalid_request",
        429: "rate_limited",
    }.get(code, "error")


def _maybe_build_mcp_app(settings: Settings):
    """Build the MCP sub-app if the extra is installed and enabled.

    Returns the streamable-http ASGI app, or ``None`` when MCP is disabled.
    Raises ``RuntimeError`` when the user explicitly opts in via
    ``FASTSSV_API_MCP_ENABLED=true`` but the optional ``mcp`` package is
    missing — silently skipping the mount in non-interactive deployments
    (CI, docker) lets misconfigured containers boot with `/mcp` absent and
    surface the problem only when a client first tries to use it.
    """
    if not settings.mcp_enabled:
        return None
    try:
        from fastssv.mcp import build_mcp_server
    except ImportError as exc:
        raise RuntimeError(
            "FASTSSV_API_MCP_ENABLED=true but the 'mcp' extra is not installed. "
            "Install with `pip install 'fastssv[api,mcp]'` (or set "
            "FASTSSV_API_MCP_ENABLED=false to disable the endpoint)."
        ) from exc
    server = build_mcp_server(settings)
    return server.streamable_http_app()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging(settings.log_level)

    limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])

    mcp_app = _maybe_build_mcp_app(settings)
    mcp_apps = [mcp_app] if mcp_app is not None else []

    app = FastAPI(
        title="FastSSV API",
        description="Static validation of OMOP CDM SQL queries.",
        version="v1",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=_build_mcp_lifespan(mcp_apps),
    )
    app.state.settings = settings
    app.state.limiter = limiter
    app.state.mcp_mounted = mcp_app is not None

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # add_middleware prepends to the stack, so the *last* registration is the
    # outermost layer. Order matters: MCPOriginMiddleware short-circuits with
    # a 403 when the Origin is not allow-listed, so SecurityHeadersMiddleware
    # and RequestIDMiddleware must be *outside* it (registered after) for
    # those headers to wrap the rejection response.
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_sql_bytes + 4096)

    if mcp_app is not None:
        app.add_middleware(
            MCPOriginMiddleware,
            mount_path=MCP_MOUNT_PATH,
            allowed=settings.mcp_allow_origins(),
        )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)

    if settings.behind_proxy:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    if settings.cors_allow_origins():
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins(),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["content-type", "x-request-id"],
            max_age=600,
        )

    _install_exception_handlers(app)
    mount_static(app)
    app.include_router(router)
    app.include_router(ui_router)

    if mcp_app is not None:
        app.mount(MCP_MOUNT_PATH, mcp_app)

    return app


app = create_app()

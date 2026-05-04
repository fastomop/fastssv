"""FastAPI application factory for FastSSV."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from fastssv.api.config import Settings, get_settings
from fastssv.api.models import ErrorResponse
from fastssv.api.ratelimit import configure_rate_limit, limiter
from fastssv.api.routes import router
from fastssv.api.ui import mount_static, router as ui_router
from fastssv.core.logging import JSONFormatter

logger = logging.getLogger("fastssv.api")


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


# Allows the FastAPI Swagger UI at /docs (CSS/JS from cdn.jsdelivr.net,
# favicon from fastapi.tiangolo.com) and our same-origin htmx UI. Inline
# scripts/styles are permitted because base.html seeds the theme
# attribute inline before CSS loads (avoids a flash of unstyled colour)
# and Swagger UI also uses inline init scripts.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _request_is_https(request: Request) -> bool:
    """Report the *original* scheme.

    When uvicorn runs behind a proxy and gunicorn's --forwarded-allow-ips
    trusts that proxy, ProxyHeadersMiddleware rewrites ``request.url.scheme``
    from ``X-Forwarded-Proto``. Without that trust we'd be reading the
    inner-loop scheme, so this falls back to inspecting the
    ``x-forwarded-proto`` header only when the peer header is present —
    HSTS is consequently NOT sent on plain HTTP, which avoids pinning
    browsers to https before TLS is in place.
    """
    return request.url.scheme == "https"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "interest-cohort=(), camera=(), microphone=(), geolocation=(), payment=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        response.headers.setdefault("Content-Security-Policy", _CSP)
        if _request_is_https(request):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("api_startup", extra={"rules_loaded": _rules_count()})
    yield
    logger.info("api_shutdown")


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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging(settings.log_level)

    # The shared limiter lives in fastssv.api.ratelimit so route decorators
    # can attach to it at import time. Per-app the only thing that varies is
    # the configured limit string, which is published into the module so the
    # callable limit_value on @limiter.limit() picks it up at request time.
    configure_rate_limit(settings.rate_limit)

    app = FastAPI(
        title="FastSSV API",
        description="Static validation of OMOP CDM SQL queries.",
        version="v1",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.limiter = limiter

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # SlowAPIMiddleware is still added so 429 responses include the
    # X-RateLimit-* headers slowapi injects, but with no default_limits set
    # on the limiter only routes carrying @limiter.limit() are throttled —
    # /v1/health, /v1/rules, /static/*, and the HTML pages are exempt.
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_sql_bytes + 4096)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)

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

    return app


app = create_app()

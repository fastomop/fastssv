"""HTML UI routes for FastSSV.

Thin server-rendered frontend layered on top of the JSON API.
Uses HTMX for in-place fragment swapping; no build step, no JS framework.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi.util import get_remote_address

from fastssv import validate_sql_structured
from fastssv.api.config import Settings
from fastssv.core.base import Severity
from fastssv.core.registry import get_all_rules

logger = logging.getLogger("fastssv.api.ui")

_BASE = Path(__file__).resolve().parent
TEMPLATES_DIR = _BASE / "templates"
STATIC_DIR = _BASE / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8", errors="replace")).hexdigest()[:16]


def _rules_loaded() -> int:
    return len(get_all_rules())


def _pluralize(n: int) -> str:
    return "" if n == 1 else "s"


templates.env.filters["pluralize"] = lambda n: _pluralize(int(n))


router = APIRouter(tags=["ui"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request):
    settings: Settings = request.app.state.settings
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active": "validate",
            "rules_loaded": _rules_loaded(),
            "max_sql_bytes": settings.max_sql_bytes,
        },
    )


@router.get("/rules", response_class=HTMLResponse, include_in_schema=False)
async def rules_page(request: Request):
    rules = sorted(
        (
            {
                "rule_id": r.rule_id,
                "name": r.name,
                "description": r.description,
                "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                "category": r.rule_id.split(".", 1)[0] if "." in r.rule_id else "uncategorized",
            }
            for r in get_all_rules()
        ),
        key=lambda r: (r["category"], r["rule_id"]),
    )
    categories = sorted({r["category"] for r in rules})
    return templates.TemplateResponse(
        request,
        "rules.html",
        {
            "active": "rules",
            "rules": rules,
            "categories": categories,
            "rules_loaded": len(rules),
        },
    )


@router.post("/ui/validate", response_class=HTMLResponse, include_in_schema=False)
async def ui_validate(
    request: Request,
    sql: str = Form(...),
    dialect: str = Form("auto"),
) -> HTMLResponse:
    settings: Settings = request.app.state.settings

    if dialect not in ("auto", "postgres", "tsql"):
        return _render_results(
            request,
            error="Invalid dialect.",
            detail=f"'{dialect}' is not one of: auto, postgres, tsql.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if not sql.strip():
        return _render_results(
            request,
            error="SQL is empty.",
            detail="Paste a query to validate.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    sql_bytes = len(sql.encode("utf-8"))
    if sql_bytes > settings.max_sql_bytes:
        return _render_results(
            request,
            error="SQL too large.",
            detail=f"Input is {sql_bytes} bytes; limit is {settings.max_sql_bytes}.",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    started = time.perf_counter()
    try:
        violations = await asyncio.wait_for(
            asyncio.to_thread(validate_sql_structured, sql, dialect),
            timeout=settings.parse_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "ui_validation_timeout",
            extra={"sql_hash": _sql_hash(sql), "dialect": dialect, "client": get_remote_address(request)},
        )
        return _render_results(
            request,
            error="Validation timed out.",
            detail=f"Exceeded {settings.parse_timeout_seconds}s. Try a smaller query.",
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
        )
    except Exception:
        logger.exception("ui_validation_error")
        return _render_results(
            request,
            error="Internal error.",
            detail="Unable to validate this query.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    duration_ms = (time.perf_counter() - started) * 1000.0
    errors = [v for v in violations if v.severity == Severity.ERROR]
    warnings = [v for v in violations if v.severity == Severity.WARNING]

    logger.info(
        "ui_validation_complete",
        extra={
            "sql_hash": _sql_hash(sql),
            "dialect": dialect,
            "errors": len(errors),
            "warnings": len(warnings),
            "duration_ms": round(duration_ms, 2),
            "client": get_remote_address(request),
        },
    )

    all_violations: List[Dict[str, Any]] = [
        {
            "rule_id": v.rule_id,
            "severity": v.severity.value,
            "issue": v.message,
            "suggested_fix": v.suggested_fix,
        }
        for v in list(errors) + list(warnings)
    ]

    return _render_results(
        request,
        is_valid=len(errors) == 0,
        error_count=len(errors),
        warning_count=len(warnings),
        violations=all_violations,
        duration_ms=round(duration_ms, 2),
        dialect=dialect,
    )


def _render_results(
    request: Request,
    *,
    error: str | None = None,
    detail: str | None = None,
    is_valid: bool = False,
    error_count: int = 0,
    warning_count: int = 0,
    violations: List[Dict[str, Any]] | None = None,
    duration_ms: float = 0.0,
    dialect: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        "partials/results.html",
        {
            "error": error,
            "detail": detail,
            "is_valid": is_valid,
            "error_count": error_count,
            "warning_count": warning_count,
            "violations": violations or [],
            "duration_ms": duration_ms,
            "dialect": dialect,
            "rules_loaded": _rules_loaded(),
        },
        status_code=status_code,
    )
    return response


def mount_static(app) -> None:
    """Attach /static route to a FastAPI app."""
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

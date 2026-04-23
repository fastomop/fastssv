"""API routes."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, HTTPException, Request, status
from slowapi.util import get_remote_address

from fastssv import validate_sql_structured
from fastssv.api.config import Settings
from fastssv.api.models import (
    HealthResponse,
    RuleInfo,
    RulesResponse,
    ValidationRequest,
    ValidationResponse,
    Violation,
)
from fastssv.core.base import Severity
from fastssv.core.registry import get_all_rules

logger = logging.getLogger("fastssv.api")

router = APIRouter(prefix="/v1")


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8", errors="replace")).hexdigest()[:16]


def _category_from_rule_id(rule_id: str) -> str:
    return rule_id.split(".", 1)[0] if "." in rule_id else "uncategorized"


@router.post(
    "/validate",
    response_model=ValidationResponse,
    summary="Validate an OMOP SQL query",
)
async def validate(req: ValidationRequest, request: Request) -> ValidationResponse:
    settings: Settings = request.app.state.settings
    sql_bytes = len(req.sql.encode("utf-8"))

    if sql_bytes > settings.max_sql_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"SQL exceeds {settings.max_sql_bytes} byte limit.",
        )

    started = time.perf_counter()
    try:
        violations = await asyncio.wait_for(
            asyncio.to_thread(
                validate_sql_structured,
                req.sql,
                req.dialect,
            ),
            timeout=settings.parse_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        logger.warning(
            "validation_timeout",
            extra={
                "sql_hash": _sql_hash(req.sql),
                "dialect": req.dialect,
                "client": get_remote_address(request),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=f"Validation exceeded {settings.parse_timeout_seconds}s timeout.",
        ) from exc

    duration_ms = (time.perf_counter() - started) * 1000.0

    errors = [Violation(**v.to_dict()) for v in violations if v.severity == Severity.ERROR]
    warnings = [Violation(**v.to_dict()) for v in violations if v.severity == Severity.WARNING]

    logger.info(
        "validation_complete",
        extra={
            "sql_hash": _sql_hash(req.sql),
            "dialect": req.dialect,
            "errors": len(errors),
            "warnings": len(warnings),
            "duration_ms": round(duration_ms, 2),
            "client": get_remote_address(request),
        },
    )

    return ValidationResponse(
        is_valid=len(errors) == 0,
        error_count=len(errors),
        warning_count=len(warnings),
        errors=errors,
        warnings=warnings,
        dialect=req.dialect,
        duration_ms=round(duration_ms, 2),
    )


@router.get(
    "/rules",
    response_model=RulesResponse,
    summary="List all registered validation rules",
)
async def list_rules_endpoint() -> RulesResponse:
    rules = get_all_rules()
    infos = [
        RuleInfo(
            rule_id=r.rule_id,
            name=r.name,
            description=r.description,
            severity=r.severity.value if hasattr(r.severity, "value") else str(r.severity),
            category=_category_from_rule_id(r.rule_id),
        )
        for r in rules
    ]
    infos.sort(key=lambda x: (x.category, x.rule_id))
    return RulesResponse(total=len(infos), rules=infos)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
)
async def health() -> HealthResponse:
    try:
        v = pkg_version("fastssv")
    except Exception:
        v = "unknown"
    return HealthResponse(
        status="ok",
        version=v,
        rules_loaded=len(get_all_rules()),
    )

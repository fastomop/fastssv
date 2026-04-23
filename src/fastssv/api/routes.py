"""API routes."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from importlib.metadata import version as pkg_version
from typing import List

from fastapi import APIRouter, HTTPException, Request, status
from slowapi.util import get_remote_address

from fastssv import validate_sql_structured
from fastssv.api.config import Settings
from fastssv.api.models import (
    HealthResponse,
    QueryResult,
    RuleInfo,
    RulesResponse,
    ValidationRequest,
    ValidationResponse,
    Violation,
)
from fastssv.core.base import Severity
from fastssv.core.helpers import split_sql_statements
from fastssv.core.registry import get_all_rules
from fastssv.core.validation_context import with_strict_mode

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

    # Split the submission into statements so each violation can be attributed
    # to its source query. A submission with no splittable content (e.g. a
    # bare keyword or comment-only input) is still handed to the validator as
    # a single statement — the existing parse-error path surfaces it cleanly.
    statements = split_sql_statements(req.sql) or [req.sql]

    started = time.perf_counter()
    try:
        with with_strict_mode(req.strict):
            per_query = await asyncio.wait_for(
                asyncio.to_thread(_validate_each, statements, req.dialect),
                timeout=settings.parse_timeout_seconds,
            )
    except asyncio.TimeoutError as exc:
        logger.warning(
            "validation_timeout",
            extra={
                "sql_hash": _sql_hash(req.sql),
                "dialect": req.dialect,
                "strict": req.strict,
                "query_count": len(statements),
                "client": get_remote_address(request),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=f"Validation exceeded {settings.parse_timeout_seconds}s timeout.",
        ) from exc

    duration_ms = (time.perf_counter() - started) * 1000.0

    results: List[QueryResult] = []
    all_errors: List[Violation] = []
    all_warnings: List[Violation] = []
    for idx, stmt, violations in per_query:
        errs = [Violation(**v.to_dict()) for v in violations if v.severity == Severity.ERROR]
        warns = [Violation(**v.to_dict()) for v in violations if v.severity == Severity.WARNING]
        results.append(
            QueryResult(
                query_index=idx,
                sql=stmt,
                is_valid=len(errs) == 0,
                error_count=len(errs),
                warning_count=len(warns),
                errors=errs,
                warnings=warns,
            )
        )
        all_errors.extend(errs)
        all_warnings.extend(warns)

    logger.info(
        "validation_complete",
        extra={
            "sql_hash": _sql_hash(req.sql),
            "dialect": req.dialect,
            "strict": req.strict,
            "query_count": len(statements),
            "errors": len(all_errors),
            "warnings": len(all_warnings),
            "duration_ms": round(duration_ms, 2),
            "client": get_remote_address(request),
        },
    )

    return ValidationResponse(
        is_valid=len(all_errors) == 0,
        error_count=len(all_errors),
        warning_count=len(all_warnings),
        errors=all_errors,
        warnings=all_warnings,
        query_count=len(statements),
        results=results,
        dialect=req.dialect,
        duration_ms=round(duration_ms, 2),
        strict=req.strict,
    )


def _validate_each(statements, dialect):
    """Validate each statement independently. Runs in the threadpool."""
    out = []
    for idx, stmt in enumerate(statements, start=1):
        out.append((idx, stmt, validate_sql_structured(stmt, dialect=dialect)))
    return out


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

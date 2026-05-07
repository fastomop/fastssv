"""Shared validation runner used by both the HTTP route and the MCP tool.

Keeps statement splitting, strict-mode handling, the parse timeout, and
result aggregation in one place so the two transports can't drift.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import List

from fastapi import HTTPException, status

from fastssv import validate_sql_structured
from fastssv.api.config import Settings
from fastssv.api.models import QueryResult, ValidationResponse, Violation
from fastssv.core.base import Severity
from fastssv.core.helpers import split_sql_statements
from fastssv.core.validation_context import with_strict_mode

logger = logging.getLogger("fastssv.api")


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8", errors="replace")).hexdigest()[:16]


def _validate_each(statements, dialect):
    out = []
    for idx, stmt in enumerate(statements, start=1):
        out.append((idx, stmt, validate_sql_structured(stmt, dialect=dialect)))
    return out


async def run_validation(
    sql: str,
    dialect: str,
    strict: bool,
    settings: Settings,
    *,
    client: str | None = None,
) -> ValidationResponse:
    """Validate a SQL submission and return the structured response.

    Raises HTTPException(413) if the submission exceeds max_sql_bytes,
    HTTPException(408) if validation exceeds parse_timeout_seconds.
    """
    sql_bytes = len(sql.encode("utf-8"))
    if sql_bytes > settings.max_sql_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"SQL exceeds {settings.max_sql_bytes} byte limit.",
        )

    # A submission with no splittable content (bare keyword, comment-only
    # input) still needs to reach the validator so the parse-error path can
    # surface it.
    statements = split_sql_statements(sql) or [sql]

    started = time.perf_counter()
    try:
        with with_strict_mode(strict):
            per_query = await asyncio.wait_for(
                asyncio.to_thread(_validate_each, statements, dialect),
                timeout=settings.parse_timeout_seconds,
            )
    except asyncio.TimeoutError as exc:
        logger.warning(
            "validation_timeout",
            extra={
                "sql_hash": _sql_hash(sql),
                "dialect": dialect,
                "strict": strict,
                "query_count": len(statements),
                "client": client,
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
            "sql_hash": _sql_hash(sql),
            "dialect": dialect,
            "strict": strict,
            "query_count": len(statements),
            "errors": len(all_errors),
            "warnings": len(all_warnings),
            "duration_ms": round(duration_ms, 2),
            "client": client,
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
        dialect=dialect,
        duration_ms=round(duration_ms, 2),
        strict=strict,
    )

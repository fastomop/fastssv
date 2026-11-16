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
from fastssv.core.helpers import (
    collect_locally_defined_tables,
    collect_locally_defined_unqualified_tables,
    detect_dialect,
    split_sql_statements,
)
from fastssv.core.validation_context import with_local_tables, with_strict_mode

logger = logging.getLogger("fastssv.api")


class ValidationLimiter:
    """Fail-fast bound on concurrent validation work, tied to thread lifetime.

    Deliberately NOT an ``asyncio.Semaphore``:

    - ``try_acquire`` is a synchronous check-and-increment on the event
      loop, so fail-fast has no locked()/acquire race — a request either
      takes a permit immediately or is refused.
    - The permit is released from the worker future's done callback (see
      ``run_bounded``), not on exiting an ``async with`` block. A
      ``wait_for`` timeout abandons the await while the CPU-bound sqlglot
      parse keeps its thread; releasing on timeout would let repeated
      slow-parse submissions pin every thread while the limiter reports
      free capacity.
    """

    def __init__(self, max_concurrent: int) -> None:
        self._max = max_concurrent
        self._active = 0

    @property
    def active(self) -> int:
        return self._active

    def try_acquire(self) -> bool:
        if self._active >= self._max:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        self._active = max(0, self._active - 1)


class ValidationCapacityError(RuntimeError):
    """Raised by ``run_bounded`` when the limiter has no free permit."""


async def run_bounded(func, /, *args, limiter: ValidationLimiter | None, timeout: float):
    """Run ``func(*args)`` on a worker thread with a timeout and a permit
    held for the *thread's* lifetime.

    Raises ``ValidationCapacityError`` immediately when the limiter is
    saturated, ``asyncio.TimeoutError`` on timeout. The worker future is
    shielded from ``wait_for``'s cancellation and the permit is released
    from its done callback, so capacity only frees up when the thread
    actually finishes — not when the client is told 408.
    """
    if limiter is not None and not limiter.try_acquire():
        raise ValidationCapacityError

    inner = asyncio.ensure_future(asyncio.to_thread(func, *args))

    def _on_done(fut: asyncio.Future) -> None:
        if limiter is not None:
            limiter.release()
        # Retrieve the exception of an abandoned (timed-out) worker so
        # asyncio doesn't log "exception was never retrieved".
        if not fut.cancelled():
            fut.exception()

    inner.add_done_callback(_on_done)
    return await asyncio.wait_for(asyncio.shield(inner), timeout=timeout)


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
    request_id: str | None = None,
    limiter: ValidationLimiter | None = None,
) -> ValidationResponse:
    """Validate a SQL submission and return the structured response.

    Raises HTTPException(413) if the submission exceeds max_sql_bytes,
    HTTPException(408) if validation exceeds parse_timeout_seconds, and
    HTTPException(503) when ``limiter`` is saturated — a timed-out
    validation keeps running on its worker thread (CPU-bound sqlglot work
    can't be cancelled), so refusing new work beats pinning every thread.
    """
    sql_bytes = len(sql.encode("utf-8"))
    if sql_bytes > settings.max_sql_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"SQL exceeds {settings.max_sql_bytes} byte limit.",
        )

    # Resolve "auto" once, on the whole submission (mirrors the CLI). The
    # local-table collectors below can't parse with a pseudo-dialect —
    # they'd silently return empty and cross-statement scoping would
    # no-op for the default dialect. Resolving here also means every
    # statement is validated under one detected dialect and the response
    # reports the dialect actually used rather than echoing "auto".
    if dialect == "auto":
        dialect = detect_dialect(sql)

    # A submission with no splittable content (bare keyword, comment-only
    # input) still needs to reach the validator so the parse-error path can
    # surface it.
    statements = split_sql_statements(sql) or [sql]

    # Cross-statement scope: tables created elsewhere in the same submission
    # are treated as known so per-statement schema validation doesn't flag
    # intra-batch scratch tables as unknown OMOP tables. The unqualified
    # subset separately gates the destructive-operations shadow exemption.
    local_tables = collect_locally_defined_tables(sql, dialect)
    local_unqualified = collect_locally_defined_unqualified_tables(sql, dialect)

    started = time.perf_counter()
    try:
        with with_strict_mode(strict), with_local_tables(local_tables, local_unqualified):
            per_query = await run_bounded(
                _validate_each,
                statements,
                dialect,
                limiter=limiter,
                timeout=settings.parse_timeout_seconds,
            )
    except ValidationCapacityError as exc:
        logger.warning(
            "validation_capacity_exceeded",
            extra={"sql_hash": _sql_hash(sql), "client": client, "request_id": request_id},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server is at validation capacity; retry shortly.",
            headers={"Retry-After": "1"},
        ) from exc
    except asyncio.TimeoutError as exc:
        logger.warning(
            "validation_timeout",
            extra={
                "sql_hash": _sql_hash(sql),
                "dialect": dialect,
                "strict": strict,
                "query_count": len(statements),
                "client": client,
                "request_id": request_id,
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
            "request_id": request_id,
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

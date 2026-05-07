"""API routes."""

from __future__ import annotations

import logging
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Request
from slowapi.util import get_remote_address

from fastssv.api._validation import run_validation
from fastssv.api.config import Settings
from fastssv.api.models import (
    HealthResponse,
    RuleInfo,
    RulesResponse,
    ValidationRequest,
    ValidationResponse,
)
from fastssv.core.registry import get_all_rules

logger = logging.getLogger("fastssv.api")

router = APIRouter(prefix="/v1")


def _category_from_rule_id(rule_id: str) -> str:
    return rule_id.split(".", 1)[0] if "." in rule_id else "uncategorized"


@router.post(
    "/validate",
    response_model=ValidationResponse,
    summary="Validate an OMOP SQL query",
)
async def validate(req: ValidationRequest, request: Request) -> ValidationResponse:
    settings: Settings = request.app.state.settings
    return await run_validation(
        req.sql,
        req.dialect,
        req.strict,
        settings,
        client=get_remote_address(request),
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
async def health(request: Request) -> HealthResponse:
    try:
        v = pkg_version("fastssv")
    except Exception:
        v = "unknown"
    return HealthResponse(
        status="ok",
        version=v,
        rules_loaded=len(get_all_rules()),
        mcp_mounted=getattr(request.app.state, "mcp_mounted", False),
    )

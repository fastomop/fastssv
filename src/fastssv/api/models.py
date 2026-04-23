"""Pydantic request/response schemas for the FastSSV API."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ValidationRequest(BaseModel):
    sql: str = Field(..., min_length=1, description="SQL query to validate.")
    dialect: Literal["auto", "postgres", "tsql"] = Field(default="auto")


class Violation(BaseModel):
    rule_id: str
    severity: str
    issue: str
    suggested_fix: str
    location: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class ValidationResponse(BaseModel):
    is_valid: bool
    error_count: int
    warning_count: int
    errors: List[Violation] = Field(default_factory=list)
    warnings: List[Violation] = Field(default_factory=list)
    dialect: str
    duration_ms: float


class RuleInfo(BaseModel):
    rule_id: str
    name: str
    description: str
    severity: str
    category: str


class RulesResponse(BaseModel):
    total: int
    rules: List[RuleInfo]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    rules_loaded: int


class ErrorResponse(BaseModel):
    error: str
    message: str
    request_id: Optional[str] = None

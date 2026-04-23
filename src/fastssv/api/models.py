"""Pydantic request/response schemas for the FastSSV API."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ValidationRequest(BaseModel):
    sql: str = Field(..., min_length=1, description="SQL query to validate.")
    dialect: Literal["auto", "postgres", "tsql"] = Field(default="auto")
    strict: bool = Field(
        default=False,
        description="Strict mode: escalates best-practice warnings to errors.",
    )


class Violation(BaseModel):
    rule_id: str
    severity: str
    issue: str
    suggested_fix: str
    location: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class QueryResult(BaseModel):
    """Per-statement result when a submission contains multiple SQL statements."""

    query_index: int = Field(..., ge=1, description="1-based position in the submission.")
    sql: str = Field(..., description="The statement as it was parsed out of the submission.")
    is_valid: bool
    error_count: int
    warning_count: int
    errors: List[Violation] = Field(default_factory=list)
    warnings: List[Violation] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    # Aggregate summary across all statements in the submission.
    is_valid: bool
    error_count: int
    warning_count: int
    errors: List[Violation] = Field(
        default_factory=list,
        description="All errors across every statement, flattened.",
    )
    warnings: List[Violation] = Field(
        default_factory=list,
        description="All warnings across every statement, flattened.",
    )
    # Per-statement breakdown (length == query_count). For single-statement
    # submissions this contains one entry whose content mirrors the aggregate.
    query_count: int
    results: List[QueryResult] = Field(default_factory=list)
    dialect: str
    duration_ms: float
    strict: bool = False


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

"""Environment-driven configuration for the FastSSV API."""

import json
from typing import Annotated, List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FASTSSV_API_",
        env_file=".env",
        extra="ignore",
    )

    max_sql_bytes: int = Field(default=100_000, ge=1, le=10_000_000)
    parse_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    rate_limit: str = Field(default="60/minute")
    # NoDecode skips pydantic-settings' built-in JSON parsing for these
    # complex fields so an empty string from the env doesn't blow up before
    # the validators below run.
    cors_origins: Annotated[List[str], NoDecode] = Field(default_factory=list)
    log_level: str = Field(default="INFO")
    behind_proxy: bool = Field(default=False)

    # MCP (Streamable HTTP) endpoint mounted at /mcp. Opt-in everywhere:
    # the in-code default, the compose default, and the .env.example template
    # all default to False so a deployment that doesn't think about MCP gets
    # no MCP endpoint. Operators flip FASTSSV_API_MCP_ENABLED=true explicitly
    # when they want it (and have configured a reverse proxy to gate it).
    # The `mcp` extra must also be installed for the mount to actually happen;
    # if the operator opts in but the extra is missing,
    # api/app.py:_maybe_build_mcp_app raises RuntimeError at startup so the
    # misconfiguration fails loudly in CI/docker rather than silently booting
    # without /mcp.
    mcp_enabled: bool = Field(default=False)
    mcp_allowed_origins: Annotated[List[str], NoDecode] = Field(default_factory=list)
    # Reserved knob for future OAuth 2.1 conformance. Today only "none" is
    # accepted; the spec permits unauthenticated MCP servers, and fastssv
    # delegates auth to the reverse proxy. Widening the Literal is how we
    # opt in later without churning the env-var name.
    mcp_auth_mode: Literal["none"] = Field(default="none")

    @field_validator("cors_origins", "mcp_allowed_origins", mode="before")
    @classmethod
    def _parse_origin_list(cls, value):
        # Tolerate the common env-var shapes: empty/whitespace string → [],
        # JSON list → parsed list, comma-separated string → split list.
        if value is None or value == "":
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    def cors_allow_origins(self) -> List[str]:
        return self.cors_origins or []

    def mcp_allow_origins(self) -> List[str]:
        return self.mcp_allowed_origins or []


def get_settings() -> Settings:
    return Settings()

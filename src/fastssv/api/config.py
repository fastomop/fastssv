"""Environment-driven configuration for the FastSSV API."""

import json
from typing import Annotated, List

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
    # NoDecode skips pydantic-settings' built-in JSON parsing for this
    # complex field so an empty string from the env doesn't blow up before
    # the validator below runs.
    cors_origins: Annotated[List[str], NoDecode] = Field(default_factory=list)
    log_level: str = Field(default="INFO")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value):
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


def get_settings() -> Settings:
    return Settings()

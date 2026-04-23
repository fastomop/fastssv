"""Environment-driven configuration for the FastSSV API."""

from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FASTSSV_API_",
        env_file=".env",
        extra="ignore",
    )

    max_sql_bytes: int = Field(default=100_000, ge=1, le=10_000_000)
    parse_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    rate_limit: str = Field(default="60/minute")
    cors_origins: List[str] = Field(default_factory=list)
    log_level: str = Field(default="INFO")

    def cors_allow_origins(self) -> List[str]:
        return self.cors_origins or []


def get_settings() -> Settings:
    return Settings()

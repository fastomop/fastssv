"""Unit tests for Settings.cors_origins env-var parsing."""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic_settings")

from fastssv.api.config import Settings  # noqa: E402


def _make_settings(**env_overrides) -> Settings:
    """Construct Settings without reading from any .env file."""
    return Settings(_env_file=None, **env_overrides)


class TestCorsOriginsValidator:
    def test_default_is_empty_list(self, monkeypatch):
        monkeypatch.delenv("FASTSSV_API_CORS_ORIGINS", raising=False)
        settings = _make_settings()
        assert settings.cors_origins == []

    def test_empty_string_yields_empty_list(self):
        settings = _make_settings(cors_origins="")
        assert settings.cors_origins == []

    def test_whitespace_only_yields_empty_list(self):
        settings = _make_settings(cors_origins="   ")
        assert settings.cors_origins == []

    def test_single_origin_comma_string(self):
        settings = _make_settings(cors_origins="http://localhost:3000")
        assert settings.cors_origins == ["http://localhost:3000"]

    def test_multiple_origins_comma_separated(self):
        settings = _make_settings(cors_origins="http://localhost:3000,http://localhost:8000")
        assert settings.cors_origins == [
            "http://localhost:3000",
            "http://localhost:8000",
        ]

    def test_comma_separated_with_extra_spaces(self):
        settings = _make_settings(cors_origins=" http://a.example.com , http://b.example.com ")
        assert settings.cors_origins == [
            "http://a.example.com",
            "http://b.example.com",
        ]

    def test_json_list_string(self):
        settings = _make_settings(cors_origins='["http://localhost:3000", "http://localhost:8000"]')
        assert settings.cors_origins == [
            "http://localhost:3000",
            "http://localhost:8000",
        ]

    def test_already_a_list_passthrough(self):
        origins = ["http://localhost:3000"]
        settings = _make_settings(cors_origins=origins)
        assert settings.cors_origins == origins

    def test_env_var_empty_string(self, monkeypatch):
        monkeypatch.setenv("FASTSSV_API_CORS_ORIGINS", "")
        settings = _make_settings()
        assert settings.cors_origins == []

    def test_env_var_comma_separated(self, monkeypatch):
        monkeypatch.setenv(
            "FASTSSV_API_CORS_ORIGINS",
            "http://localhost:3000,https://app.example.com",
        )
        settings = _make_settings()
        assert settings.cors_origins == [
            "http://localhost:3000",
            "https://app.example.com",
        ]

    def test_env_var_json_list(self, monkeypatch):
        monkeypatch.setenv(
            "FASTSSV_API_CORS_ORIGINS",
            '["http://localhost:3000","https://app.example.com"]',
        )
        settings = _make_settings()
        assert settings.cors_origins == [
            "http://localhost:3000",
            "https://app.example.com",
        ]

    def test_cors_allow_origins_returns_list(self):
        settings = _make_settings(cors_origins="http://localhost:3000")
        assert settings.cors_allow_origins() == ["http://localhost:3000"]

    def test_cors_allow_origins_empty_returns_empty_list(self):
        settings = _make_settings(cors_origins="")
        assert settings.cors_allow_origins() == []


class TestHardeningSettings:
    def test_defaults(self):
        settings = _make_settings()
        assert settings.max_concurrent_validations == 8
        assert settings.rate_limit_storage_uri == ""
        assert settings.trusted_proxy_hosts == "*"

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("FASTSSV_API_MAX_CONCURRENT_VALIDATIONS", "2")
        monkeypatch.setenv("FASTSSV_API_RATE_LIMIT_STORAGE_URI", "redis://redis:6379")
        monkeypatch.setenv("FASTSSV_API_TRUSTED_PROXY_HOSTS", "10.0.0.0/8, 192.168.0.1")
        settings = _make_settings()
        assert settings.max_concurrent_validations == 2
        assert settings.rate_limit_storage_uri == "redis://redis:6379"
        assert settings.trusted_proxy_hosts == "10.0.0.0/8, 192.168.0.1"

    def test_concurrency_bounds_enforced(self):
        with pytest.raises(Exception):
            _make_settings(max_concurrent_validations=0)

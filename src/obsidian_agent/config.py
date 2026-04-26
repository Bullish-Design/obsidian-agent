from pathlib import Path
from urllib.parse import urlparse, urlunparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    vault_dir: Path
    llm_model: str = "anthropic:claude-sonnet-4-20250514"
    llm_base_url: str | None = None
    llm_max_tokens: int = Field(default=4096, gt=0)
    max_iterations: int = Field(default=20, gt=0)
    operation_timeout: int = Field(default=120, gt=0)
    jj_bin: str = "jj"
    jj_timeout: int = Field(default=120, gt=0)
    site_base_url: str = "http://127.0.0.1:8080"
    flat_urls: bool = False
    deterministic_rate_limit: int = Field(default=120, ge=0)
    deterministic_rate_window_seconds: int = Field(default=60, gt=0)
    sync_after_commit: bool = False
    sync_remote: str = "origin"
    host: str = "127.0.0.1"
    port: int = Field(default=8081, ge=1, le=65535)

    @field_validator("vault_dir")
    @classmethod
    def validate_vault_dir(cls, value: Path) -> Path:
        if not value.exists():
            msg = f"vault_dir does not exist: {value}"
            raise ValueError(msg)
        if not value.is_dir():
            msg = f"vault_dir is not a directory: {value}"
            raise ValueError(msg)
        return value

    @field_validator("llm_model")
    @classmethod
    def validate_llm_model(cls, value: str) -> str:
        if ":" not in value:
            msg = "llm_model must be in 'provider:model-name' format"
            raise ValueError(msg)
        provider, model_name = value.split(":", 1)
        if not provider.strip() or not model_name.strip():
            msg = "llm_model must include non-empty provider and model name"
            raise ValueError(msg)
        return value

    @field_validator("llm_base_url")
    @classmethod
    def normalize_llm_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return value

        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"}:
            msg = "llm_base_url must use http or https"
            raise ValueError(msg)
        if not parsed.netloc:
            msg = "llm_base_url must include a host"
            raise ValueError(msg)
        normalized_path = parsed.path.rstrip("/")
        if not normalized_path:
            normalized_path = "/v1"

        return urlunparse(parsed._replace(path=normalized_path))

    @field_validator("site_base_url")
    @classmethod
    def normalize_site_base_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"}:
            msg = "site_base_url must use http or https"
            raise ValueError(msg)
        if not parsed.netloc:
            msg = "site_base_url must include a host"
            raise ValueError(msg)

        normalized_path = parsed.path.rstrip("/")
        return urlunparse(parsed._replace(path=normalized_path, query="", fragment=""))

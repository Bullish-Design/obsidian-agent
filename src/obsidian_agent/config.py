from pathlib import Path
from urllib.parse import urlparse, urlunparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    vault_dir: Path
    llm_model: str = "anthropic:claude-sonnet-4-20250514"
    llm_base_url: str | None = None
    llm_max_tokens: int = 4096
    max_iterations: int = 20
    operation_timeout: int = 120
    jj_bin: str = "jj"
    jj_timeout: int = 120
    host: str = "127.0.0.1"
    port: int = 8081

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
        return value

    @field_validator("llm_base_url")
    @classmethod
    def normalize_llm_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return value

        parsed = urlparse(value.strip())
        normalized_path = parsed.path.rstrip("/")
        if not normalized_path:
            normalized_path = "/v1"

        return urlunparse(parsed._replace(path=normalized_path))

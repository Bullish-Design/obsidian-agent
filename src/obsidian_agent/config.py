from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings


class AgentSettings(BaseSettings):
    """Runtime settings for the Obsidian Agent service."""

    vault_dir: Path
    vllm_base_url: str = "http://127.0.0.1:8000/v1"
    vllm_model: str = "local-model"
    vllm_api_key: str = ""
    jj_bin: str = "jj"
    host: str = "127.0.0.1"
    port: int = 8081
    max_tool_iterations: int = 12
    max_search_results: int = 12
    page_url_prefix: str = "/"
    operation_timeout_s: int = 120

    @field_validator("vault_dir", mode="before")
    @classmethod
    def validate_vault_dir(cls, v: Any) -> Path:
        if isinstance(v, str):
            v = Path(v)
        if not isinstance(v, Path):
            raise ValueError("vault_dir must be a Path")
        if not v.exists():
            raise ValueError(f"Vault directory does not exist: {v}")
        if not v.is_dir():
            raise ValueError(f"Vault path is not a directory: {v}")
        return v

    model_config = {"env_prefix": "AGENT_", "extra": "ignore"}


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    """Return a cached AgentSettings instance."""
    return AgentSettings()

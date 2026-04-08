from pathlib import Path

import pytest
from pydantic import ValidationError

from obsidian_agent.config import AgentConfig


def test_valid_config_from_kwargs(tmp_path: Path) -> None:
    cfg = AgentConfig(vault_dir=tmp_path)

    assert cfg.vault_dir == tmp_path
    assert cfg.llm_model == "anthropic:claude-sonnet-4-20250514"
    assert cfg.llm_base_url is None
    assert cfg.llm_max_tokens == 4096
    assert cfg.max_iterations == 20
    assert cfg.operation_timeout == 120
    assert cfg.jj_bin == "jj"
    assert cfg.jj_timeout == 120
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8081


def test_valid_config_from_env_vars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_LLM_MODEL", "openai:gpt-4o")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("AGENT_LLM_MAX_TOKENS", "2048")
    monkeypatch.setenv("AGENT_MAX_ITERATIONS", "10")
    monkeypatch.setenv("AGENT_OPERATION_TIMEOUT", "90")
    monkeypatch.setenv("AGENT_JJ_BIN", "jj-custom")
    monkeypatch.setenv("AGENT_JJ_TIMEOUT", "30")
    monkeypatch.setenv("AGENT_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENT_PORT", "9090")

    cfg = AgentConfig()

    assert cfg.vault_dir == tmp_path
    assert cfg.llm_model == "openai:gpt-4o"
    assert cfg.llm_base_url == "http://localhost:8000/v1"
    assert cfg.llm_max_tokens == 2048
    assert cfg.max_iterations == 10
    assert cfg.operation_timeout == 90
    assert cfg.jj_bin == "jj-custom"
    assert cfg.jj_timeout == 30
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9090


def test_missing_vault_dir() -> None:
    with pytest.raises(ValidationError):
        AgentConfig()


def test_vault_dir_is_file_not_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not_a_dir"
    not_a_dir.write_text("x")

    with pytest.raises(ValidationError):
        AgentConfig(vault_dir=not_a_dir)


def test_vault_dir_does_not_exist(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(ValidationError):
        AgentConfig(vault_dir=missing)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("http://localhost:8000", "http://localhost:8000/v1"),
        ("http://localhost:8000/", "http://localhost:8000/v1"),
        ("http://localhost:8000/v1/", "http://localhost:8000/v1"),
    ],
)
def test_base_url_normalization(tmp_path: Path, raw: str, normalized: str) -> None:
    cfg = AgentConfig(vault_dir=tmp_path, llm_base_url=raw)

    assert cfg.llm_base_url == normalized


def test_default_values(tmp_path: Path) -> None:
    cfg = AgentConfig(vault_dir=tmp_path)

    assert cfg.llm_model == "anthropic:claude-sonnet-4-20250514"
    assert cfg.llm_base_url is None
    assert cfg.llm_max_tokens == 4096
    assert cfg.max_iterations == 20
    assert cfg.operation_timeout == 120
    assert cfg.jj_bin == "jj"
    assert cfg.jj_timeout == 120
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8081


def test_invalid_model_string(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(vault_dir=tmp_path, llm_model="no-colon-here")


def test_extra_env_vars_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_UNKNOWN_FIELD", "foo")

    cfg = AgentConfig()

    assert cfg.vault_dir == tmp_path

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from obsidian_agent.config import AgentSettings, get_agent_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_agent_settings.cache_clear()
    yield
    get_agent_settings.cache_clear()


@pytest.fixture
def valid_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


def test_loads_valid_env(valid_vault: Path):
    settings = AgentSettings(vault_dir=valid_vault)
    assert settings.vault_dir == valid_vault
    assert settings.vllm_base_url == "http://127.0.0.1:8000/v1"
    assert settings.vllm_model == "local-model"
    assert settings.jj_bin == "jj"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8081
    assert settings.max_tool_iterations == 12
    assert settings.max_search_results == 12
    assert settings.page_url_prefix == "/"
    assert settings.operation_timeout_s == 120


def test_env_var_overrides(valid_vault: Path):
    settings = AgentSettings(
        vault_dir=valid_vault,
        vllm_base_url="http://example.com/v1",
        vllm_model="custom-model",
        jj_bin="/usr/local/bin/jj",
        host="0.0.0.0",
        port=9999,
        max_tool_iterations=20,
        operation_timeout_s=60,
    )
    assert settings.vllm_base_url == "http://example.com/v1"
    assert settings.vllm_model == "custom-model"
    assert settings.jj_bin == "/usr/local/bin/jj"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9999
    assert settings.max_tool_iterations == 20
    assert settings.operation_timeout_s == 60


def test_missing_vault_dir_raises(valid_vault: Path):
    with pytest.raises(ValidationError, match="vault_dir"):
        AgentSettings(vault_dir=valid_vault / "nonexistent")


def test_vault_dir_not_directory_raises(tmp_path: Path):
    file_path = tmp_path / "not_a_dir"
    file_path.touch()
    with pytest.raises(ValidationError, match="not a directory"):
        AgentSettings(vault_dir=file_path)


def test_get_agent_settings_cached(valid_vault: Path):
    with patch.dict(os.environ, {"AGENT_VAULT_DIR": str(valid_vault)}):
        s1 = get_agent_settings()
        s2 = get_agent_settings()
        assert s1 is s2

import pytest
from pydantic import ValidationError

from obsidian_agent.config import AgentConfig
from tests.support.vault_fs import VaultWorkspace


@pytest.fixture
def config_workspace(vault_workspace_factory) -> VaultWorkspace:
    return vault_workspace_factory("basic")


def test_valid_config_from_kwargs(config_workspace: VaultWorkspace) -> None:
    cfg = AgentConfig(vault_dir=config_workspace.work_dir)

    assert cfg.vault_dir == config_workspace.work_dir
    assert cfg.llm_model == "anthropic:claude-sonnet-4-20250514"
    assert cfg.llm_base_url is None
    assert cfg.llm_max_tokens == 4096
    assert cfg.max_iterations == 20
    assert cfg.operation_timeout == 120
    assert cfg.jj_bin == "jj"
    assert cfg.jj_timeout == 120
    assert cfg.site_base_url == "http://127.0.0.1:8080"
    assert cfg.flat_urls is False
    assert cfg.deterministic_rate_limit == 120
    assert cfg.deterministic_rate_window_seconds == 60
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8081


def test_valid_config_from_env_vars(monkeypatch: pytest.MonkeyPatch, config_workspace: VaultWorkspace) -> None:
    monkeypatch.setenv("AGENT_VAULT_DIR", str(config_workspace.work_dir))
    monkeypatch.setenv("AGENT_LLM_MODEL", "openai:gpt-4o")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("AGENT_LLM_MAX_TOKENS", "2048")
    monkeypatch.setenv("AGENT_MAX_ITERATIONS", "10")
    monkeypatch.setenv("AGENT_OPERATION_TIMEOUT", "90")
    monkeypatch.setenv("AGENT_JJ_BIN", "jj-custom")
    monkeypatch.setenv("AGENT_JJ_TIMEOUT", "30")
    monkeypatch.setenv("AGENT_SITE_BASE_URL", "https://example.com/notes/")
    monkeypatch.setenv("AGENT_FLAT_URLS", "true")
    monkeypatch.setenv("AGENT_DETERMINISTIC_RATE_LIMIT", "30")
    monkeypatch.setenv("AGENT_DETERMINISTIC_RATE_WINDOW_SECONDS", "10")
    monkeypatch.setenv("AGENT_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENT_PORT", "9090")

    cfg = AgentConfig()

    assert cfg.vault_dir == config_workspace.work_dir
    assert cfg.llm_model == "openai:gpt-4o"
    assert cfg.llm_base_url == "http://localhost:8000/v1"
    assert cfg.llm_max_tokens == 2048
    assert cfg.max_iterations == 10
    assert cfg.operation_timeout == 90
    assert cfg.jj_bin == "jj-custom"
    assert cfg.jj_timeout == 30
    assert cfg.site_base_url == "https://example.com/notes"
    assert cfg.flat_urls is True
    assert cfg.deterministic_rate_limit == 30
    assert cfg.deterministic_rate_window_seconds == 10
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9090


def test_missing_vault_dir() -> None:
    with pytest.raises(ValidationError):
        AgentConfig()


def test_vault_dir_is_file_not_directory(config_workspace: VaultWorkspace) -> None:
    not_a_dir = config_workspace.workspace_root / "not_a_dir"
    not_a_dir.write_text("x")

    with pytest.raises(ValidationError):
        AgentConfig(vault_dir=not_a_dir)


def test_vault_dir_does_not_exist(config_workspace: VaultWorkspace) -> None:
    missing = config_workspace.workspace_root / "missing"

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
def test_base_url_normalization(config_workspace: VaultWorkspace, raw: str, normalized: str) -> None:
    cfg = AgentConfig(vault_dir=config_workspace.work_dir, llm_base_url=raw)

    assert cfg.llm_base_url == normalized


def test_default_values(config_workspace: VaultWorkspace) -> None:
    cfg = AgentConfig(vault_dir=config_workspace.work_dir)

    assert cfg.llm_model == "anthropic:claude-sonnet-4-20250514"
    assert cfg.llm_base_url is None
    assert cfg.llm_max_tokens == 4096
    assert cfg.max_iterations == 20
    assert cfg.operation_timeout == 120
    assert cfg.jj_bin == "jj"
    assert cfg.jj_timeout == 120
    assert cfg.site_base_url == "http://127.0.0.1:8080"
    assert cfg.flat_urls is False
    assert cfg.deterministic_rate_limit == 120
    assert cfg.deterministic_rate_window_seconds == 60
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8081


def test_invalid_model_string(config_workspace: VaultWorkspace) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(vault_dir=config_workspace.work_dir, llm_model="no-colon-here")


@pytest.mark.parametrize("model", [":gpt-4o", "openai:", ":"])
def test_invalid_model_string_with_empty_segment(config_workspace: VaultWorkspace, model: str) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(vault_dir=config_workspace.work_dir, llm_model=model)


@pytest.mark.parametrize("url", ["ftp://localhost:8000", "localhost:8000", "http:///v1"])
def test_invalid_base_url(config_workspace: VaultWorkspace, url: str) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(vault_dir=config_workspace.work_dir, llm_base_url=url)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://localhost:8080/", "http://localhost:8080"),
        ("https://example.com/site/", "https://example.com/site"),
    ],
)
def test_site_base_url_normalization(config_workspace: VaultWorkspace, raw: str, normalized: str) -> None:
    cfg = AgentConfig(vault_dir=config_workspace.work_dir, site_base_url=raw)

    assert cfg.site_base_url == normalized


@pytest.mark.parametrize("url", ["ftp://localhost:8080", "localhost:8080", "http:///x"])
def test_invalid_site_base_url(config_workspace: VaultWorkspace, url: str) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(vault_dir=config_workspace.work_dir, site_base_url=url)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("llm_max_tokens", 0),
        ("llm_max_tokens", -1),
        ("max_iterations", 0),
        ("operation_timeout", 0),
        ("jj_timeout", 0),
        ("port", 0),
        ("port", 70000),
        ("deterministic_rate_limit", -1),
        ("deterministic_rate_window_seconds", 0),
    ],
)
def test_numeric_bounds_validation(config_workspace: VaultWorkspace, field_name: str, value: int) -> None:
    kwargs = {"vault_dir": config_workspace.work_dir, field_name: value}
    with pytest.raises(ValidationError):
        AgentConfig(**kwargs)


def test_extra_env_vars_ignored(monkeypatch: pytest.MonkeyPatch, config_workspace: VaultWorkspace) -> None:
    monkeypatch.setenv("AGENT_VAULT_DIR", str(config_workspace.work_dir))
    monkeypatch.setenv("AGENT_UNKNOWN_FIELD", "foo")

    cfg = AgentConfig()

    assert cfg.vault_dir == config_workspace.work_dir

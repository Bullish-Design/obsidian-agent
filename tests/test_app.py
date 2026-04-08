import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from obsidian_ops import Vault
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from obsidian_agent.agent import Agent, BusyError
from obsidian_agent.app import create_app
from obsidian_agent.config import AgentConfig
from obsidian_agent.models import RunResult


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("# Test\nContent.\n")

    vault = Vault(str(vault_dir))
    config = AgentConfig(vault_dir=vault_dir)
    agent = Agent(config, vault)

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        return ModelResponse(parts=[TextPart("No changes needed")])

    def commit_noop(message: str) -> None:
        _ = message

    def undo_noop() -> None:
        return None

    monkeypatch.setattr(vault, "commit", commit_noop)
    monkeypatch.setattr(vault, "undo", undo_noop)

    app = create_app(agent)
    with agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client


def test_post_apply_valid_request(client: TestClient) -> None:
    response = client.post("/api/apply", json={"instruction": "Update note.md"})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


def test_post_apply_empty_instruction(client: TestClient) -> None:
    response = client.post("/api/apply", json={"instruction": "   "})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"] == "instruction is required"


def test_post_apply_with_current_file(client: TestClient) -> None:
    response = client.post("/api/apply", json={"instruction": "Summarize this", "current_file": "note.md"})

    assert response.status_code == 200


def test_post_undo(client: TestClient) -> None:
    response = client.post("/api/undo")

    assert response.status_code == 200
    data = response.json()
    assert "ok" in data


def test_get_health(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"] == "healthy"


def test_apply_response_schema(client: TestClient) -> None:
    response = client.post("/api/apply", json={"instruction": "Update note.md"})

    assert response.status_code == 200
    data = response.json()

    assert "ok" in data
    assert "updated" in data
    assert "summary" in data
    assert "changed_files" in data
    assert "error" in data
    assert "warning" in data


def test_post_apply_missing_instruction_returns_422(client: TestClient) -> None:
    response = client.post("/api/apply", json={})

    assert response.status_code == 422


def test_apply_timeout_returns_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow_run(instruction: str, current_file: str | None = None) -> RunResult:
        _ = instruction, current_file
        await asyncio.sleep(0.05)
        return RunResult(ok=True, updated=False, summary="done")

    client.app.state.agent.config.operation_timeout = 0
    monkeypatch.setattr(client.app.state.agent, "run", slow_run)

    response = client.post("/api/apply", json={"instruction": "Timeout me"})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "timed out" in data["error"]


def test_apply_busy_returns_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def busy_run(instruction: str, current_file: str | None = None) -> RunResult:
        _ = instruction, current_file
        raise BusyError("Another operation is already running")

    monkeypatch.setattr(client.app.state.agent, "run", busy_run)

    response = client.post("/api/apply", json={"instruction": "Run concurrently"})

    assert response.status_code == 409
    assert response.json()["detail"] == "Another operation is already running"


def test_undo_busy_returns_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def busy_undo() -> RunResult:
        raise BusyError("Another operation is already running")

    monkeypatch.setattr(client.app.state.agent, "undo", busy_undo)

    response = client.post("/api/undo")

    assert response.status_code == 409
    assert response.json()["detail"] == "Another operation is already running"


def test_default_app_lifespan_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setenv("AGENT_VAULT_DIR", str(vault_dir))

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/health")

    assert response.status_code == 200

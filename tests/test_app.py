from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from obsidian_ops import Vault
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from obsidian_agent.agent import Agent
from obsidian_agent.app import create_app
from obsidian_agent.config import AgentConfig


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

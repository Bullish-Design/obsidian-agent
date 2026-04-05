from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from obsidian_agent.config import get_agent_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_agent_settings.cache_clear()
    yield
    get_agent_settings.cache_clear()


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def app(vault_dir: Path):
    mock_jj = MagicMock()
    mock_jj.ensure_workspace = AsyncMock()
    mock_jj.commit = AsyncMock(return_value="committed")
    mock_jj.undo = AsyncMock(return_value="undone")

    mock_agent = MagicMock()
    mock_tool_runtime = MagicMock()
    mock_tool_runtime.changed_files = []
    mock_tool_runtime.reset = MagicMock()

    mock_settings = MagicMock()
    mock_settings.vault_dir = vault_dir
    mock_settings.page_url_prefix = "/"
    mock_settings.operation_timeout_s = 120

    from obsidian_agent.app import app as fastapi_app

    fastapi_app.state.settings = mock_settings
    fastapi_app.state.jj = mock_jj
    fastapi_app.state.agent = mock_agent
    fastapi_app.state.tool_runtime = mock_tool_runtime

    return fastapi_app, mock_agent, mock_jj, mock_tool_runtime, mock_settings


def test_health_endpoint(app):
    fastapi_app, *_ = app
    client = TestClient(fastapi_app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_apply_success_with_changes(app):
    fastapi_app, mock_agent, mock_jj, mock_tool_runtime, _ = app

    async def fake_run(instruction, file_path, on_progress):
        mock_tool_runtime.changed_files = ["note.md"]
        return {"summary": "Updated note", "changed_files": ["note.md"]}

    mock_agent.run = AsyncMock(side_effect=fake_run)

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/apply",
        json={"instruction": "Improve note", "current_url_path": "/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["updated"] is True
    assert data["summary"] == "Updated note"
    assert data["changed_files"] == ["note.md"]
    assert data["error"] is None
    mock_jj.commit.assert_called_once()


def test_apply_success_no_changes(app):
    fastapi_app, mock_agent, mock_jj, mock_tool_runtime, _ = app

    async def fake_run(instruction, file_path, on_progress):
        mock_tool_runtime.changed_files = []
        return {"summary": "No edits necessary", "changed_files": []}

    mock_agent.run = AsyncMock(side_effect=fake_run)

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/apply",
        json={"instruction": "Check note", "current_url_path": "/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["updated"] is False
    assert data["summary"] == "No edits necessary"
    assert data["changed_files"] == []
    mock_jj.commit.assert_not_called()


def test_apply_agent_failure(app):
    fastapi_app, mock_agent, _, _, _ = app
    mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM connection failed"))

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/apply",
        json={"instruction": "Improve note", "current_url_path": "/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["updated"] is False
    assert "LLM connection failed" in data["error"]


def test_apply_timeout(app):
    import asyncio

    fastapi_app, mock_agent, _, _, mock_settings = app
    mock_agent.run = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_settings.operation_timeout_s = 120

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/apply",
        json={"instruction": "Slow operation", "current_url_path": "/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "timed out" in data["error"]


def test_undo_success(app):
    fastapi_app, _, mock_jj, _, _ = app
    mock_jj.undo = AsyncMock(return_value="undone")

    client = TestClient(fastapi_app)
    response = client.post("/api/undo")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["updated"] is True
    assert data["summary"] == "Last change undone."
    assert data["changed_files"] == []
    mock_jj.undo.assert_called_once()


def test_undo_failure(app):
    fastapi_app, _, mock_jj, _, _ = app
    mock_jj.undo = AsyncMock(side_effect=RuntimeError("Nothing to undo"))

    client = TestClient(fastapi_app)
    response = client.post("/api/undo")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["updated"] is False
    assert "Nothing to undo" in data["error"]

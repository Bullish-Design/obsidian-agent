"""End-to-end integration tests using the demo-vault fixture."""

import asyncio
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
def demo_vault() -> Path:
    """Return the demo-vault path from the repo."""
    vault = Path(__file__).resolve().parent.parent / "demo-vault"
    assert vault.exists(), f"demo-vault not found at {vault}"
    assert (vault / ".jj").exists(), "demo-vault is not a jj workspace"
    return vault


@pytest.fixture
def app(demo_vault: Path):
    """Build a FastAPI app wired to the demo-vault with mocked dependencies."""
    mock_jj = MagicMock()
    mock_jj.ensure_workspace = AsyncMock()
    mock_jj.commit = AsyncMock(return_value="committed")
    mock_jj.undo = AsyncMock(return_value="undone")

    mock_agent = MagicMock()
    mock_tool_runtime = MagicMock()
    mock_tool_runtime.changed_files = []
    mock_tool_runtime.reset = MagicMock()

    mock_settings = MagicMock()
    mock_settings.vault_dir = demo_vault
    mock_settings.page_url_prefix = "/"
    mock_settings.operation_timeout_s = 120

    from obsidian_agent.app import app as fastapi_app

    fastapi_app.state.settings = mock_settings
    fastapi_app.state.jj = mock_jj
    fastapi_app.state.agent = mock_agent
    fastapi_app.state.tool_runtime = mock_tool_runtime

    return fastapi_app, mock_agent, mock_jj, mock_tool_runtime, mock_settings


def test_e2e_health(app):
    """Health endpoint returns ok."""
    fastapi_app, *_ = app
    client = TestClient(fastapi_app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_e2e_apply_with_changes(app):
    """Apply with file changes commits and returns updated=true."""
    fastapi_app, mock_agent, mock_jj, mock_tool_runtime, _ = app

    async def fake_run(instruction, file_path, on_progress):
        mock_tool_runtime.changed_files = ["index.md"]
        return {"summary": "Added summary.", "changed_files": ["index.md"]}

    mock_agent.run = fake_run

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/apply",
        json={"instruction": "Add a summary", "current_url_path": "/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["updated"] is True
    assert data["summary"] == "Added summary."
    assert data["changed_files"] == ["index.md"]
    assert data["error"] is None
    mock_jj.commit.assert_called_once()


def test_e2e_apply_no_changes(app):
    """Apply without file changes skips commit and returns updated=false."""
    fastapi_app, mock_agent, mock_jj, mock_tool_runtime, _ = app

    async def fake_run(instruction, file_path, on_progress):
        mock_tool_runtime.changed_files = []
        return {"summary": "No edits necessary.", "changed_files": []}

    mock_agent.run = fake_run

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/apply",
        json={"instruction": "Check note", "current_url_path": "/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["updated"] is False
    assert data["summary"] == "No edits necessary."
    assert data["changed_files"] == []
    mock_jj.commit.assert_not_called()


def test_e2e_apply_failure(app):
    """Apply with agent failure returns ok=false with error message."""
    fastapi_app, mock_agent, _, _, _ = app

    async def failing_run(instruction, file_path, on_progress):
        raise RuntimeError("LLM unavailable")

    mock_agent.run = failing_run

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/apply",
        json={"instruction": "Do something", "current_url_path": "/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["updated"] is False
    assert data["summary"] == ""
    assert data["changed_files"] == []
    assert "LLM unavailable" in data["error"]


def test_e2e_apply_timeout(app):
    """Apply timeout returns ok=false with timeout message."""
    fastapi_app, mock_agent, _, _, mock_settings = app

    async def slow_run(instruction, file_path, on_progress):
        await asyncio.sleep(999)

    mock_agent.run = slow_run
    mock_settings.operation_timeout_s = 0.01

    client = TestClient(fastapi_app)
    response = client.post(
        "/api/apply",
        json={"instruction": "Slow operation", "current_url_path": "/"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "timed out" in data["error"]


def test_e2e_undo_success(app):
    """Undo returns ok=true with updated=true."""
    fastapi_app, _, mock_jj, *_ = app

    async def fake_undo():
        return "undone"

    mock_jj.undo = fake_undo

    client = TestClient(fastapi_app)
    response = client.post("/api/undo")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["updated"] is True
    assert data["summary"] == "Last change undone."
    assert data["changed_files"] == []
    assert data["error"] is None


def test_e2e_undo_failure(app):
    """Undo failure returns ok=false with error message."""
    fastapi_app, _, mock_jj, *_ = app

    async def failing_undo():
        raise RuntimeError("Nothing to undo")

    mock_jj.undo = failing_undo

    client = TestClient(fastapi_app)
    response = client.post("/api/undo")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["updated"] is False
    assert data["summary"] == ""
    assert data["changed_files"] == []
    assert "Nothing to undo" in data["error"]

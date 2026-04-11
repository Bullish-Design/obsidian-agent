import pytest
from fastapi.testclient import TestClient
from obsidian_ops import Vault
from obsidian_ops.errors import BusyError as VaultBusyError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from obsidian_agent.agent import Agent, BusyError
from obsidian_agent.app import create_app
from obsidian_agent.config import AgentConfig
from obsidian_agent.models import RunResult
from tests.support.vault_fs import VaultWorkspace


def text_only_model(text: str) -> FunctionModel:
    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(model_fn)


def write_note_model(new_content: str) -> FunctionModel:
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(parts=[ToolCallPart("write_file", {"path": "note.md", "content": new_content})])
        return ModelResponse(parts=[TextPart("Updated note")])

    return FunctionModel(model_fn)


@pytest.fixture
def app_workspace(vault_workspace_factory) -> VaultWorkspace:
    return vault_workspace_factory("app")


@pytest.fixture
def client(app_workspace: VaultWorkspace, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    vault = Vault(str(app_workspace.work_dir))
    config = AgentConfig(vault_dir=app_workspace.work_dir)
    agent = Agent(config, vault)

    def commit_noop(message: str) -> None:
        _ = message

    class UndoResult:
        warning = None

    def undo_noop() -> UndoResult:
        return UndoResult()

    async def run_noop(
        instruction: str,
        current_file: str | None = None,
        **kwargs,
    ) -> RunResult:
        _ = instruction, current_file, kwargs
        return RunResult(ok=True, updated=False, summary="No changes needed")

    monkeypatch.setattr(vault, "commit", commit_noop)
    monkeypatch.setattr(vault, "undo_last_change", undo_noop)
    monkeypatch.setattr(agent, "run", run_noop)

    app = create_app(agent)
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
    response = client.post(
        "/api/apply",
        json={"instruction": "Summarize this", "current_file": "note.md", "interface_id": "command"},
    )

    assert response.status_code == 200


def test_post_apply_with_scope_and_forge_web_interface(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str | None, dict]] = []

    async def run_spy(
        instruction: str,
        current_file: str | None = None,
        **kwargs,
    ) -> RunResult:
        captured.append((instruction, current_file, kwargs))
        return RunResult(ok=True, updated=False, summary="No changes needed")

    monkeypatch.setattr(client.app.state.agent, "run", run_spy)

    response = client.post(
        "/api/apply",
        json={
            "instruction": "Summarize this section",
            "interface_id": "forge_web",
            "scope": {"kind": "heading", "path": "note.md", "heading": "## Test"},
            "intent": "summarize",
        },
    )

    assert response.status_code == 200
    assert captured[0][0] == "Summarize this section"
    assert captured[0][1] == "note.md"
    assert captured[0][2]["interface_id"] == "forge_web"
    assert captured[0][2]["allowed_write_paths"] == {"note.md"}
    assert "write_file" not in captured[0][2]["allowed_tool_names"]


def test_post_apply_rejects_mismatched_scope_and_current_file(client: TestClient) -> None:
    response = client.post(
        "/api/apply",
        json={
            "instruction": "Summarize this",
            "current_file": "note.md",
            "scope": {"kind": "file", "path": "other.md"},
        },
    )

    assert response.status_code == 422


def test_post_apply_with_invalid_current_file_returns_422(client: TestClient) -> None:
    response = client.post("/api/apply", json={"instruction": "Summarize this", "current_file": "../note.md"})

    assert response.status_code == 422


def test_post_apply_with_current_url_path_field_returns_422(client: TestClient) -> None:
    response = client.post("/api/apply", json={"instruction": "Summarize this", "current_url_path": "/vault/note"})

    assert response.status_code == 422


def test_post_apply_with_empty_interface_id_returns_422(client: TestClient) -> None:
    response = client.post("/api/apply", json={"instruction": "Summarize this", "interface_id": " "})

    assert response.status_code == 422


def test_post_apply_unknown_interface_id_returns_400(client: TestClient) -> None:
    response = client.post("/api/apply", json={"instruction": "Summarize this", "interface_id": "chat"})

    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported interface_id: chat"


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


def test_post_apply_missing_instruction_returns_200_error(client: TestClient) -> None:
    response = client.post("/api/apply", json={})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"] == "instruction is required"


def test_post_apply_defaults_interface_to_command(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str | None, dict]] = []

    async def run_spy(
        instruction: str,
        current_file: str | None = None,
        **kwargs,
    ) -> RunResult:
        captured.append((instruction, current_file, kwargs))
        return RunResult(ok=True, updated=False, summary="No changes needed")

    monkeypatch.setattr(client.app.state.agent, "run", run_spy)

    response = client.post("/api/apply", json={"instruction": "Summarize this", "current_file": "note.md"})

    assert response.status_code == 200
    assert captured == [
        (
            "Summarize this",
            "note.md",
            {
                "interface_id": "command",
                "scope": None,
                "intent": None,
                "allowed_write_scope": "target_only",
                "allowed_tool_names": {
                    "delete_file",
                    "delete_frontmatter_field",
                    "get_frontmatter",
                    "list_files",
                    "read_block",
                    "read_file",
                    "read_heading",
                    "search_files",
                    "create_from_template",
                    "set_frontmatter",
                    "update_frontmatter",
                    "write_block",
                    "write_file",
                    "write_heading",
                },
                "allowed_write_paths": None,
                "profile_prompt_suffix": "",
            },
        )
    ]


def test_apply_timeout_returns_error_from_agent(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def timeout_run(
        instruction: str,
        current_file: str | None = None,
        **kwargs,
    ) -> RunResult:
        _ = instruction, current_file, kwargs
        return RunResult(
            ok=False,
            updated=False,
            summary="",
            error="Operation timed out after 120s",
        )

    monkeypatch.setattr(client.app.state.agent, "run", timeout_run)

    response = client.post("/api/apply", json={"instruction": "Timeout me"})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"] == "Operation timed out after 120s"


def test_apply_busy_returns_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def busy_run(
        instruction: str,
        current_file: str | None = None,
        **kwargs,
    ) -> RunResult:
        _ = instruction, current_file, kwargs
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


def test_apply_vault_busy_returns_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def vault_busy_run(
        instruction: str,
        current_file: str | None = None,
        **kwargs,
    ) -> RunResult:
        _ = instruction, current_file, kwargs
        raise VaultBusyError("vault is busy elsewhere")

    monkeypatch.setattr(client.app.state.agent, "run", vault_busy_run)

    response = client.post("/api/apply", json={"instruction": "Run concurrently"})

    assert response.status_code == 409
    assert response.json()["detail"] == "vault is busy elsewhere"


def test_post_apply_mutates_file_on_disk(
    app_workspace: VaultWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = Vault(str(app_workspace.work_dir))
    config = AgentConfig(vault_dir=app_workspace.work_dir)
    agent = Agent(config, vault)

    def commit_noop(message: str) -> None:
        _ = message

    monkeypatch.setattr(vault, "commit", commit_noop)

    app = create_app(agent)
    with agent._pydantic_agent.override(model=write_note_model("# Test\nUpdated from API.\n")):
        with TestClient(app, raise_server_exceptions=False) as test_client:
            response = test_client.post("/api/apply", json={"instruction": "Update note content"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["updated"] is True
    assert "note.md" in payload["changed_files"]
    assert vault.read_file("note.md") == "# Test\nUpdated from API.\n"


def test_default_app_lifespan_from_env(monkeypatch: pytest.MonkeyPatch, app_workspace: VaultWorkspace) -> None:
    monkeypatch.setenv("AGENT_VAULT_DIR", str(app_workspace.work_dir))

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/health")

    assert response.status_code == 200

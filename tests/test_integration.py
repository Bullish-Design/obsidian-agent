import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from obsidian_ops import Vault
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from obsidian_agent.agent import Agent
from obsidian_agent.app import create_app
from obsidian_agent.config import AgentConfig
from tests.support.vault_fs import VaultWorkspace

pytestmark = pytest.mark.anyio


@pytest.fixture
def integration_workspace(vault_workspace_factory) -> VaultWorkspace:
    return vault_workspace_factory("integration")


@pytest.fixture
def integration_vault(integration_workspace: VaultWorkspace) -> Vault:
    if shutil.which("jj") is None:
        pytest.skip("jj is required for integration tests")

    vault_dir = integration_workspace.work_dir

    subprocess.run(["jj", "git", "init", str(vault_dir)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["jj", "config", "set", "--repo", "user.name", "Integration Tester"],
        cwd=vault_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["jj", "config", "set", "--repo", "user.email", "integration@example.com"],
        cwd=vault_dir,
        check=True,
        capture_output=True,
        text=True,
    )

    vault = Vault(str(vault_dir))
    vault.commit("baseline integration state")
    return vault


@pytest.fixture
def integration_agent(integration_vault: Vault) -> Agent:
    config = AgentConfig(vault_dir=Path(integration_vault.root), jj_bin="jj")
    return Agent(config, integration_vault)


async def test_apply_verify_and_undo_operation(integration_agent: Agent, integration_vault: Vault) -> None:
    original_content = integration_vault.read_file("Projects/Alpha.md")
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(parts=[ToolCallPart("read_file", {"path": "Projects/Alpha.md"})])
        if current == 1:
            return ModelResponse(
                parts=[ToolCallPart("write_file", {"path": "Projects/Alpha.md", "content": "# Alpha\nUpdated integration content.\n"})]
            )
        return ModelResponse(parts=[TextPart("Updated Alpha")])

    with integration_agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        apply_result = await integration_agent.run("Update Alpha file")

    assert apply_result.ok is True
    assert apply_result.warning is None
    assert "Projects/Alpha.md" in apply_result.changed_files
    assert "Updated integration content." in integration_vault.read_file("Projects/Alpha.md")

    undo_result = await integration_agent.undo()

    assert undo_result.ok is True
    assert undo_result.summary == "Last change undone."
    assert integration_vault.read_file("Projects/Alpha.md") == original_content


async def test_apply_with_no_changes(integration_agent: Agent, integration_vault: Vault) -> None:
    before = integration_vault.read_file("Projects/Alpha.md")

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        return ModelResponse(parts=[TextPart("No updates needed")])

    with integration_agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        result = await integration_agent.run("Review only")

    assert result.updated is False
    assert result.changed_files == []
    assert integration_vault.read_file("Projects/Alpha.md") == before


async def test_multiple_file_changes_in_one_run(integration_agent: Agent) -> None:
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(parts=[ToolCallPart("write_file", {"path": "Projects/Alpha.md", "content": "# Alpha\nA\n"})])
        if current == 1:
            return ModelResponse(parts=[ToolCallPart("write_file", {"path": "Projects/Beta.md", "content": "# Beta\nB\n"})])
        return ModelResponse(parts=[TextPart("Updated two files")])

    with integration_agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        result = await integration_agent.run("Update alpha and beta")

    assert sorted(result.changed_files) == ["Projects/Alpha.md", "Projects/Beta.md"]


async def test_http_integration_apply_and_undo(integration_agent: Agent, integration_vault: Vault) -> None:
    original = integration_vault.read_file("Projects/Alpha.md")
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(
                parts=[ToolCallPart("write_file", {"path": "Projects/Alpha.md", "content": "# Alpha\nHTTP write + undo.\n"})]
            )
        return ModelResponse(parts=[TextPart("Updated through HTTP")])

    app = create_app(integration_agent)
    with integration_agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        with TestClient(app, raise_server_exceptions=False) as client:
            apply_response = client.post("/api/apply", json={"instruction": "Write and verify undo"})
            undo_response = client.post("/api/undo")

    assert apply_response.status_code == 200
    assert undo_response.status_code == 200
    assert integration_vault.read_file("Projects/Alpha.md") == original


async def test_http_integration_apply_write_path(integration_agent: Agent, integration_vault: Vault) -> None:
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(
                parts=[ToolCallPart("write_file", {"path": "Projects/Alpha.md", "content": "# Alpha\nHTTP integration update.\n"})]
            )
        return ModelResponse(parts=[TextPart("Updated through HTTP")])

    app = create_app(integration_agent)
    with integration_agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/apply", json={"instruction": "Update Alpha through API"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["updated"] is True
    assert "Projects/Alpha.md" in payload["changed_files"]
    assert "HTTP integration update." in integration_vault.read_file("Projects/Alpha.md")

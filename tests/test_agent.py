import asyncio
from pathlib import Path

import pytest
from obsidian_ops import Vault
from obsidian_ops.errors import BusyError as VaultBusyError
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIModel

from obsidian_agent.agent import Agent, BusyError
from obsidian_agent.config import AgentConfig

pytestmark = pytest.mark.anyio


@pytest.fixture
def agent(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> Agent:
    config = AgentConfig(vault_dir=Path(vault.root))
    instance = Agent(config, vault)

    def commit_noop(message: str) -> None:
        _ = message

    monkeypatch.setattr(vault, "commit", commit_noop)
    return instance


def scripted_read_write_model() -> FunctionModel:
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(parts=[ToolCallPart("read_file", {"path": "note.md"})])
        if current == 1:
            return ModelResponse(parts=[ToolCallPart("write_file", {"path": "note.md", "content": "updated"})])
        return ModelResponse(parts=[TextPart("Updated note.md")])

    return FunctionModel(model_fn)


def text_only_model(text: str = "No changes") -> FunctionModel:
    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(model_fn)


async def test_happy_path(agent: Agent, vault: Vault) -> None:
    with agent._pydantic_agent.override(model=scripted_read_write_model()):
        result = await agent.run("Update note.md")

    assert result.ok is True
    assert result.updated is True
    assert "note.md" in result.changed_files
    assert result.summary
    assert vault.read_file("note.md") == "updated"


async def test_no_changes_text_only_response(agent: Agent) -> None:
    with agent._pydantic_agent.override(model=text_only_model("No edits required")):
        result = await agent.run("Check note")

    assert result.ok is True
    assert result.updated is False
    assert result.changed_files == []


async def test_tool_execution_error_is_non_fatal(agent: Agent) -> None:
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(parts=[ToolCallPart("read_file", {"path": "nonexistent.md"})])
        return ModelResponse(parts=[TextPart("Handled missing file")])

    with agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        result = await agent.run("Read missing file")

    assert result.ok is True
    assert result.updated is False


async def test_usage_limit_exceeded(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    config = AgentConfig(vault_dir=Path(vault.root), max_iterations=2)
    limited_agent = Agent(config, vault)

    def commit_noop(message: str) -> None:
        _ = message

    monkeypatch.setattr(vault, "commit", commit_noop)

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        return ModelResponse(parts=[ToolCallPart("read_file", {"path": "note.md"})])

    with limited_agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        result = await limited_agent.run("Loop forever")

    assert result.ok is False
    assert result.error is not None
    assert "max iterations" in result.error.lower()


async def test_changed_files_read_tools_not_tracked(agent: Agent) -> None:
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(parts=[ToolCallPart("read_file", {"path": "note.md"})])
        return ModelResponse(parts=[TextPart("Done")])

    with agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        result = await agent.run("Read note")

    assert result.changed_files == []


async def test_changed_files_write_tools_tracked(agent: Agent) -> None:
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(parts=[ToolCallPart("write_file", {"path": "new.md", "content": "hello"})])
        if current == 1:
            return ModelResponse(
                parts=[ToolCallPart("write_heading", {"path": "note.md", "heading": "# Hello", "content": "new"})]
            )
        return ModelResponse(parts=[TextPart("Done")])

    with agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        result = await agent.run("Update multiple files")

    assert sorted(result.changed_files) == ["new.md", "note.md"]


async def test_busy_error_on_concurrent_run(agent: Agent) -> None:
    async def slow_model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        await asyncio.sleep(0.3)
        return ModelResponse(parts=[TextPart("done")])

    with agent._pydantic_agent.override(model=FunctionModel(slow_model_fn)):
        task = asyncio.create_task(agent.run("slow task"))
        await asyncio.sleep(0.05)
        with pytest.raises(BusyError):
            await agent.run("second task")
        await task


async def test_vault_busy_error_propagates(agent: Agent, vault: Vault) -> None:
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(parts=[ToolCallPart("write_file", {"path": "note.md", "content": "updated"})])
        return ModelResponse(parts=[TextPart("done")])

    def busy_write(path: str, content: str) -> None:
        _ = path, content
        raise VaultBusyError("vault is busy elsewhere")

    vault.write_file = busy_write  # type: ignore[method-assign]

    with agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        with pytest.raises(VaultBusyError, match="vault is busy elsewhere"):
            await agent.run("Update note")


async def test_undo_success(agent: Agent, vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    def undo_noop() -> None:
        return None

    monkeypatch.setattr(vault, "undo", undo_noop)

    result = await agent.undo()

    assert result.ok is True
    assert result.summary == "Last change undone."


async def test_undo_failure(agent: Agent, vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    def undo_fail() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(vault, "undo", undo_fail)

    result = await agent.undo()

    assert result.ok is False
    assert result.error is not None
    assert "undo failed" in result.error


async def test_commit_failure_after_changes(agent: Agent, vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    turn = {"value": 0}

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        current = turn["value"]
        turn["value"] += 1
        if current == 0:
            return ModelResponse(parts=[ToolCallPart("write_file", {"path": "note.md", "content": "updated"})])
        return ModelResponse(parts=[TextPart("Done")])

    def commit_fail(message: str) -> None:
        _ = message
        raise RuntimeError("commit failed")

    monkeypatch.setattr(vault, "commit", commit_fail)

    with agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        result = await agent.run("Update note")

    assert result.ok is True
    assert result.warning is not None
    assert "Commit failed" in result.warning


async def test_model_api_error_is_reported(agent: Agent) -> None:
    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        raise ModelAPIError("test-model", "api down")

    with agent._pydantic_agent.override(model=FunctionModel(model_fn)):
        result = await agent.run("Trigger API error")

    assert result.ok is False
    assert result.error is not None
    assert result.error.startswith("LLM call failed:")


async def test_run_timeout_returns_error(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    config = AgentConfig(vault_dir=Path(vault.root), operation_timeout=0)
    timeout_agent = Agent(config, vault)

    def commit_noop(message: str) -> None:
        _ = message

    monkeypatch.setattr(vault, "commit", commit_noop)

    async def slow_model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        _ = messages, info
        await asyncio.sleep(0.05)
        return ModelResponse(parts=[TextPart("done")])

    with timeout_agent._pydantic_agent.override(model=FunctionModel(slow_model_fn)):
        result = await timeout_agent.run("slow task")

    assert result.ok is False
    assert result.error == "Operation timed out after 0s"
    assert timeout_agent._busy is False


def test_normalize_commit_message() -> None:
    assert Agent._normalize_commit_message("   a   b   c   ") == "a b c"
    assert Agent._normalize_commit_message("   ") == "obsidian-agent update"
    assert len(Agent._normalize_commit_message("x" * 200)) == 72


def test_extract_model_ids_variants() -> None:
    from_dict = Agent._extract_model_ids({"data": [{"id": "a"}, {"model": "b"}, {"name": "c"}, "d"]})
    from_list = Agent._extract_model_ids(["x", {"id": "y"}])
    from_other = Agent._extract_model_ids("invalid")

    assert from_dict == ["a", "b", "c", "d"]
    assert from_list == ["x", "y"]
    assert from_other == []


def test_build_model_with_non_openai_provider_keeps_string(vault: Vault) -> None:
    config = AgentConfig(
        vault_dir=Path(vault.root),
        llm_model="anthropic:claude-sonnet-4-20250514",
        llm_base_url="http://localhost:8000/v1",
    )
    instance = Agent(config, vault)

    assert instance._build_model() == "anthropic:claude-sonnet-4-20250514"


def test_build_model_with_openai_auto_uses_resolved_model(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Agent, "_resolve_model_name_from_base_url", lambda self, base_url: "resolved-instruct")
    config = AgentConfig(
        vault_dir=Path(vault.root),
        llm_model="openai:auto",
        llm_base_url="http://localhost:8000/v1",
    )
    instance = Agent(config, vault)

    model = instance._build_model()

    assert isinstance(model, OpenAIModel)


def test_resolve_model_name_prefers_instruct(agent: Agent, monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, str]]]:
            return {"data": [{"id": "base-model"}, {"id": "best-instruct-model"}]}

    monkeypatch.setattr("obsidian_agent.agent.httpx.get", lambda url, timeout: DummyResponse())

    result = agent._resolve_model_name_from_base_url("http://localhost:8000/v1")

    assert result == "best-instruct-model"


def test_resolve_model_name_raises_when_empty(agent: Agent, monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, str]]]:
            return {"data": []}

    monkeypatch.setattr("obsidian_agent.agent.httpx.get", lambda url, timeout: DummyResponse())

    with pytest.raises(ValueError):
        agent._resolve_model_name_from_base_url("http://localhost:8000/v1")


def test_resolve_model_name_raises_when_no_instruct_match(agent: Agent, monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[dict[str, str]]]:
            return {"data": [{"id": "embedding-model"}, {"id": "base-chat-model"}]}

    monkeypatch.setattr("obsidian_agent.agent.httpx.get", lambda url, timeout: DummyResponse())

    with pytest.raises(ValueError, match="none matched an instruct model"):
        agent._resolve_model_name_from_base_url("http://localhost:8000/v1")

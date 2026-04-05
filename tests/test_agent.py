from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obsidian_agent.agent import Agent, build_system_prompt


class FakeSettings:
    def __init__(self):
        self.vault_dir = Path("/tmp/vault")
        self.vllm_base_url = "http://localhost:8000/v1"
        self.vllm_model = "test-model"
        self.vllm_api_key = ""
        self.max_tool_iterations = 5


class FakeToolRuntime:
    def __init__(self):
        self.changed_files: list[str] = []
        self._reset_called = False

    def reset(self):
        self.changed_files = []
        self._reset_called = True

    async def call_tool(self, name: str, arguments: dict) -> str:
        if name == "write_file":
            self.changed_files.append(arguments.get("path", "unknown.md"))
            return f"Wrote {arguments.get('path', 'unknown.md')}"
        return f"Tool {name} result"


@pytest.mark.asyncio
async def test_agent_tool_call_dispatch():
    settings = FakeSettings()
    tools = FakeToolRuntime()
    agent = Agent(settings, tools)

    progress_messages = []

    async def on_progress(msg: str):
        progress_messages.append(msg)

    mock_response = MagicMock()
    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_1"
    mock_tool_call.type = "function"
    mock_tool_call.function.name = "write_file"
    mock_tool_call.function.arguments = '{"path": "note.md", "content": "hello"}'
    mock_response.choices = [MagicMock(message=MagicMock(content=None, tool_calls=[mock_tool_call]))]

    mock_final_response = MagicMock()
    mock_final_response.choices = [MagicMock(message=MagicMock(content="Done", tool_calls=None))]

    agent._client.chat.completions.create = AsyncMock(side_effect=[mock_response, mock_final_response])

    result = await agent.run("Improve note", "note.md", on_progress)

    assert "Agent started" in progress_messages
    assert result["summary"] == "Done"
    assert result["changed_files"] == ["note.md"]


@pytest.mark.asyncio
async def test_agent_iteration_cap():
    settings = FakeSettings()
    settings.max_tool_iterations = 2
    tools = FakeToolRuntime()
    agent = Agent(settings, tools)

    progress_messages = []

    async def on_progress(msg: str):
        progress_messages.append(msg)

    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_1"
    mock_tool_call.type = "function"
    mock_tool_call.function.name = "write_file"
    mock_tool_call.function.arguments = '{"path": "note.md", "content": "hello"}'

    agent._client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=None, tool_calls=[mock_tool_call]))])
    )

    result = await agent.run("Improve note", None, on_progress)

    assert "iteration limit" in result["summary"]


@pytest.mark.asyncio
async def test_agent_no_tool_calls():
    settings = FakeSettings()
    tools = FakeToolRuntime()
    agent = Agent(settings, tools)

    progress_messages = []

    async def on_progress(msg: str):
        progress_messages.append(msg)

    agent._client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="Here is my answer", tool_calls=None))])
    )

    result = await agent.run("What is this?", None, on_progress)

    assert result["summary"] == "Here is my answer"
    assert result["changed_files"] == []


@pytest.mark.asyncio
async def test_agent_llm_failure():
    settings = FakeSettings()
    tools = FakeToolRuntime()
    agent = Agent(settings, tools)

    async def on_progress(msg: str):
        pass

    agent._client.chat.completions.create = AsyncMock(side_effect=Exception("Connection refused"))

    with pytest.raises(RuntimeError, match="LLM call failed"):
        await agent.run("Improve note", None, on_progress)


@pytest.mark.asyncio
async def test_agent_resets_changed_files():
    settings = FakeSettings()
    tools = FakeToolRuntime()
    tools.changed_files = ["old.md"]
    agent = Agent(settings, tools)

    async def on_progress(msg: str):
        pass

    agent._client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="Done", tool_calls=None))])
    )

    await agent.run("Test", None, on_progress)
    assert tools.changed_files == []


def test_build_system_prompt_with_file():
    prompt = build_system_prompt("notes/example.md")
    assert "notes/example.md" in prompt
    assert "currently viewing" in prompt


def test_build_system_prompt_without_file():
    prompt = build_system_prompt(None)
    assert "No specific file" in prompt

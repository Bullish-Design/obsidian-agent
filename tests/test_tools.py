from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obsidian_agent.fs_atomic import write_file_atomic
from obsidian_agent.locks import FileLockManager
from obsidian_agent.tools import ToolRuntime, get_tool_definitions
from obsidian_agent.vcs import JujutsuHistory


class FakeSettings:
    def __init__(self, vault_dir: Path):
        self.vault_dir = vault_dir
        self.max_search_results = 12


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def tool_runtime(vault_dir: Path):
    settings = FakeSettings(vault_dir)
    lock_manager = FileLockManager()
    jj = MagicMock(spec=JujutsuHistory)
    return ToolRuntime(settings, lock_manager, jj)


@pytest.mark.asyncio
async def test_read_file_happy_path(tool_runtime: ToolRuntime, vault_dir: Path):
    (vault_dir / "note.md").write_text("hello world", encoding="utf-8")
    content = await tool_runtime.read_file("note.md")
    assert content == "hello world"


@pytest.mark.asyncio
async def test_write_file_happy_path(tool_runtime: ToolRuntime, vault_dir: Path):
    result = await tool_runtime.write_file("new.md", "content")
    assert "Wrote new.md" in result
    assert (vault_dir / "new.md").read_text(encoding="utf-8") == "content"
    assert tool_runtime.changed_files == ["new.md"]


@pytest.mark.asyncio
async def test_write_file_tracks_changed_files(tool_runtime: ToolRuntime, vault_dir: Path):
    await tool_runtime.write_file("a.md", "a")
    await tool_runtime.write_file("b.md", "b")
    assert sorted(tool_runtime.changed_files) == ["a.md", "b.md"]


@pytest.mark.asyncio
async def test_write_file_rejects_path_traversal(tool_runtime: ToolRuntime):
    with pytest.raises(ValueError, match="Path traversal"):
        await tool_runtime.write_file("../escape.md", "bad")


@pytest.mark.asyncio
async def test_list_files(tool_runtime: ToolRuntime, vault_dir: Path):
    (vault_dir / "a.md").write_text("a", encoding="utf-8")
    (vault_dir / "b.md").write_text("b", encoding="utf-8")
    files = await tool_runtime.list_files()
    assert files == ["a.md", "b.md"]


@pytest.mark.asyncio
async def test_search_files(tool_runtime: ToolRuntime, vault_dir: Path):
    (vault_dir / "note.md").write_text("hello world\nsecond line", encoding="utf-8")
    results = await tool_runtime.search_files("hello")
    assert len(results) == 1
    assert results[0]["path"] == "note.md"
    assert "hello world" in results[0]["snippet"]


@pytest.mark.asyncio
async def test_search_files_empty_query(tool_runtime: ToolRuntime):
    results = await tool_runtime.search_files("")
    assert results == []


@pytest.mark.asyncio
async def test_search_files_respects_limit(tool_runtime: ToolRuntime, vault_dir: Path):
    for i in range(20):
        (vault_dir / f"file_{i}.md").write_text("match here", encoding="utf-8")
    results = await tool_runtime.search_files("match")
    assert len(results) == 12


@pytest.mark.asyncio
async def test_fetch_url_success(tool_runtime: ToolRuntime):
    mock_response = MagicMock()
    mock_response.text = "fetched content"
    mock_response.raise_for_status = MagicMock()

    with patch("obsidian_agent.tools.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response
        result = await tool_runtime.fetch_url("http://example.com")
        assert result == "fetched content"


@pytest.mark.asyncio
async def test_fetch_url_failure(tool_runtime: ToolRuntime):
    with patch("obsidian_agent.tools.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("connection error")
        result = await tool_runtime.fetch_url("http://bad")
        assert "Failed to fetch URL" in result


@pytest.mark.asyncio
async def test_undo_last_change_delegates_to_vcs(tool_runtime: ToolRuntime):
    tool_runtime._jj.undo = AsyncMock(return_value="undone")
    result = await tool_runtime.undo_last_change()
    assert result == "undone"
    tool_runtime._jj.undo.assert_called_once()


@pytest.mark.asyncio
async def test_get_file_history_delegates_to_vcs(tool_runtime: ToolRuntime, vault_dir: Path):
    (vault_dir / "note.md").write_text("content", encoding="utf-8")
    tool_runtime._jj.log_for_file = AsyncMock(return_value=["commit 1", "commit 2"])
    result = await tool_runtime.get_file_history("note.md", limit=5)
    assert result == ["commit 1", "commit 2"]
    tool_runtime._jj.log_for_file.assert_called_once_with("note.md", 5)


@pytest.mark.asyncio
async def test_call_tool_unknown(tool_runtime: ToolRuntime):
    result = await tool_runtime.call_tool("unknown_tool", {})
    assert "Unknown tool" in result


@pytest.mark.asyncio
async def test_call_tool_failure(tool_runtime: ToolRuntime):
    tool_runtime.read_file = AsyncMock(side_effect=ValueError("boom"))
    result = await tool_runtime.call_tool("read_file", {"path": "note.md"})
    assert "failed" in result


@pytest.mark.asyncio
async def test_reset_clears_changed_files(tool_runtime: ToolRuntime, vault_dir: Path):
    await tool_runtime.write_file("a.md", "a")
    assert tool_runtime.changed_files == ["a.md"]
    tool_runtime.reset()
    assert tool_runtime.changed_files == []


def test_get_tool_definitions():
    definitions = get_tool_definitions()
    names = [d["function"]["name"] for d in definitions]
    assert "read_file" in names
    assert "write_file" in names
    assert "list_files" in names
    assert "search_files" in names
    assert "fetch_url" in names
    assert "undo_last_change" in names
    assert "get_file_history" in names

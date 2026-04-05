from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obsidian_agent.vcs import JujutsuHistory


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    return tmp_path / "vault"


@pytest.mark.asyncio
async def test_ensure_workspace_succeeds(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir)

    with patch.object(history, "_run_jj", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        await history.ensure_workspace()
        mock_run.assert_called_once_with("status", timeout=30)


@pytest.mark.asyncio
async def test_ensure_workspace_fails(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir)

    with patch.object(history, "_run_jj", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="not a workspace")
        with pytest.raises(RuntimeError, match="Jujutsu workspace"):
            await history.ensure_workspace()


@pytest.mark.asyncio
async def test_commit_succeeds(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir)

    with patch.object(history, "_run_jj", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="commit output")
        result = await history.commit("test commit")
        assert result == "commit output"
        mock_run.assert_called_once_with("commit", "-m", "test commit", timeout=120)


@pytest.mark.asyncio
async def test_commit_fails(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir)

    with patch.object(history, "_run_jj", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="commit failed")
        with pytest.raises(RuntimeError, match="commit failed"):
            await history.commit("bad commit")


@pytest.mark.asyncio
async def test_undo_succeeds(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir)

    with patch.object(history, "_run_jj", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="undone")
        result = await history.undo()
        assert result == "undone"
        mock_run.assert_called_once_with("undo", timeout=120)


@pytest.mark.asyncio
async def test_undo_fails(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir)

    with patch.object(history, "_run_jj", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="undo failed")
        with pytest.raises(RuntimeError, match="undo failed"):
            await history.undo()


@pytest.mark.asyncio
async def test_log_for_file_succeeds(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir)

    with patch.object(history, "_run_jj", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="commit 1\ncommit 2\n\n",
        )
        result = await history.log_for_file("note.md", limit=5)
        assert result == ["commit 1", "commit 2"]
        mock_run.assert_called_once_with(
            "log",
            "--no-graph",
            "-r",
            "all()",
            "--limit",
            "5",
            "note.md",
            timeout=60,
        )


@pytest.mark.asyncio
async def test_log_for_file_fails(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir)

    with patch.object(history, "_run_jj", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="log failed")
        with pytest.raises(RuntimeError, match="log failed"):
            await history.log_for_file("note.md")


@pytest.mark.asyncio
async def test_diff_for_file_succeeds(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir)

    with patch.object(history, "_run_jj", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="diff content")
        result = await history.diff_for_file("note.md")
        assert result == "diff content"
        mock_run.assert_called_once_with("diff", "note.md", timeout=120)


@pytest.mark.asyncio
async def test_run_jj_file_not_found(vault_dir: Path):
    vault_dir.mkdir()
    history = JujutsuHistory(vault_dir, jj_bin="nonexistent-jj")

    with pytest.raises(RuntimeError, match="Jujutsu binary not found"):
        await history._run_jj("status")

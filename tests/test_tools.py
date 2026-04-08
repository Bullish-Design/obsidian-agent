import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from obsidian_ops import Vault
from obsidian_ops.errors import BusyError

from obsidian_agent.tools import (
    VaultDeps,
    delete_file,
    get_frontmatter,
    list_files,
    read_block,
    read_file,
    read_heading,
    search_files,
    update_frontmatter,
    write_block,
    write_file,
    write_heading,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    (vault_dir / "note.md").write_text("---\ntitle: Test\n---\n# Hello\nContent here.\n")
    (vault_dir / "plain.md").write_text("# Plain\nNo frontmatter.\n")
    (vault_dir / "Projects").mkdir()
    (vault_dir / "Projects/Alpha.md").write_text("---\nstatus: draft\n---\n# Alpha\nAlpha content.\n")
    (vault_dir / "block.md").write_text("# Block\n\nParagraph with block ref ^my-block\n")

    return Vault(str(vault_dir))


@pytest.fixture
def deps(vault: Vault) -> VaultDeps:
    return VaultDeps(vault=vault)


def make_ctx(deps: VaultDeps) -> SimpleNamespace:
    return SimpleNamespace(deps=deps)


async def test_read_file_returns_content(deps: VaultDeps) -> None:
    result = await read_file(make_ctx(deps), "note.md")

    assert "Content here." in result


async def test_write_file_writes_and_tracks(vault: Vault, deps: VaultDeps) -> None:
    result = await write_file(make_ctx(deps), "new.md", "hello")

    assert result == "Successfully wrote new.md"
    assert vault.read_file("new.md") == "hello"
    assert "new.md" in deps.changed_files


async def test_delete_file_deletes_and_tracks(vault: Vault, deps: VaultDeps) -> None:
    result = await delete_file(make_ctx(deps), "note.md")

    assert result == "Deleted note.md"
    assert not (Path(vault.root) / "note.md").exists()
    assert "note.md" in deps.changed_files


async def test_list_files_returns_formatted_list(deps: VaultDeps) -> None:
    result = await list_files(make_ctx(deps), "*.md")

    assert result.startswith("Found")
    assert "note.md" in result


async def test_list_files_no_matches(deps: VaultDeps) -> None:
    result = await list_files(make_ctx(deps), "*.xyz")

    assert result == "No files found."


async def test_search_files_returns_results(deps: VaultDeps) -> None:
    result = await search_files(make_ctx(deps), "Content")

    assert "Found" in result
    assert "note.md" in result


async def test_search_files_no_matches(deps: VaultDeps) -> None:
    result = await search_files(make_ctx(deps), "nonexistent_term_xyz")

    assert result == "No matches found."


async def test_get_frontmatter_returns_json(deps: VaultDeps) -> None:
    result = await get_frontmatter(make_ctx(deps), "note.md")

    parsed = json.loads(result)
    assert parsed["title"] == "Test"


async def test_get_frontmatter_no_frontmatter(deps: VaultDeps) -> None:
    result = await get_frontmatter(make_ctx(deps), "plain.md")

    assert result == "No frontmatter found."


async def test_update_frontmatter_updates_and_tracks(vault: Vault, deps: VaultDeps) -> None:
    result = await update_frontmatter(make_ctx(deps), "note.md", {"status": "done"})

    assert result == "Updated frontmatter for note.md"
    frontmatter = vault.get_frontmatter("note.md")
    assert frontmatter is not None
    assert frontmatter["status"] == "done"
    assert "note.md" in deps.changed_files


async def test_read_heading_returns_content(deps: VaultDeps) -> None:
    result = await read_heading(make_ctx(deps), "note.md", "# Hello")

    assert "Content here." in result


async def test_read_heading_not_found(deps: VaultDeps) -> None:
    result = await read_heading(make_ctx(deps), "note.md", "# Nonexistent")

    assert "not found" in result


async def test_write_heading_writes_and_tracks(vault: Vault, deps: VaultDeps) -> None:
    result = await write_heading(make_ctx(deps), "note.md", "# Hello", "Updated heading text")

    assert result == "Updated heading '# Hello' in note.md"
    content = vault.read_heading("note.md", "# Hello")
    assert content is not None
    assert "Updated heading text" in content
    assert "note.md" in deps.changed_files


async def test_read_write_block(deps: VaultDeps) -> None:
    read_result = await read_block(make_ctx(deps), "block.md", "^my-block")
    assert "Paragraph with block ref" in read_result

    write_result = await write_block(
        make_ctx(deps),
        "block.md",
        "^my-block",
        "Replacement content ^my-block",
    )
    assert write_result == "Updated block '^my-block' in block.md"
    assert "block.md" in deps.changed_files

    new_block = await read_block(make_ctx(deps), "block.md", "^my-block")
    assert "Replacement content" in new_block


async def test_path_error_returns_error_string(deps: VaultDeps) -> None:
    result = await read_file(make_ctx(deps), "../../etc/passwd")

    assert result.startswith("Error:")


async def test_busy_error_reraises() -> None:
    class BusyVault:
        def read_file(self, path: str) -> str:
            raise BusyError("busy")

    deps = VaultDeps(vault=BusyVault())  # type: ignore[arg-type]

    with pytest.raises(BusyError):
        await read_file(make_ctx(deps), "note.md")

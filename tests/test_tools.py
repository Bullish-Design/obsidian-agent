import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from obsidian_ops import ReadinessCheck, SyncResult, VCSReadiness, Vault
from obsidian_ops.errors import BusyError, VaultError

from obsidian_agent.tools import (
    VaultDeps,
    check_sync_readiness,
    configure_sync_remote,
    create_from_template,
    delete_file,
    delete_frontmatter_field,
    ensure_sync_ready,
    get_frontmatter,
    list_files,
    read_block,
    read_file,
    read_heading,
    search_files,
    set_frontmatter,
    sync_fetch,
    sync_now,
    sync_push,
    sync_status,
    update_frontmatter,
    write_block,
    write_file,
    write_heading,
)
from tests.support.vault_fs import VaultWorkspace

pytestmark = pytest.mark.anyio


@pytest.fixture
def tools_workspace(vault_workspace_factory) -> VaultWorkspace:
    return vault_workspace_factory("tools")


@pytest.fixture
def vault(tools_workspace: VaultWorkspace) -> Vault:
    return Vault(str(tools_workspace.work_dir))


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


async def test_set_frontmatter_replaces_and_tracks(vault: Vault, deps: VaultDeps) -> None:
    result = await set_frontmatter(make_ctx(deps), "note.md", {"title": "Replaced", "status": "new"})

    assert result == "Set frontmatter for note.md"
    frontmatter = vault.get_frontmatter("note.md")
    assert frontmatter is not None
    assert frontmatter["title"] == "Replaced"
    assert frontmatter["status"] == "new"
    assert "note.md" in deps.changed_files


async def test_delete_frontmatter_field_updates_and_tracks(vault: Vault, deps: VaultDeps) -> None:
    result = await delete_frontmatter_field(make_ctx(deps), "note.md", "title")

    assert result == "Deleted frontmatter field 'title' from note.md"
    frontmatter = vault.get_frontmatter("note.md")
    assert frontmatter is not None
    assert "title" not in frontmatter
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


async def test_path_error_does_not_modify_outside_workspace(
    tools_workspace: VaultWorkspace,
    deps: VaultDeps,
) -> None:
    sentinel = tools_workspace.workspace_root / "outside.txt"
    sentinel.write_text("keep-me")

    result = await write_file(make_ctx(deps), "../../outside.txt", "mutated")

    assert result.startswith("Error:")
    assert sentinel.read_text() == "keep-me"


async def test_busy_error_reraises() -> None:
    class BusyVault:
        def read_file(self, path: str) -> str:
            raise BusyError("busy")

    deps = VaultDeps(vault=BusyVault())  # type: ignore[arg-type]

    with pytest.raises(BusyError):
        await read_file(make_ctx(deps), "note.md")


async def test_read_block_not_found_returns_message(deps: VaultDeps) -> None:
    result = await read_block(make_ctx(deps), "block.md", "^missing-block")

    assert "not found" in result


async def test_write_heading_creates_missing_heading(vault: Vault, deps: VaultDeps) -> None:
    result = await write_heading(make_ctx(deps), "plain.md", "## Added", "Fresh content")

    assert result == "Updated heading '## Added' in plain.md"
    content = vault.read_file("plain.md")
    assert "## Added" in content
    assert "Fresh content" in content
    assert "plain.md" in deps.changed_files


async def test_changed_files_deduplicates_multiple_writes(deps: VaultDeps) -> None:
    first = await write_file(make_ctx(deps), "note.md", "one")
    second = await write_file(make_ctx(deps), "note.md", "two")

    assert first == "Successfully wrote note.md"
    assert second == "Successfully wrote note.md"
    assert sorted(deps.changed_files) == ["note.md"]


@pytest.mark.parametrize(
    ("method_name", "tool_fn", "args"),
    [
        ("write_file", write_file, ("note.md", "x")),
        ("delete_file", delete_file, ("note.md",)),
        ("list_files", list_files, ("*.md",)),
        ("search_files", search_files, ("query",)),
        ("get_frontmatter", get_frontmatter, ("note.md",)),
        ("set_frontmatter", set_frontmatter, ("note.md", {"k": "v"})),
        ("update_frontmatter", update_frontmatter, ("note.md", {"k": "v"})),
        ("delete_frontmatter_field", delete_frontmatter_field, ("note.md", "k")),
        ("read_heading", read_heading, ("note.md", "# H")),
        ("write_heading", write_heading, ("note.md", "# H", "x")),
        ("read_block", read_block, ("note.md", "^b")),
        ("write_block", write_block, ("note.md", "^b", "x")),
    ],
)
async def test_tools_return_error_string_on_vault_error(method_name: str, tool_fn, args) -> None:
    class ErrorVault:
        def __getattr__(self, name: str):
            if name != method_name:
                raise AttributeError(name)

            def raise_error(*_args, **_kwargs):
                raise VaultError("boom")

            return raise_error

    deps = VaultDeps(vault=ErrorVault())  # type: ignore[arg-type]

    result = await tool_fn(make_ctx(deps), *args)

    assert result.startswith("Error:")


async def test_tools_propagate_unexpected_runtime_errors() -> None:
    class ErrorVault:
        def read_file(self, path: str) -> str:
            _ = path
            raise RuntimeError("boom")

    deps = VaultDeps(vault=ErrorVault())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="boom"):
        await read_file(make_ctx(deps), "note.md")


async def test_write_file_rejected_when_tool_not_allowed(vault: Vault) -> None:
    deps = VaultDeps(vault=vault, allowed_tool_names={"read_file"})

    result = await write_file(make_ctx(deps), "note.md", "updated")

    assert result == "Error: write_file is not allowed in this interface/scope"


async def test_write_heading_rejected_when_path_outside_allowed_scope(vault: Vault) -> None:
    deps = VaultDeps(vault=vault, allowed_tool_names={"write_heading"}, allowed_write_paths={"note.md"})

    result = await write_heading(make_ctx(deps), "plain.md", "## Added", "x")

    assert result == "Error: write target is outside allowed scope"


async def test_create_from_template_rejected_when_not_allowed(vault: Vault) -> None:
    deps = VaultDeps(vault=vault, allowed_tool_names={"read_file"})

    result = await create_from_template(make_ctx(deps), "daily", {"title": "Test"})

    assert result == "Error: create_from_template is not allowed in this interface/scope"


async def test_create_from_template_returns_unavailable_when_missing_method(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vault, "create_from_template", None, raising=False)
    deps = VaultDeps(vault=vault, allowed_tool_names={"create_from_template"})

    result = await create_from_template(make_ctx(deps), "daily", {"title": "Test"})

    assert result == "Error: create_from_template unavailable"


async def test_create_from_template_success_tracks_changed_file() -> None:
    class TemplateVault:
        def create_from_template(self, template_id: str, fields: dict[str, str]):
            assert template_id == "daily"
            assert fields == {"title": "My Day"}
            return SimpleNamespace(path="Daily/my-day.md")

    deps = VaultDeps(vault=TemplateVault(), allowed_tool_names={"create_from_template"})  # type: ignore[arg-type]

    result = await create_from_template(make_ctx(deps), "daily", {"title": "My Day"})

    assert result == "Created Daily/my-day.md"
    assert "Daily/my-day.md" in deps.changed_files


@pytest.mark.parametrize(
    ("tool_fn", "args", "tool_name"),
    [
        (check_sync_readiness, (), "check_sync_readiness"),
        (ensure_sync_ready, (), "ensure_sync_ready"),
        (configure_sync_remote, ("https://github.com/example/repo.git",), "configure_sync_remote"),
        (sync_fetch, (), "sync_fetch"),
        (sync_push, (), "sync_push"),
        (sync_now, (), "sync_now"),
        (sync_status, (), "sync_status"),
    ],
)
async def test_sync_tools_blocked_when_not_in_allowed_set(tool_fn, args, tool_name: str, vault: Vault) -> None:
    deps = VaultDeps(vault=vault, allowed_tool_names={"read_file"})

    result = await tool_fn(make_ctx(deps), *args)

    assert result == f"Error: {tool_name} is not allowed in this interface/scope"


async def test_check_sync_readiness_returns_status() -> None:
    class SyncVault:
        def check_sync_readiness(self) -> ReadinessCheck:
            return ReadinessCheck(status=VCSReadiness.READY, detail=None)

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"check_sync_readiness"})  # type: ignore[arg-type]

    result = await check_sync_readiness(make_ctx(deps))

    assert result == "Sync readiness: ready"


async def test_sync_status_returns_json() -> None:
    class SyncVault:
        def sync_status(self) -> dict[str, object]:
            return {"last_sync_ok": True}

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"sync_status"})  # type: ignore[arg-type]

    result = await sync_status(make_ctx(deps))
    parsed = json.loads(result)
    assert parsed["last_sync_ok"] is True


async def test_ensure_sync_ready_success() -> None:
    class SyncVault:
        def ensure_sync_ready(self) -> ReadinessCheck:
            return ReadinessCheck(status=VCSReadiness.READY, detail="ok")

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"ensure_sync_ready"})  # type: ignore[arg-type]

    result = await ensure_sync_ready(make_ctx(deps))

    assert result == "Sync readiness: ready (ok)"


async def test_configure_sync_remote_success() -> None:
    captured: dict[str, object] = {}

    class SyncVault:
        def configure_sync_remote(self, url: str, token: str | None = None, remote: str = "origin") -> None:
            captured["url"] = url
            captured["token"] = token
            captured["remote"] = remote

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"configure_sync_remote"})  # type: ignore[arg-type]

    result = await configure_sync_remote(
        make_ctx(deps),
        "https://github.com/example/repo.git",
        token="tok",
        remote="upstream",
    )

    assert result == "Remote 'upstream' configured for https://github.com/example/repo.git"
    assert captured == {"url": "https://github.com/example/repo.git", "token": "tok", "remote": "upstream"}


async def test_configure_sync_remote_invalid_url() -> None:
    class SyncVault:
        def configure_sync_remote(self, url: str, token: str | None = None, remote: str = "origin") -> None:
            _ = token, remote
            if not url.startswith("https://"):
                raise ValueError("invalid sync remote URL")

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"configure_sync_remote"})  # type: ignore[arg-type]

    result = await configure_sync_remote(make_ctx(deps), "bad-url")

    assert result == "Error: invalid sync remote URL"


async def test_sync_fetch_success() -> None:
    class SyncVault:
        def sync_fetch(self, remote: str = "origin") -> None:
            assert remote == "upstream"

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"sync_fetch"})  # type: ignore[arg-type]

    result = await sync_fetch(make_ctx(deps), remote="upstream")

    assert result == "Fetched from 'upstream'"


async def test_sync_push_success() -> None:
    class SyncVault:
        def sync_push(self, remote: str = "origin") -> None:
            assert remote == "origin"

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"sync_push"})  # type: ignore[arg-type]

    result = await sync_push(make_ctx(deps))

    assert result == "Pushed to 'origin'"


async def test_sync_now_clean_sync() -> None:
    class SyncVault:
        def sync(self, remote: str = "origin", conflict_prefix: str = "sync-conflict") -> SyncResult:
            _ = remote, conflict_prefix
            return SyncResult(ok=True)

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"sync_now"})  # type: ignore[arg-type]

    result = await sync_now(make_ctx(deps))

    assert result == "Sync completed successfully."


async def test_sync_now_conflict() -> None:
    class SyncVault:
        def sync(self, remote: str = "origin", conflict_prefix: str = "sync-conflict") -> SyncResult:
            _ = remote, conflict_prefix
            return SyncResult(ok=False, conflict=True, conflict_bookmark="sync-conflict/2026-04-26T17-30-00Z")

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"sync_now"})  # type: ignore[arg-type]

    result = await sync_now(make_ctx(deps))

    assert result == "Sync conflict detected. Conflict bookmark: sync-conflict/2026-04-26T17-30-00Z"


async def test_sync_now_failure() -> None:
    class SyncVault:
        def sync(self, remote: str = "origin", conflict_prefix: str = "sync-conflict") -> SyncResult:
            _ = remote, conflict_prefix
            return SyncResult(ok=False, conflict=False, error="auth failed")

    deps = VaultDeps(vault=SyncVault(), allowed_tool_names={"sync_now"})  # type: ignore[arg-type]

    result = await sync_now(make_ctx(deps))

    assert result == "Sync failed: auth failed"


@pytest.mark.parametrize(
    ("tool_fn", "args"),
    [
        (check_sync_readiness, ()),
        (ensure_sync_ready, ()),
        (configure_sync_remote, ("https://github.com/example/repo.git",)),
        (sync_fetch, ()),
        (sync_push, ()),
        (sync_now, ()),
        (sync_status, ()),
    ],
)
async def test_sync_tools_propagate_busy_error(tool_fn, args) -> None:
    class BusyVault:
        def check_sync_readiness(self) -> ReadinessCheck:
            raise BusyError("busy")

        def ensure_sync_ready(self) -> ReadinessCheck:
            raise BusyError("busy")

        def configure_sync_remote(self, url: str, token: str | None = None, remote: str = "origin") -> None:
            _ = url, token, remote
            raise BusyError("busy")

        def sync_fetch(self, remote: str = "origin") -> None:
            _ = remote
            raise BusyError("busy")

        def sync_push(self, remote: str = "origin") -> None:
            _ = remote
            raise BusyError("busy")

        def sync(self, remote: str = "origin", conflict_prefix: str = "sync-conflict") -> SyncResult:
            _ = remote, conflict_prefix
            raise BusyError("busy")

        def sync_status(self) -> dict[str, object]:
            raise BusyError("busy")

    deps = VaultDeps(
        vault=BusyVault(),  # type: ignore[arg-type]
        allowed_tool_names={
            "check_sync_readiness",
            "ensure_sync_ready",
            "configure_sync_remote",
            "sync_fetch",
            "sync_push",
            "sync_now",
            "sync_status",
        },
    )

    with pytest.raises(BusyError, match="busy"):
        await tool_fn(make_ctx(deps), *args)

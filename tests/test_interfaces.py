import pytest

from obsidian_agent.interfaces import resolve_interface
from obsidian_agent.scope import BlockScope, FileScope, HeadingScope, SelectionScope


def test_resolve_command_interface() -> None:
    profile = resolve_interface("command")

    assert profile.id == "command"
    assert "write_file" in profile.allowed_tool_names(None)
    assert "create_from_template" in profile.allowed_tool_names(None)


def test_resolve_forge_web_interface() -> None:
    profile = resolve_interface("forge_web")

    assert profile.id == "forge_web"


def test_resolve_interface_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unsupported interface_id"):
        resolve_interface("chat")


def test_forge_web_block_scope_locks_to_block_tool() -> None:
    profile = resolve_interface("forge_web")
    allowed = profile.allowed_tool_names(BlockScope(path="Projects/Alpha.md", block_id="abc"))

    assert "write_block" in allowed
    assert "write_file" not in allowed
    assert "write_heading" not in allowed
    assert "create_from_template" not in allowed


def test_forge_web_heading_scope_locks_to_heading_tools() -> None:
    profile = resolve_interface("forge_web")
    allowed = profile.allowed_tool_names(HeadingScope(path="Projects/Alpha.md", heading="## Plan"))

    assert "write_heading" in allowed
    assert "update_frontmatter" in allowed
    assert "write_file" not in allowed


def test_forge_web_selection_scope_excludes_write_file() -> None:
    profile = resolve_interface("forge_web")
    allowed = profile.allowed_tool_names(
        SelectionScope(path="Projects/Alpha.md", text="x", line_start=2, line_end=3)
    )

    assert "write_file" not in allowed
    assert "write_heading" in allowed
    assert "write_block" in allowed


def test_forge_web_file_scope_allows_file_writes() -> None:
    profile = resolve_interface("forge_web")
    allowed = profile.allowed_tool_names(FileScope(path="Projects/Alpha.md"))

    assert "write_file" in allowed
    assert "create_from_template" in allowed


def test_forge_web_prompt_suffix_contains_scope_and_intent() -> None:
    profile = resolve_interface("forge_web")

    suffix = profile.prompt_suffix(
        HeadingScope(path="Projects/Alpha.md", heading="## Plan"),
        "summarize",
    )

    assert "Forge web interface mode" in suffix
    assert "Scope kind: heading" in suffix
    assert "Intent mode: summarize" in suffix

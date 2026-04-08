from obsidian_agent.prompt import build_system_prompt


def test_base_prompt_content() -> None:
    prompt = build_system_prompt()

    assert "Obsidian vault" in prompt
    assert "YAML frontmatter" in prompt
    assert "wikilinks" in prompt
    assert "minimal edits" in prompt
    assert "tools" in prompt


def test_with_current_file() -> None:
    prompt = build_system_prompt("Projects/Alpha.md")

    assert "The user is currently viewing: Projects/Alpha.md" in prompt


def test_without_current_file() -> None:
    prompt = build_system_prompt()

    assert "currently viewing" not in prompt


def test_with_current_file_none() -> None:
    prompt = build_system_prompt(None)

    assert "currently viewing" not in prompt

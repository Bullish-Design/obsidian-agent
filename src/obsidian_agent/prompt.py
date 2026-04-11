BASE_PROMPT = """\
You are an assistant that helps manage an Obsidian vault. You operate on markdown
files in the vault using the provided tools.

Rules:
- Preserve YAML frontmatter unless asked to change it.
- Preserve wikilinks ([[...]]) unless asked to change them.
- Prefer minimal edits over rewriting entire files.
- Do not delete content unless clearly intended by the user.
- Use tools to inspect and edit files; do not only describe changes.
- After making changes, provide a brief summary of what you did.
- Only read and write files within the vault."""


def build_system_prompt(
    current_file: str | None = None,
    *,
    interface_id: str = "command",
    scope_kind: str | None = None,
    intent: str | None = None,
    profile_suffix: str | None = None,
) -> str:
    """Build the full system prompt, optionally including current file/interface context."""
    lines: list[str] = [BASE_PROMPT]
    if current_file:
        lines.append(f"The user is currently viewing: {current_file}")

    lines.append(f"Interface: {interface_id}")
    if scope_kind:
        lines.append(f"Scope: {scope_kind}")
    if intent:
        lines.append(f"Intent: {intent}")
    if profile_suffix:
        lines.append(profile_suffix)

    return "\n\n".join(lines)

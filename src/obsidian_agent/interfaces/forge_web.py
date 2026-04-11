from __future__ import annotations

from ..scope import BlockScope, EditScope, HeadingScope, SelectionScope

READ_ONLY = {
    "read_file",
    "list_files",
    "search_files",
    "get_frontmatter",
    "read_heading",
    "read_block",
}


class ForgeWebProfile:
    id = "forge_web"

    def allowed_tool_names(self, scope: EditScope | None) -> set[str]:
        if scope is None:
            return READ_ONLY | {"write_file", "write_heading", "write_block", "update_frontmatter", "create_from_template"}

        if isinstance(scope, BlockScope):
            return READ_ONLY | {"write_block"}

        if isinstance(scope, HeadingScope):
            return READ_ONLY | {"write_heading", "update_frontmatter"}

        if isinstance(scope, SelectionScope):
            return READ_ONLY | {"write_heading", "write_block"}

        return READ_ONLY | {"write_file", "write_heading", "write_block", "update_frontmatter", "create_from_template"}

    def prompt_suffix(self, scope: EditScope | None, intent: str | None) -> str:
        lines = ["You are operating in Forge web interface mode."]
        if intent:
            lines.append(f"Intent mode: {intent}")
        if scope is not None:
            lines.append(f"Scope kind: {scope.kind}")
            lines.append("Do not modify content outside the target scope.")
        return "\n".join(lines)

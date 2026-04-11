from __future__ import annotations

from ..scope import EditScope


class CommandProfile:
    id = "command"

    def allowed_tool_names(self, scope: EditScope | None) -> set[str]:
        _ = scope
        return {
            "read_file",
            "write_file",
            "delete_file",
            "list_files",
            "search_files",
            "get_frontmatter",
            "set_frontmatter",
            "update_frontmatter",
            "delete_frontmatter_field",
            "read_heading",
            "write_heading",
            "read_block",
            "write_block",
            "create_from_template",
        }

    def prompt_suffix(self, scope: EditScope | None, intent: str | None) -> str:
        _ = scope, intent
        return ""

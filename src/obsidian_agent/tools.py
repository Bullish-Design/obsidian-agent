from dataclasses import dataclass, field
import json
from typing import Any

from obsidian_ops import Vault
from obsidian_ops.errors import BusyError, VaultError
from pydantic_ai import RunContext


@dataclass
class VaultDeps:
    vault: Vault
    changed_files: set[str] = field(default_factory=set)
    current_file: str | None = None


WRITE_TOOLS = {
    "write_file",
    "delete_file",
    "set_frontmatter",
    "update_frontmatter",
    "delete_frontmatter_field",
    "write_heading",
    "write_block",
}


async def read_file(ctx: RunContext[VaultDeps], path: str) -> str:
    """Read the contents of a file in the vault. Path is relative to vault root."""
    try:
        return ctx.deps.vault.read_file(path)
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def write_file(ctx: RunContext[VaultDeps], path: str, content: str) -> str:
    """Write content to a file in the vault. Creates or overwrites. Path is relative to vault root."""
    try:
        ctx.deps.vault.write_file(path, content)
        ctx.deps.changed_files.add(path)
        return f"Successfully wrote {path}"
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def delete_file(ctx: RunContext[VaultDeps], path: str) -> str:
    """Delete a file from the vault. Path is relative to vault root."""
    try:
        ctx.deps.vault.delete_file(path)
        ctx.deps.changed_files.add(path)
        return f"Deleted {path}"
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def list_files(ctx: RunContext[VaultDeps], pattern: str) -> str:
    """List files in the vault matching a filename glob pattern, e.g. '*.md'."""
    try:
        files = ctx.deps.vault.list_files(pattern)
        if not files:
            return "No files found."
        return f"Found {len(files)} files:\n" + "\n".join(files)
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def search_files(ctx: RunContext[VaultDeps], query: str, glob: str = "*.md") -> str:
    """Search file contents for a text query. Returns matching files with context snippets."""
    try:
        results = ctx.deps.vault.search_files(query, glob=glob)
        if not results:
            return "No matches found."
        lines = [f"Found {len(results)} matching files:"]
        for result in results:
            lines.append(f"\n--- {result.path} ---\n{result.snippet}")
        return "\n".join(lines)
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def get_frontmatter(ctx: RunContext[VaultDeps], path: str) -> str:
    """Read the YAML frontmatter from a vault file. Returns JSON object or null."""
    try:
        frontmatter = ctx.deps.vault.get_frontmatter(path)
        if frontmatter is None:
            return "No frontmatter found."
        return json.dumps(frontmatter, indent=2, default=str)
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def update_frontmatter(ctx: RunContext[VaultDeps], path: str, updates: dict[str, Any]) -> str:
    """Update specific fields in a file's YAML frontmatter. Only specified fields change."""
    try:
        ctx.deps.vault.update_frontmatter(path, updates)
        ctx.deps.changed_files.add(path)
        return f"Updated frontmatter for {path}"
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def set_frontmatter(ctx: RunContext[VaultDeps], path: str, data: dict[str, Any]) -> str:
    """Replace a file's entire YAML frontmatter with the provided object."""
    try:
        ctx.deps.vault.set_frontmatter(path, data)
        ctx.deps.changed_files.add(path)
        return f"Set frontmatter for {path}"
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def delete_frontmatter_field(ctx: RunContext[VaultDeps], path: str, field: str) -> str:
    """Delete a specific YAML frontmatter field from a file."""
    try:
        ctx.deps.vault.delete_frontmatter_field(path, field)
        ctx.deps.changed_files.add(path)
        return f"Deleted frontmatter field '{field}' from {path}"
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def read_heading(ctx: RunContext[VaultDeps], path: str, heading: str) -> str:
    """Read content under a heading. Heading includes '#' prefix, e.g. '## Summary'."""
    try:
        content = ctx.deps.vault.read_heading(path, heading)
        if content is None:
            return f"Heading '{heading}' not found in {path}"
        return content
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def write_heading(ctx: RunContext[VaultDeps], path: str, heading: str, content: str) -> str:
    """Replace content under a heading. If heading doesn't exist, it's appended."""
    try:
        ctx.deps.vault.write_heading(path, heading, content)
        ctx.deps.changed_files.add(path)
        return f"Updated heading '{heading}' in {path}"
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def read_block(ctx: RunContext[VaultDeps], path: str, block_id: str) -> str:
    """Read the content of a block identified by its ^block-id."""
    try:
        content = ctx.deps.vault.read_block(path, block_id)
        if content is None:
            return f"Block '{block_id}' not found in {path}"
        return content
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


async def write_block(ctx: RunContext[VaultDeps], path: str, block_id: str, content: str) -> str:
    """Replace the content of a block identified by its ^block-id."""
    try:
        ctx.deps.vault.write_block(path, block_id, content)
        ctx.deps.changed_files.add(path)
        return f"Updated block '{block_id}' in {path}"
    except BusyError:
        raise
    except (VaultError, FileNotFoundError) as exc:
        return f"Error: {exc}"


def register_tools(agent: Any) -> None:
    """Register all vault tools on a pydantic-ai Agent."""
    agent.tool(read_file)
    agent.tool(write_file)
    agent.tool(delete_file)
    agent.tool(list_files)
    agent.tool(search_files)
    agent.tool(get_frontmatter)
    agent.tool(set_frontmatter)
    agent.tool(update_frontmatter)
    agent.tool(delete_frontmatter_field)
    agent.tool(read_heading)
    agent.tool(write_heading)
    agent.tool(read_block)
    agent.tool(write_block)

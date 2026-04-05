"""Executable entrypoint for the Obsidian Agent service."""

import uvicorn

from obsidian_agent.config import get_agent_settings


def main() -> None:
    """Load settings and start the Uvicorn server."""
    settings = get_agent_settings()
    uvicorn.run(
        "obsidian_agent.app:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

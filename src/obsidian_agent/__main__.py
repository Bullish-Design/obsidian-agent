import uvicorn

from .config import AgentConfig


def main() -> None:
    config = AgentConfig()
    uvicorn.run(
        "obsidian_agent.app:app",
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

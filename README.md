# Obsidian Agent

A standalone Python service that exposes `/api/apply`, `/api/undo`, and `/api/health` endpoints for Obsidian vault operations.

## Quick Start

```bash
uv sync --extra dev
AGENT_VAULT_DIR=/path/to/vault python -m obsidian_agent
```

## Endpoints

- `GET /api/health` - Health check
- `POST /api/apply` - Apply an instruction to a vault page
- `POST /api/undo` - Undo the last change

## Development

```bash
devenv shell -- uv sync --extra dev
devenv shell -- pytest tests/ -q
devenv shell -- ruff check src tests
```

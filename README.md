# obsidian-agent

`obsidian-agent` is a backend service for LLM-driven vault edits over a stable HTTP API.

## Ownership Boundary

`obsidian-agent` owns:
- request handling (`/api/apply`, `/api/undo`, `/api/health`)
- agent orchestration and prompt construction
- tool dispatch and response shaping

`obsidian-agent` does not own:
- raw filesystem mutation logic
- raw `jj` subprocess lifecycle management
- URL-to-file resolution for caller web routes

All vault and VCS mechanics must remain in `obsidian-ops` (`Vault` and its API surface).

## API Summary

- `POST /api/apply`
  - input: `instruction` (required semantic field), `current_file` (optional vault-relative path)
  - output: `OperationResult` with `ok`, `updated`, `summary`, `changed_files`, `error`, `warning`
- `POST /api/undo`
  - output: `OperationResult`
- `GET /api/health`
  - output: `{ "ok": true, "status": "healthy" }`

## Runtime Environment

- `AGENT_VAULT_DIR` (required)
- `AGENT_LLM_MODEL` (default: `anthropic:claude-sonnet-4-20250514`)
- `AGENT_LLM_BASE_URL` (optional OpenAI-compatible base URL)
- `AGENT_LLM_MAX_TOKENS` (default: `4096`)
- `AGENT_MAX_ITERATIONS` (default: `20`)
- `AGENT_OPERATION_TIMEOUT` (default: `120`)
- `AGENT_JJ_BIN` (default: `jj`)
- `AGENT_JJ_TIMEOUT` (default: `120`)
- `AGENT_HOST` (default: `127.0.0.1`)
- `AGENT_PORT` (default: `8081`)

## Local Development

Dependency strategy:
- normal package dependency: `obsidian-ops` (no git-pinned direct reference in this repo)
- active multi-repo development: `uv` workspace source override in `pyproject.toml`:
  - `[tool.uv.sources] obsidian-ops = { path = "../obsidian-ops", editable = true }`

Expected local layout:
- `/home/andrew/Documents/Projects/obsidian-agent`
- `/home/andrew/Documents/Projects/obsidian-ops`

Setup flow:

```bash
devenv shell -- uv sync --extra dev
```

Validation flow:

```bash
devenv shell -- pytest -q
```

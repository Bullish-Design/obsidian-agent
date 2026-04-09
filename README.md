# obsidian-agent

`obsidian-agent` is a backend service for LLM-driven vault edits over a stable HTTP API.

Python runtime floor is intentionally `>=3.13` for this service; `obsidian-ops` remains compatible with `>=3.12`.

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
  - invalid `current_file` payloads fail deterministically with request validation errors (HTTP 422)
- `POST /api/undo`
  - output: `OperationResult`
- `GET /api/health`
  - output: `{ "ok": true, "status": "healthy" }`

## `current_file` Contract

- `current_file` is optional.
- If provided, it must be a non-empty vault-relative path using `/` separators.
- URL values and traversal forms (for example `..`) are rejected.
- `current_url_path` is not accepted by this service.
- URL-to-file resolution is the caller's responsibility before calling `obsidian-agent`.

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
- this repo pins `obsidian-ops` using a git source in `pyproject.toml` for reproducible installs

Setup flow:

```bash
devenv shell -- uv sync --extra dev
```

Validation flow:

```bash
devenv shell -- pytest -q
```

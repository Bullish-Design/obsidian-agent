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

## Vault Sync Endpoints

`obsidian-agent` now exposes sync operations backed by `obsidian-ops` under `/api/vault/vcs/sync/*`:

- `GET /api/vault/vcs/sync/readiness`
  - output: `{ "ok": true, "status": "ready|migration_needed|error", "detail": "..." }`
- `POST /api/vault/vcs/sync/ensure`
  - output: same shape as readiness; attempts safe sync initialization
- `PUT /api/vault/vcs/sync/remote`
  - input: `{ "url": "...", "token": "...optional...", "remote": "origin" }`
  - output: `{ "ok": true, "detail": null }`
- `POST /api/vault/vcs/sync/fetch`
  - input: `{ "remote": "origin" }`
  - output: `{ "ok": true, "detail": null }`
- `POST /api/vault/vcs/sync/push`
  - input: `{ "remote": "origin" }`
  - output: `{ "ok": true, "detail": null }`
- `POST /api/vault/vcs/sync`
  - input: `{ "remote": "origin", "conflict_prefix": "sync-conflict" }`
  - output: `{ "ok": true, "sync_ok": true|false, "conflict": bool, "conflict_bookmark": "...", "error": "..." }`
- `GET /api/vault/vcs/sync/status`
  - output: `{ "ok": true, "status": { ...opaque sync state from obsidian-ops... } }`

Conflict outcomes are modeled in the response body (`sync_ok=false`, `conflict=true`) and returned as HTTP 200.

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
- `AGENT_SYNC_AFTER_COMMIT` (default: `false`)
- `AGENT_SYNC_REMOTE` (default: `origin`)
- `AGENT_HOST` (default: `127.0.0.1`)
- `AGENT_PORT` (default: `8081`)

## Sync Behavior Notes

- `/api/apply` remains commit-only by default.
- Automatic post-commit sync is opt-in via `AGENT_SYNC_AFTER_COMMIT=true`.
- Sync token values provided through sync remote configuration are handled by `obsidian-ops`, which stores credentials in vault-local `.forge/git-credential.sh`.
- The agent request logger does not log request bodies, so sync tokens are not emitted in structured request logs.

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

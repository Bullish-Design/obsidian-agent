# Obsidian Agent Update Analysis For `obsidian-ops` v0.7.x

## Scope

This analysis maps the delta from the currently pinned `obsidian-ops` in this repo (`v0.5.0`) to the latest shipped sync functionality (`v0.7.0`/`v0.7.1`), and defines what `obsidian-agent` should change to expose the new capability safely.

## What Changed In `obsidian-ops`

From `obsidian-ops` `CHANGELOG.md` (`0.7.0 - 2026-04-26`) and code:

- New `Vault` sync readiness APIs:
  - `check_sync_readiness() -> ReadinessCheck`
  - `ensure_sync_ready() -> ReadinessCheck`
- New remote setup API:
  - `configure_sync_remote(url, token=None, remote="origin")`
- New sync operation APIs:
  - `sync_fetch(remote="origin")`
  - `sync_push(remote="origin")`
  - `sync(remote="origin", conflict_prefix="sync-conflict") -> SyncResult`
  - `sync_status() -> dict[str, Any]`
- New VCS models exported:
  - `VCSReadiness`, `ReadinessCheck`, `SyncResult`
- Sync metadata persistence in vault:
  - `.forge/sync-state.json`
  - optional `.forge/git-credential.sh` askpass helper
- New optional server routes (inside `obsidian-ops` server, not yet in this repo):
  - `/vcs/sync/readiness`, `/vcs/sync/ensure`, `/vcs/sync/remote`,
    `/vcs/sync/fetch`, `/vcs/sync/push`, `/vcs/sync`, `/vcs/sync/status`

Important compatibility note: existing VCS flows (`commit`, `undo_last_change`, `vcs_status`) remain intact.

## Current `obsidian-agent` State

### Dependency Pin

- `pyproject.toml` pins `obsidian-ops` git source to `tag = "v0.5.0"`.
- `uv.lock` is also locked to that same tag/SHA.

### Exposed API Surface

Current app routes include:

- agent endpoints (`/api/apply`, `/api/undo`)
- vault endpoints for file read/write/undo + structure/anchors/templates

No sync routes exist in `obsidian-agent` today.

### Tool Surface

LLM tool registration (`src/obsidian_agent/tools.py`) includes file/frontmatter/content/template tools only. No sync tools are available to the model.

### Boundary Pattern Already In Use

`vault_routes.py` already uses feature-detection (`getattr(..., None)`) with `501` fallbacks for methods not available in older `obsidian-ops` installs. That pattern should be reused for sync to keep graceful compatibility.

## Additional Functionality We Can Now Provide

With `obsidian-ops` updated, `obsidian-agent` can provide:

1. Deterministic sync readiness diagnostics.
2. Safe readiness auto-initialize when migration is safe.
3. Remote configuration (including token-auth setup).
4. On-demand fetch/push/full-sync operations.
5. Conflict-aware sync outcomes with bookmark reporting.
6. Sync state inspection for UI/status polling.

## Required Updates In `obsidian-agent`

## 1. Upgrade Dependency

Files:

- `pyproject.toml`
- `uv.lock`
- `README.md` (dependency pin reference text)

Changes:

- Bump `obsidian-ops` source tag from `v0.5.0` to latest stable (`v0.7.1` currently).
- Refresh lockfile (`uv lock` / `uv sync`) so CI and local dev resolve the new version.

## 2. Add API Models For Sync

File:

- `src/obsidian_agent/models.py`

Add request/response models mirroring agent conventions:

- `VaultSyncReadinessResponse` (`ok`, `status`, `detail`)
- `VaultSyncRemoteRequest` (`url`, `token`, `remote="origin"`)
- `VaultSyncRemoteOpRequest` (`remote="origin"`)
- `VaultSyncRequest` (`remote="origin"`, `conflict_prefix="sync-conflict"`)
- `VaultSyncResultResponse` (`ok`, `sync_ok`, `conflict`, `conflict_bookmark`, `error`)
- `VaultSyncStatusResponse` (`ok`, `status` object or normalized fields)

Recommendation:

- Keep `extra="forbid"` on request payloads, consistent with existing models.

## 3. Add Vault Sync Routes

File:

- `src/obsidian_agent/routes/vault_routes.py`

Add endpoints under `/api/vault/vcs/sync/*`:

- `GET /api/vault/vcs/sync/readiness`
- `POST /api/vault/vcs/sync/ensure`
- `PUT /api/vault/vcs/sync/remote`
- `POST /api/vault/vcs/sync/fetch`
- `POST /api/vault/vcs/sync/push`
- `POST /api/vault/vcs/sync`
- `GET /api/vault/vcs/sync/status`

Route behavior guidance:

- Reuse `_enforce_rate_limit` for write/mutation routes (`ensure`, `remote`, `fetch`, `push`, `sync`).
- Use `getattr(vault, "...", None)` gate:
  - return `501` if unavailable (same style as `list_structure`, `ensure_block_id`, templates).
- Error mapping:
  - `VaultBusyError` -> `409`
  - readiness/setup/validation `VCSError` -> `424` (or `400` for bad input if explicitly validated pre-call)
  - unexpected -> `500`
- For `/sync`, return both transport success and sync outcome:
  - HTTP `200` with body carrying `sync_ok`/`conflict`/`error`
  - avoid using HTTP errors for expected conflict outcomes, since `SyncResult` already models them

## 4. Add LLM Tooling For Sync (Optional But High Value)

File:

- `src/obsidian_agent/tools.py`

Potential tools:

- `check_sync_readiness`
- `ensure_sync_ready`
- `configure_sync_remote`
- `sync_fetch`
- `sync_push`
- `sync_now`
- `sync_status`

Recommended policy:

- Add these tools but restrict to trusted interfaces via existing `allowed_tool_names` gating.
- Do not auto-run sync inside general content-edit prompts by default.
- Prefer explicit user intent phrases in prompt profile or API scope before tool availability.

If not exposing to model yet, still add HTTP routes first so caller/UI can orchestrate sync deterministically.

## 5. Agent Orchestration Behavior (Decide Explicitly)

File (if adopted):

- `src/obsidian_agent/agent.py`

Decision point:

- Keep current behavior (commit only, no automatic remote sync), or
- Add optional post-commit sync phase controlled by config flag(s).

Recommendation:

- Keep default as commit-only for now.
- Add opt-in post-commit sync later behind config:
  - `AGENT_SYNC_AFTER_COMMIT=false` default
  - `AGENT_SYNC_REMOTE=origin`
- Reason: avoids introducing remote/network side effects into existing deterministic apply flow without explicit rollout.

## 6. Tests To Add

Files:

- `tests/test_vault_routes.py`
- `tests/test_tools.py` (if sync tools added)
- `tests/test_agent.py` (only if orchestration behavior changes)

Add route tests for:

- availability gating (`501`) when sync methods missing
- readiness response mapping
- ensure response mapping
- remote config success/failure
- fetch/push success + busy/error mapping
- sync outcomes:
  - success (`sync_ok=true`)
  - conflict (`sync_ok=false`, `conflict=true`, bookmark present)
  - non-conflict failure (`sync_ok=false`, `error` present)
- sync status passthrough/shape validation

## 7. Documentation Updates

Files:

- `README.md`

Add:

- new vault sync endpoint documentation
- explicit note that sync is separate from `/api/apply` commit flow unless configured
- operational note for token handling (stored in vault-local `.forge/git-credential.sh` by `obsidian-ops`)

## Risks And Mitigations

- Risk: breaking existing installs still on older `obsidian-ops`.
  - Mitigation: keep `getattr`-based 501 gating.
- Risk: accidental remote side effects if sync auto-enabled.
  - Mitigation: default off, explicit opt-in config.
- Risk: leaking token via logs.
  - Mitigation: never log token payload; sanitize request logging if needed.
- Risk: ambiguous HTTP semantics for sync conflict.
  - Mitigation: model conflict in response body, keep HTTP 200 for expected conflict state.

## Recommended Rollout Plan

1. Dependency bump + lockfile refresh.
2. Add sync API models + `/api/vault/vcs/sync/*` routes + tests.
3. Update README API docs.
4. Optionally add sync tools for LLM access (gated by interface scope).
5. Optionally add opt-in post-commit sync config and orchestration.

## Minimal Viable Update (if we want smallest safe delta)

- Bump to `obsidian-ops v0.7.1`.
- Add only vault sync HTTP routes and tests.
- Do not modify agent run/commit flow yet.
- Do not expose sync tools to LLM yet.

This immediately enables all new dependency functionality through deterministic API calls while minimizing behavioral risk.

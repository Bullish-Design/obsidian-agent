# Obsidian Agent — `obsidian-ops` v0.7.1 Integration Guide

This document is a step-by-step implementation guide for upgrading `obsidian-agent` to consume the sync APIs introduced in `obsidian-ops` v0.7.0/v0.7.1. Each step is self-contained and should be validated before moving to the next.

---

## Step 1: Bump Dependency & Refresh Lockfile

### Goal

Update the `obsidian-ops` pin from `v0.5.0` → `v0.7.1` and confirm the project resolves cleanly.

### Files Changed

- `pyproject.toml`
- `uv.lock`

### Changes

**`pyproject.toml`** — Update the source tag:

```toml
# Before
obsidian-ops = { git = "https://github.com/Bullish-Design/obsidian-ops.git", tag = "v0.5.0" }

# After
obsidian-ops = { git = "https://github.com/Bullish-Design/obsidian-ops.git", tag = "v0.7.1" }
```

### Commands

```bash
uv lock
uv sync --all-extras
```

### Validation

```bash
uv run python -c "from obsidian_ops import Vault, ReadinessCheck, SyncResult, VCSReadiness; print('OK')"
uv run pytest tests/ -x
```

All existing tests must pass unchanged — v0.7.1 is backwards-compatible with the v0.5.0 API surface.

---

## Step 2: Add Sync API Models

### Goal

Add Pydantic request/response models for the 7 new sync endpoints, following established conventions.

### Files Changed

- `src/obsidian_agent/models.py`

### Models To Add

```python
# --- Sync Request Models ---

class SyncRemoteRequest(BaseModel):
    """Configure a sync remote (URL + optional token)."""
    model_config = ConfigDict(extra="forbid")

    url: str
    token: str | None = None
    remote: str = "origin"

class SyncRemoteOpRequest(BaseModel):
    """Request body for fetch/push (remote selection only)."""
    model_config = ConfigDict(extra="forbid")

    remote: str = "origin"

class SyncRequest(BaseModel):
    """Request body for full sync cycle."""
    model_config = ConfigDict(extra="forbid")

    remote: str = "origin"
    conflict_prefix: str = "sync-conflict"


# --- Sync Response Models ---

class SyncReadinessResponse(BaseModel):
    ok: bool = True
    status: str          # "ready" | "migration_needed" | "error"
    detail: str | None = None

class SyncResultResponse(BaseModel):
    ok: bool = True
    sync_ok: bool
    conflict: bool = False
    conflict_bookmark: str | None = None
    error: str | None = None

class SyncStatusResponse(BaseModel):
    ok: bool = True
    status: dict
```

### Design Notes

- **`extra="forbid"`** on all request bodies — consistent with `ApplyRequest`, `VaultFileWriteRequest`, `CreatePageRequest`.
- **Response models carry `ok: bool = True`** — consistent with all existing response models.
- **`SyncReadinessResponse.status` is a plain `str`**, not the `VCSReadiness` enum. This keeps the HTTP contract decoupled from the `obsidian-ops` internal enum. The route handler maps `readiness.status.value` → string.
- **`SyncResultResponse` separates `ok` (HTTP success) from `sync_ok` (sync outcome)**. A conflict is a valid 200 response — not an HTTP error. This avoids ambiguous HTTP semantics for expected sync states.
- **`SyncStatusResponse.status` is an opaque `dict`** — passthrough of `vault.sync_status()`. Avoid over-specifying the shape since it's owned by `obsidian-ops` and may evolve.
- **No `SyncEnsureRequest`** — the ensure endpoint takes no parameters (it's an idempotent "make it ready" action).
- **No response model for fetch/push/remote-config** — these return the existing `StatusResponse`-equivalent pattern. Use a simple model:

```python
class SyncOpResponse(BaseModel):
    ok: bool = True
    detail: str | None = None
```

This single response model covers: `configure_sync_remote`, `sync_fetch`, `sync_push`, and `ensure_sync_ready` success cases. The `SyncReadinessResponse` covers readiness/ensure when returning readiness state.

**Revised model set (6 total):**

| Model | Used By |
|---|---|
| `SyncRemoteRequest` | `PUT .../remote` |
| `SyncRemoteOpRequest` | `POST .../fetch`, `POST .../push` |
| `SyncRequest` | `POST .../sync` |
| `SyncReadinessResponse` | `GET .../readiness`, `POST .../ensure` |
| `SyncResultResponse` | `POST .../sync` |
| `SyncStatusResponse` | `GET .../status` |

Plus `SyncOpResponse` for the simple success/fail ack on `remote`, `fetch`, `push`.

### Validation

```bash
uv run python -c "from obsidian_agent.models import SyncRemoteRequest, SyncResultResponse; print('OK')"
uv run pytest tests/test_models.py -x
```

Existing model tests still pass; no behavioral change.

---

## Step 3: Add Vault Sync Routes

### Goal

Add 7 HTTP endpoints under `/api/vault/vcs/sync/*` to expose all `obsidian-ops` sync functionality.

### Files Changed

- `src/obsidian_agent/routes/vault_routes.py`

### Route Map

| Method | Path | Handler | Rate Limited | Request Body | Response Model |
|--------|------|---------|:---:|---|---|
| `GET` | `/api/vault/vcs/sync/readiness` | `get_sync_readiness` | No | — | `SyncReadinessResponse` |
| `POST` | `/api/vault/vcs/sync/ensure` | `ensure_sync_ready` | Yes | — | `SyncReadinessResponse` |
| `PUT` | `/api/vault/vcs/sync/remote` | `configure_sync_remote` | Yes | `SyncRemoteRequest` | `SyncOpResponse` |
| `POST` | `/api/vault/vcs/sync/fetch` | `sync_fetch` | Yes | `SyncRemoteOpRequest` | `SyncOpResponse` |
| `POST` | `/api/vault/vcs/sync/push` | `sync_push` | Yes | `SyncRemoteOpRequest` | `SyncOpResponse` |
| `POST` | `/api/vault/vcs/sync` | `sync` | Yes | `SyncRequest` | `SyncResultResponse` |
| `GET` | `/api/vault/vcs/sync/status` | `get_sync_status` | No | — | `SyncStatusResponse` |

### Implementation Pattern

All sync routes follow the same error-mapping pattern already used in `vault_routes.py`:

```python
# Error mapping for sync routes:
#   VaultBusyError → 409
#   VCSError       → 424  (VCS precondition/operation failure)
#   ValueError     → 400  (bad input validated pre-call)
#   Exception      → 500  (unexpected)
```

### Route Implementations

```python
# --- Readiness ---

@vault_router.get("/vcs/sync/readiness", response_model=SyncReadinessResponse)
async def get_sync_readiness(request: Request) -> SyncReadinessResponse:
    vault: Vault = request.app.state.vault
    try:
        result = vault.check_sync_readiness()
        return SyncReadinessResponse(status=result.status.value, detail=result.detail)
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@vault_router.post("/vcs/sync/ensure", response_model=SyncReadinessResponse)
async def ensure_sync_ready(request: Request) -> SyncReadinessResponse:
    _enforce_rate_limit(request, "vault.sync_ensure")
    vault: Vault = request.app.state.vault
    try:
        result = vault.ensure_sync_ready()
        return SyncReadinessResponse(status=result.status.value, detail=result.detail)
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VCSError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc


# --- Remote Configuration ---

@vault_router.put("/vcs/sync/remote", response_model=SyncOpResponse)
async def configure_sync_remote(request: Request, payload: SyncRemoteRequest) -> SyncOpResponse:
    _enforce_rate_limit(request, "vault.sync_remote")
    vault: Vault = request.app.state.vault
    try:
        vault.configure_sync_remote(payload.url, token=payload.token, remote=payload.remote)
        return SyncOpResponse()
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VCSError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Fetch / Push ---

@vault_router.post("/vcs/sync/fetch", response_model=SyncOpResponse)
async def sync_fetch(request: Request, payload: SyncRemoteOpRequest) -> SyncOpResponse:
    _enforce_rate_limit(request, "vault.sync_fetch")
    vault: Vault = request.app.state.vault
    try:
        vault.sync_fetch(remote=payload.remote)
        return SyncOpResponse()
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VCSError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc


@vault_router.post("/vcs/sync/push", response_model=SyncOpResponse)
async def sync_push(request: Request, payload: SyncRemoteOpRequest) -> SyncOpResponse:
    _enforce_rate_limit(request, "vault.sync_push")
    vault: Vault = request.app.state.vault
    try:
        vault.sync_push(remote=payload.remote)
        return SyncOpResponse()
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VCSError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc


# --- Full Sync ---

@vault_router.post("/vcs/sync", response_model=SyncResultResponse)
async def sync(request: Request, payload: SyncRequest) -> SyncResultResponse:
    _enforce_rate_limit(request, "vault.sync")
    vault: Vault = request.app.state.vault
    try:
        result = vault.sync(remote=payload.remote, conflict_prefix=payload.conflict_prefix)
        return SyncResultResponse(
            sync_ok=result.ok,
            conflict=result.conflict,
            conflict_bookmark=result.conflict_bookmark,
            error=result.error,
        )
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VCSError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc


# --- Status ---

@vault_router.get("/vcs/sync/status", response_model=SyncStatusResponse)
async def get_sync_status(request: Request) -> SyncStatusResponse:
    vault: Vault = request.app.state.vault
    try:
        status = vault.sync_status()
        return SyncStatusResponse(status=status)
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
```

### Design Decisions

1. **No `getattr` 501 gates.** We're pinning v0.7.1 — the sync methods are guaranteed to exist. Simpler code.
2. **Rate limiting on all mutation routes** (`ensure`, `remote`, `fetch`, `push`, `sync`). Read-only routes (`readiness`, `status`) are not rate limited — consistent with `get_file` and `get_file_structure`.
3. **`SyncResultResponse` maps `result.ok` → `sync_ok`** to avoid ambiguity with the envelope `ok` field (which means "HTTP handler succeeded").
4. **Conflict is a 200, not a 4xx.** The `SyncResult` already models conflict state. HTTP errors are for transport/precondition failures only.
5. **Token is never logged.** The request logging middleware only logs method/path/status/duration — no body inspection. The `SyncRemoteRequest.token` field flows directly to `vault.configure_sync_remote()` which stores it in `.forge/git-credential.sh`.

### Import Additions

Add to the import block at top of `vault_routes.py`:

```python
from ..models import (
    # ... existing imports ...
    SyncRemoteRequest,
    SyncRemoteOpRequest,
    SyncRequest,
    SyncReadinessResponse,
    SyncResultResponse,
    SyncStatusResponse,
    SyncOpResponse,
)
```

### Validation

```bash
uv run pytest tests/test_vault_routes.py -x
```

Existing route tests still pass. New routes tested in Step 4.

---

## Step 4: Add Sync Route Tests

### Goal

Test all 7 sync endpoints with happy path, error mapping, and edge cases.

### Files Changed

- `tests/test_vault_routes.py`

### Test Cases

#### 4a. Readiness

```python
def test_sync_readiness_ready(client, monkeypatch):
    """GET /vcs/sync/readiness returns status when vault is sync-ready."""

def test_sync_readiness_migration_needed(client, monkeypatch):
    """GET /vcs/sync/readiness returns migration_needed with detail."""

def test_sync_readiness_busy_409(client, monkeypatch):
    """GET /vcs/sync/readiness returns 409 when vault is busy."""
```

#### 4b. Ensure

```python
def test_sync_ensure_ready(client, monkeypatch):
    """POST /vcs/sync/ensure returns ready after successful initialization."""

def test_sync_ensure_vcs_error_424(client, monkeypatch):
    """POST /vcs/sync/ensure returns 424 when VCS operation fails."""

def test_sync_ensure_busy_409(client, monkeypatch):
    """POST /vcs/sync/ensure returns 409 when vault is busy."""
```

#### 4c. Remote Configuration

```python
def test_sync_remote_configure_success(client, monkeypatch):
    """PUT /vcs/sync/remote configures remote and returns ok."""

def test_sync_remote_configure_with_token(client, monkeypatch):
    """PUT /vcs/sync/remote passes token to vault method."""

def test_sync_remote_invalid_url_400(client, monkeypatch):
    """PUT /vcs/sync/remote returns 400 for invalid remote URL."""

def test_sync_remote_vcs_error_424(client, monkeypatch):
    """PUT /vcs/sync/remote returns 424 when remote setup fails."""
```

#### 4d. Fetch / Push

```python
def test_sync_fetch_success(client, monkeypatch):
    """POST /vcs/sync/fetch succeeds with default remote."""

def test_sync_fetch_custom_remote(client, monkeypatch):
    """POST /vcs/sync/fetch passes custom remote name."""

def test_sync_fetch_vcs_error_424(client, monkeypatch):
    """POST /vcs/sync/fetch returns 424 when fetch fails."""

def test_sync_push_success(client, monkeypatch):
    """POST /vcs/sync/push succeeds."""

def test_sync_push_vcs_error_424(client, monkeypatch):
    """POST /vcs/sync/push returns 424 when push fails."""
```

#### 4e. Full Sync

```python
def test_sync_success(client, monkeypatch):
    """POST /vcs/sync returns sync_ok=true on clean sync."""

def test_sync_conflict(client, monkeypatch):
    """POST /vcs/sync returns sync_ok=false, conflict=true, bookmark present."""

def test_sync_non_conflict_failure(client, monkeypatch):
    """POST /vcs/sync returns sync_ok=false, error present, conflict=false."""

def test_sync_vcs_error_424(client, monkeypatch):
    """POST /vcs/sync returns 424 on VCS precondition failure."""

def test_sync_busy_409(client, monkeypatch):
    """POST /vcs/sync returns 409 when vault is busy."""
```

#### 4f. Status

```python
def test_sync_status_success(client, monkeypatch):
    """GET /vcs/sync/status returns status dict."""

def test_sync_status_busy_409(client, monkeypatch):
    """GET /vcs/sync/status returns 409 when vault is busy."""
```

#### 4g. Rate Limiting

```python
def test_sync_mutation_routes_respect_rate_limit(client):
    """Mutation sync routes return 429 when rate limit exceeded."""
```

### Mock Pattern

Follow the existing `monkeypatch.setattr(client.app.state.vault, "method_name", mock_fn)` pattern already used throughout `test_vault_routes.py`. Example for sync:

```python
def test_sync_readiness_ready(client, monkeypatch):
    from obsidian_ops import ReadinessCheck, VCSReadiness

    def check_sync_readiness():
        return ReadinessCheck(status=VCSReadiness.READY, detail=None)

    monkeypatch.setattr(client.app.state.vault, "check_sync_readiness", check_sync_readiness)

    response = client.get("/api/vault/vcs/sync/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["detail"] is None
```

### Validation

```bash
uv run pytest tests/test_vault_routes.py -v
```

All tests pass — both existing and new.

---

## Step 5: Add LLM Sync Tools

### Goal

Register sync tools on the pydantic-ai Agent so the LLM can invoke sync operations when permitted by the interface profile.

### Files Changed

- `src/obsidian_agent/tools.py`

### Tools To Add (7 total)

| Tool Name | Vault Method | Write Tool? | Description |
|---|---|:---:|---|
| `check_sync_readiness` | `vault.check_sync_readiness()` | No | Check if vault is ready for sync |
| `ensure_sync_ready` | `vault.ensure_sync_ready()` | Yes | Initialize vault for sync if needed |
| `configure_sync_remote` | `vault.configure_sync_remote(...)` | Yes | Set up remote URL and optional token |
| `sync_fetch` | `vault.sync_fetch(...)` | Yes | Fetch from remote |
| `sync_push` | `vault.sync_push(...)` | Yes | Push to remote |
| `sync_now` | `vault.sync(...)` | Yes | Full sync cycle (fetch + rebase + push) |
| `sync_status` | `vault.sync_status()` | No | Get current sync state |

### Implementation

```python
# --- Sync Tools (read-only) ---

async def check_sync_readiness(ctx: RunContext[VaultDeps]) -> str:
    """Check whether the vault is ready for sync operations."""
    if not _tool_allowed(ctx, "check_sync_readiness"):
        return "Error: check_sync_readiness is not allowed in this interface/scope"
    try:
        result = ctx.deps.vault.check_sync_readiness()
        return f"Sync readiness: {result.status.value}" + (f" ({result.detail})" if result.detail else "")
    except BusyError:
        raise
    except VaultError as exc:
        return f"Error: {exc}"


async def ensure_sync_ready(ctx: RunContext[VaultDeps]) -> str:
    """Initialize the vault for sync if not already ready. Safe to call repeatedly."""
    if not _tool_allowed(ctx, "ensure_sync_ready"):
        return "Error: ensure_sync_ready is not allowed in this interface/scope"
    try:
        result = ctx.deps.vault.ensure_sync_ready()
        return f"Sync readiness: {result.status.value}" + (f" ({result.detail})" if result.detail else "")
    except BusyError:
        raise
    except VaultError as exc:
        return f"Error: {exc}"


async def configure_sync_remote(
    ctx: RunContext[VaultDeps], url: str, token: str | None = None, remote: str = "origin"
) -> str:
    """Configure a git remote for sync. Optionally provide an auth token."""
    if not _tool_allowed(ctx, "configure_sync_remote"):
        return "Error: configure_sync_remote is not allowed in this interface/scope"
    try:
        ctx.deps.vault.configure_sync_remote(url, token=token, remote=remote)
        return f"Remote '{remote}' configured for {url}"
    except BusyError:
        raise
    except (VaultError, ValueError) as exc:
        return f"Error: {exc}"


async def sync_fetch(ctx: RunContext[VaultDeps], remote: str = "origin") -> str:
    """Fetch changes from the sync remote."""
    if not _tool_allowed(ctx, "sync_fetch"):
        return "Error: sync_fetch is not allowed in this interface/scope"
    try:
        ctx.deps.vault.sync_fetch(remote=remote)
        return f"Fetched from '{remote}'"
    except BusyError:
        raise
    except VaultError as exc:
        return f"Error: {exc}"


async def sync_push(ctx: RunContext[VaultDeps], remote: str = "origin") -> str:
    """Push local changes to the sync remote."""
    if not _tool_allowed(ctx, "sync_push"):
        return "Error: sync_push is not allowed in this interface/scope"
    try:
        ctx.deps.vault.sync_push(remote=remote)
        return f"Pushed to '{remote}'"
    except BusyError:
        raise
    except VaultError as exc:
        return f"Error: {exc}"


async def sync_now(
    ctx: RunContext[VaultDeps], remote: str = "origin", conflict_prefix: str = "sync-conflict"
) -> str:
    """Run a full sync cycle: fetch, rebase, push. Reports conflicts if any."""
    if not _tool_allowed(ctx, "sync_now"):
        return "Error: sync_now is not allowed in this interface/scope"
    try:
        result = ctx.deps.vault.sync(remote=remote, conflict_prefix=conflict_prefix)
        if result.ok:
            return "Sync completed successfully."
        if result.conflict:
            return f"Sync conflict detected. Conflict bookmark: {result.conflict_bookmark}"
        return f"Sync failed: {result.error}"
    except BusyError:
        raise
    except VaultError as exc:
        return f"Error: {exc}"


async def sync_status(ctx: RunContext[VaultDeps]) -> str:
    """Get the current sync state (last sync time, conflict status, etc.)."""
    if not _tool_allowed(ctx, "sync_status"):
        return "Error: sync_status is not allowed in this interface/scope"
    try:
        status = ctx.deps.vault.sync_status()
        return json.dumps(status, indent=2, default=str)
    except BusyError:
        raise
    except VaultError as exc:
        return f"Error: {exc}"
```

### Update `WRITE_TOOLS`

Add the sync mutation tools to the write tools set:

```python
WRITE_TOOLS = {
    "write_file",
    "delete_file",
    "set_frontmatter",
    "update_frontmatter",
    "delete_frontmatter_field",
    "write_heading",
    "write_block",
    # Sync mutation tools:
    "ensure_sync_ready",
    "configure_sync_remote",
    "sync_fetch",
    "sync_push",
    "sync_now",
}
```

### Update `register_tools`

```python
def register_tools(agent: Any) -> None:
    """Register all vault tools on a pydantic-ai Agent."""
    # ... existing 14 tools ...
    agent.tool(check_sync_readiness)
    agent.tool(ensure_sync_ready)
    agent.tool(configure_sync_remote)
    agent.tool(sync_fetch)
    agent.tool(sync_push)
    agent.tool(sync_now)
    agent.tool(sync_status)
```

### Design Notes

- **Sync tools return error strings, not exceptions** — consistent with all existing tools. This prevents sync failures from breaking the agent's reasoning loop.
- **`BusyError` is always re-raised** — same pattern as all existing tools.
- **`sync_now` (not `sync`)** — avoids shadowing the Python builtin and is more explicit about the action.
- **No path policy checks** — sync tools operate on the vault as a whole, not individual files. The `_tool_allowed` gate is sufficient.
- **`configure_sync_remote` accepts `token` as an LLM parameter.** This is intentional — the LLM may receive sync setup instructions from the user. The token flows to `obsidian-ops` which stores it securely in `.forge/git-credential.sh`. It is never logged by the agent.

### Validation

```bash
uv run pytest tests/test_tools.py -x
```

---

## Step 6: Update Interface Profiles

### Goal

Control which interfaces can access sync tools. The `command` profile gets full access; `forge_web` gets read-only sync status.

### Files Changed

- `src/obsidian_agent/interfaces/command.py`
- `src/obsidian_agent/interfaces/forge_web.py`

### Changes

**`command.py`** — Add all 7 sync tools to the allowed set:

```python
class CommandProfile:
    id = "command"

    def allowed_tool_names(self, scope: EditScope | None) -> set[str]:
        _ = scope
        return {
            # ... existing 14 tools ...
            "check_sync_readiness",
            "ensure_sync_ready",
            "configure_sync_remote",
            "sync_fetch",
            "sync_push",
            "sync_now",
            "sync_status",
        }
```

**`forge_web.py`** — Add read-only sync tools to the base `READ_ONLY` set:

```python
READ_ONLY = {
    "read_file",
    "list_files",
    "search_files",
    "get_frontmatter",
    "read_heading",
    "read_block",
    "check_sync_readiness",
    "sync_status",
}
```

### Design Notes

- **`forge_web` gets `check_sync_readiness` and `sync_status` only.** These are read-only diagnostic tools safe for any interface.
- **`forge_web` does NOT get mutation sync tools** (`ensure_sync_ready`, `configure_sync_remote`, `sync_fetch`, `sync_push`, `sync_now`). Sync mutations should only be triggered by explicit user intent via the `command` interface or deterministic HTTP calls.
- **No scope-dependent sync tool gating in `forge_web`.** Sync tools don't operate on file-level scopes, so the scope-based dispatch in `ForgeWebProfile.allowed_tool_names` doesn't need sync-specific branches.

### Validation

```bash
uv run pytest tests/ -x
```

---

## Step 7: Add LLM Sync Tool Tests

### Goal

Unit test the 7 new LLM tools for tool-allowed gating, happy paths, and error handling.

### Files Changed

- `tests/test_tools.py`

### Test Cases

#### 7a. Tool Allowed Gating

```python
def test_sync_tools_blocked_when_not_in_allowed_set():
    """All 7 sync tools return error string when not in allowed_tool_names."""
```

#### 7b. Read-Only Sync Tools

```python
def test_check_sync_readiness_returns_status():
    """check_sync_readiness returns formatted readiness status."""

def test_sync_status_returns_json():
    """sync_status returns JSON-formatted status dict."""
```

#### 7c. Mutation Sync Tools

```python
def test_ensure_sync_ready_success():
    """ensure_sync_ready calls vault method and returns status."""

def test_configure_sync_remote_success():
    """configure_sync_remote calls vault with url/token/remote."""

def test_configure_sync_remote_invalid_url():
    """configure_sync_remote returns error string for bad URL."""

def test_sync_fetch_success():
    """sync_fetch calls vault.sync_fetch and returns confirmation."""

def test_sync_push_success():
    """sync_push calls vault.sync_push and returns confirmation."""

def test_sync_now_clean_sync():
    """sync_now returns success message when sync completes cleanly."""

def test_sync_now_conflict():
    """sync_now returns conflict message with bookmark when conflict detected."""

def test_sync_now_failure():
    """sync_now returns error message when sync fails without conflict."""
```

#### 7d. BusyError Propagation

```python
def test_sync_tools_propagate_busy_error():
    """All sync tools re-raise BusyError (not caught as error string)."""
```

### Mock Pattern

Follow the existing `test_tools.py` pattern — create a mock `VaultDeps` with a mock `Vault` and call the tool functions directly:

```python
async def test_check_sync_readiness_returns_status():
    from obsidian_ops import ReadinessCheck, VCSReadiness

    vault = Mock()
    vault.check_sync_readiness.return_value = ReadinessCheck(status=VCSReadiness.READY)
    deps = VaultDeps(vault=vault, allowed_tool_names={"check_sync_readiness"})
    ctx = make_ctx(deps)  # helper to create RunContext

    result = await check_sync_readiness(ctx)
    assert "ready" in result
```

### Validation

```bash
uv run pytest tests/test_tools.py -v
```

---

## Step 8: Optional Post-Commit Sync Config

### Goal

Add an opt-in config flag that triggers a sync after every successful agent commit. Default: off.

### Files Changed

- `src/obsidian_agent/config.py`
- `src/obsidian_agent/agent.py`

### Config Addition

**`config.py`** — Add two new fields:

```python
class AgentConfig(BaseSettings):
    # ... existing fields ...
    sync_after_commit: bool = False
    sync_remote: str = "origin"
```

Environment variables: `AGENT_SYNC_AFTER_COMMIT`, `AGENT_SYNC_REMOTE`.

### Agent Changes

**`agent.py`** — In `_run_impl`, after the successful commit block:

```python
# After successful commit...
if changed_files:
    commit_message = self._normalize_commit_message(instruction)
    try:
        self.vault.commit(commit_message)
        logger.info(
            "agent.commit_success",
            extra={"changed_file_count": len(changed_files), "message_len": len(commit_message)},
        )
    except Exception as exc:
        warning = f"Commit failed: {exc}"
        logger.exception(
            "agent.commit_failed",
            extra={"changed_file_count": len(changed_files), "message_len": len(commit_message)},
        )

    # Optional post-commit sync
    if warning is None and self.config.sync_after_commit:
        try:
            sync_result = self.vault.sync(remote=self.config.sync_remote)
            if sync_result.ok:
                logger.info("agent.post_commit_sync_success", extra={"remote": self.config.sync_remote})
            elif sync_result.conflict:
                warning = f"Post-commit sync conflict: {sync_result.conflict_bookmark}"
                logger.warning(
                    "agent.post_commit_sync_conflict",
                    extra={"bookmark": sync_result.conflict_bookmark, "remote": self.config.sync_remote},
                )
            else:
                warning = f"Post-commit sync failed: {sync_result.error}"
                logger.warning(
                    "agent.post_commit_sync_failed",
                    extra={"error": sync_result.error, "remote": self.config.sync_remote},
                )
        except Exception as exc:
            warning = f"Post-commit sync error: {exc}"
            logger.exception("agent.post_commit_sync_error", extra={"remote": self.config.sync_remote})
```

### Design Notes

- **Default off (`sync_after_commit: bool = False`).** No behavioral change unless explicitly opted in.
- **Sync failure is a warning, not an error.** The commit already succeeded — sync failure doesn't invalidate the operation. The `RunResult.warning` field carries the message.
- **Only syncs when commit succeeded** (`warning is None`). If commit itself failed, skip sync.
- **Uses `self.vault.sync()` directly** — stays within the boundary rule (vault owns VCS).
- **No retry logic.** If sync fails, the user can manually trigger via the HTTP route or next apply will try again.

### Validation

```bash
uv run pytest tests/test_agent.py -v
```

Add tests:

```python
def test_post_commit_sync_success_when_enabled():
    """Agent syncs after commit when sync_after_commit=True."""

def test_post_commit_sync_conflict_sets_warning():
    """Sync conflict after commit sets warning, ok remains True."""

def test_post_commit_sync_skipped_when_disabled():
    """No sync call when sync_after_commit=False (default)."""

def test_post_commit_sync_skipped_when_commit_failed():
    """No sync call when commit itself failed."""
```

---

## Step 9: Update README

### Goal

Document the new sync endpoints and config options.

### Files Changed

- `README.md`

### Additions

1. **New endpoints section** — Document all 7 `/api/vault/vcs/sync/*` routes with request/response schemas.
2. **Sync config** — Add `AGENT_SYNC_AFTER_COMMIT` and `AGENT_SYNC_REMOTE` to the environment variable table.
3. **Operational note** — Token handling: stored vault-local in `.forge/git-credential.sh` by `obsidian-ops`, never logged by `obsidian-agent`.
4. **Sync vs Apply note** — Sync is separate from `/api/agent/apply` commit flow unless `AGENT_SYNC_AFTER_COMMIT=true`.

---

## Rollout Summary

| Step | Scope | Validates |
|------|-------|-----------|
| 1 | Dep bump | Existing tests pass with v0.7.1 |
| 2 | Models | Import check, model tests pass |
| 3 | Routes | Route handlers compile, existing tests pass |
| 4 | Route tests | All sync routes tested (22+ cases) |
| 5 | LLM tools | Tool functions registered, existing tests pass |
| 6 | Interface profiles | Tool gating correct per interface |
| 7 | LLM tool tests | All sync tools tested (12+ cases) |
| 8 | Post-commit sync | Config flag + agent behavior tested |
| 9 | README | Documentation complete |

## Files Changed (Complete)

| File | Steps |
|------|-------|
| `pyproject.toml` | 1 |
| `uv.lock` | 1 |
| `src/obsidian_agent/models.py` | 2 |
| `src/obsidian_agent/routes/vault_routes.py` | 3 |
| `tests/test_vault_routes.py` | 4 |
| `src/obsidian_agent/tools.py` | 5 |
| `src/obsidian_agent/interfaces/command.py` | 6 |
| `src/obsidian_agent/interfaces/forge_web.py` | 6 |
| `tests/test_tools.py` | 7 |
| `src/obsidian_agent/config.py` | 8 |
| `src/obsidian_agent/agent.py` | 8 |
| `tests/test_agent.py` | 8 |
| `README.md` | 9 |

## Architecture Invariants Preserved

1. **Boundary rule**: Agent never runs VCS commands. All sync logic delegated to `vault.*` methods.
2. **Error pattern**: Tools return error strings; routes raise HTTPExceptions. BusyError always propagates.
3. **Model conventions**: `extra="forbid"` on requests, `ok: bool = True` on responses.
4. **Rate limiting**: Mutation routes rate-limited, read-only routes are not.
5. **Interface gating**: Tool availability controlled by profile + `allowed_tool_names`.
6. **Logging**: Structured event logging with `extra={}`. No sensitive data (tokens) logged.
7. **Concurrency**: `_busy` lock unchanged. Sync is synchronous within the vault call — no background workers.

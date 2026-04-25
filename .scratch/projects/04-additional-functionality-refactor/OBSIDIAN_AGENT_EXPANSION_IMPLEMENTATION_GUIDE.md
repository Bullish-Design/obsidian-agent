# OBSIDIAN_AGENT_EXPANSION_IMPLEMENTATION_GUIDE.md

## Audience

This guide is for the engineer implementing the obsidian-agent backend side of
`FORGE_EXPANSION_CONCEPT.md`.

This version is intentionally implementation-ready for this repository's current
layout (`src/obsidian_agent/*`, `tests/*`) and includes concrete file-level code
that can be copied and adapted directly.

## Scope and non-goals

This guide covers obsidian-agent only:

- FastAPI route split (`/api/agent/*` + `/api/vault/*`)
- typed scope contract (`EditScope`)
- interface registry (`command`, `forge_web`)
- deterministic vault routes and template routes

This guide does not implement obsidian-ops internals. It assumes obsidian-ops
will expose the primitives referenced in the concept (`list_structure`,
`ensure_block_id`, `create_from_template`).

## Ground rules

1. Forge continues to proxy all `/api/*` calls to obsidian-agent.
2. All file writes still flow through `obsidian_ops.Vault`.
3. Keep `/api/apply` and `/api/undo` as legacy aliases while migrating frontend.
4. Ship in phases, with a test gate before starting the next phase.
5. Prefer additive and compatible changes first; remove compatibility shims only
   after Forge is fully switched.

## Current baseline (from this repo)

At start, obsidian-agent has:

- one monolithic `src/obsidian_agent/app.py` with `/api/apply`, `/api/undo`,
  `/api/health`
- `ApplyRequest` with `instruction`, `current_file`, `interface_id`
- a single command interface path to `Agent.run(instruction, current_file)`
- no deterministic `/api/vault/*` routes yet

## Prerequisites

- Python 3.13+ (`pyproject.toml` runtime floor)
- `devenv` shell available
- a compatible obsidian-ops build (at least current methods + new methods for
  later phases)
- a scratch vault initialized with jj for integration checks

Recommended setup commands:

```bash
devenv shell -- uv sync --extra dev
devenv shell -- pytest -q
```

## Target architecture after Phase 3

```text
src/obsidian_agent/
  app.py
  web_paths.py
  scope.py
  interfaces/
    __init__.py
    command.py
    forge_web.py
  routes/
    __init__.py
    agent_routes.py
    vault_routes.py
  models.py
  agent.py
  tools.py
```

---

# Phase 1 - Route split and `/api/vault/*` foundations

Goal: introduce deterministic vault APIs without breaking existing clients.

## Step 1.1 - Split routes into `routes/`

### 1.1.1 Create `src/obsidian_agent/routes/__init__.py`

```python
from .agent_routes import agent_router
from .vault_routes import vault_router

__all__ = ["agent_router", "vault_router"]
```

### 1.1.2 Create `src/obsidian_agent/routes/agent_routes.py`

Use this as the initial router extracted from current `app.py` behavior:

```python
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from obsidian_ops.errors import BusyError as VaultBusyError

from ..agent import Agent, BusyError
from ..models import ApplyRequest, OperationResult, RunResult

logger = logging.getLogger(__name__)

DEFAULT_INTERFACE_ID = "command"

agent_router = APIRouter(prefix="/api/agent", tags=["agent"])


def to_operation_result(result: RunResult) -> OperationResult:
    return OperationResult(
        ok=result.ok,
        updated=result.updated,
        summary=result.summary,
        changed_files=result.changed_files,
        error=result.error,
        warning=result.warning,
    )


async def handle_apply(request: Request, payload: ApplyRequest) -> OperationResult:
    active_agent: Agent = request.app.state.agent
    interface_id = payload.interface_id or DEFAULT_INTERFACE_ID

    if payload.instruction is None or not payload.instruction.strip():
        return OperationResult(ok=False, updated=False, summary="", error="instruction is required")

    if interface_id != DEFAULT_INTERFACE_ID:
        raise HTTPException(status_code=400, detail=f"unsupported interface_id: {interface_id}")

    try:
        result = await active_agent.run(payload.instruction, payload.current_file)
        return to_operation_result(result)
    except (BusyError, VaultBusyError) as exc:
        logger.warning(
            "api.apply_busy_rejected",
            extra={
                "error": str(exc),
                "has_current_file": bool(payload.current_file),
                "interface_id": interface_id,
            },
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def handle_undo(request: Request) -> OperationResult:
    active_agent: Agent = request.app.state.agent
    try:
        result = await active_agent.undo()
        return to_operation_result(result)
    except (BusyError, VaultBusyError) as exc:
        logger.warning("api.undo_busy_rejected", extra={"error": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@agent_router.post("/apply", response_model=OperationResult)
async def apply_instruction(request: Request, payload: ApplyRequest) -> OperationResult:
    return await handle_apply(request, payload)
```

### 1.1.3 Create `src/obsidian_agent/routes/vault_routes.py`

Start with a minimal router shell in this step:

```python
from __future__ import annotations

from fastapi import APIRouter

vault_router = APIRouter(prefix="/api/vault", tags=["vault"])
```

### 1.1.4 Update `src/obsidian_agent/app.py`

Refactor app wiring to mount both routers and keep aliases:

```python
from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from fastapi import FastAPI, Request
from obsidian_ops import Vault

from .agent import Agent
from .config import AgentConfig
from .models import ApplyRequest, HealthResponse, OperationResult
from .routes import agent_router, vault_router
from .routes.agent_routes import handle_apply, handle_undo

logger = logging.getLogger(__name__)


def create_app(agent: Agent | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if agent is not None:
            app.state.agent = agent
            app.state.vault = agent.vault
            app.state.config = agent.config
            yield
            return

        config = AgentConfig()
        vault = Vault(str(config.vault_dir), jj_bin=config.jj_bin, jj_timeout=config.jj_timeout)
        app.state.agent = Agent(config, vault)
        app.state.vault = vault
        app.state.config = config
        yield

    app = FastAPI(lifespan=lifespan)

    app.include_router(agent_router)
    app.include_router(vault_router)

    @app.post("/api/apply", response_model=OperationResult, deprecated=True)
    async def legacy_apply(request: Request, payload: ApplyRequest) -> OperationResult:
        logger.warning("api.legacy_apply_used")
        return await handle_apply(request, payload)

    @app.post("/api/undo", response_model=OperationResult, deprecated=True)
    async def legacy_undo(request: Request) -> OperationResult:
        logger.warning("api.legacy_undo_used")
        return await handle_undo(request)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(ok=True, status="healthy")

    return app


app = create_app()
```

### 1.1.5 Test gate

- run existing tests first:

```bash
devenv shell -- pytest tests/test_app.py -q
devenv shell -- pytest -q
```

Expected: no behavior change.

## Step 1.2 - Add `web_paths.py` URL/path resolver

Create `src/obsidian_agent/web_paths.py`:

```python
from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import urlparse


def normalize_vault_path(path: str) -> str:
    raw = path.strip()
    if not raw:
        raise ValueError("path must be non-empty")
    if "\\" in raw:
        raise ValueError("path must use '/' separators")

    normalized = PurePosixPath(raw)
    if normalized.is_absolute():
        raise ValueError("path must be vault-relative")
    if ".." in normalized.parts:
        raise ValueError("path must not traverse parent directories")

    return str(normalized)


def url_to_vault_path(*, url: str, site_base_url: str, flat_urls: bool) -> str:
    parsed = urlparse(url)
    base = urlparse(site_base_url)

    if parsed.scheme and parsed.netloc:
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
            raise ValueError("url host does not match site_base_url")
        path_part = parsed.path
    else:
        path_part = parsed.path or url

    if not path_part:
        raise ValueError("url must include a path")

    cleaned = path_part.strip("/")
    if not cleaned:
        cleaned = "index"

    if flat_urls:
        candidate = f"{cleaned}.md" if not cleaned.endswith(".md") else cleaned
    else:
        candidate = f"{cleaned}.md" if not cleaned.endswith(".md") else cleaned

    return normalize_vault_path(candidate)


def vault_path_to_url(*, path: str, site_base_url: str, flat_urls: bool) -> str:
    normalized = normalize_vault_path(path)
    without_ext = normalized[:-3] if normalized.endswith(".md") else normalized
    base = site_base_url.rstrip("/")

    if not without_ext:
        return f"{base}/"

    if flat_urls:
        return f"{base}/{without_ext}"

    return f"{base}/{without_ext}/"


def resolve_path_or_url(
    *,
    path: str | None,
    url: str | None,
    site_base_url: str,
    flat_urls: bool,
) -> str:
    if bool(path) == bool(url):
        raise ValueError("provide exactly one of path or url")

    if path is not None:
        return normalize_vault_path(path)

    return url_to_vault_path(url=url or "", site_base_url=site_base_url, flat_urls=flat_urls)
```

Update config with site URL knobs (`src/obsidian_agent/config.py`):

```python
# add fields inside AgentConfig
site_base_url: str = "http://127.0.0.1:8080"
flat_urls: bool = False
```

Normalize trailing slash with a validator:

```python
@field_validator("site_base_url")
@classmethod
def normalize_site_base_url(cls, value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("site_base_url must use http or https")
    if not parsed.netloc:
        raise ValueError("site_base_url must include a host")
    return urlunparse(parsed._replace(path=parsed.path.rstrip("/"), query="", fragment=""))
```

Test gate:

- add `tests/test_web_paths.py` for normalization and mismatch cases
- run `devenv shell -- pytest tests/test_web_paths.py -q`

## Step 1.3 - Implement `GET/PUT /api/vault/files`

### 1.3.1 Extend `src/obsidian_agent/models.py`

Add deterministic request/response models:

```python
from datetime import datetime
from typing import Literal

# ...existing imports...

class VaultFileWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    url: str | None = None
    content: str
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class VaultFileReadResponse(BaseModel):
    ok: bool = True
    path: str
    url: str | None = None
    content: str
    sha256: str
    modified_at: datetime


class VaultFileWriteResponse(BaseModel):
    ok: bool = True
    path: str
    url: str | None = None
    sha256: str
    modified_at: datetime
    warning: str | None = None
```

### 1.3.2 Fill out `src/obsidian_agent/routes/vault_routes.py`

```python
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from obsidian_ops import Vault
from obsidian_ops.errors import BusyError as VaultBusyError

from ..models import VaultFileReadResponse, VaultFileWriteRequest, VaultFileWriteResponse
from ..web_paths import resolve_path_or_url, vault_path_to_url

logger = logging.getLogger(__name__)

vault_router = APIRouter(prefix="/api/vault", tags=["vault"])


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _file_mtime(vault: Vault, path: str) -> datetime:
    disk_path = Path(vault.root) / path
    stat = disk_path.stat()
    return datetime.fromtimestamp(stat.st_mtime, tz=UTC)


@vault_router.get("/files", response_model=VaultFileReadResponse)
async def get_file(
    request: Request,
    path: str | None = Query(default=None),
    url: str | None = Query(default=None),
) -> VaultFileReadResponse:
    vault: Vault = request.app.state.vault
    config = request.app.state.config

    try:
        resolved = resolve_path_or_url(path=path, url=url, site_base_url=config.site_base_url, flat_urls=config.flat_urls)
        content = vault.read_file(resolved)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return VaultFileReadResponse(
        path=resolved,
        url=vault_path_to_url(path=resolved, site_base_url=config.site_base_url, flat_urls=config.flat_urls),
        content=content,
        sha256=_sha256_text(content),
        modified_at=_file_mtime(vault, resolved),
    )


@vault_router.put("/files", response_model=VaultFileWriteResponse)
async def put_file(request: Request, payload: VaultFileWriteRequest) -> VaultFileWriteResponse:
    vault: Vault = request.app.state.vault
    config = request.app.state.config

    try:
        resolved = resolve_path_or_url(
            path=payload.path,
            url=payload.url,
            site_base_url=config.site_base_url,
            flat_urls=config.flat_urls,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        try:
            current = vault.read_file(resolved)
        except FileNotFoundError:
            current = None

        current_sha = _sha256_text(current) if current is not None else None
        if payload.expected_sha256 is not None and current_sha != payload.expected_sha256:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_write",
                    "path": resolved,
                    "expected_sha256": payload.expected_sha256,
                    "current_sha256": current_sha,
                },
            )

        vault.write_file(resolved, payload.content)

        warning = None
        try:
            vault.commit(f"vault api write: {resolved}")
        except Exception as exc:  # pragma: no cover (warning path)
            warning = f"commit failed: {exc}"
            logger.exception("vault.file_commit_failed", extra={"path": resolved})

        written = vault.read_file(resolved)
        return VaultFileWriteResponse(
            path=resolved,
            url=vault_path_to_url(path=resolved, site_base_url=config.site_base_url, flat_urls=config.flat_urls),
            sha256=_sha256_text(written),
            modified_at=_file_mtime(vault, resolved),
            warning=warning,
        )
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
```

### 1.3.3 Tests to add (`tests/test_vault_routes.py`)

Start with these cases:

```python
def test_get_file_by_path(client): ...
def test_get_file_by_url(client): ...
def test_put_file_happy_path(client): ...
def test_put_file_stale_hash_returns_409(client): ...
def test_put_file_rejects_path_and_url_together(client): ...
def test_put_file_rejects_neither_path_nor_url(client): ...
```

409 assertion example:

```python
assert response.status_code == 409
detail = response.json()["detail"]
assert detail["code"] == "stale_write"
assert detail["current_sha256"] is not None
```

Test gate:

```bash
devenv shell -- pytest tests/test_vault_routes.py -q
devenv shell -- pytest -q
```

## Step 1.4 - Add `POST /api/vault/undo`

Add undo model in `models.py`:

```python
class VaultUndoResponse(BaseModel):
    ok: bool = True
    updated: bool = True
    summary: str = "Last change undone."
    warning: str | None = None
```

Add route in `vault_routes.py`:

```python
from ..models import VaultUndoResponse

@vault_router.post("/undo", response_model=VaultUndoResponse)
async def vault_undo(request: Request) -> VaultUndoResponse:
    vault: Vault = request.app.state.vault
    try:
        if hasattr(vault, "undo_last_change"):
            result = vault.undo_last_change()
            warning = getattr(result, "warning", None)
        else:
            vault.undo()
            warning = None

        return VaultUndoResponse(warning=warning)
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"undo failed: {exc}") from exc
```

Test gate:

- add `test_vault_undo_success`
- add `test_vault_undo_busy_409`

## Step 1.5 - Keep legacy aliases functional

Legacy behavior during migration:

- `/api/apply` -> internally call `/api/agent/apply` handler logic
- `/api/undo` -> call `/api/vault/undo` in final migrated state

Recommended compatibility window:

- one full Forge release cycle after frontend switch

Add deprecation log fields:

```python
logger.warning("api.legacy_apply_used", extra={"route": "/api/apply"})
logger.warning("api.legacy_undo_used", extra={"route": "/api/undo"})
```

## Step 1.6 - Phase 1 exit gate

Required before Phase 2:

```bash
devenv shell -- pytest tests/test_app.py tests/test_vault_routes.py -q
devenv shell -- pytest -q
```

Manual checks:

1. `GET /api/vault/files?path=note.md` returns content + sha.
2. `PUT /api/vault/files` with stale hash returns 409 with server hash.
3. `/api/apply` still works unchanged.

---

# Phase 2 - Scoped contract and structure/anchor routes

Goal: make scope-targeted editing first-class and enforceable.

## Step 2.1 - Add `EditScope` discriminated union

Create `src/obsidian_agent/scope.py`:

```python
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .web_paths import normalize_vault_path


class FileScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["file"] = "file"
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)


class HeadingScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["heading"] = "heading"
    path: str
    heading: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)


class BlockScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["block"] = "block"
    path: str
    block_id: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)


class SelectionScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["selection"] = "selection"
    path: str
    text: str
    line_start: int
    line_end: int
    context_before: str | None = None
    context_after: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)

    @model_validator(mode="after")
    def validate_lines(self) -> "SelectionScope":
        if self.line_start < 1:
            raise ValueError("line_start must be >= 1")
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class MultiScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["multi"] = "multi"
    path: str
    scopes: list[HeadingScope | BlockScope | SelectionScope]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_vault_path(value)

    @model_validator(mode="after")
    def validate_nested_paths(self) -> "MultiScope":
        for scope in self.scopes:
            if scope.path != self.path:
                raise ValueError("all nested scopes must target the same path as multi.path")
        return self


EditScope = Annotated[
    FileScope | HeadingScope | BlockScope | SelectionScope | MultiScope,
    Field(discriminator="kind"),
]
```

Add tests in `tests/test_scope.py`:

- validates each kind
- rejects invalid line ranges
- rejects mixed-path `MultiScope`

## Step 2.2 - Add interface registry

Create `src/obsidian_agent/interfaces/__init__.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..scope import EditScope
from .command import CommandProfile
from .forge_web import ForgeWebProfile


class InterfaceProfile(Protocol):
    id: str

    def allowed_tool_names(self, scope: EditScope | None) -> set[str]: ...

    def prompt_suffix(self, scope: EditScope | None, intent: str | None) -> str: ...


INTERFACES: dict[str, InterfaceProfile] = {
    "command": CommandProfile(),
    "forge_web": ForgeWebProfile(),
}


def resolve_interface(interface_id: str) -> InterfaceProfile:
    profile = INTERFACES.get(interface_id)
    if profile is None:
        raise ValueError(f"unsupported interface_id: {interface_id}")
    return profile
```

Create `src/obsidian_agent/interfaces/command.py`:

```python
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
        }

    def prompt_suffix(self, scope: EditScope | None, intent: str | None) -> str:
        _ = scope, intent
        return ""
```

Create `src/obsidian_agent/interfaces/forge_web.py`:

```python
from __future__ import annotations

from ..scope import BlockScope, EditScope, HeadingScope, SelectionScope


READ_ONLY = {"read_file", "list_files", "search_files", "get_frontmatter", "read_heading", "read_block"}


class ForgeWebProfile:
    id = "forge_web"

    def allowed_tool_names(self, scope: EditScope | None) -> set[str]:
        if scope is None:
            return READ_ONLY | {"write_file", "write_heading", "write_block", "update_frontmatter"}

        if isinstance(scope, BlockScope):
            return READ_ONLY | {"write_block"}

        if isinstance(scope, HeadingScope):
            return READ_ONLY | {"write_heading", "update_frontmatter"}

        if isinstance(scope, SelectionScope):
            return READ_ONLY | {"write_heading", "write_block"}

        # file + multi fallback
        return READ_ONLY | {"write_file", "write_heading", "write_block", "update_frontmatter"}

    def prompt_suffix(self, scope: EditScope | None, intent: str | None) -> str:
        lines = ["You are operating in Forge web interface mode."]
        if intent:
            lines.append(f"Intent mode: {intent}")
        if scope is not None:
            lines.append(f"Scope kind: {scope.kind}")
            lines.append("Do not modify content outside the target scope.")
        return "\n".join(lines)
```

## Step 2.3 - Extend ApplyRequest and dispatch

### 2.3.1 Update `src/obsidian_agent/models.py`

Add new fields on `ApplyRequest`:

```python
from typing import Literal

from .scope import EditScope

# inside ApplyRequest
scope: EditScope | None = None
intent: Literal["rewrite", "summarize", "insert_below", "annotate", "extract_tasks"] | None = None
allowed_write_scope: Literal["target_only", "target_plus_frontmatter", "unrestricted"] = "target_only"
```

Validation rule recommended:

```python
@model_validator(mode="after")
def validate_scope_path_alignment(self) -> "ApplyRequest":
    if self.scope is not None and self.current_file is not None and self.scope.path != self.current_file:
        raise ValueError("scope.path must match current_file when both are provided")
    return self
```

### 2.3.2 Extend `VaultDeps` and tool guards in `src/obsidian_agent/tools.py`

Add fields:

```python
allowed_tool_names: set[str] | None = None
allowed_write_paths: set[str] | None = None
allowed_write_scope: str = "unrestricted"
```

Add guard helpers:

```python
def _tool_allowed(ctx: RunContext[VaultDeps], tool_name: str) -> bool:
    allowed = ctx.deps.allowed_tool_names
    return allowed is None or tool_name in allowed


def _path_allowed(ctx: RunContext[VaultDeps], path: str) -> bool:
    allowed_paths = ctx.deps.allowed_write_paths
    if allowed_paths is None:
        return True
    return path in allowed_paths
```

Apply at start of every write-capable tool:

```python
if not _tool_allowed(ctx, "write_file"):
    return "Error: write_file is not allowed in this interface/scope"
if not _path_allowed(ctx, path):
    return "Error: write target is outside allowed scope"
```

### 2.3.3 Extend `Agent.run` signature (`src/obsidian_agent/agent.py`)

Add optional interface/scope args while keeping current callers compatible:

```python
async def run(
    self,
    instruction: str,
    current_file: str | None = None,
    *,
    interface_id: str = "command",
    scope: object | None = None,
    intent: str | None = None,
    allowed_write_scope: str = "unrestricted",
    allowed_tool_names: set[str] | None = None,
    allowed_write_paths: set[str] | None = None,
) -> RunResult:
    ...
```

Pass these through `VaultDeps` in `_run_impl`.

### 2.3.4 Update prompt construction (`src/obsidian_agent/prompt.py`)

Upgrade function signature:

```python
def build_system_prompt(
    current_file: str | None = None,
    *,
    interface_id: str = "command",
    scope_kind: str | None = None,
    intent: str | None = None,
    profile_suffix: str | None = None,
) -> str:
    ...
```

Append deterministic sections like:

- `Interface: forge_web`
- `Scope: heading`
- `Intent: summarize`
- profile suffix text

### 2.3.5 Update `/api/agent/apply` dispatch (`src/obsidian_agent/routes/agent_routes.py`)

Core handler changes:

```python
from ..interfaces import resolve_interface
from ..scope import MultiScope


def _allowed_write_paths(scope) -> set[str] | None:
    if scope is None:
        return None
    return {scope.path}


async def handle_apply(request: Request, payload: ApplyRequest) -> OperationResult:
    active_agent: Agent = request.app.state.agent
    interface_id = payload.interface_id or DEFAULT_INTERFACE_ID

    if payload.instruction is None or not payload.instruction.strip():
        return OperationResult(ok=False, updated=False, summary="", error="instruction is required")

    try:
        profile = resolve_interface(interface_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    effective_current_file = payload.current_file or (payload.scope.path if payload.scope is not None else None)

    result = await active_agent.run(
        payload.instruction,
        effective_current_file,
        interface_id=profile.id,
        scope=payload.scope,
        intent=payload.intent,
        allowed_write_scope=payload.allowed_write_scope,
        allowed_tool_names=profile.allowed_tool_names(payload.scope),
        allowed_write_paths=_allowed_write_paths(payload.scope),
    )
    return to_operation_result(result)
```

### 2.3.6 Legacy `/api/apply` shim strategy

Keep alias route accepting old payloads and normalizing:

- if `scope` missing, default to file scope from `current_file` if present
- preserve existing behavior for current clients sending only `instruction`

## Step 2.4 - Add `GET /api/vault/files/structure`

Add model in `models.py`:

```python
class VaultStructureResponse(BaseModel):
    ok: bool = True
    path: str
    sha256: str | None = None
    headings: list[dict] = Field(default_factory=list)
    blocks: list[dict] = Field(default_factory=list)
```

Add route in `vault_routes.py`:

```python
@vault_router.get("/files/structure", response_model=VaultStructureResponse)
async def get_structure(
    request: Request,
    path: str | None = Query(default=None),
    url: str | None = Query(default=None),
) -> VaultStructureResponse:
    vault: Vault = request.app.state.vault
    config = request.app.state.config

    resolved = resolve_path_or_url(path=path, url=url, site_base_url=config.site_base_url, flat_urls=config.flat_urls)

    if not hasattr(vault, "list_structure"):
        raise HTTPException(status_code=501, detail="list_structure not available in installed obsidian-ops")

    structure = vault.list_structure(resolved)

    headings = [h.__dict__ for h in getattr(structure, "headings", [])]
    blocks = [b.__dict__ for b in getattr(structure, "blocks", [])]
    sha256 = getattr(structure, "sha256", None)

    return VaultStructureResponse(path=resolved, sha256=sha256, headings=headings, blocks=blocks)
```

## Step 2.5 - Add `POST /api/vault/files/anchors`

Add request/response models:

```python
class EnsureAnchorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    url: str | None = None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class EnsureAnchorResponse(BaseModel):
    ok: bool = True
    path: str
    block_id: str
    sha256: str | None = None
```

Add route:

```python
@vault_router.post("/files/anchors", response_model=EnsureAnchorResponse)
async def ensure_anchor(request: Request, payload: EnsureAnchorRequest) -> EnsureAnchorResponse:
    vault: Vault = request.app.state.vault
    config = request.app.state.config

    if payload.line_end < payload.line_start:
        raise HTTPException(status_code=400, detail="line_end must be >= line_start")

    resolved = resolve_path_or_url(
        path=payload.path,
        url=payload.url,
        site_base_url=config.site_base_url,
        flat_urls=config.flat_urls,
    )

    if not hasattr(vault, "ensure_block_id"):
        raise HTTPException(status_code=501, detail="ensure_block_id not available in installed obsidian-ops")

    result = vault.ensure_block_id(resolved, payload.line_start, payload.line_end)
    return EnsureAnchorResponse(
        path=resolved,
        block_id=getattr(result, "block_id"),
        sha256=getattr(result, "sha256", None),
    )
```

## Step 2.6 - Phase 2 exit gate

Required automated checks:

```bash
devenv shell -- pytest tests/test_scope.py tests/test_app.py tests/test_tools.py tests/test_vault_routes.py -q
devenv shell -- pytest -q
```

Required behavior checks:

1. `forge_web + block scope` rejects `write_file` tool use.
2. scoped write attempts to a different file path fail deterministically.
3. `GET /api/vault/files/structure` and `POST /api/vault/files/anchors` work
   when obsidian-ops exposes methods, and return 501 otherwise.

---

# Phase 3 - Template APIs

Goal: deterministic page creation through backend routes.

## Step 3.1 - Add `GET /api/vault/pages/templates`

Add model:

```python
class TemplateInfo(BaseModel):
    key: str
    title: str
    description: str | None = None
    fields: list[dict] = Field(default_factory=list)


class TemplateListResponse(BaseModel):
    ok: bool = True
    templates: list[TemplateInfo] = Field(default_factory=list)
```

Add route (with method availability guard):

```python
@vault_router.get("/pages/templates", response_model=TemplateListResponse)
async def list_templates(request: Request) -> TemplateListResponse:
    vault: Vault = request.app.state.vault

    if not hasattr(vault, "list_templates"):
        raise HTTPException(status_code=501, detail="list_templates not available in installed obsidian-ops")

    templates = vault.list_templates()
    items = [
        TemplateInfo(
            key=getattr(t, "key"),
            title=getattr(t, "title", getattr(t, "key")),
            description=getattr(t, "description", None),
            fields=list(getattr(t, "fields", [])),
        )
        for t in templates
    ]
    return TemplateListResponse(templates=items)
```

## Step 3.2 - Add `POST /api/vault/pages`

Add models:

```python
class CreatePageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_key: str
    fields: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class CreatePageResponse(BaseModel):
    ok: bool = True
    path: str
    url: str
    sha256: str | None = None
```

Route:

```python
@vault_router.post("/pages", response_model=CreatePageResponse)
async def create_page(request: Request, payload: CreatePageRequest) -> CreatePageResponse:
    vault: Vault = request.app.state.vault
    config = request.app.state.config

    if not hasattr(vault, "create_from_template"):
        raise HTTPException(status_code=501, detail="create_from_template not available in installed obsidian-ops")

    try:
        created = vault.create_from_template(payload.template_key, payload.fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    path = getattr(created, "path")
    url = vault_path_to_url(path=path, site_base_url=config.site_base_url, flat_urls=config.flat_urls)
    return CreatePageResponse(path=path, url=url, sha256=getattr(created, "sha256", None))
```

## Step 3.3 - Optional default template bootstrap

If template directory is empty at startup:

- copy package data from `src/obsidian_agent/_default_templates/*`
- do this once, behind config flag `AGENT_SEED_DEFAULT_TEMPLATES=true`

Implementation location suggestion:

- helper in `app.py` lifespan after config load
- no-op unless vault template dir exists and empty

## Step 3.4 - Phase 3 exit gate

Tests to add:

- `test_list_templates_happy_path`
- `test_create_page_happy_path`
- `test_create_page_conflict_409`
- `test_template_routes_return_501_when_ops_missing`

Run:

```bash
devenv shell -- pytest tests/test_vault_routes.py -q
devenv shell -- pytest -q
```

Manual:

1. call templates list route from Forge overlay
2. create page from template
3. open returned URL and verify content

---

# Phase 4 - Polish and hardening

## Step 4.1 - expose `create_from_template` as an LLM tool (optional)

In `tools.py`, add tool with explicit gating under interface profile:

```python
async def create_from_template_tool(ctx: RunContext[VaultDeps], template_key: str, fields: dict[str, Any]) -> str:
    if not _tool_allowed(ctx, "create_from_template"):
        return "Error: create_from_template is not allowed in this interface/scope"
    if not hasattr(ctx.deps.vault, "create_from_template"):
        return "Error: create_from_template unavailable"

    result = ctx.deps.vault.create_from_template(template_key, fields)
    path = getattr(result, "path")
    ctx.deps.changed_files.add(path)
    return f"Created {path}"
```

Only include this tool in `forge_web` profile once frontend flow is stable.

## Step 4.2 - route-level lightweight rate limiting

Recommended for deterministic endpoints under high click rates:

- in-memory token bucket keyed by client IP + route
- apply to `PUT /api/vault/files`, `POST /api/vault/files/anchors`,
  `POST /api/vault/pages`

If a reverse proxy already handles this, skip app-level limiter.

## Step 4.3 - structured request logging

Add middleware in `app.py`:

```python
@app.middleware("http")
async def request_logging(request: Request, call_next):
    started = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    logger.info(
        "http.request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response
```

For `/api/agent/apply`, log `interface_id`, `scope.kind`, and intent.

---

# Recommended test layout additions

Add these files over the migration:

```text
tests/
  test_web_paths.py
  test_scope.py
  test_vault_routes.py
  test_interfaces.py
```

Suggested coverage checklist:

- path/url resolver correctness (including hostile inputs)
- discriminated-union validation and error quality
- interface tool allowlist behavior
- deterministic route status mapping (400/404/409/501)
- legacy alias compatibility

---

# Rollout plan (safe sequence)

1. Land Phase 1 route split + vault files + alias compatibility.
2. Upgrade Forge editor mode to use `/api/vault/files`.
3. Land Phase 2 scope + interface enforcement.
4. Upgrade Forge scope mode to use `/api/agent/apply` (`interface_id=forge_web`).
5. Land Phase 3 template routes.
6. Upgrade Forge new-page mode to `/api/vault/pages*`.
7. Keep legacy aliases for one release cycle, then remove.

---

# Definition of done

You are done when all are true:

- `/api/agent/apply` is the primary semantic endpoint.
- `/api/vault/*` serves deterministic editor/scope/template needs.
- scope constraints are enforced by tool/path allowlists, not prompt text only.
- legacy `/api/apply` and `/api/undo` still function during migration window.
- full test suite passes from a clean checkout.

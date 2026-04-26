from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from obsidian_ops import Vault
from obsidian_ops.errors import BusyError as VaultBusyError
from obsidian_ops.errors import VCSError, VaultError

from ..models import (
    EnsureAnchorRequest,
    EnsureAnchorResponse,
    CreatePageRequest,
    CreatePageResponse,
    SyncOpResponse,
    SyncReadinessResponse,
    SyncRemoteOpRequest,
    SyncRemoteRequest,
    SyncRequest,
    SyncResultResponse,
    SyncStatusResponse,
    TemplateFieldInfo,
    TemplateInfo,
    TemplateListResponse,
    VaultFileReadResponse,
    VaultFileWriteRequest,
    VaultFileWriteResponse,
    VaultStructureResponse,
    VaultUndoResponse,
)
from ..web_paths import resolve_path_or_url, vault_path_to_url

logger = logging.getLogger(__name__)

vault_router = APIRouter(prefix="/api/vault", tags=["vault"])


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _file_modified_at(vault: Vault, path: str) -> datetime:
    disk_path = Path(vault.root) / path
    file_stat = disk_path.stat()
    return datetime.fromtimestamp(file_stat.st_mtime, tz=UTC)


def _enforce_rate_limit(request: Request, route_key: str) -> None:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return

    client = request.client.host if request.client is not None else "unknown"
    key = f"{client}:{route_key}"
    if not limiter.allow(key):
        raise HTTPException(status_code=429, detail="rate limit exceeded")


@vault_router.get("/files", response_model=VaultFileReadResponse)
async def get_file(
    request: Request,
    path: str | None = Query(default=None),
    url: str | None = Query(default=None),
) -> VaultFileReadResponse:
    vault: Vault = request.app.state.vault
    config = request.app.state.config

    try:
        resolved_path = resolve_path_or_url(
            path=path,
            url=url,
            site_base_url=config.site_base_url,
            flat_urls=config.flat_urls,
        )
        content = vault.read_file(resolved_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return VaultFileReadResponse(
        path=resolved_path,
        url=vault_path_to_url(path=resolved_path, site_base_url=config.site_base_url, flat_urls=config.flat_urls),
        content=content,
        sha256=_sha256_text(content),
        modified_at=_file_modified_at(vault, resolved_path),
    )


@vault_router.put("/files", response_model=VaultFileWriteResponse)
async def put_file(request: Request, payload: VaultFileWriteRequest) -> VaultFileWriteResponse:
    _enforce_rate_limit(request, "vault.put_file")

    vault: Vault = request.app.state.vault
    config = request.app.state.config

    try:
        resolved_path = resolve_path_or_url(
            path=payload.path,
            url=payload.url,
            site_base_url=config.site_base_url,
            flat_urls=config.flat_urls,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        try:
            current_content = vault.read_file(resolved_path)
        except FileNotFoundError:
            current_content = None

        current_sha = _sha256_text(current_content) if current_content is not None else None
        if payload.expected_sha256 is not None and current_sha != payload.expected_sha256:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_write",
                    "path": resolved_path,
                    "expected_sha256": payload.expected_sha256,
                    "current_sha256": current_sha,
                },
            )

        vault.write_file(resolved_path, payload.content)

        warning = None
        try:
            vault.commit(f"vault api write: {resolved_path}")
        except Exception as exc:  # pragma: no cover
            warning = f"commit failed: {exc}"
            logger.exception("vault.file_commit_failed", extra={"path": resolved_path})

        final_content = vault.read_file(resolved_path)
        return VaultFileWriteResponse(
            path=resolved_path,
            url=vault_path_to_url(path=resolved_path, site_base_url=config.site_base_url, flat_urls=config.flat_urls),
            sha256=_sha256_text(final_content),
            modified_at=_file_modified_at(vault, resolved_path),
            warning=warning,
        )
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@vault_router.post("/undo", response_model=VaultUndoResponse)
async def vault_undo(request: Request) -> VaultUndoResponse:
    _enforce_rate_limit(request, "vault.undo")

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


@vault_router.get("/files/structure", response_model=VaultStructureResponse)
async def get_file_structure(
    request: Request,
    path: str | None = Query(default=None),
    url: str | None = Query(default=None),
) -> VaultStructureResponse:
    vault: Vault = request.app.state.vault
    config = request.app.state.config

    try:
        resolved_path = resolve_path_or_url(
            path=path,
            url=url,
            site_base_url=config.site_base_url,
            flat_urls=config.flat_urls,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    list_structure = getattr(vault, "list_structure", None)
    if list_structure is None:
        raise HTTPException(status_code=501, detail="list_structure not available in installed obsidian-ops")

    try:
        structure = list_structure(resolved_path)
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    headings = [heading.__dict__ if hasattr(heading, "__dict__") else dict(heading) for heading in structure.headings]
    blocks = [block.__dict__ if hasattr(block, "__dict__") else dict(block) for block in structure.blocks]
    return VaultStructureResponse(
        path=resolved_path,
        sha256=getattr(structure, "sha256", None),
        headings=headings,
        blocks=blocks,
    )


@vault_router.post("/files/anchors", response_model=EnsureAnchorResponse)
async def ensure_file_anchor(request: Request, payload: EnsureAnchorRequest) -> EnsureAnchorResponse:
    _enforce_rate_limit(request, "vault.ensure_file_anchor")

    if payload.line_end < payload.line_start:
        raise HTTPException(status_code=400, detail="line_end must be >= line_start")

    vault: Vault = request.app.state.vault
    config = request.app.state.config

    try:
        resolved_path = resolve_path_or_url(
            path=payload.path,
            url=payload.url,
            site_base_url=config.site_base_url,
            flat_urls=config.flat_urls,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ensure_block_id = getattr(vault, "ensure_block_id", None)
    if ensure_block_id is None:
        raise HTTPException(status_code=501, detail="ensure_block_id not available in installed obsidian-ops")

    try:
        result = ensure_block_id(resolved_path, payload.line_start, payload.line_end)
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return EnsureAnchorResponse(
        path=resolved_path,
        block_id=getattr(result, "block_id"),
        sha256=getattr(result, "sha256", None),
    )


@vault_router.get("/pages/templates", response_model=TemplateListResponse)
async def list_page_templates(request: Request) -> TemplateListResponse:
    vault: Vault = request.app.state.vault
    list_templates = getattr(vault, "list_templates", None)
    if list_templates is None:
        raise HTTPException(status_code=501, detail="list_templates not available in installed obsidian-ops")

    try:
        templates = list_templates()
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload_items: list[TemplateInfo] = []
    for template in templates:
        fields = [
            TemplateFieldInfo(
                name=field.name,
                label=field.label,
                required=field.required,
                description=field.description,
                default=field.default,
            )
            for field in template.fields
        ]
        payload_items.append(
            TemplateInfo(
                key=template.key,
                label=template.label,
                fields=fields,
                commit_message=template.commit_message,
            )
        )
    return TemplateListResponse(templates=payload_items)


@vault_router.post("/pages", response_model=CreatePageResponse)
async def create_page_from_template(request: Request, payload: CreatePageRequest) -> CreatePageResponse:
    _enforce_rate_limit(request, "vault.create_page_from_template")

    vault: Vault = request.app.state.vault
    config = request.app.state.config
    create_from_template = getattr(vault, "create_from_template", None)
    if create_from_template is None:
        raise HTTPException(status_code=501, detail="create_from_template not available in installed obsidian-ops")

    try:
        created = create_from_template(payload.template_id, payload.fields)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VCSError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CreatePageResponse(
        template_id=created.template_id,
        path=created.path,
        url=vault_path_to_url(path=created.path, site_base_url=config.site_base_url, flat_urls=config.flat_urls),
        sha256=created.sha256,
    )


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


@vault_router.get("/vcs/sync/status", response_model=SyncStatusResponse)
async def get_sync_status(request: Request) -> SyncStatusResponse:
    vault: Vault = request.app.state.vault
    try:
        status = vault.sync_status()
        return SyncStatusResponse(status=status)
    except VaultBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

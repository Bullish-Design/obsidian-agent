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


def _file_modified_at(vault: Vault, path: str) -> datetime:
    disk_path = Path(vault.root) / path
    file_stat = disk_path.stat()
    return datetime.fromtimestamp(file_stat.st_mtime, tz=UTC)


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

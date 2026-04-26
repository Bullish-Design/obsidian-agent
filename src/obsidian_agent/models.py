from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .scope import EditScope


@dataclass
class RunResult:
    ok: bool
    updated: bool
    summary: str
    changed_files: list[str] = field(default_factory=list)
    error: str | None = None
    warning: str | None = None


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str | None = None
    current_file: str | None = None
    interface_id: str | None = None
    scope: EditScope | None = None
    intent: Literal["rewrite", "summarize", "insert_below", "annotate", "extract_tasks"] | None = None
    allowed_write_scope: Literal["target_only", "target_plus_frontmatter", "unrestricted"] = "target_only"

    @field_validator("current_file")
    @classmethod
    def validate_current_file(cls, value: str | None) -> str | None:
        if value is None:
            return value

        path = value.strip()
        if not path:
            msg = "current_file must be a non-empty vault-relative path"
            raise ValueError(msg)
        if "://" in path:
            msg = "current_file must be a vault-relative path, not a URL"
            raise ValueError(msg)
        if "\\" in path:
            msg = "current_file must use '/' separators"
            raise ValueError(msg)

        normalized = PurePosixPath(path)
        if normalized.is_absolute():
            msg = "current_file must be a vault-relative path"
            raise ValueError(msg)
        if ".." in normalized.parts:
            msg = "current_file must not traverse parent directories"
            raise ValueError(msg)

        return path

    @field_validator("interface_id")
    @classmethod
    def validate_interface_id(cls, value: str | None) -> str | None:
        if value is None:
            return value

        interface_id = value.strip()
        if not interface_id:
            msg = "interface_id must be a non-empty string when provided"
            raise ValueError(msg)
        return interface_id

    @model_validator(mode="after")
    def validate_scope_path_alignment(self) -> "ApplyRequest":
        if self.scope is not None and self.current_file is not None and self.scope.path != self.current_file:
            msg = "scope.path must match current_file when both are provided"
            raise ValueError(msg)
        return self


class OperationResult(BaseModel):
    ok: bool
    updated: bool
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    error: str | None = None
    warning: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    status: str


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


class VaultUndoResponse(BaseModel):
    ok: bool = True
    updated: bool = True
    summary: str = "Last change undone."
    warning: str | None = None


class VaultStructureResponse(BaseModel):
    ok: bool = True
    path: str
    sha256: str | None = None
    headings: list[dict] = Field(default_factory=list)
    blocks: list[dict] = Field(default_factory=list)


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


class TemplateFieldInfo(BaseModel):
    name: str
    label: str
    required: bool = True
    description: str | None = None
    default: str | None = None


class TemplateInfo(BaseModel):
    key: str
    label: str
    fields: list[TemplateFieldInfo] = Field(default_factory=list)
    commit_message: str | None = None


class TemplateListResponse(BaseModel):
    ok: bool = True
    templates: list[TemplateInfo] = Field(default_factory=list)


class CreatePageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    fields: dict[str, str] = Field(default_factory=dict)


class CreatePageResponse(BaseModel):
    ok: bool = True
    template_id: str
    path: str
    url: str
    sha256: str


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


class SyncReadinessResponse(BaseModel):
    ok: bool = True
    status: str
    detail: str | None = None


class SyncOpResponse(BaseModel):
    ok: bool = True
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

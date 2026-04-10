from dataclasses import dataclass, field
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

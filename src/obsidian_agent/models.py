from dataclasses import dataclass, field

from pydantic import BaseModel, Field


@dataclass
class RunResult:
    ok: bool
    updated: bool
    summary: str
    changed_files: list[str] = field(default_factory=list)
    error: str | None = None
    warning: str | None = None


class ApplyRequest(BaseModel):
    instruction: str
    current_file: str | None = None


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


from pydantic import BaseModel


class ApplyRequest(BaseModel):
    """Request payload for the /api/apply endpoint."""

    instruction: str
    current_url_path: str
    current_file_path: str | None = None


class OperationResult(BaseModel):
    """Shared response model for /api/apply and /api/undo."""

    ok: bool
    updated: bool
    summary: str = ""
    changed_files: list[str] = []
    warning: str | None = None
    error: str | None = None

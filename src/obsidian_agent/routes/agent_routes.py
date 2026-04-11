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


@agent_router.post("/apply", response_model=OperationResult)
async def apply_instruction(request: Request, payload: ApplyRequest) -> OperationResult:
    return await handle_apply(request, payload)


async def handle_undo(request: Request) -> OperationResult:
    active_agent: Agent = request.app.state.agent
    try:
        result = await active_agent.undo()
        return to_operation_result(result)
    except (BusyError, VaultBusyError) as exc:
        logger.warning("api.undo_busy_rejected", extra={"error": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc

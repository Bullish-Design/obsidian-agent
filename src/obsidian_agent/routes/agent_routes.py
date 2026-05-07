from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from ..agent import Agent
from ..interfaces import resolve_interface
from ..models import ApplyRequest, OperationResult, RunResult
from ..queue import JobQueue
from ..scope import EditScope

logger = logging.getLogger(__name__)

DEFAULT_INTERFACE_ID = "command"

agent_router = APIRouter(prefix="/api/agent", tags=["agent"])


def _allowed_write_paths(scope: EditScope | None) -> set[str] | None:
    if scope is None:
        return None
    return {scope.path}


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
    queue: JobQueue = request.app.state.job_queue
    interface_id = payload.interface_id or DEFAULT_INTERFACE_ID

    if payload.instruction is None or not payload.instruction.strip():
        return OperationResult(ok=False, updated=False, summary="", error="instruction is required")

    try:
        profile = resolve_interface(interface_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    effective_current_file = payload.current_file
    if effective_current_file is None and payload.scope is not None:
        effective_current_file = payload.scope.path

    job = queue.submit(
        "apply",
        {
            "instruction": payload.instruction,
            "current_file": effective_current_file,
            "kwargs": {
                "interface_id": profile.id,
                "scope": payload.scope,
                "intent": payload.intent,
                "allowed_write_scope": payload.allowed_write_scope,
                "allowed_tool_names": profile.allowed_tool_names(payload.scope),
                "allowed_write_paths": _allowed_write_paths(payload.scope),
                "profile_prompt_suffix": profile.prompt_suffix(payload.scope, payload.intent),
            },
        },
    )
    done_event = queue.get_done_event(job.id)
    if done_event is None:
        raise HTTPException(status_code=500, detail="job completion event missing")
    timeout_s = active_agent.config.operation_timeout
    try:
        await asyncio.wait_for(done_event.wait(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"Operation timed out after {timeout_s}s") from exc

    completed = queue.get(job.id)
    if completed is None:
        raise HTTPException(status_code=404, detail="job not found")
    if completed.result is not None:
        return to_operation_result(completed.result)
    return OperationResult(ok=False, updated=False, summary="", error=completed.error or "job failed")


@agent_router.post("/apply", response_model=OperationResult)
async def apply_instruction(request: Request, payload: ApplyRequest) -> OperationResult:
    return await handle_apply(request, payload)


async def handle_undo(request: Request) -> OperationResult:
    active_agent: Agent = request.app.state.agent
    queue: JobQueue = request.app.state.job_queue
    job = queue.submit("undo", {})
    done_event = queue.get_done_event(job.id)
    if done_event is None:
        raise HTTPException(status_code=500, detail="job completion event missing")
    timeout_s = active_agent.config.operation_timeout
    try:
        await asyncio.wait_for(done_event.wait(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"Operation timed out after {timeout_s}s") from exc

    completed = queue.get(job.id)
    if completed is None:
        raise HTTPException(status_code=404, detail="job not found")
    if completed.result is not None:
        return to_operation_result(completed.result)
    return OperationResult(ok=False, updated=False, summary="", error=completed.error or "job failed")


@agent_router.post("/undo", response_model=OperationResult)
async def undo_instruction(request: Request) -> OperationResult:
    return await handle_undo(request)

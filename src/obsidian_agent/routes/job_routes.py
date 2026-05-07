from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from ..interfaces import resolve_interface
from ..models import Job, JobAcceptedResponse, JobListResponse, JobResponse, JobSubmitRequest, OperationResult
from ..queue import JobQueue
from ..routes.agent_routes import DEFAULT_INTERFACE_ID, _allowed_write_paths

job_router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        operation=job.operation,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        request=job.request,
        result=(
            OperationResult(
                ok=job.result.ok,
                updated=job.result.updated,
                summary=job.result.summary,
                changed_files=job.result.changed_files,
                error=job.result.error,
                warning=job.result.warning,
            )
            if job.result is not None
            else None
        ),
        error=job.error,
    )


@job_router.post("", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_job(request: Request, payload: JobSubmitRequest) -> JobAcceptedResponse:
    queue: JobQueue = request.app.state.job_queue
    if payload.operation == "undo":
        job = queue.submit("undo", {})
        return JobAcceptedResponse(job_id=job.id, status=job.status, created_at=job.created_at)

    apply_payload = payload.payload
    if apply_payload is None:
        raise HTTPException(status_code=400, detail="payload is required for apply")
    if apply_payload.instruction is None or not apply_payload.instruction.strip():
        raise HTTPException(status_code=400, detail="instruction is required")

    interface_id = apply_payload.interface_id or DEFAULT_INTERFACE_ID
    try:
        profile = resolve_interface(interface_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    effective_current_file = apply_payload.current_file
    if effective_current_file is None and apply_payload.scope is not None:
        effective_current_file = apply_payload.scope.path

    job = queue.submit(
        "apply",
        {
            "instruction": apply_payload.instruction,
            "current_file": effective_current_file,
            "kwargs": {
                "interface_id": profile.id,
                "scope": apply_payload.scope,
                "intent": apply_payload.intent,
                "allowed_write_scope": apply_payload.allowed_write_scope,
                "allowed_tool_names": profile.allowed_tool_names(apply_payload.scope),
                "allowed_write_paths": _allowed_write_paths(apply_payload.scope),
                "profile_prompt_suffix": profile.prompt_suffix(apply_payload.scope, apply_payload.intent),
            },
        },
    )
    return JobAcceptedResponse(job_id=job.id, status=job.status, created_at=job.created_at)


@job_router.get("/{job_id}", response_model=JobResponse)
async def get_job(request: Request, job_id: str) -> JobResponse:
    queue: JobQueue = request.app.state.job_queue
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_response(job)


@job_router.get("", response_model=JobListResponse)
async def list_jobs(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> JobListResponse:
    queue: JobQueue = request.app.state.job_queue
    jobs = [_job_response(job) for job in queue.list_recent(limit=limit)]
    return JobListResponse(jobs=jobs)

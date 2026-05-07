from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .agent import Agent
from .models import Job, JobOperation

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class JobQueue:
    def __init__(self, agent: Agent, *, max_history: int = 200):
        self._agent = agent
        self._max_history = max_history
        self._pending: asyncio.Queue[str] = asyncio.Queue()
        self._jobs: dict[str, Job] = {}
        self._history: deque[str] = deque()
        self._done_events: dict[str, asyncio.Event] = {}
        self._worker_task: asyncio.Task[None] | None = None

    def submit(self, operation: JobOperation, request: dict[str, Any]) -> Job:
        job = Job(
            id=str(uuid4()),
            operation=operation,
            status="queued",
            created_at=_now_utc(),
            request=dict(request),
        )
        self._jobs[job.id] = job
        self._done_events[job.id] = asyncio.Event()
        self._append_history(job.id)
        self._pending.put_nowait(job.id)
        logger.info("queue.job_submitted", extra={"job_id": job.id, "operation": operation})
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def get_done_event(self, job_id: str) -> asyncio.Event | None:
        return self._done_events.get(job_id)

    def list_recent(self, limit: int = 50) -> list[Job]:
        capped = max(1, min(limit, self._max_history))
        ids = list(self._history)[-capped:]
        return [self._jobs[job_id] for job_id in reversed(ids) if job_id in self._jobs]

    def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker(), name="obsidian-agent-job-queue")

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None

    def _append_history(self, job_id: str) -> None:
        self._history.append(job_id)
        if len(self._history) <= self._max_history:
            return
        evicted = self._history.popleft()
        self._jobs.pop(evicted, None)
        self._done_events.pop(evicted, None)

    async def _execute(self, job: Job):
        if job.operation == "apply":
            instruction = str(job.request.get("instruction", ""))
            current_file = job.request.get("current_file")
            kwargs = dict(job.request.get("kwargs") or {})
            return await self._agent.run(instruction, current_file, **kwargs)
        if job.operation == "undo":
            return await self._agent.undo()
        msg = f"Unknown operation: {job.operation}"
        raise ValueError(msg)

    async def _worker(self) -> None:
        while True:
            job_id = await self._pending.get()
            job = self._jobs.get(job_id)
            if job is None:
                continue

            job.status = "running"
            job.started_at = _now_utc()
            logger.info("queue.job_started", extra={"job_id": job.id, "operation": job.operation})
            try:
                result = await self._execute(job)
                job.result = result
                if result.ok:
                    job.status = "succeeded"
                else:
                    job.status = "failed"
                    job.error = result.error
            except Exception as exc:  # noqa: BLE001
                job.status = "failed"
                job.error = str(exc)
            finally:
                job.finished_at = _now_utc()
                done_event = self._done_events.get(job.id)
                if done_event is not None:
                    done_event.set()
                logger.info("queue.job_finished", extra={"job_id": job.id, "status": job.status})

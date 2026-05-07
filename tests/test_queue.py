import asyncio

import pytest

from obsidian_agent.models import RunResult
from obsidian_agent.queue import JobQueue

pytestmark = pytest.mark.anyio


class StubAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run(self, instruction: str, current_file: str | None = None, **kwargs) -> RunResult:
        _ = current_file, kwargs
        self.calls.append(("apply", instruction))
        await asyncio.sleep(0.01)
        if instruction == "fail":
            return RunResult(ok=False, updated=False, summary="", error="failed apply")
        return RunResult(ok=True, updated=False, summary=f"ok:{instruction}")

    async def undo(self) -> RunResult:
        self.calls.append(("undo", ""))
        await asyncio.sleep(0.01)
        return RunResult(ok=True, updated=True, summary="undone")


async def test_queue_job_lifecycle_success() -> None:
    queue = JobQueue(StubAgent())
    queue.start()
    job = queue.submit("apply", {"instruction": "a", "current_file": None})
    event = queue.get_done_event(job.id)
    assert event is not None
    await asyncio.wait_for(event.wait(), timeout=1)
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.result is not None
    assert stored.result.ok is True
    await queue.stop()


async def test_queue_job_lifecycle_failure_result() -> None:
    queue = JobQueue(StubAgent())
    queue.start()
    job = queue.submit("apply", {"instruction": "fail", "current_file": None})
    event = queue.get_done_event(job.id)
    assert event is not None
    await asyncio.wait_for(event.wait(), timeout=1)
    stored = queue.get(job.id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == "failed apply"
    await queue.stop()


async def test_queue_fifo_single_worker() -> None:
    agent = StubAgent()
    queue = JobQueue(agent)
    queue.start()
    first = queue.submit("apply", {"instruction": "first", "current_file": None})
    second = queue.submit("apply", {"instruction": "second", "current_file": None})
    first_done = queue.get_done_event(first.id)
    second_done = queue.get_done_event(second.id)
    assert first_done is not None
    assert second_done is not None
    await asyncio.wait_for(first_done.wait(), timeout=1)
    await asyncio.wait_for(second_done.wait(), timeout=1)
    assert agent.calls[:2] == [("apply", "first"), ("apply", "second")]
    await queue.stop()


async def test_queue_history_rotation_evicts_old_jobs() -> None:
    queue = JobQueue(StubAgent(), max_history=2)
    queue.start()
    a = queue.submit("apply", {"instruction": "a", "current_file": None})
    b = queue.submit("apply", {"instruction": "b", "current_file": None})
    c = queue.submit("apply", {"instruction": "c", "current_file": None})
    for job in (a, b, c):
        event = queue.get_done_event(job.id)
        if event is not None:
            await asyncio.wait_for(event.wait(), timeout=1)
    assert queue.get(a.id) is None
    recent = queue.list_recent(limit=10)
    assert [job.id for job in recent] == [c.id, b.id]
    await queue.stop()

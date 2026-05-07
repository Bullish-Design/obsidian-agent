# QUEUE REFACTOR SPEC

## 1) Job Model

### Job Entity

```python
@dataclass
class Job:
    id: str                           # uuid4, assigned at creation
    operation: str                    # "apply" | "undo"
    status: str                       # "queued" | "running" | "succeeded" | "failed"
    created_at: datetime              # UTC, set at creation
    started_at: datetime | None       # UTC, set when worker picks up
    finished_at: datetime | None      # UTC, set on terminal state
    request: dict                     # original request payload
    result: RunResult | None          # reuses existing RunResult model
    error: str | None                 # error message if failed
```

### Lifecycle Transitions
- `queued → running` (worker picks up job)
- `running → succeeded` (Agent.run/undo returns ok=True)
- `running → failed` (Agent.run/undo returns ok=False, or raises)
- Terminal states (`succeeded`, `failed`) are immutable.

Cancel is a future consideration — not included in v1. The agent's
`operation_timeout` (default 120s) already bounds execution time.

### Design Rationale: What's Excluded and Why
- **`priority`**: Queue is FIFO. No use case for reordering.
- **`attempt` / `max_attempts`**: No automatic retries. Users resubmit.
- **`request_id` / idempotency tokens**: Localhost HTTP between controlled consumers doesn't need distributed-systems dedup.
- **`concurrency_class`**: All mutations serialize. No concurrent class needed.
- **`queue_position`**: Costs recalculation on every change. Clients can derive position from the list endpoint.

## 2) Queue Implementation

### JobQueue Class

```
┌──────────────────────────────────────┐
│ JobQueue                             │
│                                      │
│ _pending: asyncio.Queue[str]         │
│ _jobs: dict[str, Job]                │
│ _history: deque[str] (maxlen=200)    │
│ _worker_task: asyncio.Task           │
│ _agent: Agent                        │
│                                      │
│ submit(operation, request) → Job     │
│ get(job_id) → Job | None             │
│ list_recent(limit=50) → list[Job]    │
│ start() → None                       │
│ stop() → None                        │
└──────────────────────────────────────┘
```

### Single-Writer Rules
- Exactly one job may be `running` at a time.
- Jobs execute in FIFO order by `created_at`.
- The worker is a single `asyncio.Task` that loops: pull from queue, set running, call agent, set terminal state.

### Worker Loop (Pseudocode)
```python
async def _worker(self):
    while True:
        job_id = await self._pending.get()
        job = self._jobs[job_id]
        job.status = "running"
        job.started_at = now()
        try:
            result = await self._execute(job)
            job.status = "succeeded" if result.ok else "failed"
            job.result = result
            if not result.ok:
                job.error = result.error
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = now()
```

### Memory Management
- `_jobs` dict holds all active + recent jobs.
- `_history` deque (maxlen=200) tracks job IDs for the list endpoint.
- When history rotates out, the corresponding `_jobs` entry is deleted.
- This bounds memory without needing retention policies or background compaction.

### Startup / Shutdown
- `start()`: creates the worker task. Called during FastAPI lifespan startup.
- `stop()`: cancels the worker task. Any `running` job is left in its current state (the process is shutting down anyway).
- No restart recovery needed — in-memory state is ephemeral by design. If persistence becomes necessary later, a single SQLite table is the obvious upgrade path.

## 3) API Endpoints

### New Async Endpoints

**`POST /v1/jobs`**
- Request: `{ operation: "apply" | "undo", payload: { ...ApplyRequest fields... } }`
- Response: `202 Accepted` with `{ job_id, status: "queued", created_at }`

**`GET /v1/jobs/{job_id}`**
- Response: `200` with full job object, or `404` if not found / rotated out.

**`GET /v1/jobs`**
- Query: `limit` (default 50, max 200)
- Response: `200` with `{ jobs: [...] }` sorted newest-first.

### Integration with Existing Routes

The existing `POST /api/agent/apply` and `POST /api/agent/undo` handlers change from:
```python
# Before: call agent directly, reject if busy
result = await agent.run(instruction, ...)
```
To:
```python
# After: submit to queue, wait for completion
job = queue.submit("apply", payload)
await job_done_event.wait(timeout=config.operation_timeout)
return to_operation_result(job.result)
```

This preserves the synchronous request/response contract for existing
consumers while they migrate to the async job endpoints. No separate
compatibility layer, deprecation headers, or timeout-to-202 fallback
needed — the existing endpoints simply block on the job completing,
same as they block on `Agent.run()` today.

Once forge-overlay and forge migrate to `POST /v1/jobs`, the old
endpoints can be removed in a single coordinated change.

## 4) VCS Side-Effect Handling

No changes needed. The existing behavior in `agent.py:278-313` is already correct:
- Mutation success + commit failure → `RunResult(ok=True, warning="Commit failed: ...")`
- Mutation success + sync failure → `RunResult(ok=True, warning="Post-commit sync ...")`
- Mutation failure → `RunResult(ok=False, error="...")` regardless of VCS state.

The job model inherits this directly via `job.result = RunResult(...)`.

## 5) Error Handling

### Error Categories (for structured logging)
- `timeout`: execution exceeded `operation_timeout`
- `llm`: `ModelAPIError` from pydantic-ai
- `tool`: `UsageLimitExceeded` or tool-level exceptions
- `vcs`: `VaultBusyError`, `VCSError` from obsidian-ops
- `validation`: bad request before execution
- `internal`: unexpected exceptions in queue/worker

These map directly to existing exception types in `agent.py:252-273`.
No new error taxonomy is needed — just consistent tagging in log entries.

### Structured Logging
Evolve existing log events (not a parallel system):
- `agent.run_start` / `agent.run_complete` → add `job_id` field
- `agent.busy_rejected` → removed (queue replaces rejection)
- New: `queue.job_submitted`, `queue.job_started`, `queue.job_finished`

## 6) Implementation Phases

### Phase 1: Queue Core (~200 lines)
- Add `Job` dataclass to `models.py`.
- Add `JobQueue` class in new `queue.py` module.
- Unit tests: submit, FIFO ordering, concurrent submit queues instead of rejects, worker lifecycle.

### Phase 2: Wire Up + API (~100 lines)
- Add `/v1/jobs` routes in new `routes/job_routes.py`.
- Modify `agent_routes.py` to submit through queue.
- Remove `Agent._busy` / `_acquire_busy` / `_release_busy`.
- Integration tests: submit via HTTP, poll status, list jobs.

### Phase 3: Consumer Migration (forge-overlay + forge)
- Update forge-overlay to proxy `/v1/jobs` endpoints.
- Update forge UI to use job submission + polling.
- Remove legacy `/api/apply`, `/api/undo` endpoints.

## 7) Test Plan

### Unit Tests
- Job lifecycle transitions (queued → running → succeeded/failed).
- FIFO ordering with multiple submitted jobs.
- Concurrent submits queue instead of raising BusyError.
- History rotation evicts old jobs from memory.
- Worker stops cleanly on shutdown.

### Integration Tests
- `POST /v1/jobs` returns 202 with job_id.
- `GET /v1/jobs/{job_id}` returns correct status progression.
- `GET /v1/jobs` returns recent jobs newest-first.
- Existing `/api/agent/apply` still works (blocks until job completes).
- Job with failing agent run has status `failed` with error message.

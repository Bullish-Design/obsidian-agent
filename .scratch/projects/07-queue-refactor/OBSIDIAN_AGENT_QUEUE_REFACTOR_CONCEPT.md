# QUEUE REFACTOR CONCEPT

## Problem
`Agent._busy` (`agent.py:155-162`) is a boolean flag that rejects concurrent requests with HTTP 409 instead of queuing them. Callers get "Another operation is already running" and must retry manually.

## Solution
Replace the flag with an in-memory async job queue. Incoming mutation requests become jobs that wait their turn. A single worker pulls jobs and calls `Agent.run()` / `Agent.undo()` sequentially.

## Responsibility Boundary
- `obsidian-agent`: owns the queue, job lifecycle, and ordering.
- `forge-overlay`: proxies job endpoints unchanged.
- `forge`: consumes job APIs for status display.

## What Changes
1. New `Job` dataclass and `JobQueue` class (~200-300 lines total).
2. Three new endpoints: submit, status, list.
3. Existing route handlers submit to queue instead of calling Agent directly.
4. `Agent._busy` flag removed entirely — the queue replaces it.

## What Stays the Same
- `Agent.run()` and `Agent.undo()` core logic unchanged.
- `RunResult` model unchanged.
- VCS commit/sync behavior unchanged.
- All vault read/write/structure endpoints unchanged (they don't go through the agent).

## Non-Goals
- Persistent storage (SQLite, files). In-memory is sufficient.
- Retry infrastructure. Failed jobs stay failed; users resubmit.
- Priority ordering. FIFO is correct for this use case.
- Idempotency tokens. Localhost communication between controlled consumers.
- UI rendering concerns.

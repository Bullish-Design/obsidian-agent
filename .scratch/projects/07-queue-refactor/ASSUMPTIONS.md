# ASSUMPTIONS

## Audience
- Primary implementer: maintainer of `obsidian-agent`.
- Consumers: `forge-overlay` (proxy) and `forge` (UI), both under same maintainer control.

## Product Assumptions
- Mutation operations must be serialized (single-writer). This is the existing invariant.
- The problem is that concurrent requests are rejected (409) instead of queued.
- Queue introspection (job status, recent history) is useful for debugging and UI feedback.

## Technical Assumptions
- Single-instance, single-user deployment. No multi-tenant or distributed concerns.
- In-memory state is sufficient. Job data does not need to survive process restarts.
- The `asyncio` event loop is the correct concurrency primitive (FastAPI is already async).
- `Agent.run()` and `Agent.undo()` remain the execution boundary — the queue orchestrates, not replaces.

## Constraints
- Total new code should be ~200-300 lines. The queue must not outweigh the rest of the codebase.
- No new external dependencies (SQLite, Redis, etc.). Standard library only.
- Preserve `RunResult` semantics — all existing consumers depend on its shape.
- VCS side-effect behavior (`agent.py:278-313`) is already correct and unchanged.

## Invariants
- Exactly one mutation job running at a time.
- Jobs execute in FIFO order.
- Terminal states are immutable.
- Memory is bounded (deque with maxlen for history).

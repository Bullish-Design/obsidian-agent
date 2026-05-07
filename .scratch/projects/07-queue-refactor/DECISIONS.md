# DECISIONS

## Decision Log

### D-001: In-memory queue, not SQLite
- Status: accepted
- Date: 2026-05-06
- Choice: `asyncio.Queue` + `dict` + `deque` for job state. No database.
- Rationale:
  - The codebase has zero persistence layer today. Adding SQLite with tables, migrations, retention, and compaction is disproportionate to the problem.
  - Jobs are short-lived (bounded by `operation_timeout`, default 120s). Surviving restarts is not a requirement — restarting the process clears the queue, and that's fine.
  - If durable persistence becomes necessary later, upgrading to a single SQLite table is straightforward.
- What was rejected: SQLite with 3 tables (`jobs`, `job_events`, `queue_state`), append-only audit logs, worker heartbeats, background compaction, 14-day retention policies.

### D-002: Minimal job model — no speculative fields
- Status: accepted
- Date: 2026-05-06
- Choice: Job has `id`, `operation`, `status`, timestamps, `request`, `result`, `error`. Nothing else.
- Rationale:
  - `priority` is dead weight when the queue is FIFO.
  - `attempt`/`max_attempts` are dead weight when retries are off.
  - `request_id` idempotency tokens solve a distributed-systems problem that doesn't exist on localhost.
  - `concurrency_class` has one value in practice.
  - Fields are easy to add later; premature fields are hard to remove.

### D-003: Immutable terminal states
- Status: accepted
- Date: 2026-05-06
- Choice: `succeeded` and `failed` are terminal and immutable.
- Rationale: Race-free status interpretation. Once a job is done, its state never changes.

### D-004: Single-writer FIFO via asyncio worker task
- Status: accepted
- Date: 2026-05-06
- Choice: One `asyncio.Task` pulls jobs sequentially. Replaces `Agent._busy` flag entirely.
- Rationale: Exactly matches the existing serialization requirement. The `_busy` flag was already enforcing single-writer; the queue just adds "wait" instead of "reject."

### D-005: Existing endpoints block on job completion (no compatibility bridge)
- Status: accepted
- Date: 2026-05-06
- Choice: `POST /api/agent/apply` submits a job and `await`s its completion, same as it currently `await`s `Agent.run()`.
- Rationale:
  - No behavioral change for current consumers.
  - No need for `X-Async-Job-Id` headers, timeout-to-202 fallback, or deprecation windows.
  - forge-overlay and forge can migrate to async endpoints at their own pace.
- What was rejected: Formal sync-to-async compatibility bridge with `SYNC_WAIT_TIMEOUT_MS`, `Retry-After` headers, and multi-release deprecation program.

### D-006: Coordinated consumer migration, not staged rollout
- Status: accepted
- Date: 2026-05-06
- Choice: Update forge-overlay and forge in a single coordinated change. No feature flags, no staged rollout percentages.
- Rationale: Three repos under the same maintainer. A coordinated cut is simpler and faster than maintaining parallel code paths behind feature flags.

### D-007: Codify existing VCS side-effect behavior (no new design)
- Status: accepted
- Date: 2026-05-06
- Choice: The job result directly wraps `RunResult`, which already separates mutation success from VCS warnings.
- Rationale: `agent.py:278-313` already implements the correct behavior. The queue doesn't need a new side-effect model — it inherits `RunResult` as-is.

### D-008: Evolve existing logging, don't create parallel telemetry
- Status: accepted
- Date: 2026-05-06
- Choice: Add `job_id` to existing structured log events. Add minimal new events for queue lifecycle.
- Rationale: The codebase already has good structured logging (`agent.run_start`, `agent.run_complete`, `agent.commit_success`, etc.). Adding a separate telemetry layer with mandatory fields and formal event schemas is over-engineering.

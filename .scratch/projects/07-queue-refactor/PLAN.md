# PLAN

## Objective
Replace the ad-hoc `Agent._busy` boolean flag with a proper in-memory job queue so mutation operations are queued instead of rejected, and job status is inspectable via simple API endpoints.

## Ordered Steps
1. Define minimal job model and lifecycle states.
2. Implement in-memory job queue with single async worker.
3. Wire existing routes to submit jobs instead of calling Agent directly.
4. Add three new job API endpoints (submit, status, list).
5. Update forge-overlay and forge to consume job APIs.
6. Remove legacy sync endpoints and old busy-flag machinery.

## Acceptance Criteria
- Concurrent mutation requests are queued (not 409-rejected).
- Exactly one mutation runs at a time (single-writer FIFO).
- Job status is retrievable by `job_id`.
- Recent job history is listable.
- Existing `RunResult` semantics are preserved in job outcomes.
- Total new code is ~200-300 lines.

## Deliverables
- `SPEC.md`: implementation-ready specification.
- Project tracking docs kept current.

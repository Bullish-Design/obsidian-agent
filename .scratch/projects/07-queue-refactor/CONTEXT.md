# CONTEXT

## Current State
- Active project: `07-queue-refactor`.
- Planning artifacts rewritten after architecture review.
- Spec is implementation-ready.

## What Changed in This Review
- Original spec proposed SQLite (3 tables), enterprise job model (15+ fields), 6 implementation phases, formal deprecation program, feature flags, and staged rollout.
- Review found this was ~10x over-engineered for a 1,700-line single-instance local tool.
- Rewrote all planning docs to reflect a minimal in-memory queue approach.

## Key Simplifications
1. **No database** — `asyncio.Queue` + `dict` + `deque` replaces proposed SQLite with 3 tables.
2. **Minimal job model** — 9 fields instead of 15+. No priority, retries, idempotency tokens, concurrency classes.
3. **No compatibility bridge** — existing endpoints block on job completion (behavioral parity). Consumers migrate via coordinated change.
4. **3 phases** instead of 6. No feature flags, no staged rollout.
5. **~200-300 lines** of new code instead of a major infrastructure addition.

## Key Codebase Facts
- Concurrency guard: `Agent._busy` flag at `agent.py:39, 155-162`.
- Mutation entry points: `Agent.run()` (`agent.py:164`) and `Agent.undo()` (`agent.py:323`).
- VCS side-effects: `agent.py:278-313` (commit + optional post-commit sync).
- Existing error types: `BusyError`, `VaultBusyError` (409), `VCSError` (424), `ModelAPIError`, `UsageLimitExceeded`.
- Consumers: `forge-overlay` (proxy layer), `forge` (UI).

## Next Action
- Start implementation from `SPEC.md` Phase 1.

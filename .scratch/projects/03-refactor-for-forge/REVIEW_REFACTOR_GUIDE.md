# obsidian-agent Review Refactor Guide

## Purpose

This document is an implementation guide for hardening `obsidian-agent` before Forge is switched over to the extracted Python backend.

It is written for a brand new intern. Follow the steps in order. Do not skip tests. Do not add fallback behavior that hides architectural problems. If an upstream change in `obsidian-ops` is required and not yet available, stop and resolve that dependency first.

Repository under implementation:

- `/home/andrew/Documents/Projects/obsidian-agent`

Related context documents:

- `/home/andrew/Documents/Projects/forge/.scratch/projects/06-forge-architecture-refactor/DEPENDENCY_LIBRARY_REVIEW.md`
- `/home/andrew/Documents/Projects/forge/.scratch/projects/06-forge-architecture-refactor/obsidian-agent/CONCEPT.md`
- `/home/andrew/Documents/Projects/forge/.scratch/projects/06-forge-architecture-refactor/obsidian-agent/SPEC.md`
- `/home/andrew/Documents/Projects/forge/.scratch/projects/06-forge-architecture-refactor/obsidian-ops/REVIEW_REFACTOR_GUIDE.md`

## Mission

Make `obsidian-agent` a stable backend service that:

- owns only agent orchestration, tool dispatch, API models, and request handling,
- delegates all vault and VCS mechanics to `obsidian-ops`,
- has a reproducible and explicit dependency strategy for `obsidian-ops`,
- enforces a clear `current_file` integration contract,
- has enough logging and tests to support operational debugging after Forge starts proxying to it.

## Non-Goals

Do not add any of the following to this repo:

- direct filesystem operations that bypass `obsidian-ops`
- raw `jj` subprocess management in the agent layer
- URL-to-file resolution for Forge page paths
- Forge-specific reverse proxy logic
- UI mode registry logic that is not yet part of the current API contract

## Required Coordination With `obsidian-ops`

You cannot complete all of this work in isolation.

Before Step 2 below is implemented, `obsidian-ops` must provide a stable higher-level undo API that encapsulates the full VCS undo lifecycle. Read the `obsidian-ops` refactor guide first and confirm that the required method exists before changing `Agent.undo()`.

## Definition Of Done

This work is done only when all of the following are true:

1. `obsidian-agent` no longer shells out to `jj` directly.
2. the dependency strategy for `obsidian-ops` is explicit, reproducible, and documented.
3. the `/api/apply` contract around `current_file` is explicit and validated.
4. the tool layer is aligned with the stable `obsidian-ops` API.
5. logging is sufficient to diagnose busy, timeout, commit, and undo failures.
6. the full test suite passes in the default development environment.

## Current Repository Map

Primary implementation files:

- `pyproject.toml`
- `devenv.nix`
- `src/obsidian_agent/__main__.py`
- `src/obsidian_agent/agent.py`
- `src/obsidian_agent/app.py`
- `src/obsidian_agent/config.py`
- `src/obsidian_agent/models.py`
- `src/obsidian_agent/prompt.py`
- `src/obsidian_agent/tools.py`
- `src/obsidian_agent/demo.py`

Primary test files:

- `tests/conftest.py`
- `tests/test_agent.py`
- `tests/test_tools.py`
- `tests/test_app.py`
- `tests/test_config.py`
- `tests/test_integration.py`
- `tests/test_models.py`
- `tests/test_prompt.py`
- `tests/test_demo.py`
- `tests/support/vault_fs.py`

Current known facts from the dependency review:

- the default repo test run already passes in the current dev environment,
- `obsidian-agent` still invokes `subprocess.run([jj, restore, --from, @-])` directly during undo,
- `pyproject.toml` currently depends on `obsidian-ops` through a Git-pinned direct reference,
- the service expects `current_file`, not web URLs.

## Working Rules

1. Keep the HTTP API stable unless this guide explicitly instructs otherwise.
2. Do not mask architectural leaks with temporary fallbacks.
3. Keep all vault and VCS behavior inside `obsidian-ops`.
4. Update tests in the same step as behavior changes.
5. Preserve the artifact-producing test workflow under `tests/artifacts/`.
6. If a dependency strategy change affects local setup, update docs immediately.

## Step 0: Establish Baseline

### Objective

Capture the current behavior of the service before changing anything.

### Tasks

1. Create a working branch in `obsidian-agent`.
2. Run the full suite and note timing.
3. Read the current `pyproject.toml`, `devenv.nix`, `agent.py`, `app.py`, and `tests/test_integration.py`.
4. Record the current API behavior for:
   - `POST /api/apply`
   - `POST /api/undo`
   - `GET /api/health`

### Commands

```bash
devenv shell -- pytest -q
```

### Expected Baseline

- the suite should currently pass,
- undo currently mixes `vault.undo()` with a direct `jj restore` subprocess call in the agent layer.

### Acceptance Criteria

- baseline full-suite result is recorded,
- current API shapes are noted before changes begin.

## Step 1: Confirm And Document The Boundary With `obsidian-ops`

### Why This Is First

If the repo boundary is not explicit, later changes will drift back toward the old Forge-internal architecture.

### Files Likely To Change

- project docs if present
- `src/obsidian_agent/agent.py`
- `src/obsidian_agent/app.py`
- `src/obsidian_agent/models.py`

### Implementation Tasks

1. Write down the boundary rule in developer-facing docs or comments:
   - `obsidian-agent` may call `obsidian_ops.Vault`,
   - `obsidian-agent` may not directly manage raw filesystem or raw `jj` subprocess logic.
2. Review current code paths for anything that violates that rule.
3. Confirm that the only active architectural leak is the undo restore subprocess path.

### Validation

This is mostly a code-reading and documentation step. Re-run a quick subset after any small doc or comment changes:

```bash
devenv shell -- pytest -q tests/test_agent.py tests/test_app.py
```

### Acceptance Criteria

- the boundary rule is explicit in repo-facing documentation or code comments,
- you have identified exactly which parts of the code violate it.

## Step 2: Remove Direct `jj restore` From The Agent Layer

### Why This Matters

This is the most important architecture fix in this repo.

The current undo path violates the intended dependency split by invoking Jujutsu directly from `obsidian-agent`. That logic must move behind `obsidian-ops`.

### Precondition

Do not start this step until `obsidian-ops` has a stable higher-level undo API that performs the full undo lifecycle.

### Files Likely To Change

- `src/obsidian_agent/agent.py`
- `tests/test_agent.py`
- `tests/test_integration.py`
- possibly `src/obsidian_agent/models.py`

### Implementation Tasks

1. Replace the direct subprocess restore logic inside `Agent.undo()` with a single call into the new `obsidian-ops` API.
2. Preserve busy-lock behavior exactly.
3. Preserve user-visible `RunResult` behavior unless a deliberate API change is approved.
4. If the lower layer can now produce warning information, surface it consistently.
5. Remove unused imports and dead code after the change.

### Required Tests To Add Or Update

- unit test for successful undo through the new `obsidian-ops` method
- unit test for undo failure path
- integration test that confirms a real JJ-backed modification is restored through the new flow
- any existing tests that assert direct subprocess behavior must be rewritten to assert the new boundary instead

### Validation

Run:

```bash
devenv shell -- pytest -q tests/test_agent.py
devenv shell -- pytest -q tests/test_integration.py
devenv shell -- pytest -q
```

### Acceptance Criteria

- no direct `subprocess.run` call remains in `agent.py` for undo behavior,
- undo still works end-to-end with a real JJ repo,
- busy and failure semantics do not regress.

### Stop Conditions

Stop if `obsidian-ops` does not yet expose the required high-level undo method. Do not implement an agent-side temporary fallback.

## Step 3: Replace The Git-Pinned `obsidian-ops` Dependency With A Stable Strategy

### Why This Matters

The current direct Git pin is acceptable for bootstrapping but weak for long-term reproducibility and local multi-repo development.

### Files Likely To Change

- `pyproject.toml`
- `devenv.nix`
- repo documentation if present

### Decision You Must Make

Pick one dependency strategy and implement it consistently.

Acceptable options:

1. published package version range for normal installs,
2. documented editable/workspace install for local development,
3. a hybrid of published versions for CI and local editable installs for active development.

Recommended approach:

- move toward a stable semver dependency for normal use,
- document the local editable workflow for working across both repos during active refactoring.

### Implementation Tasks

1. Update `pyproject.toml` to remove the Git-pinned direct reference.
2. Remove `allow-direct-references` if it is no longer needed.
3. Update local setup docs so a new contributor can install both repos without improvising.
4. Make sure the `devenv` path still supports running the full suite.

### Validation

Run:

```bash
devenv shell -- pytest -q
```

Also perform a clean install path using the documented setup approach.

### Acceptance Criteria

- dependency resolution is reproducible and documented,
- the repo no longer depends on a one-off Git pin for normal development,
- the test suite still passes after the dependency change.

### Stop Conditions

Stop if the team has not yet decided whether `obsidian-ops` will be published before Forge integration. In that case, document both candidate strategies and get a decision.

## Step 4: Align Python Version Requirements Deliberately

### Why This Matters

`obsidian-agent` and `obsidian-ops` currently advertise different Python version floors. That may be intentional, but it should not remain accidental.

### Files Likely To Change

- `pyproject.toml`
- docs if version requirements are explained there

### Implementation Tasks

1. Check whether `obsidian-agent` actually depends on any Python 3.13-only feature.
2. If not, align the version floor with `obsidian-ops`.
3. If yes, document clearly why the higher floor is required.
4. Make sure the chosen runtime version is consistent with `devenv.nix`.

### Validation

Run:

```bash
devenv shell -- pytest -q
```

### Acceptance Criteria

- version requirements are intentional and documented,
- there is no unexplained mismatch between the two dependency repos.

## Step 5: Harden The `current_file` API Contract

### Why This Matters

The service boundary is clean only if the caller contract is explicit. `obsidian-agent` should consume vault-relative `current_file` values and should not silently accept ambiguous or invalid inputs.

### Files Likely To Change

- `src/obsidian_agent/models.py`
- `src/obsidian_agent/app.py`
- `src/obsidian_agent/prompt.py`
- `tests/test_app.py`
- `tests/test_prompt.py`
- docs if present

### Required Behavior

1. `current_file` remains optional.
2. if provided, it must be a non-empty vault-relative path string.
3. the service does not attempt URL resolution.
4. invalid payloads should fail deterministically.

### Recommended Implementation Detail

Tighten request validation rather than silently ignoring malformed `current_file` values. Do not accept `current_url_path` as an alternate field in this service.

### Implementation Tasks

1. Add validation rules to `ApplyRequest` or an equivalent request model.
2. Keep prompt construction behavior stable for valid inputs.
3. Add tests for invalid `current_file` values.
4. Document explicitly that Forge or another caller must resolve URLs before calling this service.

### Validation

Run:

```bash
devenv shell -- pytest -q tests/test_prompt.py tests/test_app.py tests/test_models.py
```

### Acceptance Criteria

- invalid `current_file` payloads produce deterministic, documented behavior,
- valid `current_file` values still flow into prompts and tool context correctly,
- the service does not acquire URL resolution responsibilities.

## Step 6: Align Tool Surface With Stable `obsidian-ops` APIs

### Why This Matters

The agent's tool layer should reflect the stable lower-layer API, not an arbitrary subset frozen from the first extraction.

### Files Likely To Change

- `src/obsidian_agent/tools.py`
- `src/obsidian_agent/agent.py`
- `tests/test_tools.py`
- `tests/test_agent.py`

### Preconditions

Only do this step after the `obsidian-ops` API is stable for the methods you plan to expose.

### Recommended Scope

Consider adding wrappers for stable operations such as:

- `set_frontmatter`
- `delete_frontmatter_field`

Do not add new tools just because they are possible. Only expose operations that are:

- stable,
- tested in `obsidian-ops`,
- actually useful for the current architecture.

### Implementation Tasks

1. Add any approved new tool wrappers.
2. Ensure mutating tools update `changed_files` consistently.
3. Keep recoverable tool-failure formatting stable as `Error: ...`.
4. Update registration and tool tests.

### Validation

Run:

```bash
devenv shell -- pytest -q tests/test_tools.py
devenv shell -- pytest -q tests/test_agent.py
devenv shell -- pytest -q
```

### Acceptance Criteria

- tool list matches the intended agent contract,
- changed-file tracking remains correct for all mutating tools,
- no existing tool behavior regresses.

## Step 7: Add Operational Logging Without Changing The API Contract

### Why This Matters

Once Forge proxies requests to this service, failures need to be diagnosable. The old Go implementation had stronger structured logging. The Python service needs enough logging to support production debugging.

### Files Likely To Change

- `src/obsidian_agent/agent.py`
- `src/obsidian_agent/app.py`
- optionally `src/obsidian_agent/config.py`

### Required Logging Points

Add structured logs around:

- run start and completion
- timeout path
- busy rejection path
- commit success and failure
- undo success and failure
- model-resolution behavior when using OpenAI-compatible base URLs

### Constraints

- do not log secrets,
- do not log raw API keys,
- do not dump large prompt payloads into logs by default,
- do not change HTTP response shapes just because logging is added.

### Validation

Run:

```bash
devenv shell -- pytest -q
```

If you add tests around warning/error paths, run those targeted tests as well.

### Acceptance Criteria

- logs are sufficient to diagnose major operational failures,
- no API responses regress,
- no sensitive data is leaked in logs.

## Step 8: Preserve Test Artifact Workflow

### Why This Matters

This repo already has a better-than-average test artifact strategy. Do not break it.

### Files To Inspect

- `tests/support/vault_fs.py`
- `tests/conftest.py`
- `tests/artifacts/`

### Tasks

1. Confirm tests still create before/work/after snapshots.
2. Confirm manifest generation remains valid.
3. If any path handling changes affect this workflow, update tests carefully.

### Validation

Run:

```bash
devenv shell -- pytest -q tests/test_tools.py tests/test_app.py tests/test_integration.py
```

Then inspect `tests/artifacts/` and generated manifests manually.

### Acceptance Criteria

- artifact generation still works,
- changed-file manifests remain meaningful,
- no new test relies on invisible or machine-specific state unless clearly documented.

## Step 9: Final Documentation Pass

### Why This Matters

The repo must be understandable without tribal knowledge if an intern is expected to implement and maintain it.

### Files Likely To Change

- README or contributor docs if present
- any architecture notes relevant to local setup

### Required Documentation Topics

1. What `obsidian-agent` owns
2. What `obsidian-agent` does not own
3. Local setup and dependency strategy for `obsidian-ops`
4. Runtime environment variables
5. API contract for `/api/apply`, `/api/undo`, `/api/health`
6. The meaning of `current_file`
7. Boundary statement that VCS and vault mechanics live in `obsidian-ops`

### Acceptance Criteria

- a new developer can set up the repo, run tests, and explain the architectural boundary after reading the docs.

## Step 10: Final Validation Matrix

Run all of the following before considering the work done:

```bash
devenv shell -- pytest -q tests/test_config.py tests/test_models.py tests/test_prompt.py tests/test_app.py
devenv shell -- pytest -q tests/test_agent.py tests/test_tools.py
devenv shell -- pytest -q tests/test_integration.py
devenv shell -- pytest -q
```

If linting/formatting is already in use, also run:

```bash
devenv shell -- ruff check src tests
devenv shell -- ruff format --check src tests
```

## Recommended Commit Sequence

Use this order so regressions are attributable:

1. boundary clarification and docs
2. agent undo/VCS boundary fix
3. dependency strategy cleanup
4. Python version alignment
5. `current_file` validation
6. tool-surface alignment
7. logging
8. final docs and validation

Do not collapse this into one large commit.

## Critical Cautions

1. Do not implement a temporary dual undo path. If `obsidian-ops` is not ready, stop and wait.
2. Do not reintroduce raw `jj` subprocesses anywhere in the agent layer.
3. Do not silently accept unresolved URL paths in place of `current_file`.
4. Do not broaden exception handling so much that `VaultBusyError` semantics disappear.
5. Do not let dependency strategy changes break the working local test environment.
6. Do not add tool wrappers for unstable lower-layer APIs.
7. Do not change HTTP response shapes unless the guide explicitly requires it.

## Handoff Checklist

Before handing this work back for review, confirm all boxes are true:

- [ ] `obsidian-agent` no longer shells out to `jj`
- [ ] dependency strategy for `obsidian-ops` is stable and documented
- [ ] Python version floor is intentional and documented
- [ ] `current_file` contract is validated and explicit
- [ ] tool surface matches stable `obsidian-ops` APIs
- [ ] logging covers major operational paths without leaking secrets
- [ ] artifact-based test workflow still works
- [ ] full validation matrix passes

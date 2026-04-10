# obsidian-agent Review Refactoring Guide

Date: 2026-04-08

## Purpose

This guide translates the findings in [CODE_REVIEW.md](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/02-analysis-and-code-review/CODE_REVIEW.md) into a concrete implementation plan.

The goal is to bring the library back into alignment with the intended contract described in:

- [OBSIDIAN_AGENT_README.md](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md)
- [OBSIDIAN_AGENT_IMPLEMENTATION_GUIDE_V2.md](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_IMPLEMENTATION_GUIDE_V2.md)

This is a refactoring and correctness-hardening plan, not a rewrite.

## Ground Rules

- Preserve the current public package structure under `src/obsidian_agent/`.
- Do not broaden the library surface area unless needed for correctness.
- Fix tests that encode incorrect behavior before relying on them as protection.
- Keep behavior changes explicit in docs and tests.
- Prefer narrow exception handling and explicit contracts over “best effort” recovery.

## Recommended Order

Implement changes in this order:

1. Fix exception boundaries and concurrency semantics.
2. Fix timeout semantics.
3. Fix local model resolution behavior.
4. Align HTTP contract and docs.
5. Harden configuration validation.
6. Replace deprecated API usage and tighten dependency constraints.
7. Expand and correct the test suite.

That order matters. The first four items affect system behavior. The remaining items stabilize and defend it.

## Pre-Work

Before changing code:

1. Read [CODE_REVIEW.md](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/02-analysis-and-code-review/CODE_REVIEW.md).
2. Re-read these implementation files:
   - [agent.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py)
   - [tools.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/tools.py)
   - [app.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/app.py)
   - [config.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/config.py)
   - [models.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/models.py)
3. Run the current baseline:

```bash
devenv shell -- pytest tests/ -q
devenv shell -- pytest tests/ --cov=obsidian_agent --cov-report=term-missing -q
```

4. Record the baseline in the PR description or work log:
   - `79 passed`
   - coverage `94%`
   - deprecation warning for `OpenAIModel`

## Phase 1: Correct Exception Boundaries

### Objective

Make the agent distinguish between:

- expected vault operation errors
- true concurrency failures
- model/provider failures
- internal programming errors

### Files

- [src/obsidian_agent/tools.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/tools.py)
- [src/obsidian_agent/agent.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py)
- [tests/test_tools.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_tools.py)
- [tests/test_agent.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_agent.py)
- [tests/test_app.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_app.py)

### Step-by-step

1. In `tools.py`, import `VaultError` from `obsidian_ops.errors`.
2. Replace every `except Exception as exc:` in tool functions with `except VaultError as exc:`.
3. Keep `except BusyError: raise` above the `VaultError` catch.
4. Do not add fallback `except Exception` in the tool wrappers.
5. Review all 11 tool functions and make them consistent.
6. In `agent.py`, import the vault-layer `BusyError` with an explicit alias, for example `VaultBusyError`.
7. Add an explicit `except VaultBusyError: raise` branch in `_run_impl()` before the broad exception block.
8. Keep `ModelAPIError` and `UsageLimitExceeded` as structured user-facing failures.
9. Reconsider the last `except Exception as exc:` block:
   - Minimum acceptable fix: keep it, but only after `VaultBusyError` is re-raised.
   - Better fix: let unexpected exceptions propagate so they fail loudly during development and testing.
10. If you keep the broad catch, document why, and make sure it does not swallow concurrency errors.

### Tests to update

1. In `tests/test_tools.py`, remove or rewrite the parametrized test that expects generic `RuntimeError("boom")` to become `"Error: boom"`.
2. Replace it with:
   - one parametrized test for `VaultError` subclasses returning `"Error: ..."`
   - one test showing generic `RuntimeError` propagates
3. Add an agent test where a tool hits vault `BusyError` and `Agent.run()` raises `BusyError`.
4. Add an API test where the underlying tool path raises vault `BusyError` and `/api/apply` returns 409.

### Acceptance criteria

- Generic unexpected tool exceptions are not converted into normal tool results.
- True vault `BusyError` reaches the HTTP layer as a 409 path.
- Existing happy-path behavior is unchanged.

## Phase 2: Fix Timeout Semantics

### Objective

Make timeout behavior consistent between the library and the documented contract.

### Decision required

Choose one of these two models:

1. `operation_timeout` is part of the library contract.
2. `operation_timeout` is HTTP-only infrastructure behavior.

The grounding docs currently imply option 1. Unless there is a strong reason not to, implement option 1.

### Files

- [src/obsidian_agent/agent.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py)
- [src/obsidian_agent/app.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/app.py)
- [tests/test_agent.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_agent.py)
- [tests/test_app.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_app.py)
- docs under `.scratch/projects/01-obsidian-agent-refactor/` if contract changes

### Step-by-step if timeout is part of the library contract

1. In `Agent.run()`, wrap the call to `_run_impl()` in `asyncio.wait_for(...)` using `self.config.operation_timeout`.
2. Catch `asyncio.TimeoutError` in `run()` and return:
   - `ok=False`
   - `updated=False`
   - `summary=""`
   - `error=f"Operation timed out after {self.config.operation_timeout}s"`
3. Preserve the busy-lock release in `finally`.
4. In `app.py`, decide whether to keep the endpoint-level timeout wrapper:
   - simplest approach: keep it as a second guard, but ensure the returned error string matches the library behavior
   - cleaner approach: remove duplicate timeout logic and trust `Agent.run()`
5. If you keep both layers, make sure they cannot produce conflicting messages or double-handle the result.

### Step-by-step if timeout is HTTP-only

1. Keep timeout handling in `app.py`.
2. Update the grounding docs and public README language so they stop implying a library-level timeout.
3. Rename config or comments if needed so the meaning is explicit.

### Tests to add

1. Direct library test: slow model + low timeout.
2. API test: `/api/apply` returns the documented timeout result.
3. Busy-lock test after timeout: ensure `_busy` is released correctly.

### Acceptance criteria

- Timeout behavior is documented once and implemented consistently.
- Library and HTTP callers no longer have conflicting semantics.

## Phase 3: Fix Local Model Resolution

### Objective

Make model auto-selection for local OpenAI-compatible servers match the design docs.

### Files

- [src/obsidian_agent/agent.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py)
- [tests/test_agent.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_agent.py)
- [tests/test_config.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_config.py) if you move any validation there

### Step-by-step

1. Review `_is_generic_model_name()` and confirm which names should trigger auto-resolution.
2. In `_resolve_model_name_from_base_url()`:
   - keep the current “one model -> use it” behavior
   - keep the current “prefer one containing `instruct`” behavior
   - change the final fallback so it raises `ValueError` instead of returning the first model
3. Improve the error message to include:
   - the base URL
   - the list of returned model IDs
   - why the selection failed
4. Consider whether `"chat"` should also count as a preferred candidate.
   - If yes, update the docs to match.
   - If no, keep the implementation strict and aligned to the docs.

### Tests to add

1. `/models` returns one model -> selected.
2. `/models` returns multiple with one `instruct` -> `instruct` selected.
3. `/models` returns multiple without `instruct` -> `ValueError`.
4. `/models` returns no models -> `ValueError`.
5. Non-OpenAI provider with `llm_base_url` still returns the original string model.

### Acceptance criteria

- Model resolution is deterministic and documented.
- The code no longer silently picks an arbitrary first model in ambiguous cases.

## Phase 4: Align HTTP Schema, Handler Behavior, and Docs

### Objective

Remove contract drift between the request model, handler behavior, tests, and examples.

### Files

- [src/obsidian_agent/models.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/models.py)
- [src/obsidian_agent/app.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/app.py)
- [tests/test_app.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_app.py)
- [demo-vault/README.md](/home/andrew/Documents/Projects/obsidian-agent/demo-vault/README.md)
- [OBSIDIAN_AGENT_README.md](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md)

### Decision required

Choose one public API contract:

1. Missing `instruction` returns HTTP 200 with `ok=false`.
2. Missing `instruction` returns FastAPI validation error 422.

The current code implements option 2. The design docs describe option 1.

Pick one and make everything match.

### Step-by-step if choosing HTTP 200 with `ok=false`

1. Change `ApplyRequest.instruction` to `str | None = None`.
2. Keep the handler-level validation in `app.py`.
3. Reject:
   - missing `instruction`
   - empty string
   - whitespace-only string
4. Return the documented `OperationResult` with `ok=False`.
5. Update tests to assert 200 and error payload instead of 422.

### Step-by-step if choosing 422

1. Keep `ApplyRequest.instruction: str`.
2. Remove the handler branch that attempts to handle “missing instruction” as an application-level error.
3. Keep only the whitespace-only validation in the handler.
4. Update the README error semantics table to reflect 422 for missing field.
5. Update request examples and prose accordingly.

### Demo-doc fix

Regardless of which option you choose:

1. Update [demo-vault/README.md](/home/andrew/Documents/Projects/obsidian-agent/demo-vault/README.md) to use `current_file`.
2. Verify all JSON examples use the actual API shape.

### Acceptance criteria

- Request schema, handler logic, tests, and docs all describe the same behavior.
- Manual curl examples are usable as written.

## Phase 5: Harden Configuration Validation

### Objective

Tighten config validation so obviously invalid values fail early.

### Files

- [src/obsidian_agent/config.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/config.py)
- [tests/test_config.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_config.py)

### Step-by-step

1. Improve `llm_model` validation:
   - require a non-empty provider segment
   - require a non-empty model segment
   - reject values like `":"`, `"openai:"`, and `":gpt-4o"`
2. Improve `llm_base_url` validation:
   - require `http` or `https`
   - require a host when set
3. Keep the current path normalization behavior for `/v1`.
4. Consider whether `operation_timeout`, `max_iterations`, `jj_timeout`, and `llm_max_tokens` should be constrained to positive integers.
5. If yes, add field constraints or validators.

### Tests to add

1. Invalid model strings with empty provider/model.
2. Invalid base URLs.
3. Zero or negative values for timeout/iteration/token settings if you choose to reject them.
4. Confirm existing normalization cases still pass.

### Acceptance criteria

- Misconfigured environments fail clearly at startup.
- Validation errors are specific enough to diagnose quickly.

## Phase 6: Replace Deprecated `pydantic-ai` API Usage

### Objective

Remove the deprecation warning and reduce upgrade risk.

### Files

- [src/obsidian_agent/agent.py](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py)
- [pyproject.toml](/home/andrew/Documents/Projects/obsidian-agent/pyproject.toml)
- relevant tests in [tests/test_agent.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_agent.py)

### Step-by-step

1. Check the installed `pydantic-ai` version and current model-class recommendation.
2. Replace `OpenAIModel` with the supported replacement for chat-completions-compatible backends.
3. Update imports and type hints accordingly.
4. Re-run the specific model construction tests.
5. Re-run the full suite and confirm the warning disappears.

### Acceptance criteria

- Test suite is clean of the current deprecation warning.
- Local OpenAI-compatible base URL setup still works.

## Phase 7: Tighten Dependency Constraints

### Objective

Make installs more reproducible and reduce surprise breakage from upstream changes.

### Files

- [pyproject.toml](/home/andrew/Documents/Projects/obsidian-agent/pyproject.toml)
- possibly `uv.lock` if the project maintains it intentionally

### Step-by-step

1. Replace the floating git dependency for `obsidian-ops` with:
   - a release version, or
   - a pinned git ref if no release exists
2. Add an upper bound or narrower compatible range for `pydantic-ai`.
3. Review whether `fastapi`, `httpx`, and `uvicorn` also need compatible upper bounds.
4. Regenerate locks if that is part of repo workflow.
5. Re-run tests in the managed environment after dependency changes.

### Acceptance criteria

- Dependency resolution is more predictable.
- The project does not rely on unbounded upstream API changes.

## Phase 8: Repair and Expand the Test Suite

### Objective

Make the tests defend the intended contract instead of the current accidental behavior.

### Files

- [tests/test_tools.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_tools.py)
- [tests/test_agent.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_agent.py)
- [tests/test_app.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_app.py)
- [tests/test_integration.py](/home/andrew/Documents/Projects/obsidian-agent/tests/test_integration.py)

### Step-by-step

1. Remove tests that enshrine broad `Exception` swallowing in tool wrappers.
2. Add tests for:
   - vault `BusyError` propagation
   - generic unexpected exception propagation
   - library-level timeout, if applicable
   - multi-model/no-`instruct` resolution failure
3. Strengthen the integration test for `undo()`:
   - capture original file contents
   - mutate file
   - call `undo()`
   - assert file content matches original exactly
4. Add one API-level test that exercises the full write path and verifies the shaped response.
5. Keep any tests that validate current strengths:
   - changed-file tracking
   - commit warning path
   - current-file prompt injection

### Acceptance criteria

- The test suite fails if the documented contract regresses.
- Green tests actually mean the behavior matches the design.

## Phase 9: Documentation Cleanup

### Objective

Make the docs trustworthy again.

### Files

- [OBSIDIAN_AGENT_README.md](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md)
- [OBSIDIAN_AGENT_IMPLEMENTATION_GUIDE_V2.md](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_IMPLEMENTATION_GUIDE_V2.md)
- [demo-vault/README.md](/home/andrew/Documents/Projects/obsidian-agent/demo-vault/README.md)

### Step-by-step

1. Update error handling sections to match actual exception boundaries.
2. Update timeout semantics to match the implemented behavior.
3. Update model auto-resolution docs if you broaden selection rules beyond `instruct`.
4. Update HTTP request and error examples.
5. Fix stale field names in demo examples.
6. Re-read the docs after code changes and remove contradictions.

### Acceptance criteria

- A reader can implement against the docs without discovering mismatches in runtime behavior.

## Suggested Implementation Checklist

Use this as the execution checklist:

- [ ] Narrow tool exception handling to `VaultError`
- [ ] Preserve vault `BusyError` through `Agent.run()`
- [ ] Add busy propagation tests at tool, agent, and HTTP levels
- [ ] Decide and implement timeout contract
- [ ] Add timeout tests
- [ ] Fix local model resolution ambiguity
- [ ] Add ambiguous `/models` tests
- [ ] Choose and implement missing-instruction HTTP contract
- [ ] Align request model, handler, tests, and docs
- [ ] Fix demo example field names
- [ ] Harden config validators
- [ ] Replace deprecated `OpenAIModel`
- [ ] Tighten dependency constraints
- [ ] Strengthen undo integration test
- [ ] Run full suite with coverage
- [ ] Re-read docs for final consistency pass

## Validation Commands

Run these after each major phase, not just at the end:

```bash
devenv shell -- pytest tests/test_tools.py -q
devenv shell -- pytest tests/test_agent.py -q
devenv shell -- pytest tests/test_app.py -q
devenv shell -- pytest tests/test_integration.py -q
devenv shell -- pytest tests/ -q
devenv shell -- pytest tests/ --cov=obsidian_agent --cov-report=term-missing -q
```

## Definition of Done

The refactoring is complete when all of the following are true:

1. Exception boundaries match the design:
   - vault errors are returned as tool errors
   - busy errors propagate as concurrency failures
   - internal bugs are not silently swallowed
2. Timeout behavior is consistent across the library and HTTP layers, or clearly documented as intentionally different.
3. Local model resolution no longer makes arbitrary selections in ambiguous cases.
4. API request/response behavior is aligned across code, tests, and docs.
5. Configuration validation is stricter and more explicit.
6. The deprecation warning is removed.
7. Dependency constraints are more stable.
8. The test suite passes and covers the corrected contract.
9. Documentation matches the running implementation.

## Final Recommendation

Do not batch all of this into one giant unreviewable change.

Use small, disciplined commits or PR sections in this order:

1. exception handling and concurrency
2. timeout semantics
3. model resolution
4. HTTP contract alignment
5. config hardening
6. dependency/API modernization
7. docs and test cleanup

That sequence minimizes ambiguity and keeps each behavioral change reviewable.

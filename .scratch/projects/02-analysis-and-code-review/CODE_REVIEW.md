# obsidian-agent Code Review

Date: 2026-04-08

## Scope

This review covers the `obsidian-agent` library in `src/obsidian_agent/` and its test suite in `tests/`.

The review is grounded against the intended contract described in:

- [OBSIDIAN_AGENT_README.md](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md)
- [OBSIDIAN_AGENT_IMPLEMENTATION_GUIDE_V2.md](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_IMPLEMENTATION_GUIDE_V2.md)

## Intended Repo Goal

Based on the grounding documents, the library is supposed to be a thin orchestration layer over `obsidian-ops` and `pydantic-ai`.

The core intended properties are:

- All vault I/O goes through `obsidian_ops.Vault`.
- The agent exposes a small, deterministic tool surface to the LLM.
- Tool failures from `obsidian-ops` are returned to the model as `"Error: ..."` strings, except `BusyError`, which should propagate as a concurrency failure.
- The agent fails fast on concurrent operations via an agent-level lock.
- Changed files are tracked from write tools and committed after a successful run.
- The library and HTTP API return a structured result contract.
- The HTTP layer converts true concurrency conflicts into HTTP 409.

## Review Method

The review used four inputs:

- The two grounding documents listed above.
- Static inspection of the implementation in `src/obsidian_agent/`.
- Static inspection of the tests in `tests/`.
- Runtime verification inside the repo `devenv` shell.

Commands run:

```bash
devenv shell -- pytest tests/ -q
devenv shell -- pytest tests/ --cov=obsidian_agent --cov-report=term-missing -q
```

Observed results:

- Test suite: 79 passed
- Coverage: 94%
- Warning: `OpenAIModel` is deprecated in the currently installed `pydantic-ai`

The green suite does not mean the library fully matches the intended design. Several important behavioral contracts are either not implemented or are contradicted by the tests.

## Executive Summary

The library has a reasonable top-level structure. Module boundaries are clean, the public API is small, the FastAPI wrapper is straightforward, and the test suite is broad enough to catch many basic regressions.

The main problem is contract drift.

The current implementation passes its own tests, but some of those tests validate behavior that conflicts with the design docs. The highest-risk issues are around exception boundaries and concurrency semantics:

- true vault lock conflicts are not preserved as `BusyError`/HTTP 409
- tool wrappers swallow unexpected exceptions that should fail fast
- the library-level timeout described in the docs is only enforced at the HTTP layer

The intern produced code that is structurally decent but too forgiving in the wrong places. The review priority should be correctness and contract discipline, not cosmetic refactoring.

## Strengths

- Clear separation between configuration, prompt building, tools, agent orchestration, and HTTP transport.
- Tool wrappers are thin and mostly map directly to `obsidian-ops`, which matches the intended architecture.
- Changed-file tracking is simple and understandable.
- The agent-level lock exists and is exercised in tests.
- The test suite uses real vault interactions in many places instead of pure mocks.
- Coverage is high enough that future fixes can be made with confidence once the tests are corrected.

## Findings

### 1. High: vault-level `BusyError` is swallowed and downgraded into a generic application failure

Relevant code:

- [tools.py:24](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/tools.py:24)
- [agent.py:152](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:152)
- [app.py:59](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/app.py:59)

Grounding contract:

- [OBSIDIAN_AGENT_README.md:381](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md:381)
- [OBSIDIAN_AGENT_README.md:502](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md:502)

What the docs require:

- Tool functions should re-raise `BusyError`.
- The HTTP layer should turn a true concurrency conflict into HTTP 409.

What the code does:

- The tool wrappers correctly re-raise `BusyError`.
- `_run_impl()` then catches that re-raised exception in `except Exception` and converts it into `RunResult(ok=False, error="Agent error: ...")`.
- Because of that, the HTTP layer never receives the `BusyError`, so the documented 409 path is bypassed.

Why this matters:

- Clients cannot distinguish a real concurrency conflict from an ordinary application failure.
- Retries and UX behavior become incorrect.
- This violates one of the explicit design reasons for the agent-level lock and the error contract.

Runtime verification:

- Forcing `vault.write_file()` to raise `obsidian_ops.errors.BusyError` produced `RunResult(ok=False, error="Agent error: vault is busy elsewhere")` instead of a propagated busy error.

Recommendation:

- Catch `obsidian_ops.errors.BusyError` explicitly in `agent.py` and re-raise it.
- Narrow the broad exception handler so internal exceptions are not all collapsed into one generic error.
- Add an end-to-end test: tool raises vault `BusyError` -> `Agent.run()` raises -> `/api/apply` returns 409.

### 2. High: tool wrappers catch every exception, including programmer bugs and unexpected runtime failures

Relevant code:

- [tools.py:26](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/tools.py:26)
- [tools.py:38](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/tools.py:38)
- [tools.py:63](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/tools.py:63)
- [tools.py:104](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/tools.py:104)

Grounding contract:

- [OBSIDIAN_AGENT_README.md:389](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md:389)

What the docs require:

- Catch `VaultError` subclasses from `obsidian-ops` and return `"Error: ..."` to the model.
- Re-raise `BusyError`.

What the code does:

- Every tool catches `Exception`.
- Any unexpected bug is converted into a normal-looking tool error string.

Why this matters:

- Internal bugs are hidden from developers.
- The agent can continue executing after non-recoverable faults.
- Debugging becomes harder because broken code looks like an expected vault error.
- The model may attempt to recover from conditions it should never see.

Why the test suite is currently misleading:

- [tests/test_tools.py:198](/home/andrew/Documents/Projects/obsidian-agent/tests/test_tools.py:198) asserts that generic `RuntimeError("boom")` should become an `"Error:"` string.
- That test enshrines behavior that contradicts the grounding docs.

Recommendation:

- Replace `except Exception` with `except VaultError`.
- Keep `BusyError` as a separate re-raise.
- Let unexpected exceptions fail fast.
- Update tests so generic runtime exceptions propagate and fail the test.

### 3. Medium: `operation_timeout` is not enforced for library callers

Relevant code:

- [agent.py:121](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:121)
- [agent.py:132](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:132)
- [app.py:47](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/app.py:47)

Grounding contract:

- [OBSIDIAN_AGENT_README.md:65](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md:65)
- [OBSIDIAN_AGENT_README.md:207](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md:207)

What the docs imply:

- `operation_timeout` is an agent-level operation limit.
- A timed-out operation should result in a structured timeout error.

What the code does:

- The timeout is only applied in `app.py` via `asyncio.wait_for(...)`.
- Direct library users calling `await agent.run(...)` get no timeout enforcement.

Runtime verification:

- With `operation_timeout=0`, a direct `await agent.run(...)` still completed successfully.

Why this matters:

- The library behavior diverges from the documented contract.
- A Python caller can hang indefinitely even though configuration suggests otherwise.
- HTTP and library consumers do not get the same semantics.

Recommendation:

- Decide whether timeout is part of the library contract or only the HTTP contract.
- If it is part of the library contract, enforce it in `Agent.run()`.
- If it is HTTP-only, update the docs to say so explicitly.
- Add a direct library timeout test either way.

### 4. Medium: local model auto-resolution does not follow the documented selection rules

Relevant code:

- [agent.py:88](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:88)
- [agent.py:100](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:100)
- [agent.py:104](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:104)

Grounding contract:

- [OBSIDIAN_AGENT_README.md:106](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md:106)

What the docs require:

- Query `/models`
- If one model is returned, use it
- If multiple models are returned, prefer one containing `"instruct"`
- If none match, raise a configuration error

What the code does:

- If none of the returned model IDs contain `"instruct"`, it silently picks the first model.

Why this matters:

- Local servers often expose multiple model types.
- The first model may be an embedding model, reranker, or administrative alias.
- Startup can bind to the wrong model without obvious failure.

Recommendation:

- Raise `ValueError` when multiple models exist and none look like an instruct/chat model.
- Add a test for the multi-model/no-`instruct` case.

### 5. Medium: HTTP contract drift between docs, schema, and demo documentation

Relevant code and docs:

- [models.py:17](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/models.py:17)
- [app.py:43](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/app.py:43)
- [tests/test_app.py:99](/home/andrew/Documents/Projects/obsidian-agent/tests/test_app.py:99)
- [demo-vault/README.md:27](/home/andrew/Documents/Projects/obsidian-agent/demo-vault/README.md:27)
- [OBSIDIAN_AGENT_README.md:453](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md:453)
- [OBSIDIAN_AGENT_README.md:503](/home/andrew/Documents/Projects/obsidian-agent/.scratch/projects/01-obsidian-agent-refactor/OBSIDIAN_AGENT_README.md:503)

Observed drift:

- The README says a missing instruction should return HTTP 200 with `ok=false`.
- The actual request model requires `instruction`, so FastAPI returns 422 before handler logic runs.
- The demo vault README still documents `current_url_path`, which no longer exists in the API.

Why this matters:

- The public API is ambiguous.
- Clients built from the README will behave differently from clients built against the running server.
- The demo instructions are stale and can mislead anyone testing the service manually.

Recommendation:

- Choose one contract and make code, tests, and docs consistent.
- If you want HTTP 200 plus `ok=false`, change the request model to allow a missing instruction and validate inside the handler.
- If you want HTTP 422, update the README and examples accordingly.
- Fix the demo vault README to use `current_file`.

### 6. Low: dependency management is not stable enough for a reusable library

Relevant files:

- [pyproject.toml:6](/home/andrew/Documents/Projects/obsidian-agent/pyproject.toml:6)
- [pyproject.toml:7](/home/andrew/Documents/Projects/obsidian-agent/pyproject.toml:7)

Observed issues:

- `obsidian-ops` is installed from a git URL with no pinned commit or tag.
- `pydantic-ai` has a broad lower bound and no upper bound.

Why this matters:

- Installs are not reproducible.
- Upstream changes can break the library unexpectedly.
- This is especially risky because the code already depends on pydantic-ai behavior that is moving.

Recommendation:

- Pin `obsidian-ops` to a commit or release tag.
- Add meaningful version constraints for `pydantic-ai`.
- Treat the package as a published library contract, not just a local app.

### 7. Low: the library is already using a deprecated `pydantic-ai` model class

Relevant code:

- [agent.py:55](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:55)

Observed behavior:

- Test runs emit a deprecation warning because `OpenAIModel` has been renamed.

Why this matters:

- Deprecations become removals.
- The local-server path is the most customized part of the model setup and therefore the most likely to break on upgrade.

Recommendation:

- Update to the currently supported `pydantic-ai` OpenAI chat model class.
- Add a focused test around local OpenAI-compatible server model construction so future upgrades fail loudly.

### 8. Low: the integration test suite misses some of the most important behavioral guarantees

Relevant tests:

- [tests/test_integration.py:57](/home/andrew/Documents/Projects/obsidian-agent/tests/test_integration.py:57)
- [tests/test_integration.py:80](/home/andrew/Documents/Projects/obsidian-agent/tests/test_integration.py:80)
- [tests/test_app.py:99](/home/andrew/Documents/Projects/obsidian-agent/tests/test_app.py:99)

Observed gaps:

- The apply/undo integration test does not verify that the file content is restored after `undo()`.
- There is no end-to-end test for tool `BusyError` propagating to HTTP 409.
- There is no direct library timeout test.
- There is no test for the documented model auto-resolution failure mode.
- Some tests validate the current implementation rather than the intended design.

Why this matters:

- The suite is green while important contract failures remain.
- Future contributors will trust the wrong invariants.

Recommendation:

- Add tests for the missing behavioral contracts above.
- Remove or rewrite tests that lock in behavior contrary to the docs.

## Additional Improvement Opportunities

These are lower-priority than the findings above, but still worth addressing.

### Validation hardening

Relevant code:

- [config.py:35](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/config.py:35)
- [config.py:43](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/config.py:43)

Observations:

- `llm_model` validation only checks for the presence of `:`.
- Values like `":"` or `"openai:"` will pass validation.
- `llm_base_url` is normalized but not meaningfully validated as a URL.

Recommendation:

- Validate provider and model segments explicitly.
- Validate that `llm_base_url` has a supported scheme and host when set.

### Error typing in `agent.py`

Relevant code:

- [agent.py:145](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:145)
- [agent.py:152](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:152)

Observation:

- `_run_impl()` collapses too many error categories into `"LLM call failed"` or `"Agent error"`.

Recommendation:

- Separate model/provider failures, vault busy failures, timeout failures, and internal programming failures.
- Keep user-facing error strings stable, but preserve structure internally.

### Document the commit behavior more strictly

Relevant code:

- [agent.py:107](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:107)
- [agent.py:165](/home/andrew/Documents/Projects/obsidian-agent/src/obsidian_agent/agent.py:165)

Observation:

- Commit message normalization is sensible, but the public docs focus on truncation more than normalization behavior.

Recommendation:

- Document that whitespace is normalized before truncation.
- Add one test at the API or integration level that asserts commit messages are derived from instructions in the intended format.

## Recommended Remediation Order

### Phase 1: Correctness and contract integrity

1. Fix `BusyError` propagation through `agent.py`.
2. Narrow tool exception handling to `VaultError`.
3. Decide and implement the real timeout contract.
4. Fix the multi-model local-resolution behavior.

### Phase 2: Tests and documentation

1. Rewrite tests that currently validate incorrect behavior.
2. Add missing end-to-end tests for busy handling, timeout handling, and undo verification.
3. Align README, demo docs, and HTTP schema semantics.

### Phase 3: Stability and maintenance

1. Replace deprecated `OpenAIModel`.
2. Tighten dependency versioning.
3. Harden configuration validation.

## Suggested Test Additions

Add the following tests before making further feature changes:

1. Tool raises `obsidian_ops.errors.BusyError` -> `Agent.run()` raises `BusyError`.
2. `/api/apply` returns 409 when a vault-level busy error occurs inside a tool.
3. Direct `Agent.run()` enforces timeout if that is the intended contract.
4. `/models` with multiple non-`instruct` results raises configuration error.
5. `undo()` integration test verifies on-disk content is actually restored.
6. Generic non-vault exceptions from tools propagate and fail tests.

## Conclusion

The code is not a disaster. It is organized better than many first implementations, and the intern clearly followed the broad architecture.

The problem is that the implementation is too permissive around failure handling and has drifted from the stated contract in several places. That is exactly the kind of issue a green test suite can hide when the tests are written against implementation details instead of system guarantees.

The right next move is not a rewrite. It is to tighten the exception boundaries, align the library and HTTP contracts, and fix the tests so they defend the intended behavior instead of the current accidental behavior.

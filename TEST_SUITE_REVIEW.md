# Test Suite Review

## Scope

This review covers the library and the current pytest suite in `tests/`, with special attention on filesystem behavior and whether tests run against visible, inspectable files on disk.

I reviewed:

- `src/obsidian_agent/*.py`
- `tests/*.py`
- `demo-vault/`
- `pyproject.toml`

I also attempted to run the suite. Execution was blocked in this environment because the project depends on `obsidian-ops` via a Git URL in `pyproject.toml`, the local environment does not already have dependencies installed, and the sandbox cannot resolve `github.com` to fetch them. That matters because the suite is not currently easy to execute in an offline or partially provisioned environment.

## Executive Summary

- The suite currently contains 78 tests across 8 test modules.
- The suite already uses real files on disk for most filesystem-oriented tests. It is not an in-memory fake suite.
- The problem is that those files are mostly created under `tmp_path`, so they are ephemeral, opaque, and usually gone before a developer can inspect the before/after state.
- The existing committed `demo-vault/` is barely leveraged. The tests synthesize disposable vaults instead of using a visible baseline fixture and preserving the mutated result.
- The suite is strongest at branch coverage for response handling and error translation.
- The suite is weaker at proving real-world filesystem workflows end-to-end, especially around commit/undo/JJ behavior, visible artifact retention, and HTTP-to-filesystem state transitions.

Blunt version: the tests mostly prove that the code can return the right shape of object under controlled conditions. They do not yet prove the product in the way a developer actually wants to inspect it: starting from a known on-disk vault, mutating it, and leaving evidence behind.

## Inventory

Current test count by module:

- `tests/test_agent.py`: 20 tests
- `tests/test_tools.py`: 19 tests
- `tests/test_app.py`: 12 tests
- `tests/test_config.py`: 12 tests
- `tests/test_integration.py`: 5 tests
- `tests/test_models.py`: 5 tests
- `tests/test_prompt.py`: 4 tests
- `tests/test_scaffold.py`: 1 test

Filesystem setup patterns:

- Shared vault fixture via `tmp_path`: `tests/conftest.py:11-18`
- Separate `tmp_path` vault in tool tests: `tests/test_tools.py:27-38`
- Separate `tmp_path` vault in app tests: `tests/test_app.py:16-42`
- Separate `tmp_path` vault plus JJ init in integration tests: `tests/test_integration.py:18-50`
- Config tests also rely on `tmp_path` for path validation: `tests/test_config.py:9-139`

Relevant implementation surfaces under test:

- Agent orchestration and commit/undo flow: `src/obsidian_agent/agent.py:23-212`
- Tool wrappers over `obsidian_ops.Vault`: `src/obsidian_agent/tools.py:10-170`
- FastAPI wiring: `src/obsidian_agent/app.py:13-68`
- Config validation: `src/obsidian_agent/config.py:8-62`

## Findings

### 1. High: the suite uses real disk, but hides it behind ephemeral `tmp_path`

References:

- `tests/conftest.py:11-18`
- `tests/test_tools.py:27-38`
- `tests/test_app.py:16-42`
- `tests/test_integration.py:18-50`
- `tests/test_config.py:9-139`

Important distinction: `tmp_path` is still real filesystem I/O. These tests are not fake in-memory tests. But they are still bad for manual verification because:

- the directories are auto-generated and disposable
- they are not part of a stable repo-local location
- they are normally cleaned up after the run
- they do not preserve a baseline snapshot versus a post-test snapshot
- they make it harder to inspect failures unless you drop into pytest internals

That directly conflicts with the stated goal: developers want to see files before and after a test run and confirm the changes themselves.

Current state is best described as "real but transient." The target state should be "real, deterministic, and retained."

### 2. High: the integration suite recreates a disposable vault instead of using a committed visible fixture

References:

- `tests/test_integration.py:18-50`
- `demo-vault/README.md`
- `demo-vault/index.md`

The repository already contains `demo-vault/`, which is explicitly described as a sample vault for testing and verification. The test suite does not actually use it as a baseline fixture. Instead, `integration_vault` builds a new vault under `tmp_path`, writes a few files by hand, initializes JJ, sets repo config, and commits a baseline.

That has a few consequences:

- the integration fixture is invisible after the run
- the committed `demo-vault/` provides little confidence because it is not the thing under test
- there is no durable "before" tree a developer can inspect
- fixture content is duplicated between code and repository assets

This is the biggest architectural miss in the current suite. The repo already has the beginnings of the right idea, but the tests ignore it.

### 3. High: commit/undo behavior is central to the product but mostly mocked away

References:

- `src/obsidian_agent/agent.py:176-208`
- `tests/test_agent.py:18-27`
- `tests/test_agent.py:92-99`
- `tests/test_agent.py:187-227`
- `tests/test_agent.py:250-257`
- `tests/test_app.py:30-37`
- `tests/test_integration.py:59-87`

The code path that actually matters operationally is not just "tool ran" but "vault changed, commit happened, undo happened, restore happened, and the working copy ended up correct."

Most agent and app tests suppress that behavior:

- `vault.commit` is monkeypatched to a no-op in the core agent fixture
- `vault.undo` is monkeypatched in app tests
- timeout and failure paths are simulated with monkeypatches instead of exercising real repo state

Only the small integration slice does any meaningful end-to-end validation of on-disk commit/undo behavior.

This leaves obvious gaps:

- no test proves the normalized commit message from `Agent._normalize_commit_message()` is actually passed to `vault.commit`
- no test proves the `jj restore --from @-` subprocess is called with the expected cwd and timeout
- no test proves the warning branch when `restore` fails after `undo` succeeds
- no test proves partial real repo state after commit failure or undo failure
- app-level tests almost never validate actual file mutation through the HTTP layer

Given the product, commit/undo is not incidental. It is core behavior. The suite treats too much of it like a side detail.

### 4. High: there is no durable before/after artifact strategy

References:

- `tests/` broadly
- absence of any `tests/artifacts/`, `tests/runs/`, or similar retained worktree directory

There is no mechanism to:

- preserve the initial state used by a test
- preserve the final state after mutation
- emit a diff between those states
- retain artifacts under a stable path
- correlate artifacts to a specific test name

As written, even when a test mutates real files, the evidence is short-lived.

If the goal is developer confirmation, the suite needs a first-class artifact model, not just ad hoc writes under pytest temp directories.

### 5. Medium-High: app tests mostly verify HTTP envelope behavior, not filesystem outcomes

References:

- `tests/test_app.py:45-175`
- `src/obsidian_agent/app.py:24-68`

`test_app.py` is mostly concerned with:

- status codes
- response schema fields
- error translation to 409
- empty instruction handling

That is useful, but thin. The default client fixture returns `"No changes needed"` from a function model and monkeypatches `commit` and `undo` away. So most app tests do not prove:

- that `/api/apply` can mutate a real vault and leave the expected file contents
- that `/api/undo` actually restores file contents
- that `current_file` context affects downstream behavior in a way visible at the app boundary
- that app lifespan creates a usable real agent against a persistent fixture vault

There is one HTTP write integration test in `tests/test_integration.py:139-162`, which helps, but it is too small to carry the whole API surface.

### 6. Medium: tool tests hit disk, but error-path realism drops off fast

References:

- `tests/test_tools.py:50-225`
- `src/obsidian_agent/tools.py:20-155`

`test_tools.py` is one of the better modules in the suite. It does exercise real files on disk for:

- `read_file`
- `write_file`
- `delete_file`
- `list_files`
- `search_files`
- frontmatter access
- heading reads/writes
- block reads/writes

That is good. The weak points are:

- the fixture is still ephemeral under `tmp_path`
- negative/error tests quickly switch to fake vault classes instead of real filesystem edge cases
- path traversal coverage only asserts `"Error:"`; it does not verify that nothing escaped the vault root
- there is no before/after snapshot retention
- formatting assertions are shallow and mostly string-prefix checks

Specific missing cases worth adding once the artifact story is fixed:

- overwrite existing file while preserving expected surrounding content
- heading write when the heading does not already exist
- block write when the block does not already exist
- nested path creation behavior
- duplicate writes to the same file and changed-file deduplication
- path traversal with explicit filesystem assertions that no outside path changed

### 7. Medium: config tests are acceptable technically, but they still violate the "no tmp files" policy

References:

- `tests/test_config.py:9-139`
- `src/obsidian_agent/config.py:8-62`

These tests are structurally fine for what they cover. They validate:

- required `vault_dir`
- existing directory versus file versus missing path
- env var loading
- URL normalization
- numeric bounds

If the policy is strict, though, they still need to move off `tmp_path`. These are lower priority than the mutating vault tests, but they still rely on transient temp paths rather than stable repo-local paths.

I would not prioritize these first, but I would not exempt them either if the goal is consistency across the suite.

### 8. Medium: several modules are too thin to justify their presence as-is

References:

- `tests/test_prompt.py:4-29`
- `tests/test_models.py:4-52`
- `tests/test_scaffold.py:1-2`

`test_prompt.py` and `test_models.py` are not harmful, but they are thin.

`test_scaffold.py` is empty calories:

- it contains one test
- that test is `assert True`

That test contributes nothing. It inflates the count without increasing confidence.

`test_prompt.py` mostly checks substring presence. `test_models.py` mostly checks straightforward defaults and serialization. Those are low-risk surfaces, so the current thinness is not fatal, but it does highlight a broader pattern: parts of the suite optimize for easy counts rather than strong signals.

### 9. Medium: fixture setup is duplicated across modules and will drift

References:

- `tests/conftest.py:11-18`
- `tests/test_tools.py:27-38`
- `tests/test_app.py:16-42`
- `tests/test_integration.py:18-50`

There are multiple independently defined vault setups:

- base shared vault in `conftest.py`
- tool-specific vault in `test_tools.py`
- app-specific vault in `test_app.py`
- integration-specific vault in `test_integration.py`

They all create slightly different file trees and note contents. That is manageable at this scale, but it is a drift trap:

- file names overlap but contents differ
- semantics differ by module
- expected behavior becomes tied to local fixture quirks rather than shared reality

If the suite moves to committed visible fixtures, this duplication should be reduced hard.

### 10. Medium: the suite is not easy to run in constrained environments

References:

- `pyproject.toml`

Observed execution attempt:

- `pytest` was not installed globally in this shell
- `uv run pytest` required dependency resolution
- dependency resolution needed `obsidian-ops` from GitHub
- network access to GitHub was unavailable in this environment

This is not purely a test-code issue, but it does affect the practical quality of the suite. A test suite that cannot be stood up easily is a slow suite to trust.

If you want developers frequently inspecting on-disk before/after states, the path to running tests needs to be simpler and less fragile.

## Coverage Assessment By Module

### `tests/test_tools.py`

Strengths:

- best direct filesystem coverage in the suite
- good breadth across vault operations
- validates changed-file tracking for write operations

Weaknesses:

- ephemeral vault
- synthetic error cases instead of real ones
- no retained artifacts

### `tests/test_agent.py`

Strengths:

- decent branch coverage of `run()`
- busy state and timeout handling are exercised
- model selection helper methods are covered

Weaknesses:

- too much core repo behavior is monkeypatched away
- file mutation is only lightly inspected
- commit/undo integration confidence is too concentrated in a different module

### `tests/test_app.py`

Strengths:

- good HTTP envelope and response-shape checks
- explicit coverage for busy/error translation

Weaknesses:

- weak evidence of actual filesystem outcomes
- fixture design intentionally suppresses mutations
- almost no durable end-to-end state verification

### `tests/test_integration.py`

Strengths:

- closest thing to real product verification
- touches JJ setup, agent execution, HTTP path, and undo path

Weaknesses:

- still `tmp_path`-based
- builds its own fixture instead of using repo-visible assets
- only 5 tests carry too much responsibility

### `tests/test_config.py`

Strengths:

- clean, readable validation checks

Weaknesses:

- still temp-path-based
- not aligned with the desired visible-on-disk testing policy

### `tests/test_prompt.py`, `tests/test_models.py`, `tests/test_scaffold.py`

Strengths:

- fast and simple

Weaknesses:

- limited signal
- one test is worthless

## What "Real Files On Disk" Should Mean Here

If the desired standard is serious, it should not mean "mutate the committed fixture directory in place." That creates order dependence, accidental dirty worktrees, and cross-test contamination.

The right interpretation is:

- source fixtures live in the repository as committed, readable baseline vaults
- each test materializes a working copy under a stable repo-local artifacts directory
- that working copy is mutated by the test
- before/after states are preserved for inspection
- the artifact path is deterministic enough that a developer can find it without spelunking pytest internals

In other words: not temp dirs, not shared mutable fixtures, not hidden state.

## Recommended Target Layout

Suggested structure:

```text
tests/
  fixtures/
    vaults/
      basic/
        note.md
        Projects/
          Alpha.md
      tools/
        note.md
        plain.md
        block.md
      integration/
        README.md
        Projects/
          Alpha.md
          Beta.md
        Daily/
          2025-01-01.md
  artifacts/
    .gitkeep                # optional if you want the directory present
  support/
    vault_fs.py
```

Behavior of `tests/support/vault_fs.py` should be:

- take a named fixture vault
- create `tests/artifacts/<run-id>/<sanitized-test-nodeid>/before/`
- copy fixture contents into `before/`
- create `tests/artifacts/<run-id>/<sanitized-test-nodeid>/work/`
- run the test against `work/`
- on teardown, copy `work/` to `after/` or keep `work/` as the after-state
- optionally emit `manifest.json` with:
  - test node id
  - source fixture
  - timestamps
  - changed files
  - command/environment metadata
- optionally emit `diff.txt`

If storage growth is a concern:

- keep only the latest run by default
- or keep the latest N runs
- but do not revert to `tmp_path`

## Migration Priorities

### Priority 1: replace `tmp_path` vault fixtures for mutating tests

Replace:

- `tests/conftest.py:11-18`
- `tests/test_tools.py:27-38`
- `tests/test_app.py:16-42`
- `tests/test_integration.py:18-50`

with a shared repo-local artifact-producing fixture helper.

This is the main structural change.

### Priority 2: make integration tests use committed fixture sources

Use committed vault trees under `tests/fixtures/vaults/` or adapt `demo-vault/` into that role.

Recommendation: do not run tests directly against `demo-vault/` in place. Treat it as a source fixture and copy it into visible artifacts.

### Priority 3: expand real commit/undo verification

Add tests that prove:

- commit message passed to `vault.commit` is normalized/truncated correctly
- `undo()` restores visible file contents
- `jj restore --from @-` warning path is surfaced correctly
- app endpoints mutate and restore real on-disk state

### Priority 4: remove or replace low-value tests

Start with:

- `tests/test_scaffold.py:1-2`

Then either deepen or keep minimal:

- `tests/test_prompt.py`
- `tests/test_models.py`

### Priority 5: reduce fixture duplication

Standardize around a small number of committed fixture vaults with distinct purposes:

- `basic`
- `tools`
- `integration`
- `http`

## Concrete Gaps Worth Adding

These are the missing tests I would add after the artifact model is fixed:

- real HTTP apply test that writes a file and preserves visible before/after snapshots
- real HTTP undo test that proves file restoration on disk
- agent test that verifies the exact commit message passed into `vault.commit`
- undo test that simulates successful `vault.undo()` but failing `jj restore`, asserting warning text from `src/obsidian_agent/agent.py:197-208`
- tool tests for append-on-missing-heading behavior from `src/obsidian_agent/tools.py:121-130`
- tool tests for path traversal that assert no external path changed, not just that an error string was returned
- multi-write tests that hit the same file twice and confirm `changed_files` remains deduplicated and sorted
- app lifespan test that validates the created agent points at the expected visible working directory, not just that `/api/health` returns 200

## Bottom Line

The suite is not fake. It already uses real files on disk for most meaningful tests. But it still fails the actual requirement because those files are hidden behind temp fixtures and are not preserved for inspection.

That is the key judgment:

- current suite: real I/O, low inspectability
- desired suite: real I/O, high inspectability, stable artifacts, visible before/after state

The fastest path to that outcome is not rewriting assertions first. It is replacing the filesystem fixture model:

- committed baseline vault fixtures
- repo-local retained work directories
- preserved before/after snapshots
- real end-to-end validation of commit/undo and HTTP write flows

Until that happens, the suite will continue to provide partial confidence while denying the one thing the developer explicitly wants: being able to look at the files and judge the behavior directly.

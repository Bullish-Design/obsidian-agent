# Refactor Implementation Log

## Step 0: Establish Baseline

- Branch: `refactor` (tracking `origin/refactor`)
- Baseline full-suite command: `devenv shell -- pytest -q`
- Baseline result: `109 passed in 19.89s` (shell total: `28.342s`)
- Baseline API behavior:
  - `POST /api/apply` returns `OperationResult`; blank/missing `instruction` returns HTTP 200 with `{ok:false,error:"instruction is required"}`
  - `POST /api/undo` returns `OperationResult` from `Agent.undo()`
  - `GET /api/health` returns HTTP 200 with `{ok:true,status:"healthy"}`
- Baseline architecture observation:
  - `Agent.undo()` currently calls `vault.undo()` and then performs a direct `subprocess.run([jj, restore, --from, @-])` in `obsidian-agent`.

## Step 1: Boundary Confirmation

- Boundary rule documented in `README.md`.
- Verified boundary leak location:
  - direct raw `jj` subprocess path exists in `src/obsidian_agent/agent.py` undo flow.
- No other active direct raw filesystem or raw `jj` subprocess paths were identified in the service layer.

## Step 2: Remove Direct `jj restore` From Agent Layer

- `Agent.undo()` now calls `vault.undo_last_change()` and maps lower-layer warning output into `RunResult.warning`.
- Removed direct agent-layer `subprocess.run([jj, restore, --from, @-])` usage.
- Updated tests to assert new boundary:
  - unit tests now verify `undo_last_change()` success/warning/failure flows.
  - integration undo flow still verifies end-to-end restore behavior in a real JJ repo.
- Validation:
  - `devenv shell -- pytest -q tests/test_agent.py` -> `22 passed`
  - `devenv shell -- pytest -q tests/test_integration.py` -> `5 passed`
  - `devenv shell -- pytest -q` -> `108 passed`

## Step 3: Dependency Strategy Update

- Removed git-pinned `obsidian-ops` dependency source in favor of explicit local workspace override for active development:
  - `[tool.uv.sources] obsidian-ops = { path = "../obsidian-ops", editable = true }`
- Removed `allow-direct-references` metadata (no longer needed).
- Documented local setup and validation workflow in `README.md`.
- Validation:
  - clean setup flow: `devenv shell -- uv sync --extra dev`
  - full suite: `devenv shell -- pytest -q` -> `108 passed`

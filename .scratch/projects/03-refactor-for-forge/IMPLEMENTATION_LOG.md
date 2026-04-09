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

- Dependency decision updated: keep an explicit git-pinned `obsidian-ops` source for reproducibility:
  - `[tool.uv.sources] obsidian-ops = { git = "https://github.com/Bullish-Design/obsidian-ops.git", tag = "v0.4.0" }`
- Removed `allow-direct-references` metadata (no longer needed).
- Documented the pinned strategy and local setup workflow in `README.md`.
- Validation:
  - clean setup flow: `devenv shell -- uv sync --extra dev`
  - full suite: `devenv shell -- pytest -q` -> `108 passed`

## Step 4: Python Version Requirement Alignment

- Version mismatch is intentional and documented:
  - `obsidian-agent` keeps `requires-python = ">=3.13"`
  - `obsidian-ops` remains `>=3.12`
- `devenv.nix` kept at Python `3.13` to match service runtime and existing validated test environment.

## Step 5: Harden `current_file` Contract

- Tightened `ApplyRequest` validation:
  - extra fields forbidden
  - `current_file` optional, but when provided must be a non-empty vault-relative path
  - rejects URLs, absolute paths, parent traversal (`..`), and backslash separators
- Added model and app tests for valid/invalid `current_file` and explicit rejection of `current_url_path`.
- Validation:
  - `devenv shell -- pytest -q tests/test_prompt.py tests/test_app.py tests/test_models.py` -> `32 passed`

## Step 6: Tool Surface Alignment

- Added stable tool wrappers backed by `obsidian-ops` APIs:
  - `set_frontmatter(path, data)`
  - `delete_frontmatter_field(path, field)`
- Registered both tools in the agent toolset.
- Ensured both tools mark files in `changed_files`.
- Preserved recoverable tool failure format (`Error: ...`).
- Validation:
  - `devenv shell -- pytest -q tests/test_tools.py` -> `35 passed`
  - `devenv shell -- pytest -q tests/test_agent.py` -> `22 passed`
  - `devenv shell -- pytest -q` -> `122 passed`

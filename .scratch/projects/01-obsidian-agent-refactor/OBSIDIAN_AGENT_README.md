# obsidian-agent

LLM-powered tool-calling agent for Obsidian vault operations. Accepts a natural-language instruction, runs an async agent loop against an Obsidian vault via [obsidian-ops](https://github.com/Bullish-Design/obsidian-ops), commits changes with Jujutsu, and returns a structured result.

Exposed as both a Python library and an HTTP API.

---

## Architecture

```
obsidian-agent
├── obsidian-ops          vault operations (file CRUD, frontmatter, content patching, VCS)
├── pydantic-ai           LLM agent framework (multi-provider, tool calling, DI, testing)
├── fastapi + uvicorn     HTTP server
└── pydantic-settings     configuration
```

obsidian-agent never reads or writes vault files directly. All vault interaction goes through `obsidian_ops.Vault`. pydantic-ai handles all LLM interaction — provider routing, tool-call dispatch, retries, and message management.

### Dependency direction

```
HTTP client / reverse proxy
    └── obsidian-agent (orchestration, LLM, HTTP API)
            └── obsidian-ops (vault I/O, path sandboxing, mutation lock, VCS)
```

obsidian-agent knows nothing about frontends, site generators, or URL routing. The caller resolves URLs to vault-relative file paths before calling the agent.

---

## Installation

Requires Python >= 3.13.

```bash
# From source (development)
uv pip install -e ".[dev]"

# Dependencies
#   obsidian-ops >= 0.1.0
#   pydantic-ai >= 1.70.0
#   pydantic >= 2.12.0
#   pydantic-settings >= 2.0.0
#   fastapi >= 0.115.0
#   uvicorn >= 0.34.0
#   httpx >= 0.28.0
```

---

## Configuration

All configuration is via `AGENT_`-prefixed environment variables, managed by `pydantic-settings`.

| Variable | Default | Description |
|---|---|---|
| `AGENT_VAULT_DIR` | *(required)* | Absolute path to the Obsidian vault |
| `AGENT_LLM_MODEL` | `"anthropic:claude-sonnet-4-20250514"` | pydantic-ai model string (see [Model Strings](#model-strings)) |
| `AGENT_LLM_BASE_URL` | `None` | Override base URL for OpenAI-compatible servers (vLLM, Ollama) |
| `AGENT_HOST` | `"127.0.0.1"` | HTTP server bind address |
| `AGENT_PORT` | `8081` | HTTP server port |
| `AGENT_MAX_ITERATIONS` | `20` | Max tool-calling rounds per operation |
| `AGENT_OPERATION_TIMEOUT` | `120` | Seconds before the entire operation times out |
| `AGENT_LLM_MAX_TOKENS` | `4096` | Max tokens per LLM response |
| `AGENT_JJ_BIN` | `"jj"` | Path to the Jujutsu binary |
| `AGENT_JJ_TIMEOUT` | `120` | Timeout for jj subprocess calls |

API keys are resolved from the standard provider environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) by pydantic-ai's provider layer. No `AGENT_LLM_API_KEY` is needed — configure the provider key directly.

### Model strings

pydantic-ai uses `"provider:model-name"` format:

```bash
# Anthropic
AGENT_LLM_MODEL="anthropic:claude-sonnet-4-20250514"
AGENT_LLM_MODEL="anthropic:claude-opus-4-20250514"

# OpenAI
AGENT_LLM_MODEL="openai:gpt-4o"

# Local vLLM / Ollama (OpenAI-compatible)
AGENT_LLM_MODEL="openai:my-local-model"
AGENT_LLM_BASE_URL="http://localhost:8000/v1"

# Groq, Mistral, etc. — any pydantic-ai supported provider
AGENT_LLM_MODEL="groq:llama-3.3-70b-versatile"
```

### Base URL normalization

When `AGENT_LLM_BASE_URL` is set for an OpenAI-compatible server:
- Trailing slashes are stripped
- If the URL has no path (or just `/`), `/v1` is appended

```
http://localhost:8000   → http://localhost:8000/v1
http://localhost:8000/  → http://localhost:8000/v1
http://localhost:8000/v1/ → http://localhost:8000/v1
```

### Model resolution for local servers

When `AGENT_LLM_BASE_URL` is set and `AGENT_LLM_MODEL` uses a generic name:
1. Query `GET {base_url}/models`
2. If one model: use it
3. If multiple: prefer one containing "instruct" (case-insensitive)
4. If none: raise a configuration error

### Configuration class

```python
class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    vault_dir: Path                              # Required. Must exist and be a directory.
    llm_model: str = "anthropic:claude-sonnet-4-20250514"
    llm_base_url: str | None = None
    llm_max_tokens: int = 4096
    max_iterations: int = 20
    operation_timeout: int = 120
    jj_bin: str = "jj"
    jj_timeout: int = 120
    host: str = "127.0.0.1"
    port: int = 8081
```

Validation on init:
- `vault_dir` must exist and be a directory
- `llm_model` must be a valid pydantic-ai model string
- `llm_base_url` is normalized if set

---

## Library Usage

### Quick start

```python
from obsidian_agent import Agent, AgentConfig

config = AgentConfig(vault_dir="/path/to/vault")
agent = Agent(config)

result = await agent.run(
    instruction="Clean up this note and add related links",
    current_file="Projects/Alpha.md",
)

assert result.ok
print(result.summary)        # "Cleaned up formatting and added links to Beta.md and Gamma.md"
print(result.changed_files)  # ["Projects/Alpha.md"]
```

### RunResult

```python
@dataclass
class RunResult:
    ok: bool                         # True if operation completed without fatal error
    updated: bool                    # True if vault files were modified
    summary: str                     # Human-readable description of what happened
    changed_files: list[str]         # Vault-relative paths of modified files
    error: str | None = None         # Error message if ok=False
    warning: str | None = None       # Non-fatal warning (e.g., "commit failed after changes")
```

### Undo

```python
result = await agent.undo()
# result.ok → True
# result.summary → "Last change undone."
```

**Limitation**: `undo()` calls `vault.undo()` (`jj undo`) and then attempts `jj restore --from @-` to restore working-copy content. If unrelated jj operations intervened, or repo history is unusual, undo can still target an unexpected operation or fail to restore exactly.

---

## Agent Loop

### Flow

```
1. Receive instruction + optional current_file
2. Acquire agent-level lock (fail fast with BusyError if held)
3. Build system prompt (vault context, current file info)
4. Create pydantic-ai Agent with vault tools and obsidian-ops Vault as dependency
5. Call agent.run() with instruction, usage_limits, and timeout
6. pydantic-ai manages the tool-calling loop internally:
   a. LLM receives tools + instruction
   b. LLM returns tool calls → pydantic-ai dispatches them → results sent back
   c. Repeat until LLM returns final text (no tool calls)
7. Track changed files from write-tool executions
8. If files changed: commit via vault.commit(message)
9. Return RunResult
```

### Stopping conditions

| Condition | Result |
|---|---|
| LLM returns no tool calls | Happy path. Response text becomes `summary`. |
| Usage limit exceeded | `ok=False`, error describes which limit was hit |
| Operation timeout | `ok=False`, error: "Operation timed out after {n}s" |
| LLM API error | `ok=False`, error describes the failure |

### Changed file tracking

Tools that modify files are tracked. After each write-tool execution, the file path is added to a set:

```python
WRITE_TOOLS = {"write_file", "delete_file", "update_frontmatter", "write_heading", "write_block"}
```

This set becomes `RunResult.changed_files`.

### Commit logic

After a successful agent loop:
1. If `changed_files` is non-empty:
   a. Commit message = first 72 characters of the instruction
   b. Call `vault.commit(message)`
   c. If commit fails: set `warning` but return `ok=True` (files were changed, just not committed)
2. `updated = len(changed_files) > 0`

### Agent-level lock

The agent acquires its own lock at the start of `run()`, before any LLM calls. This prevents a second request from wasting an LLM API call only to fail on the obsidian-ops mutation lock when it tries to write.

```python
async def run(self, instruction: str, current_file: str | None = None) -> RunResult:
    if self._busy:
        raise BusyError("Another operation is already running")
    self._busy = True
    try:
        ...
    finally:
        self._busy = False
```

### Usage limits

The agent passes `UsageLimits` to pydantic-ai's `agent.run()` to cap runaway loops:

```python
from pydantic_ai.usage import UsageLimits

limits = UsageLimits(
    request_limit=config.max_iterations,  # Max model turns
)
```

---

## Tool Set

The agent exposes 11 tools to the LLM, each backed by an `obsidian_ops.Vault` method. Tools are registered on the pydantic-ai Agent via `@agent.tool` decorators with `RunContext[VaultDeps]` for dependency injection.

### VaultDeps

```python
@dataclass
class VaultDeps:
    vault: Vault
    changed_files: set[str]
```

The `Vault` instance and the mutable `changed_files` set are injected into every tool via pydantic-ai's `RunContext`.

### Tool table

| Tool | Description | Mutates? | obsidian-ops method |
|---|---|---|---|
| `read_file` | Read a vault file's contents | No | `vault.read_file(path)` |
| `write_file` | Write/create a vault file | Yes | `vault.write_file(path, content)` |
| `delete_file` | Delete a vault file | Yes | `vault.delete_file(path)` |
| `list_files` | List files matching a glob pattern | No | `vault.list_files(pattern)` |
| `search_files` | Search file contents for a query | No | `vault.search_files(query, glob)` |
| `get_frontmatter` | Read YAML frontmatter | No | `vault.get_frontmatter(path)` |
| `update_frontmatter` | Patch frontmatter fields | Yes | `vault.update_frontmatter(path, updates)` |
| `read_heading` | Read content under a heading | No | `vault.read_heading(path, heading)` |
| `write_heading` | Replace content under a heading | Yes | `vault.write_heading(path, heading, content)` |
| `read_block` | Read a block by ID | No | `vault.read_block(path, block_id)` |
| `write_block` | Replace a block's content | Yes | `vault.write_block(path, block_id, content)` |

### Tool implementation pattern

All tools follow the same pattern — vault access via `RunContext`, error strings returned (never raised), write ops tracked:

```python
@agent.tool
async def read_file(ctx: RunContext[VaultDeps], path: str) -> str:
    """Read the contents of a file in the vault. Path is relative to vault root."""
    return ctx.deps.vault.read_file(path)

@agent.tool
async def write_file(ctx: RunContext[VaultDeps], path: str, content: str) -> str:
    """Write content to a file in the vault. Creates or overwrites. Path is relative to vault root."""
    ctx.deps.vault.write_file(path, content)
    ctx.deps.changed_files.add(path)
    return f"Successfully wrote {path}"

@agent.tool
async def delete_file(ctx: RunContext[VaultDeps], path: str) -> str:
    """Delete a file from the vault. Path is relative to vault root."""
    ctx.deps.vault.delete_file(path)
    ctx.deps.changed_files.add(path)
    return f"Deleted {path}"

@agent.tool
async def list_files(ctx: RunContext[VaultDeps], pattern: str) -> str:
    """List files in the vault matching a filename glob pattern, e.g. '*.md'."""
    files = ctx.deps.vault.list_files(pattern)
    if not files:
        return "No files found."
    return f"Found {len(files)} files:\n" + "\n".join(files)

@agent.tool
async def search_files(ctx: RunContext[VaultDeps], query: str, glob: str = "*.md") -> str:
    """Search file contents for a text query. Returns matching files with context snippets."""
    results = ctx.deps.vault.search_files(query, glob=glob)
    if not results:
        return "No matches found."
    lines = [f"Found {len(results)} matching files:"]
    for r in results:
        lines.append(f"\n--- {r.path} ---\n{r.snippet}")
    return "\n".join(lines)

@agent.tool
async def get_frontmatter(ctx: RunContext[VaultDeps], path: str) -> str:
    """Read the YAML frontmatter from a vault file. Returns JSON object or null."""
    import json
    fm = ctx.deps.vault.get_frontmatter(path)
    if fm is None:
        return "No frontmatter found."
    return json.dumps(fm, indent=2, default=str)

@agent.tool
async def update_frontmatter(ctx: RunContext[VaultDeps], path: str, updates: dict) -> str:
    """Update specific fields in a file's YAML frontmatter. Only specified fields change."""
    ctx.deps.vault.update_frontmatter(path, updates)
    ctx.deps.changed_files.add(path)
    return f"Updated frontmatter for {path}"

@agent.tool
async def read_heading(ctx: RunContext[VaultDeps], path: str, heading: str) -> str:
    """Read content under a heading. Heading includes '#' prefix, e.g. '## Summary'."""
    content = ctx.deps.vault.read_heading(path, heading)
    if content is None:
        return f"Heading '{heading}' not found in {path}"
    return content

@agent.tool
async def write_heading(ctx: RunContext[VaultDeps], path: str, heading: str, content: str) -> str:
    """Replace content under a heading. If heading doesn't exist, it's appended."""
    ctx.deps.vault.write_heading(path, heading, content)
    ctx.deps.changed_files.add(path)
    return f"Updated heading '{heading}' in {path}"

@agent.tool
async def read_block(ctx: RunContext[VaultDeps], path: str, block_id: str) -> str:
    """Read the content of a block identified by its ^block-id."""
    content = ctx.deps.vault.read_block(path, block_id)
    if content is None:
        return f"Block '{block_id}' not found in {path}"
    return content

@agent.tool
async def write_block(ctx: RunContext[VaultDeps], path: str, block_id: str, content: str) -> str:
    """Replace the content of a block identified by its ^block-id."""
    ctx.deps.vault.write_block(path, block_id, content)
    ctx.deps.changed_files.add(path)
    return f"Updated block '{block_id}' in {path}"
```

### Error handling in tools

Tool errors from obsidian-ops (plus `FileNotFoundError` for missing files) are caught and returned as `"Error: ..."` strings. The LLM sees the error and can decide how to proceed (try a different path, explain to the user, etc.). This is not a loop-terminating condition.

```python
# Wrapping pattern (applied to each tool):
try:
    return ctx.deps.vault.read_file(path)
except BusyError:
    raise  # Re-raise — indicates concurrency bug, not recoverable tool error
except (VaultError, FileNotFoundError) as e:
    return f"Error: {e}"
```

`BusyError` is re-raised rather than swallowed, since it indicates a concurrency problem in the agent itself (the agent-level lock should have prevented this).

obsidian-ops error hierarchy:
```
VaultError
├── PathError          (sandbox violations)
├── FileTooLargeError  (>512KB reads)
├── BusyError          (mutation lock)
├── FrontmatterError   (YAML parse failures)
├── ContentPatchError  (heading/block not found)
└── VCSError           (jj failures)
```

---

## System Prompt

### Base prompt

```
You are an assistant that helps manage an Obsidian vault. You operate on markdown
files in the vault using the provided tools.

Rules:
- Preserve YAML frontmatter unless asked to change it.
- Preserve wikilinks ([[...]]) unless asked to change them.
- Prefer minimal edits over rewriting entire files.
- Do not delete content unless clearly intended by the user.
- Use tools to inspect and edit files; do not only describe changes.
- After making changes, provide a brief summary of what you did.
- Only read and write files within the vault.
```

### Current file context

If `current_file` is provided:
```
The user is currently viewing: {current_file}
```

The system prompt is set via pydantic-ai's `instructions` parameter on the Agent, with a dynamic `@agent.instructions` decorator for the current-file context.

---

## HTTP API

### Endpoints

#### `POST /api/apply`

Execute an instruction against the vault.

**Request:**
```json
{
    "instruction": "Add a summary section to this note",
    "current_file": "Projects/Alpha.md"
}
```

`instruction` is required. `current_file` is optional.

**Response (200):**
```json
{
    "ok": true,
    "updated": true,
    "summary": "Added a summary section with key points.",
    "changed_files": ["Projects/Alpha.md"],
    "warning": null,
    "error": null
}
```

#### `POST /api/undo`

Undo the last jj change. Empty request body.

**Response (200):**
```json
{
    "ok": true,
    "updated": true,
    "summary": "Last change undone.",
    "changed_files": [],
    "warning": null,
    "error": null
}
```

#### `GET /api/health`

**Response (200):**
```json
{
    "ok": true,
    "status": "healthy"
}
```

### Error semantics

| Scenario | HTTP | `ok` | `error` |
|---|---|---|---|
| Successful operation with changes | 200 | `true` | `null` |
| Successful operation, no changes | 200 | `true` | `null` |
| LLM API failure | 200 | `false` | `"LLM call failed: {details}"` |
| Usage limit exceeded | 200 | `false` | `"Agent exceeded max iterations (20)"` |
| Operation timeout | 200 | `false` | `"Operation timed out after 120s"` |
| Mutation lock held | 409 | — | `"Another operation is already running"` |
| Missing instruction | 200 | `false` | `"instruction is required"` |
| Files changed, commit failed | 200 | `true` | `null` (but `warning` set) |
| Undo failure | 200 | `false` | `"undo failed: {details}"` |

Application-level errors return HTTP 200 with `ok=false`. Only infrastructure errors (lock conflict) return non-200. This simplifies client parsing.

### Timeout contract

The agent enforces a hard timeout on `/api/apply` (default: 120s). A reverse proxy in front should use a longer timeout (e.g., 180s) so the agent always responds before the proxy cuts the connection.

### Server entry point

```bash
AGENT_VAULT_DIR=/path/to/vault \
ANTHROPIC_API_KEY=sk-... \
python -m obsidian_agent
```

The `__main__.py` reads `AgentConfig` and starts uvicorn:

```python
uvicorn.run("obsidian_agent.app:app", host=config.host, port=config.port, log_level="info")
```

### FastAPI lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    config = AgentConfig()
    vault = Vault(config.vault_dir, jj_bin=config.jj_bin, jj_timeout=config.jj_timeout)
    agent = Agent(config, vault)
    app.state.agent = agent
    yield
```

### Endpoint implementation

```python
@app.post("/api/apply", response_model=OperationResult)
async def apply_instruction(request: ApplyRequest):
    agent = app.state.agent

    if request.instruction is None or not request.instruction.strip():
        return OperationResult(ok=False, updated=False, summary="", error="instruction is required")

    try:
        result = await agent.run(request.instruction, request.current_file)
        return result
    except BusyError:
        raise HTTPException(status_code=409, detail="Another operation is already running")
```

---

## Project Structure

```
obsidian-agent/
├── pyproject.toml
├── src/
│   └── obsidian_agent/
│       ├── __init__.py           # Public exports: Agent, AgentConfig, RunResult
│       ├── __main__.py           # Server entry point (uvicorn)
│       ├── app.py                # FastAPI application + endpoint handlers
│       ├── agent.py              # Agent class — async LLM tool loop via pydantic-ai
│       ├── config.py             # AgentConfig (pydantic-settings)
│       ├── models.py             # ApplyRequest, OperationResult, HealthResponse
│       ├── tools.py              # Tool definitions via @agent.tool + VaultDeps
│       └── prompt.py             # build_system_prompt(current_file) → str
├── tests/
│   ├── conftest.py               # Fixtures: TestModel, FunctionModel, temp vault
│   ├── test_agent.py             # Agent loop with pydantic-ai TestModel/FunctionModel
│   ├── test_tools.py             # Tool dispatch + changed file tracking
│   ├── test_app.py               # HTTP endpoint tests (TestClient)
│   ├── test_config.py            # Configuration validation + env var parsing
│   └── test_prompt.py            # System prompt construction
└── demo-vault/                   # Fixture vault for integration tests
```

### Module responsibilities

| Module | Owns | Does not own |
|---|---|---|
| `agent.py` | pydantic-ai Agent construction, `run()` / `undo()` orchestration, agent-level lock, commit-after-loop | Vault I/O, tool schemas, HTTP |
| `tools.py` | Tool function definitions (`@agent.tool`), `VaultDeps` dataclass, `WRITE_TOOLS` set, error wrapping | Vault implementation, agent loop |
| `prompt.py` | System prompt text, current-file context injection | Tool definitions, agent config |
| `config.py` | `AgentConfig`, validation, base URL normalization | Everything else |
| `models.py` | `ApplyRequest`, `OperationResult`, `HealthResponse` pydantic models | Business logic |
| `app.py` | FastAPI app, lifespan, endpoint handlers, timeout wrapping | Agent loop, tool dispatch |

---

## Testing

### Strategy

pydantic-ai provides first-class testing primitives that replace the need for custom mock LLM classes. The test strategy uses:

1. **`TestModel`** — Exercises all registered tools automatically without real LLM calls. Returns generated responses matching the output type.
2. **`FunctionModel`** — Custom logic for scripted multi-turn conversations (read file → write file → summarize).
3. **`Agent.override()`** — Swaps model and deps in tests without touching production code.
4. **`ALLOW_MODEL_REQUESTS = False`** — Global safety net in `conftest.py` to prevent accidental real API calls.

### Test fixtures

```python
# conftest.py
import pytest
from pydantic_ai import models
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel, AgentInfo
from obsidian_ops import Vault

# Block all real LLM calls in tests
models.ALLOW_MODEL_REQUESTS = False

@pytest.fixture
def vault(tmp_path):
    """Temporary vault with sample files."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("---\ntitle: Test\n---\n# Hello\nContent here.\n")
    (vault_dir / "Projects").mkdir()
    (vault_dir / "Projects/Alpha.md").write_text("---\nstatus: draft\n---\n# Alpha\n")
    return Vault(str(vault_dir))

@pytest.fixture
def test_model():
    """pydantic-ai TestModel that exercises tools without real LLM calls."""
    return TestModel()

@pytest.fixture
def scripted_model():
    """FunctionModel that replays a realistic multi-turn conversation."""
    from pydantic_ai import ModelMessage, ModelResponse, TextPart, ToolCallPart

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        turn = len([m for m in messages if hasattr(m, 'parts') and any(
            isinstance(p, ToolCallPart) for p in m.parts
        )])
        if turn == 0:
            return ModelResponse(parts=[
                ToolCallPart("read_file", {"path": "note.md"})
            ])
        elif turn == 1:
            return ModelResponse(parts=[
                ToolCallPart("write_file", {"path": "note.md", "content": "updated"})
            ])
        else:
            return ModelResponse(parts=[TextPart("Updated note.md successfully.")])

    return FunctionModel(model_fn)
```

### Test categories

#### Agent loop tests (`test_agent.py`)

Uses `FunctionModel` for scripted conversations and `TestModel` for tool-exercising smoke tests.

- **Happy path**: LLM reads a file, writes a file, returns summary. Verify `RunResult` fields.
- **No changes**: LLM returns text without tool calls. Verify `updated=False`.
- **Multiple iterations**: LLM makes several tool calls across rounds.
- **Usage limit exceeded**: `UsageLimits(request_limit=2)` with a model that keeps calling tools. Verify error result.
- **Tool execution error**: LLM calls a tool that fails (path not found). Verify error string sent back to LLM, loop continues.
- **LLM API error**: Model raises. Verify error result.
- **Commit after changes**: Verify `vault.commit()` called with instruction text.
- **Commit failure**: Commit raises but files changed. Verify `ok=True` with `warning`.
- **Changed file tracking**: Write tools tracked, read tools not.
- **Agent-level lock**: Concurrent `run()` calls raise `BusyError`.

```python
import pytest
from pydantic_ai.usage import UsageLimits
from obsidian_agent import Agent, AgentConfig

pytestmark = pytest.mark.anyio

async def test_happy_path(vault, scripted_model):
    config = AgentConfig(vault_dir=vault.root)
    agent = Agent(config, vault)

    with agent._pydantic_agent.override(model=scripted_model):
        result = await agent.run("Update note.md")

    assert result.ok
    assert result.updated
    assert "note.md" in result.changed_files
    assert result.summary  # non-empty
```

#### Tool dispatch tests (`test_tools.py`)

Tests each tool function against a real temporary vault (via obsidian-ops).

- Each tool dispatches to the correct obsidian-ops method
- `read_file` returns file content as string
- `write_file` returns success message, adds path to `changed_files`
- `delete_file` returns success message, adds path to `changed_files`
- `list_files` formats results
- `search_files` formats results with paths and snippets
- `get_frontmatter` returns JSON string
- `update_frontmatter` returns success message, tracks change
- `read_heading` / `write_heading` — content and "not found" cases
- `read_block` / `write_block` — content and "not found" cases
- Unknown tool returns error message (does not raise)
- `PathError` returns `"Error: path escapes vault: ..."`
- `FileTooLargeError` returns `"Error: ..."`
- `ContentPatchError` returns `"Error: ..."`
- `BusyError` re-raises (not caught as tool error)

#### HTTP endpoint tests (`test_app.py`)

Uses FastAPI's `TestClient` with `Agent.override(model=TestModel())`:

- `POST /api/apply` with valid request → 200 with `OperationResult`
- `POST /api/apply` with empty instruction → error result
- `POST /api/apply` during active operation → 409 Conflict
- `POST /api/undo` → 200 with `OperationResult`
- `POST /api/undo` during active operation → 409
- `GET /api/health` → 200 with `{"ok": true, "status": "healthy"}`

#### Configuration tests (`test_config.py`)

- Valid config from environment variables
- Missing `vault_dir` → error
- `vault_dir` not a directory → error
- Base URL normalization cases
- Default values correct
- Model string validated

#### System prompt tests (`test_prompt.py`)

- Base prompt contains all required rules
- With `current_file` — prompt includes file context line
- Without `current_file` — no file context line

### Running tests

```bash
pytest tests/ -v
pytest tests/ --cov=obsidian_agent --cov-report=term-missing
```

---

## Implementation Priorities

### Phase 1: Agent core (no HTTP)

1. `config.py` — `AgentConfig` with pydantic-settings, validation, base URL normalization
2. `prompt.py` — System prompt builder
3. `models.py` — `RunResult`, `ApplyRequest`, `OperationResult`, `HealthResponse`
4. `tools.py` — `VaultDeps`, tool functions with `@agent.tool`, error wrapping, changed-file tracking
5. `agent.py` — `Agent` class wrapping pydantic-ai `Agent`, async `run()` with agent-level lock, `undo()`, commit logic
6. `__init__.py` — Public API exports
7. Tests with `TestModel`, `FunctionModel`, and temp vault

### Phase 2: HTTP server

1. `app.py` — FastAPI application with lifespan, endpoint handlers, timeout wrapping
2. `__main__.py` — Server entry point
3. HTTP endpoint tests
4. Timeout behavior tests

### Phase 3: Integration testing

1. End-to-end with `FunctionModel`: apply → verify vault state → undo → verify revert
2. Concurrent request handling (409 on conflict)
3. Demo-vault fixture tests
4. Model resolution for local servers (if `llm_base_url` set)

---

## Design Decisions

### Why pydantic-ai

- **Multi-provider routing** out of the box — Anthropic, OpenAI, Groq, Ollama, and any OpenAI-compatible server via `"provider:model"` strings. No custom `LLMInterface` protocol or factory pattern needed.
- **Dependency injection** via `RunContext` — the `Vault` instance and `changed_files` set are cleanly injected into tools without globals or closures.
- **Built-in testing** — `TestModel`, `FunctionModel`, `Agent.override()`, and `ALLOW_MODEL_REQUESTS` eliminate the need for custom mock infrastructure.
- **Usage limits** — `UsageLimits(request_limit=N)` prevents infinite tool-calling loops, replacing manual iteration counting.
- **Async-native** — The agent loop is async end-to-end, matching FastAPI's async handlers. No `asyncio.to_thread` wrapping needed.
- **API stability** — v1 commitment means no breaking changes until v2.

### Why async end-to-end

pydantic-ai is async-first. The agent loop, tool functions, and FastAPI handlers are all async. This eliminates the sync/async impedance mismatch described in the review (section 3). The `agent.run()` method calls pydantic-ai's `agent.run()` directly — no thread wrapping.

### Why obsidian-ops for all vault access

obsidian-agent never touches the filesystem directly. obsidian-ops enforces:
- Path sandboxing (no traversal outside vault root)
- Mutation locking (no concurrent writes)
- VCS integration (jj commits)
- File size limits (>512KB rejection)

### Why agent-level lock before LLM call

Without this, two concurrent HTTP requests could both start expensive LLM calls, and the second would only fail when it tried to write (hitting obsidian-ops' mutation lock). The agent-level lock fails fast, saving the wasted LLM call.

---

## Non-Goals (v1)

- SSE streaming / real-time progress
- Session persistence / multi-turn conversation
- Interface registry / multiple UI modes
- Frontend code (JavaScript, CSS)
- Site generation or rebuild triggering
- URL-to-file path resolution
- Multi-user support or authentication
- Database-backed state
- Background job queue

---

## Future Extensions

- **SSE streaming** for real-time progress via pydantic-ai's `run_stream()`
- **Session persistence** using pydantic-ai's `message_history` for multi-turn chat
- **Toolsets** — group vault tools into composable `FunctionToolset` objects (read-only vs. read-write)
- **Capabilities** — pydantic-ai's capability system for bundling tools + hooks + instructions
- **Additional tools**: `fetch_url` (web content → vault), `get_file_history` (jj log)
- **Human-in-the-loop** — pydantic-ai's deferred tool approval for write operations

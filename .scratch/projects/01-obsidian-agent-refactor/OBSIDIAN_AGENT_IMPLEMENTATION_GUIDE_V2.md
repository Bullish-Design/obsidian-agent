# obsidian-agent Implementation Guide V2

Step-by-step guide to implement obsidian-agent from scratch. Each step builds on the previous one. Complete all verification checks before moving to the next step.

---

## Prerequisites

- Python >= 3.13
- `uv` package manager
- A working `obsidian-ops` install (>= 0.1.0)
- `jj` (Jujutsu) binary available on PATH
- An Anthropic API key (for manual integration testing only — all automated tests use mock models)

---

## Step 1: Project Scaffold

### What to do

Create the project structure and `pyproject.toml`.

```
obsidian-agent/
├── pyproject.toml
├── src/
│   └── obsidian_agent/
│       └── __init__.py
└── tests/
    └── __init__.py
```

### pyproject.toml

```toml
[project]
name = "obsidian-agent"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "obsidian-ops>=0.1.0",
    "pydantic-ai>=0.1.70",
    "pydantic>=2.12.0",
    "pydantic-settings>=2.0.0",
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "httpx>=0.28.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.25.0",
    "anyio>=4.0",
    "coverage>=7.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/obsidian_agent"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

### `src/obsidian_agent/__init__.py`

Leave empty for now. Public exports will be added in Step 6.

### Verification

```bash
uv pip install -e ".[dev]"
pytest tests/ -v  # Should collect 0 tests, exit clean
```

---

## Step 2: Configuration (`config.py`)

### What to do

Create `src/obsidian_agent/config.py` with the `AgentConfig` class using `pydantic-settings`.

### Implementation details

```python
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    vault_dir: Path                                    # Required. Must exist and be a directory.
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

### Validation logic

Add a `model_validator` (mode `"after"`) or `field_validator` for:

1. **`vault_dir`**: Must exist and be a directory. Raise `ValueError` if not.
2. **`llm_model`**: Must contain a `:` separator (the `"provider:model-name"` format). Raise `ValueError` if not.
3. **`llm_base_url` normalization**: If set:
   - Strip trailing slashes.
   - If the URL has no path (or just `/`), append `/v1`.
   - Examples:
     - `http://localhost:8000` → `http://localhost:8000/v1`
     - `http://localhost:8000/` → `http://localhost:8000/v1`
     - `http://localhost:8000/v1/` → `http://localhost:8000/v1`

Use `urllib.parse.urlparse` for base URL normalization. Parse the URL, check if the path is empty or `/`, and rebuild with `/v1` appended if needed.

### Tests: `tests/test_config.py`

Write tests for:

1. **Valid config from kwargs**: Pass `vault_dir` pointing to a `tmp_path` directory. Assert all defaults are correct.
2. **Valid config from env vars**: Use `monkeypatch.setenv` to set `AGENT_VAULT_DIR`, `AGENT_LLM_MODEL`, etc. Construct `AgentConfig()` and assert values are read from env.
3. **Missing vault_dir**: Expect `ValidationError`.
4. **vault_dir is a file, not a directory**: Create a file at `tmp_path / "not_a_dir"`, pass it as `vault_dir`. Expect `ValueError`.
5. **vault_dir does not exist**: Pass a nonexistent path. Expect `ValueError`.
6. **Base URL normalization**: Test all three cases above. Create config with each `llm_base_url` value, assert the normalized result.
7. **Default values**: Create config with only `vault_dir`, assert every default matches the table in the README.
8. **Invalid model string**: Pass `llm_model="no-colon-here"`. Expect `ValueError`.
9. **Extra env vars ignored**: Set `AGENT_UNKNOWN_FIELD=foo`, construct config. No error raised.

### Verification

```bash
pytest tests/test_config.py -v
# All 9+ tests pass
```

---

## Step 3: Models (`models.py`)

### What to do

Create `src/obsidian_agent/models.py` with the data models used by the agent and HTTP API.

### Implementation details

```python
from dataclasses import dataclass, field
from pydantic import BaseModel


@dataclass
class RunResult:
    ok: bool
    updated: bool
    summary: str
    changed_files: list[str] = field(default_factory=list)
    error: str | None = None
    warning: str | None = None


class ApplyRequest(BaseModel):
    instruction: str
    current_file: str | None = None


class OperationResult(BaseModel):
    ok: bool
    updated: bool
    summary: str
    changed_files: list[str] = []
    error: str | None = None
    warning: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    status: str
```

`RunResult` is a dataclass (internal use). `OperationResult` is a Pydantic model (HTTP response serialization). They have the same fields — `OperationResult` can be constructed from a `RunResult`:

```python
# In app.py later:
OperationResult(**vars(result))
# or
OperationResult(
    ok=result.ok,
    updated=result.updated,
    summary=result.summary,
    changed_files=result.changed_files,
    error=result.error,
    warning=result.warning,
)
```

### Tests: `tests/test_models.py` (lightweight)

1. **RunResult defaults**: Create `RunResult(ok=True, updated=False, summary="done")`. Assert `changed_files == []`, `error is None`, `warning is None`.
2. **OperationResult serialization**: Create an `OperationResult`, call `.model_dump()`. Assert JSON-serializable dict has all fields.
3. **ApplyRequest validation**: Create `ApplyRequest(instruction="do stuff")`. Assert `current_file is None`.
4. **ApplyRequest with current_file**: Create with both fields, assert values.

### Verification

```bash
pytest tests/test_models.py -v
```

---

## Step 4: System Prompt (`prompt.py`)

### What to do

Create `src/obsidian_agent/prompt.py` with the system prompt builder.

### Implementation details

```python
BASE_PROMPT = """\
You are an assistant that helps manage an Obsidian vault. You operate on markdown
files in the vault using the provided tools.

Rules:
- Preserve YAML frontmatter unless asked to change it.
- Preserve wikilinks ([[...]]) unless asked to change them.
- Prefer minimal edits over rewriting entire files.
- Do not delete content unless clearly intended by the user.
- Use tools to inspect and edit files; do not only describe changes.
- After making changes, provide a brief summary of what you did.
- Only read and write files within the vault."""


def build_system_prompt(current_file: str | None = None) -> str:
    """Build the full system prompt, optionally including current file context."""
    prompt = BASE_PROMPT
    if current_file:
        prompt += f"\n\nThe user is currently viewing: {current_file}"
    return prompt
```

### Tests: `tests/test_prompt.py`

1. **Base prompt content**: Call `build_system_prompt()` (no args). Assert it contains:
   - `"Obsidian vault"`
   - `"YAML frontmatter"`
   - `"wikilinks"`
   - `"minimal edits"`
   - `"tools"`
2. **With current_file**: Call `build_system_prompt("Projects/Alpha.md")`. Assert it contains `"The user is currently viewing: Projects/Alpha.md"`.
3. **Without current_file**: Call `build_system_prompt()`. Assert `"currently viewing"` is NOT in the result.
4. **With current_file=None**: Same as above — explicitly pass `None`.

### Verification

```bash
pytest tests/test_prompt.py -v
```

---

## Step 5: Tools (`tools.py`)

### What to do

Create `src/obsidian_agent/tools.py` with the `VaultDeps` dataclass and all 11 tool functions.

### Important design note

Tools are NOT registered on the pydantic-ai Agent in this file. Instead, this module defines plain async functions that take `RunContext[VaultDeps]` as their first argument. The `agent.py` module will import these functions and register them on the pydantic-ai Agent using `@agent.tool` decorators.

**Alternative approach** (simpler): Define the tool functions here and export a `register_tools(agent)` function that attaches them all. This keeps tool definitions in one file but lets `agent.py` control registration.

### Implementation details

```python
from dataclasses import dataclass, field
import json

from pydantic_ai import RunContext
from obsidian_ops import Vault
from obsidian_ops.errors import VaultError, BusyError


@dataclass
class VaultDeps:
    vault: Vault
    changed_files: set[str] = field(default_factory=set)


WRITE_TOOLS = {"write_file", "delete_file", "update_frontmatter", "write_heading", "write_block"}
```

Define each tool function. Every tool follows this pattern:

```python
async def read_file(ctx: RunContext[VaultDeps], path: str) -> str:
    """Read the contents of a file in the vault. Path is relative to vault root."""
    try:
        return ctx.deps.vault.read_file(path)
    except BusyError:
        raise
    except VaultError as e:
        return f"Error: {e}"
```

For write tools, add the tracking line before the return:

```python
async def write_file(ctx: RunContext[VaultDeps], path: str, content: str) -> str:
    """Write content to a file in the vault. Creates or overwrites. Path is relative to vault root."""
    try:
        ctx.deps.vault.write_file(path, content)
        ctx.deps.changed_files.add(path)
        return f"Successfully wrote {path}"
    except BusyError:
        raise
    except VaultError as e:
        return f"Error: {e}"
```

Implement all 11 tools from the tool table in the README:

| # | Function | Args | Return on success | Tracks change? |
|---|----------|------|--------------------|----------------|
| 1 | `read_file` | `path: str` | File contents string | No |
| 2 | `write_file` | `path: str, content: str` | `"Successfully wrote {path}"` | Yes |
| 3 | `delete_file` | `path: str` | `"Deleted {path}"` | Yes |
| 4 | `list_files` | `pattern: str` | `"Found N files:\n..."` or `"No files found."` | No |
| 5 | `search_files` | `query: str, glob: str = "*.md"` | Formatted results with paths and snippets, or `"No matches found."` | No |
| 6 | `get_frontmatter` | `path: str` | JSON string of frontmatter dict, or `"No frontmatter found."` | No |
| 7 | `update_frontmatter` | `path: str, updates: dict` | `"Updated frontmatter for {path}"` | Yes |
| 8 | `read_heading` | `path: str, heading: str` | Content string, or `"Heading '{heading}' not found in {path}"` | No |
| 9 | `write_heading` | `path: str, heading: str, content: str` | `"Updated heading '{heading}' in {path}"` | Yes |
| 10 | `read_block` | `path: str, block_id: str` | Content string, or `"Block '{block_id}' not found in {path}"` | No |
| 11 | `write_block` | `path: str, block_id: str, content: str` | `"Updated block '{block_id}' in {path}"` | Yes |

### Registration function

```python
def register_tools(agent):
    """Register all vault tools on a pydantic-ai Agent."""
    agent.tool(read_file)
    agent.tool(write_file)
    agent.tool(delete_file)
    agent.tool(list_files)
    agent.tool(search_files)
    agent.tool(get_frontmatter)
    agent.tool(update_frontmatter)
    agent.tool(read_heading)
    agent.tool(write_heading)
    agent.tool(read_block)
    agent.tool(write_block)
```

### Tests: `tests/test_tools.py`

Create a `conftest.py` fixture (or local fixture) for a temporary vault:

```python
@pytest.fixture
def vault(tmp_path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("---\ntitle: Test\n---\n# Hello\nContent here.\n")
    (vault_dir / "Projects").mkdir()
    (vault_dir / "Projects/Alpha.md").write_text("---\nstatus: draft\n---\n# Alpha\nAlpha content.\n")
    return Vault(str(vault_dir))

@pytest.fixture
def deps(vault):
    return VaultDeps(vault=vault)
```

To test tools directly without a running pydantic-ai agent, you need to call the tool functions. Since they expect `RunContext[VaultDeps]`, you have two options:

**Option A**: Create a minimal pydantic-ai Agent with TestModel, register tools, and use `agent.run()` — but this tests the agent loop, not individual tools.

**Option B (preferred for unit tests)**: Call the underlying vault methods directly, since the tool functions are thin wrappers. Test that:
- The vault method is called correctly
- `changed_files` is updated for write tools
- Error cases return `"Error: ..."` strings

For Option B, you can mock `RunContext` or restructure the tool functions to also accept `VaultDeps` directly for testing. A pragmatic approach: extract the logic into a helper that takes `VaultDeps`, and have the tool function delegate to it.

**Simplest approach**: Use pydantic-ai's test infrastructure. Create an Agent with `TestModel`, register tools, and run it. Then inspect `deps.changed_files`.

Write these tests:

1. **read_file returns content**: Read `note.md`, assert content includes `"Content here."`.
2. **write_file writes and tracks**: Write to `new.md`, assert file exists on disk, assert `"new.md"` in `deps.changed_files`.
3. **delete_file deletes and tracks**: Delete `note.md`, assert file gone, assert `"note.md"` in `deps.changed_files`.
4. **list_files returns formatted list**: List `"*.md"`, assert `"note.md"` appears in result.
5. **list_files no matches**: List `"*.xyz"`, assert `"No files found."`.
6. **search_files returns results**: Search for `"Content"`, assert `"note.md"` in result.
7. **search_files no matches**: Search for `"nonexistent_term_xyz"`, assert `"No matches found."`.
8. **get_frontmatter returns JSON**: Get frontmatter from `note.md`, parse result as JSON, assert `title == "Test"`.
9. **get_frontmatter no frontmatter**: Create a file without frontmatter, get frontmatter, assert `"No frontmatter found."`.
10. **update_frontmatter updates and tracks**: Update `note.md` frontmatter with `{"status": "done"}`. Read frontmatter back, assert `status == "done"`. Assert path tracked.
11. **read_heading returns content**: Read heading `"# Hello"` from `note.md`. Assert `"Content here."` in result.
12. **read_heading not found**: Read heading `"# Nonexistent"`. Assert `"not found"` in result.
13. **write_heading writes and tracks**: Write heading `"# Hello"` with new content. Read back, assert updated. Assert path tracked.
14. **read_block / write_block**: Create a file with a block (`^my-block`), read it, write it, verify.
15. **PathError returns error string**: Try to `read_file("../../etc/passwd")`. Assert result starts with `"Error:"`.
16. **BusyError re-raises**: This requires mocking the vault to raise `BusyError`. Assert the tool function raises `BusyError` (does not catch it).

### Verification

```bash
pytest tests/test_tools.py -v
# All 16+ tests pass
```

---

## Step 6: Agent Core (`agent.py`)

### What to do

Create `src/obsidian_agent/agent.py` with the `Agent` class that wraps pydantic-ai's `Agent`.

### Implementation details

```python
import asyncio
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.usage import UsageLimits

from obsidian_ops import Vault
from obsidian_ops.errors import BusyError

from .config import AgentConfig
from .models import RunResult
from .prompt import build_system_prompt
from .tools import VaultDeps, register_tools


class BusyError(Exception):
    """Raised when the agent is already processing a request."""
    pass


class Agent:
    def __init__(self, config: AgentConfig, vault: Vault | None = None):
        self.config = config
        self.vault = vault or Vault(
            str(config.vault_dir),
            jj_bin=config.jj_bin,
            jj_timeout=config.jj_timeout,
        )
        self._busy = False

        # Create pydantic-ai Agent
        self._pydantic_agent = PydanticAgent(
            model=config.llm_model,
            deps_type=VaultDeps,
            system_prompt=build_system_prompt(),
        )

        # Register all vault tools
        register_tools(self._pydantic_agent)

    async def run(
        self,
        instruction: str,
        current_file: str | None = None,
    ) -> RunResult:
        if self._busy:
            raise BusyError("Another operation is already running")
        self._busy = True
        try:
            return await self._run_impl(instruction, current_file)
        finally:
            self._busy = False

    async def _run_impl(
        self,
        instruction: str,
        current_file: str | None,
    ) -> RunResult:
        # Build deps with fresh changed_files set
        deps = VaultDeps(vault=self.vault)

        # Build dynamic system prompt with current_file context
        # Use @agent.instructions or override the system prompt
        prompt = build_system_prompt(current_file)

        # Set usage limits
        limits = UsageLimits(request_limit=self.config.max_iterations)

        try:
            result = await self._pydantic_agent.run(
                instruction,
                deps=deps,
                usage_limits=limits,
            )
        except Exception as e:
            return RunResult(
                ok=False,
                updated=False,
                summary="",
                error=f"Agent error: {e}",
            )

        # Extract summary from LLM's final text response
        summary = result.output if isinstance(result.output, str) else str(result.output)

        # Build changed files list
        changed_files = sorted(deps.changed_files)

        # Commit if files were changed
        warning = None
        if changed_files:
            commit_msg = instruction[:72]
            try:
                self.vault.commit(commit_msg)
            except Exception as e:
                warning = f"Commit failed: {e}"

        return RunResult(
            ok=True,
            updated=len(changed_files) > 0,
            summary=summary,
            changed_files=changed_files,
            warning=warning,
        )

    async def undo(self) -> RunResult:
        try:
            self.vault.undo()
            return RunResult(ok=True, updated=True, summary="Last change undone.")
        except Exception as e:
            return RunResult(ok=False, updated=False, summary="", error=f"undo failed: {e}")
```

### Dynamic system prompt note

pydantic-ai supports dynamic instructions via the `@agent.instructions` decorator. However, since `current_file` varies per request, you have two options:

1. **Pass system prompt override per `run()` call** — check pydantic-ai docs for how to override the system prompt per run. The `run()` method may accept a `system_prompt` parameter or you can use `message_history`.
2. **Use `@agent.instructions` with a closure** that captures `current_file` — but this would require re-creating the agent or using a mutable reference.

The simplest approach: set the base system prompt on the Agent at init, and prepend the current-file context to the user message (as a prefix) rather than injecting it into the system prompt dynamically. Or, check if pydantic-ai's `Agent.run()` accepts additional system prompt text.

**Recommended**: Use pydantic-ai's `@_pydantic_agent.instructions` decorator with a function that receives `RunContext` and returns the dynamic portion. Store `current_file` on `VaultDeps` so the instructions function can read it:

```python
@dataclass
class VaultDeps:
    vault: Vault
    changed_files: set[str] = field(default_factory=set)
    current_file: str | None = None
```

Then in `agent.py`:

```python
@self._pydantic_agent.instructions
def dynamic_instructions(ctx: RunContext[VaultDeps]) -> str:
    base = build_system_prompt()
    if ctx.deps.current_file:
        base += f"\n\nThe user is currently viewing: {ctx.deps.current_file}"
    return base
```

And when constructing deps in `_run_impl`:

```python
deps = VaultDeps(vault=self.vault, current_file=current_file)
```

### Public exports: `__init__.py`

Update `src/obsidian_agent/__init__.py`:

```python
from .agent import Agent, BusyError
from .config import AgentConfig
from .models import RunResult

__all__ = ["Agent", "AgentConfig", "RunResult", "BusyError"]
```

### Tests: `tests/test_agent.py`

Set up `conftest.py` with the global safety net and shared fixtures:

```python
# tests/conftest.py
import pytest
from pydantic_ai import models

# Block all real LLM calls in tests
models.ALLOW_MODEL_REQUESTS = False
```

Now write agent tests. All tests use `pytest.mark.anyio` (or `pytest.mark.asyncio` depending on your pytest-asyncio config).

**Fixtures needed:**

```python
@pytest.fixture
def vault(tmp_path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("---\ntitle: Test\n---\n# Hello\nContent here.\n")
    return Vault(str(vault_dir))

@pytest.fixture
def agent(vault, tmp_path):
    config = AgentConfig(vault_dir=tmp_path / "vault")
    return Agent(config, vault)
```

**Tests:**

1. **Happy path (scripted model)**:
   - Create a `FunctionModel` that: turn 0 → call `read_file(path="note.md")`, turn 1 → call `write_file(path="note.md", content="updated")`, turn 2 → return text `"Updated note.md"`.
   - Use `agent._pydantic_agent.override(model=scripted_model)` context manager.
   - Call `await agent.run("Update note.md")`.
   - Assert `result.ok is True`.
   - Assert `result.updated is True`.
   - Assert `"note.md" in result.changed_files`.
   - Assert `result.summary` is non-empty.

2. **No changes (text-only response)**:
   - Create a `FunctionModel` that immediately returns text (no tool calls).
   - Assert `result.ok is True`, `result.updated is False`, `result.changed_files == []`.

3. **Tool execution error**:
   - Create a `FunctionModel` that calls `read_file(path="nonexistent.md")` then returns text.
   - Assert `result.ok is True` (error was handled in the tool, not fatal).

4. **Usage limit exceeded**:
   - Create a `FunctionModel` that always calls a tool (never returns text).
   - Set `config.max_iterations = 2`.
   - Assert `result.ok is False`, `"limit"` or similar in `result.error`.

5. **Changed file tracking — read tools not tracked**:
   - Scripted model calls only `read_file`, then returns text.
   - Assert `result.changed_files == []`.

6. **Changed file tracking — write tools tracked**:
   - Scripted model calls `write_file`, `write_heading`, then returns text.
   - Assert both paths are in `result.changed_files`.

7. **Agent-level lock (BusyError)**:
   - Start `agent.run()` in a task.
   - While it's running, call `agent.run()` again.
   - Assert the second call raises `BusyError`.

   Implementation approach:
   ```python
   async def test_busy_error(agent, scripted_model):
       # Use a slow FunctionModel that sleeps
       async def slow_model(...):
           await asyncio.sleep(1)
           return ModelResponse(parts=[TextPart("done")])

       with agent._pydantic_agent.override(model=FunctionModel(slow_model)):
           task = asyncio.create_task(agent.run("slow task"))
           await asyncio.sleep(0.1)  # Let it acquire lock
           with pytest.raises(BusyError):
               await agent.run("second task")
           await task
   ```

8. **Undo success**: Mock `vault.undo()` to succeed. Assert `result.ok is True`.

9. **Undo failure**: Mock `vault.undo()` to raise. Assert `result.ok is False`, error message set.

10. **Commit failure after changes**: Scripted model writes a file. Mock `vault.commit()` to raise. Assert `result.ok is True`, `result.warning` contains `"Commit failed"`.

### Verification

```bash
pytest tests/test_agent.py -v
# All 10+ tests pass

# Run all tests together to make sure nothing conflicts:
pytest tests/ -v
```

---

## Step 7: HTTP API (`app.py` and `__main__.py`)

### What to do

Create `src/obsidian_agent/app.py` (FastAPI application) and `src/obsidian_agent/__main__.py` (server entry point).

### `app.py` implementation

```python
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .agent import Agent, BusyError
from .config import AgentConfig
from .models import ApplyRequest, OperationResult, HealthResponse

from obsidian_ops import Vault


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = AgentConfig()
    vault = Vault(str(config.vault_dir), jj_bin=config.jj_bin, jj_timeout=config.jj_timeout)
    agent = Agent(config, vault)
    app.state.agent = agent
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/api/apply", response_model=OperationResult)
async def apply_instruction(request: ApplyRequest):
    agent: Agent = app.state.agent

    if not request.instruction.strip():
        return OperationResult(ok=False, updated=False, summary="", error="instruction is required")

    try:
        result = await asyncio.wait_for(
            agent.run(request.instruction, request.current_file),
            timeout=agent.config.operation_timeout,
        )
        return OperationResult(
            ok=result.ok,
            updated=result.updated,
            summary=result.summary,
            changed_files=result.changed_files,
            error=result.error,
            warning=result.warning,
        )
    except asyncio.TimeoutError:
        return OperationResult(
            ok=False, updated=False, summary="",
            error=f"Operation timed out after {agent.config.operation_timeout}s",
        )
    except BusyError:
        raise HTTPException(status_code=409, detail="Another operation is already running")


@app.post("/api/undo", response_model=OperationResult)
async def undo():
    agent: Agent = app.state.agent
    try:
        result = await agent.undo()
        return OperationResult(
            ok=result.ok,
            updated=result.updated,
            summary=result.summary,
            changed_files=result.changed_files,
            error=result.error,
            warning=result.warning,
        )
    except BusyError:
        raise HTTPException(status_code=409, detail="Another operation is already running")


@app.get("/api/health", response_model=HealthResponse)
async def health():
    return HealthResponse(ok=True, status="healthy")
```

### `__main__.py` implementation

```python
import uvicorn
from .config import AgentConfig

def main():
    config = AgentConfig()
    uvicorn.run(
        "obsidian_agent.app:app",
        host=config.host,
        port=config.port,
        log_level="info",
    )

if __name__ == "__main__":
    main()
```

### Tests: `tests/test_app.py`

Use FastAPI's `TestClient` (from `fastapi.testclient`). You need to bypass the normal lifespan (which reads env vars) and instead inject a test agent directly.

**Test setup approach:**

```python
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from obsidian_agent.app import app
from obsidian_agent.agent import Agent
from obsidian_agent.config import AgentConfig
from obsidian_ops import Vault


@pytest.fixture
def client(tmp_path):
    # Create temp vault
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("# Test\nContent.\n")

    vault = Vault(str(vault_dir))
    config = AgentConfig(vault_dir=vault_dir)
    agent = Agent(config, vault)

    # Inject test agent into app state, bypassing lifespan
    app.state.agent = agent

    # Override pydantic-ai model with TestModel
    with agent._pydantic_agent.override(model=TestModel()):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
```

**Tests:**

1. **POST /api/apply — valid request**:
   ```python
   response = client.post("/api/apply", json={"instruction": "Update note.md"})
   assert response.status_code == 200
   data = response.json()
   assert data["ok"] is True
   ```

2. **POST /api/apply — empty instruction**:
   ```python
   response = client.post("/api/apply", json={"instruction": "   "})
   assert response.status_code == 200
   data = response.json()
   assert data["ok"] is False
   assert data["error"] == "instruction is required"
   ```

3. **POST /api/apply — with current_file**:
   ```python
   response = client.post("/api/apply", json={
       "instruction": "Summarize this",
       "current_file": "note.md"
   })
   assert response.status_code == 200
   ```

4. **POST /api/undo**:
   ```python
   response = client.post("/api/undo")
   assert response.status_code == 200
   data = response.json()
   assert "ok" in data
   ```

5. **GET /api/health**:
   ```python
   response = client.get("/api/health")
   assert response.status_code == 200
   data = response.json()
   assert data["ok"] is True
   assert data["status"] == "healthy"
   ```

6. **Response schema validation**: Assert that `/api/apply` response contains all `OperationResult` fields: `ok`, `updated`, `summary`, `changed_files`, `error`, `warning`.

### Verification

```bash
pytest tests/test_app.py -v
# All 6+ tests pass

# Full test suite:
pytest tests/ -v
```

---

## Step 8: Integration Tests

### What to do

Create `tests/test_integration.py` with end-to-end tests that exercise the full flow: instruction → agent loop → vault mutation → commit → verify vault state → undo → verify revert.

### Setup: demo-vault fixture

Create a `demo-vault/` directory at the project root (or use `tmp_path` to build one dynamically). The fixture should have:

```
demo-vault/
├── README.md
├── Projects/
│   ├── Alpha.md    (with frontmatter: status: draft)
│   └── Beta.md     (with frontmatter: status: active)
└── Daily/
    └── 2025-01-01.md
```

### Tests

1. **Apply → verify vault state → undo → verify revert**:
   - Use a `FunctionModel` scripted to read `Projects/Alpha.md`, then write it with updated content.
   - After `agent.run()`: assert the file on disk has the new content.
   - Call `agent.undo()`.
   - Assert the file on disk has the original content.
   - *(Note: undo depends on jj being available. If jj is not available in CI, skip this test with `pytest.mark.skipif`.)*

2. **Apply with no changes**:
   - Scripted model just reads files and returns summary text.
   - Assert `result.updated is False`.
   - Assert no files on disk were modified.

3. **Multiple file changes in one run**:
   - Scripted model writes to two different files.
   - Assert both appear in `result.changed_files`.

4. **HTTP integration** (using TestClient):
   - `POST /api/apply` → verify response → `POST /api/undo` → verify response.

### Verification

```bash
pytest tests/test_integration.py -v

# Full suite with coverage:
pytest tests/ --cov=obsidian_agent --cov-report=term-missing
```

---

## Step 9: Final Cleanup and Validation

### What to do

1. **Review all public exports** in `__init__.py`.
2. **Verify the server starts** manually:
   ```bash
   AGENT_VAULT_DIR=/tmp/test-vault ANTHROPIC_API_KEY=test python -m obsidian_agent
   # Should start uvicorn and listen on 127.0.0.1:8081
   # Ctrl+C to stop
   ```
   *(Create `/tmp/test-vault` as an empty directory first.)*

3. **Run the full test suite** with coverage:
   ```bash
   pytest tests/ -v --cov=obsidian_agent --cov-report=term-missing
   ```

4. **Check coverage targets**:
   - `config.py`: 100%
   - `models.py`: 100%
   - `prompt.py`: 100%
   - `tools.py`: >90% (may miss some rare error branches)
   - `agent.py`: >85%
   - `app.py`: >85%

5. **Verify no real LLM calls** leak through: `models.ALLOW_MODEL_REQUESTS = False` in `conftest.py` should cause any real call to raise immediately.

### Final file tree

```
obsidian-agent/
├── pyproject.toml
├── src/
│   └── obsidian_agent/
│       ├── __init__.py           # Public exports: Agent, AgentConfig, RunResult, BusyError
│       ├── __main__.py           # Server entry point
│       ├── app.py                # FastAPI app + endpoints
│       ├── agent.py              # Agent class (pydantic-ai wrapper)
│       ├── config.py             # AgentConfig (pydantic-settings)
│       ├── models.py             # ApplyRequest, OperationResult, HealthResponse, RunResult
│       ├── prompt.py             # System prompt builder
│       └── tools.py              # VaultDeps + 11 tool functions + register_tools()
├── tests/
│   ├── conftest.py               # ALLOW_MODEL_REQUESTS=False, shared fixtures
│   ├── test_config.py            # 9+ tests
│   ├── test_models.py            # 4+ tests
│   ├── test_prompt.py            # 4+ tests
│   ├── test_tools.py             # 16+ tests
│   ├── test_agent.py             # 10+ tests
│   ├── test_app.py               # 6+ tests
│   └── test_integration.py       # 4+ tests
└── demo-vault/                   # Fixture vault
```

---

## Appendix A: pydantic-ai Key APIs

Quick reference for the pydantic-ai APIs used in this project:

```python
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai import models

# Create agent
agent = Agent(model="anthropic:claude-sonnet-4-20250514", deps_type=MyDeps, system_prompt="...")

# Register tool
@agent.tool
async def my_tool(ctx: RunContext[MyDeps], arg: str) -> str:
    return ctx.deps.do_something(arg)

# Run agent
result = await agent.run("instruction", deps=my_deps, usage_limits=UsageLimits(request_limit=20))
print(result.output)  # Final text from LLM

# Test: block real calls
models.ALLOW_MODEL_REQUESTS = False

# Test: override model
with agent.override(model=TestModel()):
    result = await agent.run("test", deps=test_deps)

# Test: scripted model
def my_fn(messages, info: AgentInfo) -> ModelResponse:
    ...
model = FunctionModel(my_fn)
```

## Appendix B: obsidian-ops Key APIs

```python
from obsidian_ops import Vault
from obsidian_ops.errors import VaultError, PathError, BusyError, FileTooLargeError, FrontmatterError, ContentPatchError, VCSError

vault = Vault("/path/to/vault", jj_bin="jj", jj_timeout=120)

# Read ops
content: str = vault.read_file("note.md")
files: list[str] = vault.list_files("*.md")
results: list = vault.search_files("query", glob="*.md")
fm: dict | None = vault.get_frontmatter("note.md")
heading_content: str | None = vault.read_heading("note.md", "# Heading")
block_content: str | None = vault.read_block("note.md", "block-id")

# Write ops (acquire mutation lock internally)
vault.write_file("note.md", "content")
vault.delete_file("note.md")
vault.update_frontmatter("note.md", {"key": "value"})
vault.write_heading("note.md", "# Heading", "new content")
vault.write_block("note.md", "block-id", "new content")

# VCS
vault.commit("message")
vault.undo()
```

## Appendix C: Error Handling Cheat Sheet

| Layer | Error | Handling |
|-------|-------|----------|
| Tool function | `VaultError` (any subclass except `BusyError`) | Catch, return `"Error: {e}"` string to LLM |
| Tool function | `BusyError` | Re-raise (should never happen if agent lock works) |
| Agent `run()` | `BusyError` (agent-level) | Raise to caller |
| Agent `_run_impl()` | Any exception from pydantic-ai | Catch, return `RunResult(ok=False, error=...)` |
| Agent `_run_impl()` | Commit failure after changes | Catch, set `warning`, return `ok=True` |
| HTTP endpoint | `BusyError` | Return HTTP 409 |
| HTTP endpoint | `asyncio.TimeoutError` | Return `OperationResult(ok=False, error="timed out")` |
| HTTP endpoint | Everything else | Application-level error in 200 response with `ok=False` |

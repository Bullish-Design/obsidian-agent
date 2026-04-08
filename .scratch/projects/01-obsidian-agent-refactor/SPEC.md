# obsidian-agent: Technical Specification

## 1. Overview

obsidian-agent is a Python service that runs LLM-powered tool-calling loops against an Obsidian vault. It imports `obsidian-ops` for all vault operations and adds LLM orchestration, tool dispatch, an HTTP API, and multi-provider support.

**Read CONCEPT.md first** for motivation and high-level design decisions.

---

## 2. Agent Loop — Detailed Specification

### 2.1 Flow diagram

```
┌──────────────────────────────────────────────────────────────────┐
│  agent.run(instruction, current_file)                            │
│                                                                  │
│  1. Build system prompt                                          │
│  2. Initialize messages: [system_prompt, user: instruction]      │
│  3. Initialize changed_files = set()                             │
│  4. for iteration in range(max_iterations):                      │
│  │      5. Call LLM with messages + tool definitions             │
│  │      6. Parse response                                        │
│  │      7. If response has tool calls:                           │
│  │      │     8. For each tool call:                             │
│  │      │     │     9. Execute via obsidian-ops                  │
│  │      │     │    10. Track changed files                       │
│  │      │     │    11. Append tool result to messages            │
│  │      │    12. Continue loop                                   │
│  │      13. If response is final text (no tool calls):           │
│  │            14. If changed_files: vault.commit(message)        │
│  │            15. Return RunResult(ok=True, summary=text, ...)   │
│  16. Return RunResult(ok=False, error="max iterations exceeded") │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Message format

The agent maintains a conversation history as a list of messages. The exact format depends on the LLM provider interface, but conceptually:

```python
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": instruction},
    # After LLM responds with tool calls:
    {"role": "assistant", "tool_calls": [...]},
    {"role": "tool", "tool_call_id": "...", "content": "tool result"},
    # After LLM responds with final text:
    {"role": "assistant", "content": "Summary of changes..."},
]
```

### 2.3 Stopping conditions

The loop terminates when:
1. **LLM returns no tool calls** — The response text becomes the summary. This is the happy path.
2. **Max iterations reached** — Return error result.
3. **Operation timeout** — Cancel LLM call, return error result.
4. **Unrecoverable error** — LLM API error, tool execution error that can't be sent back to the LLM.

### 2.4 Tool execution errors

When a tool call fails (e.g., `PathError` from obsidian-ops), the error message is sent back to the LLM as the tool result:

```
"Error: path escapes vault: ../../etc/passwd"
```

The LLM can then decide how to proceed (try a different path, explain the error to the user, etc.). This is not a loop-terminating error.

### 2.5 Commit logic

After the loop completes successfully:

1. If `changed_files` is non-empty:
   a. Build commit message: `"ops: "` + first 72 characters of the instruction.
   b. Call `vault.commit(message)`.
   c. If commit fails, set `warning` on the result but still return `ok=True` (the files were changed, just not committed).
2. Set `updated = len(changed_files) > 0`.

---

## 3. Tool Definitions — Detailed Specification

### 3.1 Tool schemas

Each tool is defined with a name, description, and JSON Schema for its parameters. These definitions are passed to the LLM interface layer for function calling.

#### `read_file`

```json
{
    "name": "read_file",
    "description": "Read the contents of a file in the vault. Path is relative to vault root.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative file path, e.g. 'Projects/Alpha.md'"
            }
        },
        "required": ["path"]
    }
}
```

#### `write_file`

```json
{
    "name": "write_file",
    "description": "Write content to a file in the vault. Creates or overwrites the file. Path is relative to vault root.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative file path, e.g. 'Projects/Alpha.md'"
            },
            "content": {
                "type": "string",
                "description": "Full file content to write."
            }
        },
        "required": ["path", "content"]
    }
}
```

#### `list_files`

```json
{
    "name": "list_files",
    "description": "List files in the vault matching a filename glob pattern.",
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Filename glob, e.g. '*.md'. Default: '*.md'"
            }
        },
        "required": ["pattern"]
    }
}
```

#### `search_files`

```json
{
    "name": "search_files",
    "description": "Search file contents for a text query. Returns matching files with context snippets.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Case-insensitive search query."
            },
            "glob": {
                "type": "string",
                "description": "Filename glob to filter search scope. Default: '*.md'"
            }
        },
        "required": ["query"]
    }
}
```

#### `get_frontmatter`

```json
{
    "name": "get_frontmatter",
    "description": "Read the YAML frontmatter from a vault file. Returns the frontmatter as a JSON object, or null if the file has no frontmatter.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative file path."
            }
        },
        "required": ["path"]
    }
}
```

#### `update_frontmatter`

```json
{
    "name": "update_frontmatter",
    "description": "Update specific fields in a file's YAML frontmatter. Only the specified fields are changed; all other fields are preserved.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative file path."
            },
            "updates": {
                "type": "object",
                "description": "Dictionary of frontmatter fields to add or update."
            }
        },
        "required": ["path", "updates"]
    }
}
```

#### `read_heading`

```json
{
    "name": "read_heading",
    "description": "Read the content under a specific heading in a vault file. Returns all content from after the heading to the next heading of equal or higher level.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative file path."
            },
            "heading": {
                "type": "string",
                "description": "The full heading line including '#' prefix, e.g. '## Summary'"
            }
        },
        "required": ["path", "heading"]
    }
}
```

#### `write_heading`

```json
{
    "name": "write_heading",
    "description": "Replace the content under a specific heading. If the heading doesn't exist, it is appended to the file.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative file path."
            },
            "heading": {
                "type": "string",
                "description": "The full heading line including '#' prefix, e.g. '## Summary'"
            },
            "content": {
                "type": "string",
                "description": "The replacement content (without the heading line itself)."
            }
        },
        "required": ["path", "heading", "content"]
    }
}
```

### 3.2 Tool execution dispatch

```python
def execute_tool(vault: Vault, name: str, args: dict) -> str:
    """
    Execute a tool call and return the result as a string.

    All vault operations go through the obsidian-ops Vault instance.
    Errors are caught and returned as "Error: ..." strings (not raised).
    """
    match name:
        case "read_file":
            return vault.read_file(args["path"])

        case "write_file":
            vault.write_file(args["path"], args["content"])
            return f"Successfully wrote {args['path']}"

        case "list_files":
            pattern = args.get("pattern", "*.md")
            files = vault.list_files(pattern)
            if not files:
                return "No files found."
            return f"Found {len(files)} files:\n" + "\n".join(files)

        case "search_files":
            query = args["query"]
            glob = args.get("glob", "*.md")
            results = vault.search_files(query, glob=glob)
            if not results:
                return "No matches found."
            lines = [f"Found {len(results)} matching files:"]
            for r in results:
                lines.append(f"\n--- {r.path} ---\n{r.snippet}")
            return "\n".join(lines)

        case "get_frontmatter":
            fm = vault.get_frontmatter(args["path"])
            if fm is None:
                return "No frontmatter found."
            import json
            return json.dumps(fm, indent=2, default=str)

        case "update_frontmatter":
            vault.update_frontmatter(args["path"], args["updates"])
            return f"Updated frontmatter for {args['path']}"

        case "read_heading":
            content = vault.read_heading(args["path"], args["heading"])
            if content is None:
                return f"Heading '{args['heading']}' not found in {args['path']}"
            return content

        case "write_heading":
            vault.write_heading(args["path"], args["heading"], args["content"])
            return f"Updated heading '{args['heading']}' in {args['path']}"

        case _:
            return f"Error: unknown tool '{name}'"
```

### 3.3 Changed file tracking

Tools that modify files must be tracked. After each tool execution, check if the tool is a write operation and record the path:

```python
WRITE_TOOLS = {"write_file", "update_frontmatter", "write_heading", "write_block"}

if name in WRITE_TOOLS and "path" in args:
    changed_files.add(args["path"])
```

---

## 4. System Prompt — Detailed Specification

### 4.1 Base prompt

```python
SYSTEM_PROMPT = """You are an assistant that helps manage an Obsidian vault. You operate on markdown files in the vault using the provided tools.

Rules:
- Preserve YAML frontmatter unless asked to change it.
- Preserve wikilinks ([[...]]) unless asked to change them.
- Prefer minimal edits over rewriting entire files.
- Do not delete content unless clearly intended by the user.
- Use tools to inspect and edit files; do not only describe changes.
- After making changes, provide a brief summary of what you did.
- Only read and write files within the vault."""
```

### 4.2 Current file context

If `current_file` is provided, append to the system prompt:

```python
if current_file:
    prompt += f"\n\nThe user is currently viewing: {current_file}"
```

### 4.3 Future extensions

The system prompt can be extended with:
- Vault-specific instructions (loaded from a config file in the vault)
- Recent file history
- Selection context (highlighted text from the UI)

These are not in v1 scope.

---

## 5. LLM Provider Interface — Detailed Specification

### 5.1 Provider abstraction

The agent needs a unified interface for calling LLMs with tool support. This can be implemented via:

**Option A: pydantic-ai** — Provides a high-level agent abstraction with built-in tool support, Anthropic + OpenAI providers, and structured output parsing.

**Option B: pi-mono** — Provides a layered LLM API with multi-provider support.

**Option C: Custom protocol** — Define a minimal interface and implement it for each provider using their native SDKs.

Regardless of choice, the interface must support:

```python
class LLMInterface(Protocol):
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef],
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> LLMResponse:
        """
        Send messages to the LLM and return the response.

        The response contains either:
        - tool_calls: list of tool call requests
        - content: final text response (when no more tool calls)
        """
        ...
```

### 5.2 Provider configuration

| Provider | `llm_provider` | `llm_base_url` | `llm_api_key` | Notes |
|----------|---------------|-----------------|---------------|-------|
| Anthropic | `"anthropic"` | (not used) | Required | Uses Anthropic API directly |
| OpenAI | `"openai"` | (not used) | Required | Uses OpenAI API directly |
| vLLM | `"openai"` | `"http://host:8000/v1"` | Optional | OpenAI-compatible API |
| Ollama | `"openai"` | `"http://host:11434/v1"` | Not needed | OpenAI-compatible API |

### 5.3 Model resolution for local servers

When `llm_base_url` is set and `llm_model` is not explicitly configured:

1. Send `GET {base_url}/models` to list available models.
2. Parse the response (`{"data": [{"id": "model-name"}, ...]}`)
3. If one model: use it.
4. If multiple: prefer one containing "instruct" in the name (case-insensitive).
5. If none: raise a configuration error.

### 5.4 Base URL normalization

```python
def normalize_base_url(url: str) -> str:
    """
    Normalize an OpenAI-compatible base URL.

    - Strip trailing slashes.
    - If the URL has no path (or just "/"), append "/v1".

    Examples:
        "http://localhost:8000"     → "http://localhost:8000/v1"
        "http://localhost:8000/"    → "http://localhost:8000/v1"
        "http://localhost:8000/v1"  → "http://localhost:8000/v1"
        "http://localhost:8000/v1/" → "http://localhost:8000/v1"
    """
```

---

## 6. HTTP API — Detailed Specification

### 6.1 FastAPI application

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    config = AgentConfig()
    vault = Vault(config.vault_dir, jj_bin=config.jj_bin, jj_timeout=config.jj_timeout)
    agent = Agent(config, vault)
    app.state.agent = agent
    yield

app = FastAPI(lifespan=lifespan)
```

### 6.2 Endpoints

#### `POST /api/apply`

```python
class ApplyRequest(BaseModel):
    instruction: str
    current_file: str | None = None

class OperationResult(BaseModel):
    ok: bool
    updated: bool
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    warning: str | None = None
    error: str | None = None

@app.post("/api/apply", response_model=OperationResult)
async def apply_instruction(request: ApplyRequest):
    agent = app.state.agent

    if not request.instruction.strip():
        return OperationResult(ok=False, updated=False, summary="", error="instruction is required")

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(agent.run, request.instruction, request.current_file),
            timeout=agent.config.operation_timeout,
        )
        return result
    except asyncio.TimeoutError:
        return OperationResult(
            ok=False, updated=False, summary="",
            error=f"Operation timed out after {agent.config.operation_timeout}s",
        )
    except BusyError:
        raise HTTPException(status_code=409, detail="Another operation is already running")
```

#### `POST /api/undo`

```python
@app.post("/api/undo", response_model=OperationResult)
async def undo():
    agent = app.state.agent
    try:
        result = await asyncio.to_thread(agent.undo)
        return result
    except BusyError:
        raise HTTPException(status_code=409, detail="Another operation is already running")
```

#### `GET /api/health`

```python
@app.get("/api/health")
async def health():
    return {"ok": True, "status": "healthy"}
```

### 6.3 Server entry point

```python
# src/obsidian_agent/__main__.py
import uvicorn
from obsidian_agent.config import AgentConfig

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

Usage:
```bash
AGENT_VAULT_DIR=/path/to/vault \
AGENT_LLM_API_KEY=sk-... \
python -m obsidian_agent
```

---

## 7. Configuration — Detailed Specification

### 7.1 `AgentConfig`

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    # Vault
    vault_dir: Path                              # Required. Must exist and be a directory.

    # LLM
    llm_provider: str = "anthropic"              # "anthropic" or "openai"
    llm_api_key: str = ""                        # API key for the provider
    llm_model: str = "claude-sonnet-4-20250514"  # Model identifier
    llm_base_url: str | None = None              # Override for OpenAI-compatible servers
    llm_max_tokens: int = 4096                   # Max tokens per LLM response

    # Agent behavior
    max_iterations: int = 20                     # Max tool-calling rounds
    operation_timeout: int = 120                 # Seconds before operation times out

    # VCS
    jj_bin: str = "jj"                           # Path to Jujutsu binary
    jj_timeout: int = 120                        # Timeout for JJ subprocess calls

    # Server
    host: str = "127.0.0.1"                      # Bind address
    port: int = 8081                             # Listen port
```

### 7.2 Validation

On initialization:
- `vault_dir` must exist and be a directory. Raise `ValueError` if not.
- `llm_provider` must be `"anthropic"` or `"openai"`. Raise `ValueError` if not.
- If `llm_provider` is `"anthropic"` and `llm_api_key` is empty, check `ANTHROPIC_API_KEY` env var as fallback.
- If `llm_base_url` is set, normalize it (see section 5.4).

---

## 8. File Structure

```
obsidian-agent/
├── pyproject.toml
├── src/
│   └── obsidian_agent/
│       ├── __init__.py           # Public exports: Agent, AgentConfig, RunResult
│       ├── __main__.py           # Server entry point
│       ├── app.py                # FastAPI application + endpoint handlers
│       ├── agent.py              # Agent class — LLM tool loop
│       ├── config.py             # AgentConfig (pydantic-settings)
│       ├── models.py             # ApplyRequest, OperationResult
│       ├── tools.py              # Tool definitions (schemas) + execute_tool dispatch
│       ├── prompt.py             # build_system_prompt(current_file) → str
│       └── llm.py                # LLM provider interface + factory
├── tests/
│   ├── conftest.py               # Fixtures: mock LLM, temp vault with obsidian-ops
│   ├── test_agent.py             # Agent loop with mocked LLM
│   ├── test_tools.py             # Tool dispatch + changed file tracking
│   ├── test_app.py               # HTTP endpoint tests (TestClient)
│   ├── test_config.py            # Configuration validation + env var parsing
│   ├── test_prompt.py            # System prompt construction
│   └── test_llm.py               # LLM provider initialization + base URL normalization
└── README.md
```

---

## 9. Testing Specification

### 9.1 Test fixtures

#### Mock LLM

The primary test fixture is a mock LLM that returns predetermined responses. This avoids real API calls in tests.

```python
class MockLLM:
    """Mock LLM that returns a sequence of responses."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = responses
        self.call_count = 0
        self.call_history = []

    async def chat(self, messages, tools, **kwargs):
        self.call_history.append(messages)
        response = self.responses[self.call_count]
        self.call_count += 1
        return response
```

#### Temp vault

Tests that exercise tool execution need a real temporary vault directory. Use `obsidian-ops` directly:

```python
@pytest.fixture
def vault(tmp_path):
    """Create a temporary vault with sample files."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("---\ntitle: Test\n---\n# Hello\nContent here.\n")
    (vault_dir / "Projects").mkdir()
    (vault_dir / "Projects/Alpha.md").write_text("---\nstatus: draft\n---\n# Alpha\n")
    return Vault(str(vault_dir))
```

### 9.2 Test categories

#### Agent loop tests (`test_agent.py`)

- **Happy path**: LLM reads a file, writes a file, returns summary. Verify RunResult fields.
- **No changes**: LLM returns text without making tool calls. Verify `updated=False`.
- **Multiple iterations**: LLM makes several tool calls across multiple rounds.
- **Max iterations exceeded**: LLM keeps making tool calls. Verify error result.
- **Tool execution error**: LLM calls a tool that fails (e.g., file not found). Verify error is sent back to LLM and loop continues.
- **LLM API error**: LLM call fails. Verify error result.
- **Commit after changes**: After loop, verify `vault.commit()` is called with correct message.
- **Commit failure**: Commit fails but files changed. Verify `ok=True` with `warning`.
- **Changed file tracking**: Write operations are tracked, read operations are not.

#### Tool dispatch tests (`test_tools.py`)

- Each tool dispatches to the correct obsidian-ops method.
- `read_file` returns file content as string.
- `write_file` returns success message.
- `list_files` formats results as numbered list.
- `search_files` formats results with file paths and snippets.
- `get_frontmatter` returns JSON string.
- `update_frontmatter` returns success message.
- `read_heading` returns heading content or "not found" message.
- `write_heading` returns success message.
- Unknown tool returns error message (does not raise).
- Tool errors return "Error: ..." string (do not raise).

#### HTTP endpoint tests (`test_app.py`)

Using FastAPI's `TestClient`:

- `POST /api/apply` with valid request → 200 with OperationResult.
- `POST /api/apply` with missing instruction → 400 or error result.
- `POST /api/apply` during active operation → 409 Conflict.
- `POST /api/undo` → 200 with OperationResult.
- `POST /api/undo` during active operation → 409.
- `GET /api/health` → 200 with `{"ok": true, "status": "healthy"}`.

#### Configuration tests (`test_config.py`)

- Valid configuration from environment variables.
- Missing required `vault_dir` → error.
- Invalid `llm_provider` → error.
- `llm_api_key` fallback to `ANTHROPIC_API_KEY`.
- Base URL normalization.
- Default values are correct.

#### System prompt tests (`test_prompt.py`)

- Base prompt contains required rules.
- With `current_file` — prompt includes file context.
- Without `current_file` — no file context line.

### 9.3 Test execution

```bash
pytest tests/ -v
pytest tests/ --cov=obsidian_agent --cov-report=term-missing
```

---

## 10. Implementation Priorities

### Phase 1: Agent core (no HTTP, mock LLM)
1. `config.py` — AgentConfig with validation
2. `prompt.py` — System prompt builder
3. `tools.py` — Tool schemas and execution dispatch (requires obsidian-ops)
4. `models.py` — RunResult, ApplyRequest
5. `agent.py` — Agent loop with LLM interface protocol
6. Tests with mock LLM and temp vault

### Phase 2: LLM provider integration
1. `llm.py` — LLM provider factory + implementations
2. Test against real Anthropic API (manual/integration test)
3. Test against real OpenAI-compatible server (manual/integration test)
4. Base URL normalization
5. Model resolution for local servers

### Phase 3: HTTP server
1. `app.py` — FastAPI application
2. `__main__.py` — Server entry point
3. HTTP endpoint tests
4. Timeout handling

### Phase 4: Integration testing
1. End-to-end: start server, curl `/api/apply`, verify vault changes
2. Undo flow: apply → undo → verify revert
3. Concurrent request handling (409 on conflict)
4. Timeout behavior

---

## 11. Error Handling Summary

| Scenario | HTTP Status | `ok` | `error` |
|----------|-------------|------|---------|
| Successful operation with changes | 200 | `true` | `null` |
| Successful operation, no changes | 200 | `true` | `null` |
| LLM API call failure | 200 | `false` | `"LLM call failed: {details}"` |
| Max iterations exceeded | 200 | `false` | `"Agent exceeded maximum iterations (20)"` |
| Operation timeout | 200 | `false` | `"Operation timed out after 120s"` |
| Mutation lock held | 409 | — | `"Another operation is already running"` |
| Missing instruction | 200 | `false` | `"instruction is required"` |
| Files changed but commit failed | 200 | `true` | `null` (but `warning` set) |
| Undo failure | 200 | `false` | `"undo failed: {details}"` |

Note: Application-level errors (LLM failures, timeouts) return HTTP 200 with `ok=false` in the body. Only infrastructure-level errors (lock conflict) return non-200 status codes. This simplifies client-side error handling — the client always parses the JSON body.

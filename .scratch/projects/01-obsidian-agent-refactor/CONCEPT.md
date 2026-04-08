# obsidian-agent: Concept Guide

## Status

- Status: Proposed
- Target: Standalone Python library + HTTP service for LLM-powered Obsidian vault operations
- Dependency: Imports `obsidian-ops` for all vault interactions
- Audience: Implementers of the agent backend

---

## 1. One-Sentence Summary

obsidian-agent is a Python service that accepts a natural-language instruction and a vault file path, runs a tool-using LLM agent loop against an Obsidian vault (via obsidian-ops), commits changes with Jujutsu, and returns a structured result — exposed as both a Python library and an HTTP API.

---

## 2. Why This Library Exists

### 2.1 The problem

Using an LLM to operate on an Obsidian vault requires:

- **Agent orchestration**: A tool-calling loop that sends instructions to an LLM, executes tool calls against the vault, and iterates until the task is complete.
- **Tool definitions**: Structured schemas that tell the LLM what operations are available (read, write, search, etc.).
- **Multi-provider support**: The ability to use different LLM providers (Anthropic, OpenAI, local vLLM servers) through a unified interface.
- **Safety and serialization**: A mutation lock, timeouts, and structured error handling.
- **HTTP API**: A stable endpoint for browser-based UIs or other services to submit instructions and receive results.

obsidian-agent handles all of this. It imports obsidian-ops for vault operations and adds the LLM orchestration layer on top.

### 2.2 Design principles

1. **obsidian-ops for all vault access**: obsidian-agent never reads or writes vault files directly. All vault interaction goes through the `obsidian_ops.Vault` API. This ensures path sandboxing, mutation locking, and VCS integration are always enforced.
2. **Provider-agnostic LLM layer**: Use an interface/abstraction layer (such as pydantic-ai, pi-mono, or a custom protocol) to support multiple LLM providers — Anthropic, OpenAI, and local vLLM/Ollama servers — through a single agent implementation.
3. **Library-first, server included**: The core agent logic is importable as a Python library. A FastAPI HTTP server wraps it for deployment behind a reverse proxy.
4. **Synchronous MVP**: The first version uses blocking request/response (no SSE streaming, no background jobs, no job queue). One instruction at a time.
5. **Minimal dependencies**: Only what's necessary — an LLM interface library, obsidian-ops, FastAPI, and nothing else.

### 2.3 Dependency graph

```
obsidian-agent
├── obsidian-ops          (vault operations — file CRUD, frontmatter, content patching, VCS)
├── pydantic-ai / pi-mono (LLM interface layer — multi-provider support)
├── fastapi + uvicorn     (HTTP server)
└── pydantic              (request/response models)
```

obsidian-agent imports obsidian-ops. It never imports or knows about the web proxy, site generator, or frontend overlay that may sit in front of it.

---

## 3. Library Scope

### 3.1 What obsidian-agent owns

1. **Agent loop** — Accept an instruction and a file context, run an LLM with tools, iterate until done or limit reached, return a structured result.
2. **Tool definitions** — Define LLM tool schemas that map to obsidian-ops operations. The agent tells the LLM what tools are available; when the LLM calls a tool, the agent executes it via obsidian-ops.
3. **System prompt** — Context-aware instructions for the LLM about vault operations, Obsidian conventions, and the current file being viewed.
4. **LLM provider abstraction** — Support for Anthropic, OpenAI, and OpenAI-compatible local servers (vLLM, Ollama) through a unified interface layer.
5. **HTTP API** — FastAPI endpoints for submitting instructions, undoing changes, and health checks.
6. **Configuration** — Environment variable–based settings for LLM provider, vault path, timeouts, and server options.

### 3.2 What obsidian-agent does NOT own

- **Vault file operations** — All reads, writes, frontmatter ops, content patching, search, and VCS are delegated to obsidian-ops.
- **Path sandboxing** — obsidian-ops enforces this.
- **Mutation locking** — obsidian-ops enforces this.
- **Site generation / HTML rendering** — Not in scope.
- **Frontend / overlay UI** — Not in scope.
- **URL-to-file resolution** — The caller (a reverse proxy or API client) resolves URLs to vault-relative file paths before calling obsidian-agent.
- **Process orchestration** — obsidian-agent runs as a single process. Starting/stopping it is the responsibility of an external orchestrator (obsidian-dev) or a container runtime.

### 3.3 Future extensions (not in v1)

- SSE streaming for real-time progress during long operations.
- Session/conversation persistence (multi-turn chat).
- Interface registry (multiple UI modes: command, chat, diff, review).
- Additional tools: `fetch_url` (web content → vault), `get_file_history` (jj log).

---

## 4. Core API Design

### 4.1 Library entry point

```python
from obsidian_agent import Agent, AgentConfig

config = AgentConfig(
    vault_dir="/path/to/vault",
    # LLM configuration via interface layer
    llm_provider="anthropic",          # or "openai", "vllm"
    llm_api_key="...",                 # or from AGENT_LLM_API_KEY env
    llm_model="claude-sonnet-4-20250514",
    llm_base_url=None,                 # set for vLLM/Ollama
    max_iterations=20,
    operation_timeout=120,             # seconds
)

agent = Agent(config)

result = agent.run(
    instruction="Clean up this note and add related links",
    current_file="Projects/Alpha.md",  # vault-relative path, optional
)
# result.ok → True
# result.summary → "Cleaned up formatting and added links to Beta.md and Gamma.md"
# result.changed_files → ["Projects/Alpha.md"]
```

### 4.2 `RunResult`

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

### 4.3 Undo

```python
result = agent.undo()
# result.ok → True
# result.summary → "Last change undone."
```

### 4.4 HTTP API

```
POST /api/apply    → Run an instruction
POST /api/undo     → Undo last change
GET  /api/health   → Health check
```

---

## 5. Agent Loop Design

### 5.1 Flow

```
1. Receive instruction + optional current_file
2. Build system prompt (vault context, current file info)
3. Define available tools (mapped to obsidian-ops methods)
4. Send instruction + system prompt + tool definitions to LLM
5. If LLM returns tool calls:
   a. Execute each tool call via obsidian-ops
   b. Send tool results back to LLM
   c. Repeat from step 5
6. If LLM returns a final text response (no more tool calls):
   a. Collect changed files from tool execution tracking
   b. If files changed: commit via obsidian-ops (vault.commit)
   c. Return RunResult with summary and changed files
7. If iteration limit reached: return error result
```

### 5.2 Tool set

The agent exposes these tools to the LLM, each backed by an obsidian-ops `Vault` method:

| Tool name | Description | obsidian-ops method |
|-----------|-------------|-------------------|
| `read_file` | Read a vault file | `vault.read_file(path)` |
| `write_file` | Write/create a vault file | `vault.write_file(path, content)` |
| `list_files` | List files matching a glob | `vault.list_files(pattern)` |
| `search_files` | Search file contents | `vault.search_files(query, glob=glob)` |
| `get_frontmatter` | Read file frontmatter | `vault.get_frontmatter(path)` |
| `update_frontmatter` | Patch frontmatter fields | `vault.update_frontmatter(path, updates)` |
| `read_heading` | Read content under a heading | `vault.read_heading(path, heading)` |
| `write_heading` | Replace content under a heading | `vault.write_heading(path, heading, content)` |

Each tool has a JSON Schema definition that the LLM interface layer uses for function calling.

### 5.3 Changed file tracking

The agent tracks which files were modified during the loop. Whenever `write_file`, `update_frontmatter`, `write_heading`, or `write_block` is called, the file path is added to a set. This set becomes `RunResult.changed_files`.

### 5.4 System prompt

The system prompt tells the LLM:

```
You are an assistant that helps manage an Obsidian vault. You operate on markdown files
in the vault using the provided tools.

Rules:
- Preserve YAML frontmatter unless asked to change it.
- Preserve wikilinks ([[...]]) unless asked to change them.
- Prefer minimal edits over rewriting entire files.
- Do not delete content unless clearly intended by the user.
- Use tools to inspect and edit files; do not only describe changes.
- After making changes, provide a brief summary of what you did.
- Only read and write files within the vault.
```

If `current_file` is provided, append:
```
The user is currently viewing: {current_file}
```

### 5.5 Iteration limits and timeouts

- **Max iterations**: 20 tool-calling rounds (configurable). If exceeded, the agent returns an error result.
- **Operation timeout**: 120 seconds total for the entire operation (configurable). Enforced via context/cancellation on the LLM calls and tool executions.
- **Per-LLM-call timeout**: 120 seconds for each individual LLM API call.

---

## 6. LLM Provider Abstraction

### 6.1 Interface layer

obsidian-agent should use an LLM interface library (pydantic-ai, pi-mono, or a custom abstraction) rather than calling provider APIs directly. This provides:

- Unified tool-calling interface across providers
- Automatic tool schema translation (Anthropic format vs. OpenAI format)
- Provider-specific API handling (auth, headers, response parsing)
- Easier addition of new providers in the future

### 6.2 Supported providers

| Provider | Use case | Configuration |
|----------|----------|---------------|
| **Anthropic** | Claude models via Anthropic API | `llm_provider="anthropic"`, `llm_api_key`, `llm_model` |
| **OpenAI** | GPT models via OpenAI API | `llm_provider="openai"`, `llm_api_key`, `llm_model` |
| **vLLM / Ollama** | Local models via OpenAI-compatible API | `llm_provider="openai"`, `llm_base_url="http://localhost:8000/v1"`, `llm_model` |

### 6.3 Model resolution for local servers

When using a local vLLM or Ollama server without an explicit model name:
1. Query the `/v1/models` endpoint.
2. If exactly one model is available, use it.
3. If multiple models, prefer one with "instruct" in its name.
4. If no models, raise an error with a helpful message.

### 6.4 Base URL normalization

When a base URL is provided for an OpenAI-compatible server:
- If the URL has no path or just `/`, append `/v1` (e.g., `http://localhost:8000` → `http://localhost:8000/v1`).
- Trim trailing slashes.

---

## 7. HTTP API Design

### 7.1 `POST /api/apply`

Execute an instruction against the vault.

**Request:**
```json
{
    "instruction": "Add a summary section to this note",
    "current_file": "Projects/Alpha.md"
}
```

`instruction` is required. `current_file` is optional (vault-relative path).

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

**Response semantics:**
- `ok=true, updated=true`: Operation completed, files were changed. Caller should refresh.
- `ok=true, updated=false`: Operation completed, but no files were changed.
- `ok=false`: Fatal error. `error` contains the message.
- `warning`: Non-fatal issue (e.g., "files changed but commit failed").

**Error responses:**
- `409 Conflict`: Another operation is already running (mutation lock held).
- `400 Bad Request`: Missing or invalid `instruction`.
- `500 Internal Server Error`: Agent loop failure, LLM error, or timeout.

### 7.2 `POST /api/undo`

Undo the last Jujutsu change.

**Request:** Empty body.

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

### 7.3 `GET /api/health`

Health check.

**Response (200):**
```json
{
    "ok": true,
    "status": "healthy"
}
```

### 7.4 Timeout contract

The agent enforces a hard timeout on the entire `/api/apply` operation (default: 120 seconds). If a reverse proxy sits in front of the agent, it should use a longer timeout (e.g., 180 seconds) so the agent always has time to respond before the proxy cuts the connection.

---

## 8. Configuration

### 8.1 Environment variables

All configuration is via `AGENT_` prefixed environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_VAULT_DIR` | (required) | Absolute path to the Obsidian vault |
| `AGENT_LLM_PROVIDER` | `"anthropic"` | LLM provider: `"anthropic"`, `"openai"` |
| `AGENT_LLM_API_KEY` | (none) | API key for the LLM provider |
| `AGENT_LLM_MODEL` | `"claude-sonnet-4-20250514"` | Model identifier |
| `AGENT_LLM_BASE_URL` | (none) | Base URL for OpenAI-compatible servers |
| `AGENT_HOST` | `"127.0.0.1"` | HTTP server bind address |
| `AGENT_PORT` | `8081` | HTTP server port |
| `AGENT_MAX_ITERATIONS` | `20` | Max tool-calling rounds per operation |
| `AGENT_OPERATION_TIMEOUT` | `120` | Seconds before the operation times out |
| `AGENT_JJ_BIN` | `"jj"` | Path to the Jujutsu binary |
| `AGENT_JJ_TIMEOUT` | `120` | Timeout for JJ subprocess calls |

### 8.2 Configuration class

```python
class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    vault_dir: Path
    llm_provider: str = "anthropic"
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-4-20250514"
    llm_base_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 8081
    max_iterations: int = 20
    operation_timeout: int = 120
    jj_bin: str = "jj"
    jj_timeout: int = 120
```

---

## 9. Project Structure

```
obsidian-agent/
  pyproject.toml
  src/
    obsidian_agent/
      __init__.py            # Public API: Agent, AgentConfig, RunResult
      agent.py               # Agent class — orchestrates the LLM tool loop
      config.py              # AgentConfig (pydantic-settings)
      models.py              # RunResult, ApplyRequest, HealthResponse
      tools.py               # Tool definitions (JSON Schema) and execution dispatch
      prompt.py              # System prompt builder
      app.py                 # FastAPI application
      __main__.py            # `python -m obsidian_agent` entry point (uvicorn)
  tests/
    conftest.py              # Shared fixtures (mock LLM, temp vault)
    test_agent.py            # Agent loop tests with mocked LLM
    test_tools.py            # Tool dispatch and changed-file tracking
    test_app.py              # HTTP endpoint tests
    test_config.py           # Configuration validation
    test_prompt.py           # System prompt construction
```

### 9.1 Dependencies

```toml
[project]
name = "obsidian-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "obsidian-ops>=0.1.0",
    "pydantic>=2.12.0",
    "pydantic-settings>=2.0.0",
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "httpx>=0.28.0",
    # LLM interface layer — one of:
    # "pydantic-ai>=0.1.0",
    # or direct provider SDKs:
    "anthropic>=0.40.0",
    "openai>=1.60.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0",
]
```

The LLM interface layer choice (pydantic-ai, pi-mono, or direct SDKs) is an implementation decision. The spec is intentionally flexible here — the key requirement is that the agent supports Anthropic, OpenAI, and OpenAI-compatible local servers through a unified internal interface.

---

## 10. Acceptance Criteria (v1)

The agent service is correct when:

1. It accepts an instruction and optional file path via `POST /api/apply`.
2. It runs an LLM tool-calling loop using the configured provider.
3. Tools correctly delegate to obsidian-ops (read, write, list, search, frontmatter, content patching).
4. Changed files are tracked and reported in the response.
5. After successful mutation, it commits via obsidian-ops VCS (`vault.commit`).
6. Undo works via `POST /api/undo`.
7. The mutation lock prevents concurrent operations (409 on conflict).
8. Operations time out after the configured duration.
9. It works with at least Anthropic and one OpenAI-compatible backend.
10. `GET /api/health` returns status when the service is running.
11. Configuration is entirely via environment variables.

---

## 11. Explicit Non-Goals for v1

- SSE streaming / real-time progress
- Session persistence / multi-turn conversation
- Interface registry / multiple UI modes
- Frontend code (JavaScript, CSS)
- Site generation or rebuild triggering
- URL-to-file path resolution
- Multi-user support or authentication
- Database-backed state
- Background job queue

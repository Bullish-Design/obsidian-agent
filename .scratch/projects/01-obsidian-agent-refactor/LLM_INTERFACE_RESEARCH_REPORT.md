# LLM Interface Layer: Research Report

Research into Python LLM abstraction libraries for the obsidian-agent refactor. The goal is to select a library that provides multi-provider support and tool calling so we don't have to build and maintain provider-specific code ourselves.

---

## Requirements

What obsidian-agent needs from an LLM interface layer:

1. **Multi-provider support** — Anthropic, OpenAI, and OpenAI-compatible local servers (vLLM, Ollama).
2. **Tool/function calling** — Define a set of ~8 tools with JSON schemas, have the LLM call them.
3. **Agent loop** — Automatic tool execution and re-prompting until the LLM produces a final response. We could write this ourselves, but a built-in loop reduces boilerplate.
4. **Lightweight** — Minimal dependency footprint. No framework lock-in.
5. **Sync support** — The agent loop runs synchronously (wrapped in `asyncio.to_thread` at the HTTP layer). Async is fine too, but sync must work cleanly.
6. **Testability** — Easy to mock the LLM for unit tests.
7. **Stability** — Ideally at or approaching 1.0, or at least a stable API surface.

What we do NOT need:

- RAG / vector stores / embeddings
- Multi-agent orchestration
- Conversation memory / persistence
- Streaming (not in v1)
- Structured output validation (our agent returns free-text summaries, not typed objects)

---

## Libraries Evaluated

### 1. Pydantic AI

| | |
|---|---|
| **Repo** | [github.com/pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) |
| **Stars** | 16.2k |
| **Version** | 1.77.0 (April 3, 2026) |
| **License** | MIT |
| **Python** | >=3.10 |

**What it is**: Full agent framework by the Pydantic team. "Bring that FastAPI feeling to GenAI app and agent development." Built on Pydantic v2 for validation, type safety, and dependency injection.

**Provider support**: OpenAI, Anthropic, Gemini, DeepSeek, Groq, Cohere, Mistral, Perplexity, Azure, Bedrock, Vertex AI, Ollama, and more. Local OpenAI-compatible servers supported via `OpenAIModel(base_url=...)`.

**Tool calling**: Decorator-based. `@agent.tool` for tools needing context (via `RunContext`), `@agent.tool_plain` for pure functions. Schemas generated automatically from type annotations and docstrings. Validation errors fed back to the LLM for self-correction.

```python
from pydantic_ai import Agent, RunContext

agent = Agent('anthropic:claude-sonnet-4-20250514', system_prompt='...')

@agent.tool_plain
def read_file(path: str) -> str:
    """Read a vault file."""
    return vault.read_file(path)

result = agent.run_sync('Clean up this note')
print(result.data)       # final text
print(result.all_messages())  # full conversation history
```

**Agent loop**: Fully built-in. `agent.run()` (async) or `agent.run_sync()` handles the entire tool-calling loop: LLM call → tool dispatch → result injection → repeat until final response. Also exposes `agent.iter()` for lower-level control.

**Sync/async**: Both. `run_sync()` is a convenience wrapper. Async is the primary design target.

**Dependencies**: `pydantic>=2.0`, `httpx`, `anyio`. Provider SDKs are optional extras (`pydantic-ai[openai,anthropic]`). The `pydantic-ai-slim` package is available for minimal installs.

**Testability**: Provides `TestModel` and `FunctionModel` for deterministic unit tests without real API calls.

**Strengths**:
- Most complete agent framework of the options reviewed
- Built-in agent loop eliminates boilerplate
- Excellent test infrastructure (`TestModel`)
- Dependency injection pattern is clean for passing vault context to tools
- Reached 1.x — API is stable
- Strong community and Pydantic team backing
- Provider SDKs are optional extras (pay only for what you use)

**Weaknesses**:
- Heaviest abstraction layer of the options — most opinionated
- Hard dependency on Pydantic v2 (we already use it, so not an issue)
- The `deps_type` pattern requires designing your dependency container upfront
- Logfire integration is heavily promoted in docs (commercial product)
- Rapid release cadence (v1.77 in ~16 months) — while stable, pinning is important

**Fit for obsidian-agent**: Excellent. The built-in agent loop, tool decorators, `RunContext` for vault injection, and `TestModel` for testing match our requirements almost exactly. The main question is whether we want this much framework.

---

### 2. Mirascope

| | |
|---|---|
| **Repo** | [github.com/Mirascope/mirascope](https://github.com/Mirascope/mirascope) |
| **Stars** | 1.5k |
| **Version** | 2.4.0 (March 8, 2026) |
| **License** | MIT |
| **Python** | >=3.9 |

**What it is**: Self-described "LLM Anti-Framework." A decorator-based library that wraps provider APIs with a unified interface. Lighter than Pydantic AI — provides primitives rather than a full agent framework.

**Provider support**: OpenAI, Anthropic, Google, Mistral, Groq, Cohere, LiteLLM, Azure AI, Vertex AI, Bedrock. OpenAI-compatible local servers supported through OpenAI provider configuration.

**Tool calling**: `@llm.tool` decorator on Python functions. The LLM's tool calls are available as `response.tool_calls`. You execute them with `response.execute_tools()` and feed results back with `response.resume()`.

```python
import mirascope.llm as llm

@llm.tool
def read_file(path: str) -> str:
    """Read a vault file."""
    return vault.read_file(path)

@llm.call(provider="anthropic", model="claude-sonnet-4-20250514", tools=[read_file])
def run_agent(instruction: str) -> str:
    return instruction

# Manual agent loop
response = run_agent("Clean up this note")
while response.tool_calls:
    response = response.resume(*response.execute_tools())
print(response.text())
```

**Agent loop**: Semi-manual. The library provides the primitives (`execute_tools()`, `resume()`) but you write the loop yourself. This is a ~5-line while loop — trivial but explicit.

**Sync/async**: Both supported.

**Dependencies**: Core deps are `pydantic` and `docstring-parser` only. Provider SDKs are optional extras (`mirascope[anthropic]`, etc.). Minimal footprint.

**Testability**: No built-in test model. You'd mock at the provider SDK level or wrap the call function.

**Strengths**:
- Genuinely lightweight — minimal deps, minimal opinions
- The "anti-framework" philosophy gives you fine-grained control
- Provider-agnostic decorator API is clean
- Versioning and tracing built into the ops layer (optional)
- Reached 2.x — API has been through breaking changes and stabilized

**Weaknesses**:
- Smaller community (1.5k stars vs 16k for Pydantic AI)
- No built-in test model — mocking is more manual
- Agent loop is manual (though simple)
- Documentation had a 404 on the main docs path during research — docs may be in flux
- Less ecosystem/tooling around it

**Fit for obsidian-agent**: Good. The manual loop is fine and gives us more control. The minimal dependency footprint is appealing. The tradeoff vs Pydantic AI is less built-in infrastructure (no TestModel, no dependency injection, no automatic loop).

---

### 3. LiteLLM

| | |
|---|---|
| **Repo** | [github.com/BerriAI/litellm](https://github.com/BerriAI/litellm) |
| **Stars** | ~16k |
| **Version** | 1.83.2 (April 7, 2026) |
| **License** | MIT |
| **Python** | >=3.8 |

**What it is**: A translation layer that wraps 100+ LLM provider APIs under a single OpenAI-compatible interface. You call `litellm.completion()` with the same signature as `openai.ChatCompletion.create()` and it translates to the target provider's API.

**Provider support**: 100+ providers. The most comprehensive coverage of any option. All major providers plus dozens of smaller ones.

**Tool calling**: Yes. Tool definitions are passed in OpenAI format and translated to each provider's native format automatically.

```python
import litellm

response = litellm.completion(
    model="anthropic/claude-sonnet-4-20250514",
    messages=[{"role": "user", "content": "..."}],
    tools=[...],  # OpenAI-format tool definitions
)
```

**Agent loop**: None. Pure translation layer. You write the entire loop yourself.

**Sync/async**: Both via `litellm.completion()` and `litellm.acompletion()`.

**Dependencies**: `openai`, `httpx`, `pydantic`, `tiktoken`, plus optional provider SDKs. Heavier than Mirascope's core, lighter than pulling all providers.

**Testability**: No built-in mocking. Mock at the `litellm.completion` call level.

**Security concern**: LiteLLM had a **supply chain attack in March 2026** — versions 1.82.7 and 1.82.8 were backdoored via a CI/CD compromise. The affected versions were removed from PyPI and versions after 1.82.8 are clean. This is a serious incident that should factor into the trust assessment.

**Strengths**:
- Most provider coverage by far
- Battle-tested in production (large user base)
- Pure adapter — no opinions about how you structure your agent
- OpenAI-format tool definitions work everywhere

**Weaknesses**:
- No agent loop — you write everything yourself
- No test infrastructure
- Heavier install than Mirascope
- Supply chain attack history is concerning for a library that handles API keys
- Rapid release cadence with frequent breaking changes

**Fit for obsidian-agent**: Decent as a provider abstraction, but we'd still need to build the agent loop, tool dispatch, message management, etc. ourselves. LiteLLM solves only the provider-translation problem, not the orchestration problem. Best used as a layer under another library (e.g., Mirascope can use LiteLLM as a provider).

---

### 4. Magentic

| | |
|---|---|
| **Repo** | [github.com/jackmpcollins/magentic](https://github.com/jackmpcollins/magentic) |
| **Stars** | ~2.5k |
| **Version** | 0.41.0 |
| **License** | MIT |
| **Python** | >=3.10 |

**What it is**: Decorator-based library that turns Python functions into LLM calls via `@prompt` and `@chatprompt` decorators. Return type annotations drive structured output. Tool calling is integrated via function arguments.

**Provider support**: OpenAI, Anthropic, Mistral, Ollama (via OpenAI-compatible API), LiteLLM backend.

**Tool calling**: Functions passed as arguments to the prompt decorator become tools. Built-in tool-call/result loop handles execution automatically.

```python
from magentic import chatprompt, UserMessage

def read_file(path: str) -> str:
    """Read a vault file."""
    return vault.read_file(path)

@chatprompt(UserMessage("{instruction}"), functions=[read_file])
def run_agent(instruction: str) -> str: ...

result = run_agent("Clean up this note")
```

**Agent loop**: Built-in. When tools are provided, Magentic handles the loop internally.

**Sync/async**: Both supported.

**Dependencies**: `openai`, `pydantic`. Light.

**Strengths**:
- Clean, Pythonic API
- Built-in agent loop
- Lightweight

**Weaknesses**:
- Still pre-1.0 (0.41.0)
- Smaller community than Pydantic AI
- The decorator-as-function-body pattern (`def run_agent(...) -> str: ...`) is unusual and may confuse contributors
- Less flexibility for complex agent patterns (the decorator abstraction can be constraining)
- No built-in test model

**Fit for obsidian-agent**: Moderate. The magic decorator pattern is elegant for simple cases but may fight us when we need more control (e.g., tracking changed files, custom commit logic, error handling). The agent loop is more opaque than Mirascope's explicit loop or Pydantic AI's `iter()`.

---

### 5. Instructor

| | |
|---|---|
| **Repo** | [github.com/567-labs/instructor](https://github.com/567-labs/instructor) |
| **Stars** | ~11k |
| **Version** | 1.14.5 (January 29, 2026) |
| **License** | MIT |

**What it is**: Patches LLM clients to return validated Pydantic models. Focused on structured extraction, not agent orchestration.

**Tool calling**: Uses tool calling under the hood to force structured JSON output and validate it. Not designed for general-purpose tool dispatch.

**Agent loop**: No. Has a retry/validation loop for structured output, but not a tool-execution agent loop.

**Fit for obsidian-agent**: Poor. Instructor solves a different problem (structured extraction). We need general tool dispatch and an agent loop, not validated response schemas.

---

### 6. aisuite

| | |
|---|---|
| **Repo** | [github.com/andrewyng/aisuite](https://github.com/andrewyng/aisuite) |
| **Stars** | ~10k |
| **Version** | 0.1.x |
| **License** | MIT |

**What it is**: Andrew Ng's minimalist multi-provider wrapper. Provides a unified `client.chat.completions.create()` interface across providers. Intentionally minimal — no framework, no agents.

**Tool calling**: Basic pass-through of tool definitions. No execution or loop support.

**Agent loop**: None. Intentionally out of scope.

**Fit for obsidian-agent**: Poor for our needs. Like LiteLLM but even thinner — we'd need to build everything on top. The lack of tool calling infrastructure means we're essentially writing a custom framework.

---

### 7. Pi-Mono

| | |
|---|---|
| **Repo** | [github.com/badlogic/pi-mono](https://github.com/badlogic/pi-mono) |

**What it is**: A **TypeScript** monorepo by Mario Zechner (libGDX creator). Provides a unified LLM API, agent runtime, TUI/web UI libraries, and a coding agent CLI — all in TypeScript.

**Fit for obsidian-agent**: **Not applicable.** Pi-Mono is TypeScript-only. It has no Python package. It was referenced in the CONCEPT.md as a potential option, but it cannot be used in a Python project.

---

## Comparison Matrix

| | Pydantic AI | Mirascope | LiteLLM | Magentic | Instructor | aisuite |
|---|---|---|---|---|---|---|
| **Version** | 1.77.0 | 2.4.0 | 1.83.2 | 0.41.0 | 1.14.5 | 0.1.x |
| **Stars** | 16.2k | 1.5k | ~16k | ~2.5k | ~11k | ~10k |
| **Anthropic** | Native | Native | Translated | Native | Patched | Native |
| **OpenAI** | Native | Native | Native | Native | Native | Native |
| **Local/vLLM** | Via OpenAI | Via OpenAI | Via OpenAI | Via OpenAI | Via OpenAI | Via OpenAI |
| **Tool calling** | Decorator | Decorator | Pass-through | Decorator | Structured only | Pass-through |
| **Agent loop** | Built-in | Manual (5 lines) | None | Built-in | None | None |
| **Test model** | Yes | No | No | No | No | No |
| **Dep injection** | Yes (RunContext) | No | No | No | No | No |
| **Core deps** | pydantic, httpx, anyio | pydantic, docstring-parser | openai, httpx, pydantic, tiktoken | openai, pydantic | openai, pydantic | minimal |
| **API stability** | 1.x (stable) | 2.x (stable) | 1.x (supply chain incident) | 0.x (pre-1.0) | 1.x (stable) | 0.x |

---

## Analysis: Top Three Candidates

### Pydantic AI — The full-featured option

**Pros for obsidian-agent:**
- `RunContext` is a natural fit for injecting the `Vault` instance into tools
- `TestModel` eliminates the need for custom mock LLM fixtures
- Built-in loop means we write zero orchestration code
- `agent.iter()` gives escape hatch for custom logic if needed
- Already at 1.x with strong backing
- We already depend on Pydantic, so the ecosystem alignment is natural

**Cons:**
- Most opinionated — we adopt their agent model, dependency injection pattern, message format
- If Pydantic AI's abstractions don't match our needs precisely, we fight the framework
- The library version churn is high (1.77 in 16 months), requiring attention to upgrades

**What our agent.py would look like:**
```python
from pydantic_ai import Agent, RunContext
from obsidian_ops import Vault

agent = Agent(
    'anthropic:claude-sonnet-4-20250514',
    system_prompt=SYSTEM_PROMPT,
    deps_type=Vault,
)

@agent.tool
def read_file(ctx: RunContext[Vault], path: str) -> str:
    """Read a vault file. Path is relative to vault root."""
    return ctx.deps.read_file(path)

@agent.tool
def write_file(ctx: RunContext[Vault], path: str, content: str) -> str:
    """Write content to a vault file."""
    ctx.deps.write_file(path, content)
    return f"Successfully wrote {path}"

# ... more tools ...

def run(vault: Vault, instruction: str, current_file: str | None = None) -> RunResult:
    result = agent.run_sync(instruction, deps=vault)
    # result.data is the final text summary
    # result.all_messages() has the full conversation
    return RunResult(ok=True, summary=result.data, ...)
```

### Mirascope — The lightweight option

**Pros for obsidian-agent:**
- Minimal dependencies (pydantic + docstring-parser)
- The manual loop gives us full control over changed-file tracking, error handling, and commit logic
- "Anti-framework" philosophy means less to fight when our needs diverge
- Reached 2.x — API is stable

**Cons:**
- Smaller community, less ecosystem
- No built-in test model — we write our own mock
- No dependency injection — we'd use closure or module-level vault reference
- Documentation appeared to be in flux during research

**What our agent.py would look like:**
```python
import mirascope.llm as llm
from obsidian_ops import Vault

vault: Vault  # module-level or passed via closure

@llm.tool
def read_file(path: str) -> str:
    """Read a vault file. Path is relative to vault root."""
    return vault.read_file(path)

@llm.call(provider="anthropic", model="claude-sonnet-4-20250514", tools=[read_file, ...])
def invoke(instruction: str) -> str:
    return SYSTEM_PROMPT + "\n\n" + instruction

def run(v: Vault, instruction: str) -> RunResult:
    global vault
    vault = v
    response = invoke(instruction)
    changed = set()
    while response.tool_calls:
        # track writes here
        results = response.execute_tools()
        response = response.resume(*results)
    return RunResult(ok=True, summary=response.text(), ...)
```

### LiteLLM + custom loop — The DIY option

**Pros:**
- Maximum control — no framework opinions at all
- Best provider coverage
- OpenAI-format tool definitions match our existing spec exactly

**Cons:**
- We write and maintain the entire agent loop, message management, tool dispatch, and error handling ourselves
- Supply chain attack in March 2026 is a trust concern
- No test infrastructure
- Most code to write and maintain

---

## Recommendation

**Primary recommendation: Pydantic AI.**

Rationale:
1. The `RunContext[Vault]` pattern maps directly to our architecture — the Vault is the dependency, tools operate on it.
2. `TestModel` gives us deterministic tests without building a custom mock LLM fixture.
3. The built-in agent loop eliminates 50+ lines of orchestration code and handles edge cases (retries, validation errors, iteration limits) that we'd otherwise need to implement.
4. We already depend on Pydantic v2. Adding Pydantic AI is a natural extension, not a new paradigm.
5. At 1.x with 16k stars and the Pydantic team behind it, it's the most likely to be maintained long-term.
6. Provider SDKs are optional extras — we install only `pydantic-ai[anthropic,openai]`.
7. The `agent.iter()` escape hatch means we can drop to lower-level control for changed-file tracking and commit logic without abandoning the framework.

**The main risk** is coupling to a fast-moving framework. Mitigate by:
- Pinning the version strictly in pyproject.toml
- Keeping the Pydantic AI surface area small (Agent + tools + RunContext only)
- Wrapping the agent in our own `Agent` class so the Pydantic AI dependency doesn't leak into our public API

**Runner-up: Mirascope**, if we want to minimize framework dependency and prefer explicit control. The 5-line manual loop is not a real burden, and the minimal deps are appealing. Choose this if "less framework" is a stronger priority than "less boilerplate."

**Not recommended**: LiteLLM (too low-level for our needs + supply chain concern), Instructor (wrong problem), aisuite (too thin), Magentic (pre-1.0, magic API), Pi-Mono (TypeScript only).

---

## Open Questions

1. **Changed-file tracking with Pydantic AI**: The built-in loop executes tools automatically. We need to intercept write operations to track changed files. Options: (a) track inside the tool functions themselves (simplest), (b) use `agent.iter()` for manual loop control, (c) use a wrapper/middleware pattern.

2. **Commit logic**: After the loop completes, we need to commit if files changed. With Pydantic AI's `run_sync()`, we get the result after the loop finishes — we'd commit after the call returns. The changed-file set would be populated by the tools during execution.

3. **Error handling**: Pydantic AI catches tool exceptions and sends error messages back to the LLM. We'd want `BusyError` to propagate (not be swallowed). Need to verify Pydantic AI's error handling behavior and whether we can configure which exceptions propagate vs. get sent to the LLM.

4. **Provider string format**: Pydantic AI uses `"anthropic:model-name"` strings. We'd need to map our `AgentConfig.llm_provider` + `AgentConfig.llm_model` to this format, or adopt Pydantic AI's model string convention directly in our config.

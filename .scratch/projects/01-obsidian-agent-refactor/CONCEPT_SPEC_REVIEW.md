# CONCEPT & SPEC Review

Reviewer notes on the refactor design documents, informed by the current obsidian-ops v0.2.0 API and the previous implementation that was stripped at commit `4159667`.

---

## Overall Assessment

The documents are well-structured and thorough. The separation between CONCEPT (why/what) and SPEC (how) is clean. The design correctly treats obsidian-ops as the single authority for vault operations and keeps obsidian-agent focused on orchestration. The phased implementation plan is sensible.

A few areas need alignment with the actual obsidian-ops API, and some design choices deserve a second look.

---

## 1. Tool Surface vs. obsidian-ops API

### Missing tools worth considering

obsidian-ops v0.2.0 exposes several methods not mapped to agent tools:

| obsidian-ops method | In spec? | Notes |
|---|---|---|
| `delete_file(path)` | No | Intentional omission? The system prompt says "do not delete content unless clearly intended" but doesn't forbid it entirely. If a user says "delete this note," the agent has no way to comply. |
| `read_block(path, block_id)` | No | Block references (`^block-id`) are an Obsidian feature. Could be useful for targeted edits. |
| `write_block(path, block_id, content)` | No (but referenced in WRITE_TOOLS set) | SPEC section 3.3 lists `"write_block"` in `WRITE_TOOLS` but never defines the tool schema or dispatch case. This is a bug in the spec. |
| `set_frontmatter(path, data)` | No | `update_frontmatter` covers the merge case. Full replacement is rarer but occasionally needed. Low priority. |
| `delete_frontmatter_field(path, field)` | No | Could be handled by the LLM setting a field to `null` via `update_frontmatter`, depending on how obsidian-ops interprets that. Worth checking. |
| `vcs_status()` | No | Read-only diagnostic. Not needed for v1. |

**Recommendation**: Add `delete_file` as a tool. Remove `write_block` from the `WRITE_TOOLS` set (or add the full tool definition if block operations are wanted). The rest can wait.

### Tool schema accuracy

The `search_files` tool schema says `glob` parameter has default `"*.md"`, which matches `vault.search_files(query, *, glob="*.md")`. Good.

The `list_files` tool schema says `pattern` is required, but the description says "Default: '*.md'". The obsidian-ops method signature is `list_files(pattern="*.md")`. Making `pattern` required in the schema is fine (forces the LLM to be explicit), but the description hint is slightly misleading. Minor.

---

## 2. Dependency & SDK Mismatch

### pyproject.toml vs. CONCEPT/SPEC

The CONCEPT lists both `anthropic>=0.40.0` and `openai>=1.60.0` as dependencies. The actual `pyproject.toml` on the refactor branch only has `openai>=0.28.0`. Neither `anthropic` nor `pydantic-ai` is listed.

This needs resolution before implementation begins. Options:

1. **OpenAI SDK only** — Use the OpenAI SDK for both OpenAI and Anthropic (Anthropic now supports the OpenAI chat completions format). This would simplify the provider abstraction significantly. Local vLLM/Ollama servers already speak OpenAI protocol.
2. **Both SDKs** — Keep both as the CONCEPT proposes. More code, native tool-calling formats per provider.
3. **pydantic-ai** — The CONCEPT/SPEC mention this as an option. It handles multi-provider routing but adds a dependency with its own abstraction layer and opinions.

**Recommendation**: Option 1 (OpenAI SDK only) deserves serious consideration. Anthropic's API supports the OpenAI messages format, and using a single SDK eliminates the entire provider abstraction layer (`llm.py`), the `LLMInterface` protocol, and the factory pattern. The `llm_provider` config field becomes unnecessary — you just point `base_url` at whichever service you want. This is the simplest thing that works.

If native Anthropic features (extended thinking, prompt caching, etc.) are needed later, the abstraction can be added then. YAGNI for v1.

### Python version

obsidian-ops requires `>=3.12`. The obsidian-agent pyproject.toml says `>=3.13`. This is fine (3.13 is a superset), but the CONCEPT lists `>=3.12`. Align the docs to `>=3.13` since that's what devenv.nix provisions.

---

## 3. Sync vs. Async Design

The SPEC defines `LLMInterface.chat()` as `async` but `agent.run()` as sync (wrapped in `asyncio.to_thread` at the HTTP layer). This creates a friction point: if the LLM interface is async, the agent loop needs to run an event loop internally or be async itself.

Options:
- **Make the agent loop async end-to-end.** `agent.run()` becomes `async def run()`. The FastAPI endpoints call it directly (no `to_thread`). This is cleaner if using async LLM SDKs.
- **Make the LLM interface sync.** Both the OpenAI and Anthropic SDKs have sync clients. The agent loop stays sync, `to_thread` wraps it for FastAPI. Simpler mental model, no async machinery in the agent.
- **Current hybrid approach.** Works but is awkward — you'd need `asyncio.run()` inside the sync agent to call async LLM methods.

**Recommendation**: Pick one. If the OpenAI-SDK-only approach is taken, the sync `OpenAI` client is perfectly adequate and avoids async complexity. The `to_thread` wrapper at the HTTP layer is the right call for keeping the agent loop simple and testable.

---

## 4. Error Handling

### obsidian-ops exception hierarchy

The SPEC references `BusyError` from obsidian-ops for the 409 response, which is correct. But the tool dispatch function (`execute_tool`) uses a bare `try/except` (implied, not shown) to catch errors and return them as strings.

The obsidian-ops error hierarchy is:

```
VaultError
├── PathError          (sandbox violations)
├── FileTooLargeError  (>512KB reads)
├── BusyError          (mutation lock)
├── FrontmatterError   (YAML parse failures)
├── ContentPatchError  (heading/block not found)
└── VCSError           (jj failures)
```

The `execute_tool` dispatch should catch `VaultError` (and its subclasses) and return error strings. `BusyError` inside a tool call is a special case — it means the vault's own lock is contended, which shouldn't happen if the agent is the sole caller. It might be worth letting `BusyError` propagate rather than swallowing it as a tool error string.

**Recommendation**: Catch `VaultError` in tool dispatch, but re-raise `BusyError` since it indicates a concurrency bug, not a recoverable tool error.

---

## 5. Commit Message Format

SPEC section 2.5 says: `"ops: " + first 72 characters of the instruction`.

This is reasonable but the `ops:` prefix feels like a holdover from the previous multi-repo setup. Since this is obsidian-agent (not obsidian-ops), consider `"agent: "` as the prefix, or just use the instruction directly. The commit message is going into the vault's jj history, so it should be meaningful to someone reading the log.

Minor point — not blocking.

---

## 6. Mutation Lock Placement

The SPEC puts concurrency control at the HTTP layer (catch `BusyError` → 409). But obsidian-ops already has a `MutationLock` on every write method. This means:

- If two HTTP requests arrive simultaneously, the first one acquires the obsidian-ops lock on the first write tool call (not at the start of the agent loop).
- The second request could start its LLM call, get tool calls back, and only fail when it tries to write — wasting an LLM API call.

**Recommendation**: Add an agent-level lock (or check `vault.is_busy()`) at the start of `agent.run()`, before the LLM call. This fails fast and avoids wasted LLM calls. The SPEC's HTTP-layer `BusyError` catch is still needed as a safety net.

---

## 7. Undo Semantics

The SPEC says `agent.undo()` calls `vault.undo()` which runs `jj undo`. This undoes the last jj operation, which after a successful `agent.run()` would be the `jj new` call (not the `jj describe`). The result is that `jj undo` reverses the creation of the new working commit, effectively making the described commit the working commit again.

This is actually correct for the use case (reverting the agent's last change), but the semantics depend on jj's undo stack. If the user has done other jj operations between the agent's commit and the undo request, `jj undo` will undo _that_ operation instead.

**Recommendation**: Document this limitation. For v1 it's acceptable, but a more robust approach would be to record the commit ID after `vault.commit()` and use `jj backout` or `jj restore` to target it specifically.

---

## 8. Testing Approach

The test plan is solid. A few additions:

- **Tool dispatch should test obsidian-ops error types explicitly** — e.g., `PathError` for traversal, `FileTooLargeError` for big files, `ContentPatchError` for missing headings. The current spec just says "tool errors" generically.
- **The mock LLM fixture is good**, but consider also testing with a "scripted" LLM that replays a realistic multi-turn conversation (read file → analyze → write file → summarize). This catches integration issues between tool results and message formatting.
- **The demo-vault fixture already exists** in the repo. The test plan should reference it for integration tests rather than creating ad-hoc temp vaults.

---

## 9. Minor Issues

1. **CONCEPT section 5.2 tool table**: Lists 8 tools but the `write_block` tool is mentioned in SPEC 3.3 `WRITE_TOOLS` without a schema. Either add `read_block`/`write_block` tools or remove the reference.

2. **CONCEPT section 4.1 code example**: Shows `llm_provider="anthropic"` as default, but if we go OpenAI-SDK-only, this parameter and example need updating.

3. **SPEC section 6.1**: The lifespan function creates `AgentConfig()` with no arguments, relying entirely on env vars. This is correct but worth noting that tests will need to pass config explicitly (the SPEC's test fixtures don't show this clearly).

4. **SPEC section 3.2 `execute_tool`**: The `get_frontmatter` case does `import json` inline. Move to top-level import.

5. **Health endpoint**: Returns a plain dict, not an `OperationResult`. This is fine but inconsistent with the other endpoints using typed response models. Consider a `HealthResponse` model (already mentioned in CONCEPT section 9 file structure but not defined in the SPEC).

---

## 10. Summary of Recommendations

| # | Item | Priority |
|---|---|---|
| 1 | Decide on SDK strategy (OpenAI-only vs. dual SDK vs. pydantic-ai) | **High** — affects architecture |
| 2 | Decide sync vs. async for agent loop | **High** — affects every module |
| 3 | Add `delete_file` tool | Medium |
| 4 | Fix `write_block` in WRITE_TOOLS (remove or define) | Medium |
| 5 | Add agent-level lock before LLM call | Medium |
| 6 | Re-raise `BusyError` in tool dispatch | Low |
| 7 | Document jj undo limitations | Low |
| 8 | Align Python version in docs to 3.13 | Low |
| 9 | Align pyproject.toml dependencies with chosen SDK strategy | Low (follows #1) |

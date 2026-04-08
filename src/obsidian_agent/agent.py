import httpx

from obsidian_ops import Vault
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import ModelAPIError, UsageLimitExceeded
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from .config import AgentConfig
from .models import RunResult
from .prompt import build_system_prompt
from .tools import VaultDeps, register_tools


class BusyError(Exception):
    """Raised when the agent is already processing a request."""


class Agent:
    def __init__(self, config: AgentConfig, vault: Vault | None = None):
        self.config = config
        self.vault = vault or Vault(
            str(config.vault_dir),
            jj_bin=config.jj_bin,
            jj_timeout=config.jj_timeout,
        )
        self._busy = False

        self._pydantic_agent = PydanticAgent(
            model=self._build_model(),
            deps_type=VaultDeps,
            defer_model_check=True,
            model_settings={"max_tokens": self.config.llm_max_tokens},
        )

        @self._pydantic_agent.instructions
        def dynamic_instructions(ctx) -> str:  # type: ignore[no-untyped-def]
            return build_system_prompt(ctx.deps.current_file)

        register_tools(self._pydantic_agent)

    def _build_model(self) -> str | OpenAIModel:
        if self.config.llm_base_url is None:
            return self.config.llm_model

        provider, _, configured_model = self.config.llm_model.partition(":")
        if provider != "openai":
            return self.config.llm_model

        selected_model = configured_model
        if self._is_generic_model_name(configured_model):
            selected_model = self._resolve_model_name_from_base_url(self.config.llm_base_url)

        return OpenAIModel(
            selected_model,
            provider=OpenAIProvider(base_url=self.config.llm_base_url),
        )

    @staticmethod
    def _is_generic_model_name(model_name: str) -> bool:
        return model_name.strip().lower() in {"", "auto", "default", "local", "generic"}

    @staticmethod
    def _extract_model_ids(payload: object) -> list[str]:
        if isinstance(payload, dict):
            candidates = payload.get("data", [])
        elif isinstance(payload, list):
            candidates = payload
        else:
            candidates = []

        model_ids: list[str] = []
        if not isinstance(candidates, list):
            return model_ids

        for item in candidates:
            if isinstance(item, str):
                model_ids.append(item)
                continue
            if isinstance(item, dict):
                model_id = item.get("id") or item.get("model") or item.get("name")
                if isinstance(model_id, str):
                    model_ids.append(model_id)

        return model_ids

    def _resolve_model_name_from_base_url(self, base_url: str) -> str:
        response = httpx.get(f"{base_url}/models", timeout=10)
        response.raise_for_status()

        model_ids = self._extract_model_ids(response.json())
        if not model_ids:
            msg = f"No models returned from {base_url}/models"
            raise ValueError(msg)

        if len(model_ids) == 1:
            return model_ids[0]

        for model_id in model_ids:
            if "instruct" in model_id.lower():
                return model_id

        return model_ids[0]

    @staticmethod
    def _normalize_commit_message(instruction: str) -> str:
        normalized = " ".join(instruction.split()).strip()
        if not normalized:
            return "obsidian-agent update"
        return normalized[:72]

    def _acquire_busy(self) -> None:
        if self._busy:
            raise BusyError("Another operation is already running")
        self._busy = True

    def _release_busy(self) -> None:
        self._busy = False

    async def run(self, instruction: str, current_file: str | None = None) -> RunResult:
        self._acquire_busy()
        try:
            return await self._run_impl(instruction, current_file)
        finally:
            self._release_busy()

    async def _run_impl(self, instruction: str, current_file: str | None) -> RunResult:
        deps = VaultDeps(vault=self.vault, current_file=current_file)
        limits = UsageLimits(request_limit=self.config.max_iterations)

        try:
            result = await self._pydantic_agent.run(
                instruction,
                deps=deps,
                usage_limits=limits,
            )
        except UsageLimitExceeded:
            return RunResult(
                ok=False,
                updated=False,
                summary="",
                error=f"Agent exceeded max iterations ({self.config.max_iterations})",
            )
        except ModelAPIError as exc:
            return RunResult(
                ok=False,
                updated=False,
                summary="",
                error=f"LLM call failed: {exc}",
            )
        except Exception as exc:
            return RunResult(
                ok=False,
                updated=False,
                summary="",
                error=f"Agent error: {exc}",
            )

        summary = result.output if isinstance(result.output, str) else str(result.output)
        changed_files = sorted(deps.changed_files)

        warning = None
        if changed_files:
            commit_message = self._normalize_commit_message(instruction)
            try:
                self.vault.commit(commit_message)
            except Exception as exc:
                warning = f"Commit failed: {exc}"

        return RunResult(
            ok=True,
            updated=bool(changed_files),
            summary=summary,
            changed_files=changed_files,
            warning=warning,
        )

    async def undo(self) -> RunResult:
        self._acquire_busy()
        try:
            self.vault.undo()
            return RunResult(ok=True, updated=True, summary="Last change undone.")
        except Exception as exc:
            return RunResult(ok=False, updated=False, summary="", error=f"undo failed: {exc}")
        finally:
            self._release_busy()

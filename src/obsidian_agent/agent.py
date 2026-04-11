import asyncio
import logging
import httpx

from obsidian_ops import Vault
from obsidian_ops.errors import BusyError as VaultBusyError
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import ModelAPIError, UsageLimitExceeded
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from .config import AgentConfig
from .models import RunResult
from .prompt import build_system_prompt
from .tools import VaultDeps, register_tools

logger = logging.getLogger(__name__)


class BusyError(Exception):
    """Raised when the agent is already processing a request."""


class Agent:
    """Agent orchestration layer.

    Boundary rule: this layer may orchestrate `obsidian_ops.Vault`, but raw filesystem
    operations and raw `jj` subprocess lifecycle logic must remain in `obsidian-ops`.
    """

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
            return build_system_prompt(
                ctx.deps.current_file,
                interface_id=ctx.deps.interface_id,
                scope_kind=ctx.deps.scope_kind,
                intent=ctx.deps.intent,
                profile_suffix=ctx.deps.profile_prompt_suffix,
            )

        register_tools(self._pydantic_agent)

    def _build_model(self) -> str | OpenAIChatModel:
        if self.config.llm_base_url is None:
            return self.config.llm_model

        provider, _, configured_model = self.config.llm_model.partition(":")
        if provider != "openai":
            logger.info(
                "llm.base_url_ignored",
                extra={
                    "provider": provider,
                    "model": self.config.llm_model,
                    "base_url": self.config.llm_base_url,
                },
            )
            return self.config.llm_model

        selected_model = configured_model
        if self._is_generic_model_name(configured_model):
            selected_model = self._resolve_model_name_from_base_url(self.config.llm_base_url)
            logger.info(
                "llm.model_auto_resolved",
                extra={"base_url": self.config.llm_base_url, "selected_model": selected_model},
            )
        else:
            logger.info(
                "llm.model_configured",
                extra={"base_url": self.config.llm_base_url, "selected_model": selected_model},
            )

        return OpenAIChatModel(
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
        logger.info("llm.model_discovery_start", extra={"base_url": base_url})
        response = httpx.get(f"{base_url}/models", timeout=10)
        response.raise_for_status()

        model_ids = self._extract_model_ids(response.json())
        if not model_ids:
            msg = f"No models returned from {base_url}/models"
            raise ValueError(msg)

        if len(model_ids) == 1:
            logger.info("llm.model_discovery_single", extra={"base_url": base_url, "model": model_ids[0]})
            return model_ids[0]

        for model_id in model_ids:
            if "instruct" in model_id.lower():
                logger.info("llm.model_discovery_instruct_match", extra={"base_url": base_url, "model": model_id})
                return model_id

        available = ", ".join(model_ids)
        msg = (
            f"Multiple models returned from {base_url}/models, but none matched "
            f"an instruct model: {available}"
        )
        raise ValueError(msg)

    @staticmethod
    def _normalize_commit_message(instruction: str) -> str:
        normalized = " ".join(instruction.split()).strip()
        if not normalized:
            return "obsidian-agent update"
        return normalized[:72]

    def _acquire_busy(self) -> None:
        if self._busy:
            logger.warning("agent.busy_rejected")
            raise BusyError("Another operation is already running")
        self._busy = True

    def _release_busy(self) -> None:
        self._busy = False

    async def run(
        self,
        instruction: str,
        current_file: str | None = None,
        *,
        interface_id: str = "command",
        scope: object | None = None,
        intent: str | None = None,
        allowed_write_scope: str = "unrestricted",
        allowed_tool_names: set[str] | None = None,
        allowed_write_paths: set[str] | None = None,
        profile_prompt_suffix: str | None = None,
    ) -> RunResult:
        self._acquire_busy()
        logger.info(
            "agent.run_start",
            extra={
                "instruction_len": len(instruction),
                "has_current_file": bool(current_file),
                "interface_id": interface_id,
                "scope_kind": getattr(scope, "kind", None),
                "intent": intent,
                "timeout_s": self.config.operation_timeout,
            },
        )
        try:
            result = await asyncio.wait_for(
                self._run_impl(
                    instruction,
                    current_file,
                    interface_id=interface_id,
                    scope=scope,
                    intent=intent,
                    allowed_write_scope=allowed_write_scope,
                    allowed_tool_names=allowed_tool_names,
                    allowed_write_paths=allowed_write_paths,
                    profile_prompt_suffix=profile_prompt_suffix,
                ),
                timeout=self.config.operation_timeout,
            )
            logger.info(
                "agent.run_complete",
                extra={
                    "ok": result.ok,
                    "updated": result.updated,
                    "changed_file_count": len(result.changed_files),
                    "has_warning": bool(result.warning),
                    "has_error": bool(result.error),
                },
            )
            return result
        except asyncio.TimeoutError:
            logger.warning("agent.run_timeout", extra={"timeout_s": self.config.operation_timeout})
            return RunResult(
                ok=False,
                updated=False,
                summary="",
                error=f"Operation timed out after {self.config.operation_timeout}s",
            )
        finally:
            self._release_busy()

    async def _run_impl(
        self,
        instruction: str,
        current_file: str | None,
        *,
        interface_id: str,
        scope: object | None,
        intent: str | None,
        allowed_write_scope: str,
        allowed_tool_names: set[str] | None,
        allowed_write_paths: set[str] | None,
        profile_prompt_suffix: str | None,
    ) -> RunResult:
        deps = VaultDeps(
            vault=self.vault,
            current_file=current_file,
            interface_id=interface_id,
            scope_kind=getattr(scope, "kind", None),
            intent=intent,
            allowed_write_scope=allowed_write_scope,
            allowed_tool_names=allowed_tool_names,
            allowed_write_paths=allowed_write_paths,
            profile_prompt_suffix=profile_prompt_suffix,
        )
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
        except VaultBusyError:
            raise

        summary = result.output if isinstance(result.output, str) else str(result.output)
        changed_files = sorted(deps.changed_files)

        warning = None
        if changed_files:
            commit_message = self._normalize_commit_message(instruction)
            try:
                self.vault.commit(commit_message)
                logger.info(
                    "agent.commit_success",
                    extra={"changed_file_count": len(changed_files), "message_len": len(commit_message)},
                )
            except Exception as exc:
                warning = f"Commit failed: {exc}"
                logger.exception(
                    "agent.commit_failed",
                    extra={"changed_file_count": len(changed_files), "message_len": len(commit_message)},
                )

        return RunResult(
            ok=True,
            updated=bool(changed_files),
            summary=summary,
            changed_files=changed_files,
            warning=warning,
        )

    async def undo(self) -> RunResult:
        self._acquire_busy()
        logger.info("agent.undo_start")
        try:
            undo_result = self.vault.undo_last_change()
            warning = getattr(undo_result, "warning", None)
            logger.info("agent.undo_complete", extra={"has_warning": bool(warning)})
            return RunResult(ok=True, updated=True, summary="Last change undone.", warning=warning)
        except Exception as exc:
            logger.exception("agent.undo_failed")
            return RunResult(ok=False, updated=False, summary="", error=f"undo failed: {exc}")
        finally:
            self._release_busy()

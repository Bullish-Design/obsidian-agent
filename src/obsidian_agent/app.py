from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from obsidian_agent.agent import Agent
from obsidian_agent.config import get_agent_settings
from obsidian_agent.locks import FileLockManager
from obsidian_agent.models import ApplyRequest, OperationResult
from obsidian_agent.page_context import resolve_page_path
from obsidian_agent.tools import ToolRuntime
from obsidian_agent.vcs import JujutsuHistory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_agent_settings()
    jj = JujutsuHistory(settings.vault_dir, settings.jj_bin)
    await jj.ensure_workspace()
    lock_manager = FileLockManager()
    tool_runtime = ToolRuntime(settings, lock_manager, jj)
    agent = Agent(settings, tool_runtime)
    app.state.settings = settings
    app.state.jj = jj
    app.state.lock_manager = lock_manager
    app.state.tool_runtime = tool_runtime
    app.state.agent = agent
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/apply")
async def apply(request: ApplyRequest) -> OperationResult:
    settings = app.state.settings
    agent: Agent = app.state.agent
    jj: JujutsuHistory = app.state.jj
    tool_runtime: ToolRuntime = app.state.tool_runtime

    try:
        file_path = request.current_file_path
        if file_path is None:
            file_path = resolve_page_path(
                settings.vault_dir,
                request.current_url_path,
                settings.page_url_prefix,
            )

        tool_runtime.reset()

        async def on_progress(msg: str) -> None:
            pass

        result = await asyncio.wait_for(
            agent.run(
                instruction=request.instruction,
                file_path=file_path,
                on_progress=on_progress,
            ),
            timeout=settings.operation_timeout_s,
        )

        if result["changed_files"]:
            await jj.commit(message=request.instruction)

        return OperationResult(
            ok=True,
            updated=bool(result["changed_files"]),
            summary=result["summary"],
            changed_files=result["changed_files"],
        )
    except TimeoutError:
        return OperationResult(
            ok=False,
            updated=False,
            summary="",
            changed_files=[],
            error=f"Operation timed out after {settings.operation_timeout_s}s",
        )
    except Exception as exc:
        return OperationResult(
            ok=False,
            updated=False,
            summary="",
            changed_files=[],
            error=str(exc),
        )


@app.post("/api/undo")
async def undo() -> OperationResult:
    settings = app.state.settings
    jj: JujutsuHistory = app.state.jj

    try:
        await asyncio.wait_for(jj.undo(), timeout=settings.operation_timeout_s)
        return OperationResult(
            ok=True,
            updated=True,
            summary="Last change undone.",
            changed_files=[],
        )
    except TimeoutError:
        return OperationResult(
            ok=False,
            updated=False,
            summary="",
            changed_files=[],
            error=f"Operation timed out after {settings.operation_timeout_s}s",
        )
    except Exception as exc:
        return OperationResult(
            ok=False,
            updated=False,
            summary="",
            changed_files=[],
            error=str(exc),
        )

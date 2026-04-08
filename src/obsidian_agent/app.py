from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from obsidian_ops import Vault
from obsidian_ops.errors import BusyError as VaultBusyError

from .agent import Agent, BusyError
from .config import AgentConfig
from .models import ApplyRequest, HealthResponse, OperationResult, RunResult


def to_operation_result(result: RunResult) -> OperationResult:
    return OperationResult(
        ok=result.ok,
        updated=result.updated,
        summary=result.summary,
        changed_files=result.changed_files,
        error=result.error,
        warning=result.warning,
    )


def create_app(agent: Agent | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if agent is not None:
            app.state.agent = agent
            yield
            return

        config = AgentConfig()
        vault = Vault(str(config.vault_dir), jj_bin=config.jj_bin, jj_timeout=config.jj_timeout)
        app.state.agent = Agent(config, vault)
        yield

    app = FastAPI(lifespan=lifespan)

    @app.post("/api/apply", response_model=OperationResult)
    async def apply_instruction(request: ApplyRequest) -> OperationResult:
        active_agent: Agent = app.state.agent

        if request.instruction is None or not request.instruction.strip():
            return OperationResult(ok=False, updated=False, summary="", error="instruction is required")

        try:
            result = await active_agent.run(request.instruction, request.current_file)
            return to_operation_result(result)
        except (BusyError, VaultBusyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/undo", response_model=OperationResult)
    async def undo() -> OperationResult:
        active_agent: Agent = app.state.agent
        try:
            result = await active_agent.undo()
            return to_operation_result(result)
        except (BusyError, VaultBusyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(ok=True, status="healthy")

    return app


app = create_app()

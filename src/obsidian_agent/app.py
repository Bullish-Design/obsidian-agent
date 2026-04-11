from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from fastapi import FastAPI, Request
from obsidian_ops import Vault

from .agent import Agent
from .config import AgentConfig
from .models import ApplyRequest, HealthResponse, OperationResult
from .routes import agent_router, vault_router
from .routes.agent_routes import handle_apply, handle_undo

logger = logging.getLogger(__name__)


def create_app(agent: Agent | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if agent is not None:
            app.state.agent = agent
            app.state.vault = agent.vault
            app.state.config = agent.config
            yield
            return

        config = AgentConfig()
        vault = Vault(str(config.vault_dir), jj_bin=config.jj_bin, jj_timeout=config.jj_timeout)
        app.state.agent = Agent(config, vault)
        app.state.vault = vault
        app.state.config = config
        yield

    app = FastAPI(lifespan=lifespan)

    app.include_router(agent_router)
    app.include_router(vault_router)

    @app.post("/api/apply", response_model=OperationResult, deprecated=True)
    async def legacy_apply(request: Request, payload: ApplyRequest) -> OperationResult:
        logger.warning("api.legacy_apply_used", extra={"route": "/api/apply"})
        return await handle_apply(request, payload)

    @app.post("/api/undo", response_model=OperationResult, deprecated=True)
    async def legacy_undo(request: Request) -> OperationResult:
        logger.warning("api.legacy_undo_used", extra={"route": "/api/undo"})
        return await handle_undo(request)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(ok=True, status="healthy")

    return app


app = create_app()

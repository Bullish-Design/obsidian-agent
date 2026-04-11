from contextlib import asynccontextmanager
import logging
import time
from typing import AsyncIterator

from fastapi import FastAPI, Request
from obsidian_ops import Vault

from .agent import Agent
from .config import AgentConfig
from .models import ApplyRequest, HealthResponse, OperationResult
from .rate_limit import RouteRateLimiter
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
            app.state.rate_limiter = RouteRateLimiter(
                max_events=agent.config.deterministic_rate_limit,
                window_seconds=agent.config.deterministic_rate_window_seconds,
            )
            yield
            return

        config = AgentConfig()
        vault = Vault(str(config.vault_dir), jj_bin=config.jj_bin, jj_timeout=config.jj_timeout)
        app.state.agent = Agent(config, vault)
        app.state.vault = vault
        app.state.config = config
        app.state.rate_limiter = RouteRateLimiter(
            max_events=config.deterministic_rate_limit,
            window_seconds=config.deterministic_rate_window_seconds,
        )
        yield

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        logger.info(
            "http.request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

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

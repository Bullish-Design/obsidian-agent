from contextlib import asynccontextmanager
import logging
import time
from typing import AsyncIterator

from fastapi import FastAPI, Request
from obsidian_ops import Vault

from .agent import Agent
from .config import AgentConfig
from .models import HealthResponse
from .queue import JobQueue
from .rate_limit import RouteRateLimiter
from .routes import agent_router, job_router, vault_router

logger = logging.getLogger(__name__)


def create_app(agent: Agent | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if agent is not None:
            app.state.agent = agent
            app.state.vault = agent.vault
            app.state.config = agent.config
            app.state.job_queue = JobQueue(agent)
            app.state.job_queue.start()
            app.state.rate_limiter = RouteRateLimiter(
                max_events=agent.config.deterministic_rate_limit,
                window_seconds=agent.config.deterministic_rate_window_seconds,
            )
            yield
            await app.state.job_queue.stop()
            return

        config = AgentConfig()
        vault = Vault(str(config.vault_dir), jj_bin=config.jj_bin, jj_timeout=config.jj_timeout)
        app.state.agent = Agent(config, vault)
        app.state.vault = vault
        app.state.config = config
        app.state.job_queue = JobQueue(app.state.agent)
        app.state.job_queue.start()
        app.state.rate_limiter = RouteRateLimiter(
            max_events=config.deterministic_rate_limit,
            window_seconds=config.deterministic_rate_window_seconds,
        )
        yield
        await app.state.job_queue.stop()

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
    app.include_router(job_router)
    app.include_router(vault_router)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(ok=True, status="healthy")

    return app


app = create_app()

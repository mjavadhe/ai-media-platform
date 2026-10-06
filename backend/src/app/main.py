"""FastAPI application factory and process entry point (``app-api``).

The API process serves HTTP only. It never runs the Scheduler or the Orchestrator
sweeps in-process: those are separate entry points (``app-scheduler``,
``app-orchestrator``) guarded by a PostgreSQL advisory lock, so running a second
API replica cannot duplicate scheduling work.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.errors import get_request_id, install_error_handlers
from app.api.v1 import auth, creative, events, gpus, health, jobs, logs, models, workers
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.events import get_event_bus
from app.infrastructure.logging import configure_logging, get_logger
from app.persistence.database import dispose_engine, ping_database
from app.services.registry import get_registry
from app.services.storage import build_storage_backend

logger = get_logger("api")

DESCRIPTION = """
Internal API for the AI media platform.

Ownership is enforced in code, not just documented:

* **Scheduler** (`app-scheduler`) is the only writer of `gpu_devices.status`,
  reservations and the worker-start transaction.
* **Orchestrator** (`app-orchestrator`) owns job lifecycle transitions that are not
  caused by the Scheduler.
* **Worker** agents may only report progress and terminal outcomes; they never
  write `jobs.status`.

Every media byte goes through the storage abstraction; PostgreSQL stores only
metadata, checksums and pointers.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging()
    if not await ping_database():
        raise RuntimeError(
            "database is unreachable; refusing to start (run migrations first)"
        )
    storage = build_storage_backend(
        settings.storage_backend, root=settings.storage_root, bucket=settings.storage_bucket
    )
    # Fail fast and loudly: a misconfigured storage root must not surface later
    # as a mysterious 500 on the first completed job.
    storage_info = await storage.health()
    # ``StorageBackend.health()`` reports its verdict under "ok"; checking any
    # other key made a healthy backend log a spurious error on every boot.
    if not storage_info.get("ok"):
        logger.error("storage.unwritable", **storage_info)
    registry = get_registry()
    logger.info(
        "api.started",
        environment=settings.environment,
        gpu_backend=settings.gpu_backend,
        storage=storage_info,
        models=len(registry.list_models()),
        workflows=len(registry.list_workflows()),
    )
    app.state.storage = storage
    app.state.event_bus = get_event_bus()
    try:
        yield
    finally:
        logger.info("api.stopping")
        await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    A factory (rather than a module-level singleton) so tests can create an app
    with overridden settings without monkeypatching globals.
    """
    settings = settings or get_settings()
    app = FastAPI(
        title="AI Media Platform API",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
    )
    app.state.settings = settings
    app.state.event_bus = get_event_bus()

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    # Job/event payloads are highly repetitive JSON: gzip keeps the UI snappy
    # without a CDN in front of the API.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(RequestContextMiddleware)

    install_error_handlers(app)

    prefix = settings.api_prefix
    app.include_router(health.router, prefix=prefix)
    app.include_router(logs.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(auth.admin_router, prefix=prefix)
    app.include_router(auth.token_router, prefix=prefix)
    app.include_router(jobs.router, prefix=prefix)
    app.include_router(workers.router, prefix=prefix)
    app.include_router(workers.admin_router, prefix=prefix)
    app.include_router(workers.read_router, prefix=prefix)
    app.include_router(workers.job_router, prefix=prefix)
    app.include_router(gpus.router, prefix=prefix)
    app.include_router(gpus.cluster_router, prefix=prefix)
    app.include_router(models.router, prefix=prefix)
    app.include_router(creative.router, prefix=prefix)
    app.include_router(events.router, prefix=prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": "ai-media-platform", "api": prefix, "docs": "/docs"}

    return app


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach/propagate a request id and make the access log structured."""

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or get_request_id()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


def build() -> FastAPI:  # pragma: no cover - uvicorn factory entry
    return create_app()


async def _serve() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    configure_logging()
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    import uvicorn

    config = uvicorn.Config(
        "app.main:build",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await server.serve()


def run() -> None:  # pragma: no cover - process entry point
    asyncio.run(_serve())


__all__ = ["RequestContextMiddleware", "build", "create_app", "run"]

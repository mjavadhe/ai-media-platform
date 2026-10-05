"""Liveness/readiness endpoints (``/api/v1/health``, ``/api/v1/metrics``).

``/health`` is unauthenticated and reports what the API can actually verify:
database reachability, storage writability and the configured GPU backend. It
never reports "healthy" from configuration alone.
"""

from __future__ import annotations

import time

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import AppSettings, DbSession
from app.infrastructure.events import get_event_bus
from app.infrastructure.logging import get_logger
from app.persistence.repositories.jobs import JobRepository

logger = get_logger(__name__)

router = APIRouter(tags=["system"])

METRICS: dict[str, float] = {}


def record_metric(name: str, value: float) -> None:
    """Process-local gauge/counter, exposed by ``/metrics``.

    Deliberately minimal: Phase 1 has no metrics backend, and shipping a full
    Prometheus client would be heavier than the value it adds here.
    """
    METRICS[name] = value


@router.get("/health")
async def health(session: DbSession, settings: AppSettings) -> dict:
    started = time.perf_counter()
    database_ok = False
    database_error: str | None = None
    try:
        await session.execute(text("SELECT 1"))
        database_ok = True
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        database_error = str(exc)[:200]

    storage_info: dict[str, object] = {"backend": settings.storage_backend, "writable": False}
    try:
        from app.services.storage import build_storage_backend

        storage = build_storage_backend(
            settings.storage_backend, root=settings.storage_root, bucket=settings.storage_bucket
        )
        storage_info.update(await storage.health())
    except Exception as exc:  # noqa: BLE001
        storage_info["error"] = str(exc)[:200]

    backend_info: dict[str, object] = {"configured": settings.gpu_backend}
    try:
        from worker.gpu.registry import build_gpu_backend

        backend = build_gpu_backend(
            settings.gpu_backend,
            mock_nodes=settings.mock_gpu_nodes,
            mock_gpus_per_node=settings.mock_gpus_per_node,
            mock_vram_mb=settings.mock_gpu_vram_mb,
        )
        backend_info.update(await backend.health())
    except Exception as exc:  # noqa: BLE001
        backend_info["error"] = str(exc)[:200]

    ok = database_ok and bool(storage_info.get("writable"))
    return {
        "status": "ok" if ok else "degraded",
        "database": database_ok,
        "database_error": database_error,
        "environment": settings.environment,
        "storage": storage_info,
        "gpu_backend": backend_info,
        "bus_subscribers": get_event_bus().subscriber_count,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


@router.get("/ready")
async def ready(session: DbSession, settings: AppSettings) -> dict:
    """Readiness: can the API serve a request that touches the database?"""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "detail": f"database unavailable: {exc}"[:200]}
    return {"ready": True, "database": True}


@router.get("/metrics")
async def metrics(session: DbSession, settings: AppSettings) -> dict:
    """Operational snapshot used by the operations UI and health probes."""
    repo = JobRepository(session)
    by_status = await repo.count_by_status()
    payload: dict[str, object] = dict(METRICS)
    payload.update(
        {
            "jobs_by_status": by_status,
            "queued_jobs": by_status.get("QUEUED", 0),
            "sse_subscribers": get_event_bus().subscriber_count,
            "environment": settings.environment,
            "gpu_backend": settings.gpu_backend,
            "scheduler_interval_seconds": settings.scheduler_loop_interval_seconds,
            "orchestrator_interval_seconds": settings.orchestrator_loop_interval_seconds,
            "lease_ttl_seconds": settings.lease_ttl_seconds,
        }
    )
    return payload


__all__ = ["record_metric", "router"]

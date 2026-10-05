"""Scheduler entry point.

Run standalone with ``app-scheduler``. The cluster advisory lock is acquired
*before* the loop starts, so a second instance refuses to run rather than
double-scheduling (ADR-0008).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from app.infrastructure.config import get_settings
from app.infrastructure.logging import configure_logging, get_logger
from app.persistence.database import dispose_engine, get_session_factory
from app.scheduler.service import scheduler_loop
from app.scheduler.singleton import SingletonAlreadyRunningError, wait_for_lock
from worker.gpu.registry import build_gpu_backend

logger = get_logger(__name__)


async def run_scheduler(settings=None) -> None:
    settings = settings or get_settings()
    configure_logging()
    logger.info("scheduler.starting", gpu_backend=settings.gpu_backend)

    factory = get_session_factory()
    try:
        lock = await wait_for_lock(
            settings.scheduler_lease_advisory_key,
            factory,
            timeout_seconds=30.0,
            poll_seconds=1.0,
        )
    except SingletonAlreadyRunningError as exc:
        logger.error("scheduler.already_running", error=str(exc))
        raise

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # NotImplementedError/RuntimeError: signal handlers are unavailable off the
        # main thread or on non-POSIX platforms.
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)

    def backend_factory():
        return build_gpu_backend(
            settings.gpu_backend,
            mock_nodes=settings.mock_gpu_nodes,
            mock_gpus_per_node=settings.mock_gpus_per_node,
            mock_vram_mb=settings.mock_gpu_vram_mb,
        )

    try:
        await scheduler_loop(factory, backend_factory, settings, stop_event=stop)
    finally:
        await lock.release()
        await dispose_engine()
        logger.info("scheduler.stopped")


def run() -> None:
    """Console-script entry point."""
    try:
        asyncio.run(run_scheduler())
    except SingletonAlreadyRunningError:
        raise SystemExit(3) from None


if __name__ == "__main__":  # pragma: no cover
    run()

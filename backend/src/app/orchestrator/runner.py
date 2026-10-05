"""Orchestrator runner — the periodic sweeps the Orchestrator owns.

The Orchestrator normally acts *reactively*, driven by worker reports. Two
transitions however need a clock rather than a request:

* ``RETRYING -> QUEUED`` once ``visible_at`` has passed (the retry decision was
  already made when the failure was classified; this only re-exposes the row)
* finishing a cancellation request whose request handler crashed

Both are Orchestrator-owned transitions per the frozen contract, so they live
here and deliberately run as a separate process from the Scheduler: the Scheduler
must never write ``RETRYING -> QUEUED``, and the Orchestrator must never perform
the physical GPU release.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.config import Settings, get_settings
from app.infrastructure.logging import get_logger
from app.orchestrator.service import OrchestratorService
from app.persistence.database import get_session_factory
from app.services.storage import build_storage_backend

logger = get_logger("orchestrator")


async def sweep_once(session: AsyncSession, settings: Settings) -> dict[str, int]:
    storage = build_storage_backend(
        settings.storage_backend, root=settings.storage_root, bucket=settings.storage_bucket
    )
    service = OrchestratorService(session, settings, storage)
    promoted = await service.sweep_due_retries()
    cancelled = await service.sweep_cancel_requests()
    return {"retries_promoted": promoted, "cancels_finished": cancelled}


async def orchestrator_loop(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    stop = stop_event or asyncio.Event()
    interval = settings.orchestrator_loop_interval_seconds
    logger.info("orchestrator.loop_started", interval=interval)
    while not stop.is_set():
        started = asyncio.get_running_loop().time()
        async with session_factory() as session:
            try:
                result = await sweep_once(session, settings)
                await session.commit()
                if result["retries_promoted"] or result["cancels_finished"]:
                    logger.info("orchestrator.swept", **result)
            except Exception as exc:  # noqa: BLE001 - a failing sweep must not kill the loop
                await session.rollback()
                logger.error("orchestrator.sweep_failed", error=str(exc))
        elapsed = asyncio.get_running_loop().time() - started
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.01, interval - elapsed))
        except TimeoutError:
            continue
    logger.info("orchestrator.loop_stopped")


async def run() -> None:
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Signal handlers are unavailable on some platforms (e.g. Windows);
        # there the loop simply runs until cancelled.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await orchestrator_loop(get_session_factory(), settings, stop_event=stop)


def main() -> None:  # pragma: no cover - process entry point
    asyncio.run(run())


__all__ = ["main", "orchestrator_loop", "run", "sweep_once"]

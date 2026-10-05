"""Scheduler singleton guard.

Exactly one Scheduler may run per cluster (ADR-0008): a second instance must not
double-schedule, double-release, or race recovery. PostgreSQL advisory locks are
used rather than Redis so the guarantee holds without extra infrastructure.

``pg_try_advisory_lock`` is *session*-scoped, which is exactly what we want: the
lock is tied to the connection holding it, so a crashed Scheduler releases it
automatically when its backend connection drops — no stale-lock cleanup needed.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


class SingletonAlreadyRunningError(RuntimeError):
    """Another Scheduler holds the cluster lock."""


class AdvisoryLock:
    """Session-scoped PostgreSQL advisory lock for the Scheduler."""

    def __init__(self, key: int) -> None:
        self._key = key
        self._session: AsyncSession | None = None

    @property
    def held(self) -> bool:
        return self._session is not None

    async def try_acquire(self, session_factory: async_sessionmaker[AsyncSession]) -> bool:
        """Attempt to take the lock. False means another Scheduler owns it."""
        if self._session is not None:
            return True
        session = session_factory()
        try:
            acquired = await session.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": self._key}
            )
            if not acquired:
                await session.close()
                return False
        except Exception:
            await session.close()
            raise
        # The lock lives on this connection: keep the session open for the whole
        # Scheduler lifetime so the lock is not released by a commit/rollback.
        self._session = session
        return True

    async def release(self) -> None:
        """Explicitly drop the lock (optional — closing the session does it)."""
        session = self._session
        self._session = None
        if session is None:
            return
        with contextlib.suppress(Exception):
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self._key})
        await session.close()

    async def holder_pid(self) -> int | None:
        """Which backend currently holds the lock (diagnostics endpoint)."""
        if self._session is None:
            return None
        return await self._session.scalar(text("SELECT pg_backend_pid()"))

    async def describe_holders(self, engine: AsyncEngine) -> list[dict[str, object]]:
        """Rows currently holding *any* advisory lock, for /health diagnostics."""
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """
                    SELECT pid, usename, application_name, state, query
                      FROM pg_locks
                     WHERE locktype = 'advisory'
                       AND granted
                    """
                )
            )
            return [dict(row._mapping) for row in rows]


@contextlib.asynccontextmanager
async def scheduler_singleton(
    key: int,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    required: bool = True,
) -> AsyncIterator[AdvisoryLock]:
    """Acquire the cluster-wide Scheduler lock for the duration of the context."""
    lock = AdvisoryLock(key)
    acquired = await lock.try_acquire(session_factory)
    if not acquired and required:
        raise SingletonAlreadyRunningError(
            "another Scheduler instance already holds the cluster advisory lock"
        )
    try:
        yield lock
    finally:
        await lock.release()


async def wait_for_lock(
    key: int,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 1.0,
) -> AdvisoryLock:
    """Retry until the lock is free or the timeout elapses.

    Used by the Scheduler entry point so a rolling restart does not crash-loop
    while the previous instance finishes draining.
    """
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        lock = AdvisoryLock(key)
        if await lock.try_acquire(session_factory):
            return lock
        if asyncio.get_running_loop().time() >= deadline:
            raise SingletonAlreadyRunningError(
                f"could not acquire the Scheduler lock within {timeout_seconds}s"
            )
        await asyncio.sleep(poll_seconds)


__all__ = [
    "AdvisoryLock",
    "SingletonAlreadyRunningError",
    "scheduler_singleton",
    "wait_for_lock",
]

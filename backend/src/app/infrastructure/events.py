"""In-process event bus backing the SSE stream.

Contract section 18: SSE is *not* the source of truth. Every event is first
persisted to ``job_events`` (append-only) by the writer's transaction, then
published here for live fan-out. A reconnecting client replays from the table via
``GET /api/v1/events/stream?after=<seq>``, so a dropped pub/sub message can
never lose a state change.

No Redis, no message broker (ADR-0004): Phase 1 is a single API process.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    """A live event, mirroring one persisted ``job_events`` row."""

    event_type: str
    job_id: uuid.UUID | None = None
    attempt_id: uuid.UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    seq: int | None = None

    def to_sse(self, event_id: str) -> dict[str, str]:
        """SSE frame fields for ``sse-starlette``."""
        import json

        return {
            "id": event_id,
            "event": self.event_type,
            "data": json.dumps(
                {
                    "ts": self.ts.isoformat(),
                    "event_type": self.event_type,
                    "job_id": str(self.job_id) if self.job_id else None,
                    "attempt_id": str(self.attempt_id) if self.attempt_id else None,
                    "seq": self.seq,
                    **self.payload,
                },
                default=str,
            ),
        }


def _event_id(event: Event) -> str:
    return str(event.seq) if event.seq is not None else uuid.uuid4().hex


class EventBus:
    """Fan-out of live events to any number of SSE subscribers."""

    def __init__(self, *, max_queue: int = 1000) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._max_queue = max_queue
        self._lock = asyncio.Lock()
        self._sequence = 0

    async def publish(self, event: Event) -> None:
        """Fan out to every current subscriber, dropping nothing silently.

        A slow subscriber overflows its queue and receives a synthetic
        ``resync`` event instead of stalling the publisher.
        """
        async with self._lock:
            self._sequence += 1
            if event.seq is None:
                event = Event(
                    event_type=event.event_type,
                    job_id=event.job_id,
                    attempt_id=event.attempt_id,
                    payload=event.payload,
                    ts=event.ts,
                    seq=self._sequence,
                )
            targets = list(self._subscribers)
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(Event(event_type="resync", payload={"reason": "overflow"}))

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        """Register a subscriber queue for the duration of the context."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


#: Process-wide bus. The API process owns it; tests may build their own.
_event_bus = EventBus()


def get_event_bus() -> EventBus:
    return _event_bus


__all__ = ["Event", "EventBus", "get_event_bus"]

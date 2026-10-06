"""Live event stream (``/api/v1/events``).

SSE is a *live view*, never the source of truth: every event also exists as a
``job_events`` row. A client that missed events (reload, reconnect, crash)
replays from ``GET /api/v1/jobs/{id}/events?after_seq=…`` and then attaches
here, so the UI stays correct even though the in-process bus is not durable.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_permissions
from app.domain.enums import PermissionCode
from app.infrastructure.events import Event, get_event_bus
from app.infrastructure.logging import get_logger
from app.persistence.database import session_scope
from app.persistence.repositories.jobs import JobRepository

logger = get_logger(__name__)

router = APIRouter(tags=["events"])

#: Matches the nginx ``proxy_read_timeout`` so the stream is never cut silently.
KEEPALIVE_SECONDS = 20.0


#: Contract paths: ``GET /api/v1/stream/{jobs,logs,gpus,nodes}`` all feed the same
#: event bus; the only difference is the default filter.
STREAM_ALIASES = ("jobs", "logs", "gpus", "nodes")


@router.get("/events")
@router.get("/stream/jobs")
async def stream_events(
    request: Request,
    principal: require_permissions(PermissionCode.JOB_READ.value),
    job_id: uuid.UUID | None = None,
    replay_from_seq: int = Query(default=0, ge=0),
) -> EventSourceResponse:
    """Server-sent events for one job, or cluster-wide when ``job_id`` is absent.

    Durable rows are replayed first so a reconnect can never leave a gap.
    """
    bus = get_event_bus()

    async def replay():
        # Deliberately opens its own short-lived session: this endpoint used to
        # depend on a request-scoped DbSession, which FastAPI keeps open for the
        # whole (potentially hours-long) stream. Each open SSE connection then
        # pinned a pooled DB connection until the client disconnected, and a
        # handful of streams exhausted the pool and 503'd every other request.
        if not replay_from_seq or job_id is None:
            return
        async with session_scope() as replay_session:
            rows = await JobRepository(replay_session).list_events(
                job_id, after_seq=replay_from_seq, limit=500
            )
        for row in rows:
            event = Event(
                event_type=row.event_type,
                job_id=row.job_id,
                seq=row.seq,
                ts=row.ts,
                payload=row.payload or {},
            )
            yield event.to_sse(str(event.seq))

    async def live():
        async with bus.subscribe() as queue:
            try:
                yield {"event": "stream.open", "data": '{"status":"open"}'}
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=KEEPALIVE_SECONDS
                        )
                    except TimeoutError:
                        # Comment-style frame: keeps proxies from closing an
                        # idle-but-healthy connection.
                        yield {"event": "keepalive", "data": "{}"}
                        continue
                    if job_id is not None and event.job_id != job_id:
                        continue
                    yield event.to_sse(
                        str(event.seq) if event.seq is not None else "live"
                    )
            except asyncio.CancelledError:  # pragma: no cover - client disconnect
                raise
            except Exception as exc:  # noqa: BLE001 - never kill the stream silently
                logger.warning("sse.stream_failed", job_id=str(job_id), error=str(exc))
                with contextlib.suppress(Exception):
                    yield {"event": "stream.error", "data": '{"detail":"stream failed"}'}

    async def stream():
        async for frame in replay():
            yield frame
        async for frame in live():
            yield frame

    return EventSourceResponse(
        stream(),
        ping=15,
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


# The contract lists /stream/jobs, /stream/logs, /stream/gpus and
# /stream/nodes. All four are the same bus with a different default filter, so
# they are registered from one handler instead of being duplicated four times.
for _alias in STREAM_ALIASES:
    if _alias == "jobs":  # already bound by the decorator above
        continue
    router.add_api_route(
        f"/stream/{_alias}",
        stream_events,
        methods=["GET"],
        include_in_schema=True,
    )


__all__ = ["router"]

"""Log centre (``/api/v1/logs``) and the contract SSE stream paths.

``log_entries`` is the durable record; ``/logs`` only ever reads it. Live tailing
goes through ``/api/v1/stream/logs``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CsrfChecked, DbSession, require_permissions
from app.api.v1.schemas import LogEntryOut
from app.domain.enums import PermissionCode
from app.infrastructure.logging import get_logger
from app.persistence.models_identity import LogEntry
from app.persistence.repositories.common import utcnow

logger = get_logger(__name__)

router = APIRouter(tags=["system"])

LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@router.get("/logs", response_model=list[LogEntryOut])
async def list_logs(
    session: DbSession,
    principal: require_permissions(PermissionCode.LOG_READ.value),
    level: str | None = None,
    service: str | None = None,
    job_id: uuid.UUID | None = None,
    worker_id: uuid.UUID | None = None,
    gpu_device_id: uuid.UUID | None = None,
    since: datetime | None = None,
    search: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[LogEntryOut]:
    stmt = select(LogEntry)
    if level:
        if level.upper() not in LEVELS:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail=f"unknown level {level!r}")
        stmt = stmt.where(LogEntry.level == level.upper())
    if service:
        stmt = stmt.where(LogEntry.service == service)
    if job_id:
        stmt = stmt.where(LogEntry.job_id == job_id)
    if worker_id:
        stmt = stmt.where(LogEntry.worker_id == worker_id)
    if gpu_device_id:
        stmt = stmt.where(LogEntry.gpu_device_id == gpu_device_id)
    if since:
        stmt = stmt.where(LogEntry.ts >= since)
    if search:
        stmt = stmt.where(LogEntry.message.ilike(f"%{search}%"))
    rows = await session.scalars(stmt.order_by(LogEntry.ts.desc()).limit(limit).offset(offset))
    return [LogEntryOut.model_validate(row) for row in rows.all()]


@router.post("/logs", response_model=LogEntryOut, status_code=201)
async def append_log(
    entry: LogEntryOut,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.LOG_READ.value),
) -> LogEntryOut:
    """Persist a client-side log line (e.g. a worker agent with no DB access).

    Rejected above level INFO from non-admin callers so the log centre cannot be
    used to inject misleading entries.
    """
    if entry.level.upper() not in {"WARNING", "ERROR", "CRITICAL"}:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail="only WARNING/ERROR/CRITICAL entries may be submitted",
        )
    row = LogEntry(
        ts=entry.ts or utcnow(),
        level=entry.level.upper(),
        service=entry.service[:48],
        message=entry.message,
        job_id=entry.job_id,
        worker_id=entry.worker_id,
        gpu_device_id=entry.gpu_device_id,
        context=entry.context,
    )
    session.add(row)
    await session.commit()
    return LogEntryOut.model_validate(row)


__all__ = ["router"]

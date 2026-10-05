"""Worker repository: registration, credentials, heartbeats and claimable jobs.

A worker authenticates with an opaque credential presented in the
``Authorization: Worker <credential>`` header (contract section 9). It may
register itself once with a short-lived registration token issued by an admin,
after which it holds a long-lived credential.

The worker never writes ``jobs.status``: :meth:`WorkerRepository.find_claimable`
only *reads* ``RESERVED`` rows on its node so the agent knows a start trigger may
be available. The actual transition happens in the Scheduler-backed claim endpoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import JobStatus, WorkerStatus
from app.infrastructure.security import (
    generate_opaque_token,
    hash_token,
    token_prefix,
    verify_token,
)
from app.persistence.models_identity import Worker
from app.persistence.models_job import Job
from app.persistence.repositories.common import utcnow


@dataclass(frozen=True, slots=True)
class RegistrationToken:
    """One-time registration token. The plaintext is returned exactly once."""

    worker_id: uuid.UUID
    token: str
    expires_at: object


@dataclass(frozen=True, slots=True)
class WorkerCredential:
    """Long-lived worker credential. The plaintext is returned exactly once."""

    worker_id: uuid.UUID
    credential: str
    prefix: str
    expires_at: object


@dataclass(frozen=True, slots=True)
class WorkerPage:
    items: list[Worker]
    total: int


class WorkerAuthError(Exception):
    """Credential missing, invalid or expired."""


class WorkerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- admin: registration + credentials -------------------------------
    async def create_worker(
        self,
        *,
        name: str,
        node_id: uuid.UUID | None = None,
        registration_ttl_hours: int = 1,
    ) -> RegistrationToken:
        """Create a worker plus a one-time registration token."""
        worker = Worker(name=name, node_id=node_id, status=WorkerStatus.OFFLINE.value)
        self._session.add(worker)
        await self._session.flush()

        token = generate_opaque_token()
        worker.registration_token_hash = hash_token(token)
        worker.registration_token_expires_at = utcnow() + timedelta(hours=registration_ttl_hours)
        await self._session.flush()
        return RegistrationToken(
            worker_id=worker.id, token=token, expires_at=worker.registration_token_expires_at
        )

    async def complete_registration(
        self,
        *,
        registration_token: str,
        name: str | None = None,
        version: str | None = None,
        platform: str | None = None,
        capabilities: dict[str, Any] | None = None,
        node_id: uuid.UUID | None = None,
        credential_ttl_hours: int = 24 * 30,
    ) -> WorkerCredential:
        """Exchange a registration token for a long-lived credential."""
        worker = await self._session.scalar(
            select(Worker)
            .where(Worker.registration_token_hash == hash_token(registration_token))
            .with_for_update()
        )
        if worker is None:
            raise WorkerAuthError("invalid registration token")
        expires = worker.registration_token_expires_at
        if expires is None or expires <= utcnow():
            raise WorkerAuthError("registration token expired")
        if worker.disabled_at is not None:
            raise WorkerAuthError("worker is disabled")

        if name:
            worker.name = name
        worker.version = version
        worker.platform = platform
        worker.capabilities = capabilities or {}
        if node_id is not None:
            worker.node_id = node_id
        worker.registered_at = utcnow()
        worker.status = WorkerStatus.IDLE.value

        credential = generate_opaque_token()
        worker.credential_hash = hash_token(credential)
        worker.credential_expires_at = utcnow() + timedelta(hours=credential_ttl_hours)
        worker.credential_rotated_at = utcnow()
        # single-use registration token
        worker.registration_token_hash = None
        worker.registration_token_expires_at = None
        await self._session.flush()
        return WorkerCredential(
            worker_id=worker.id,
            credential=credential,
            prefix=token_prefix(credential),
            expires_at=worker.credential_expires_at,
        )

    async def rotate_credential(
        self, worker_id: uuid.UUID, *, ttl_hours: int = 24 * 30
    ) -> WorkerCredential:
        worker = await self._session.get(Worker, worker_id, with_for_update=True)
        if worker is None:
            raise WorkerAuthError("worker not found")
        credential = generate_opaque_token()
        worker.credential_hash = hash_token(credential)
        worker.credential_expires_at = utcnow() + timedelta(hours=ttl_hours)
        worker.credential_rotated_at = utcnow()
        await self._session.flush()
        return WorkerCredential(
            worker_id=worker.id,
            credential=credential,
            prefix=token_prefix(credential),
            expires_at=worker.credential_expires_at,
        )

    async def authenticate(self, credential: str) -> Worker:
        """Resolve a presented credential to its worker (constant-time hash compare)."""
        if not credential:
            raise WorkerAuthError("missing worker credential")
        row = await self._session.execute(
            select(Worker, Worker.credential_hash).where(Worker.credential_hash.isnot(None))
        )
        for worker, stored_hash in row.all():
            if verify_token(credential, stored_hash or ""):
                if worker.credential_expires_at and worker.credential_expires_at <= utcnow():
                    raise WorkerAuthError("worker credential expired")
                if worker.disabled_at is not None:
                    raise WorkerAuthError("worker is disabled")
                return worker
        raise WorkerAuthError("invalid worker credential")

    # --- heartbeats -------------------------------------------------------
    async def record_heartbeat(
        self,
        worker: Worker,
        *,
        status: WorkerStatus | None = None,
        active_job_count: int | None = None,
        cache_state: dict[str, Any] | None = None,
    ) -> Worker:
        worker.last_heartbeat_at = utcnow()
        if status is not None:
            worker.status = status.value
        if active_job_count is not None:
            worker.active_job_count = active_job_count
        if cache_state is not None:
            worker.cache_state = cache_state
        await self._session.flush()
        return worker

    async def set_status(self, worker_id: uuid.UUID, status: WorkerStatus) -> None:
        await self._session.execute(
            update(Worker)
            .where(Worker.id == worker_id)
            .values(status=status.value)
            .execution_options(synchronize_session=False)
        )

    async def set_disabled(self, worker_id: uuid.UUID, *, disabled: bool) -> None:
        worker = await self._session.get(Worker, worker_id, with_for_update=True)
        if worker is None:
            return
        worker.disabled_at = utcnow() if disabled else None
        if disabled:
            worker.status = WorkerStatus.DISABLED.value
        await self._session.flush()

    # --- reads ------------------------------------------------------------
    async def get(self, worker_id: uuid.UUID) -> Worker | None:
        return await self._session.get(Worker, worker_id)

    async def get_by_name(self, name: str) -> Worker | None:
        return await self._session.scalar(select(Worker).where(Worker.name == name))

    async def list(
        self, *, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> WorkerPage:
        stmt = select(Worker)
        if status:
            stmt = stmt.where(Worker.status == status)
        total = await self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = await self._session.scalars(stmt.order_by(Worker.name).limit(limit).offset(offset))
        return WorkerPage(items=list(rows.all()), total=int(total or 0))

    async def find_claimable(self, worker: Worker, *, limit: int = 5) -> list[Job]:
        """``RESERVED`` jobs this worker could claim — a read-only hint.

        The Scheduler performed the reservation; this only lets the agent avoid
        a pointless poll. The claim itself happens through the claim endpoint,
        which is a Scheduler-executed transaction.
        """
        if worker.node_id is None:
            return []
        rows = await self._session.scalars(
            select(Job)
            .where(
                Job.status == JobStatus.RESERVED.value,
                Job.node_id == worker.node_id,
            )
            .order_by(Job.priority_rank.desc(), Job.created_at)
            .limit(limit)
        )
        return list(rows.all())

    async def count_stale(self, *, heartbeat_grace_seconds: int) -> int:
        """Workers that missed heartbeats — the Scheduler flips these to OFFLINE."""
        cutoff = utcnow() - timedelta(seconds=heartbeat_grace_seconds)
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(Worker)
                .where(
                    Worker.last_heartbeat_at.isnot(None),
                    Worker.last_heartbeat_at < cutoff,
                    Worker.status.notin_([WorkerStatus.OFFLINE.value, WorkerStatus.DISABLED.value]),
                )
            )
            or 0
        )

    async def find_stale(self, *, heartbeat_grace_seconds: int, limit: int = 100) -> list[Worker]:
        cutoff = utcnow() - timedelta(seconds=heartbeat_grace_seconds)
        rows = await self._session.scalars(
            select(Worker)
            .where(
                Worker.last_heartbeat_at.isnot(None),
                Worker.last_heartbeat_at < cutoff,
                Worker.status.notin_([WorkerStatus.OFFLINE.value, WorkerStatus.DISABLED.value]),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows.all())


__all__ = [
    "RegistrationToken",
    "WorkerAuthError",
    "WorkerCredential",
    "WorkerPage",
    "WorkerRepository",
]

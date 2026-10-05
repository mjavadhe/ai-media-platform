"""Shared repository helpers: job-event append and RBAC seeding/resolution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ROLE_PERMISSIONS, RoleCode
from app.persistence.models_identity import (
    JobEvent,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.persistence.models_job import Job


def utcnow() -> datetime:
    """Timezone-aware now. Every timestamp in the system comes from here."""
    return datetime.now(UTC)


async def bump_job_event_seq(session: AsyncSession, job: Job) -> int:
    """Allocate the next event sequence for ``job`` (row-locked)."""
    locked = await session.execute(select(Job.event_seq).where(Job.id == job.id).with_for_update())
    seq = int(locked.scalar_one_or_none() or 0) + 1
    job.event_seq = seq
    return seq


async def append_job_event(
    session: AsyncSession,
    job: Job,
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
    attempt_id: uuid.UUID | None = None,
) -> JobEvent:
    """Append one row to the durable job timeline in the caller's transaction.

    ``jobs.event_seq`` is bumped in the *same* transaction as the job state
    change, so each job's event sequence is monotonic and gapless even if the
    in-process pub/sub drops a message. SSE replay reads from this table, which
    is why SSE is never the source of truth (doc 29).
    """
    seq = await bump_job_event_seq(session, job)
    event = JobEvent(
        job_id=job.id,
        attempt_id=attempt_id,
        seq=seq,
        event_type=event_type,
        payload=payload or {},
        ts=utcnow(),
    )
    session.add(event)
    await session.flush()
    return event


async def resolve_permissions(session: AsyncSession, user: User) -> set[str]:
    """All permission codes granted to ``user`` through their roles.

    Permission codes come from :data:`app.domain.enums.ROLE_PERMISSIONS` so they
    cannot drift from the enum; the ``permissions``/``role_permissions`` tables
    exist for auditing and future custom roles and act as a fallback.
    """
    codes = await session.execute(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id)
    )
    granted: set[str] = set()
    for code in codes.scalars().all():
        try:
            granted.update(permission.value for permission in ROLE_PERMISSIONS[RoleCode(code)])
        except (ValueError, KeyError):
            continue  # custom role -> resolved from the grant table below
    if granted:
        return granted
    rows = await session.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user.id)
    )
    return set(rows.scalars().all())


def role_id(code: RoleCode) -> uuid.UUID:
    """Deterministic role id so seeding is idempotent."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"role:{code.value}")


def permission_id(code: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"permission:{code}")


async def ensure_rbac_seeded(session: AsyncSession) -> None:
    """Idempotently materialize baseline roles and permissions from the enums."""
    for role_code, permissions in ROLE_PERMISSIONS.items():
        rid = role_id(role_code)
        if await session.get(Role, rid) is None:
            session.add(
                Role(
                    id=rid,
                    code=role_code.value,
                    name=role_code.value.title(),
                    description=f"Baseline {role_code.value} role",
                )
            )
            await session.flush()
        for permission in permissions:
            pid = permission_id(permission.value)
            if await session.get(Permission, pid) is None:
                session.add(Permission(id=pid, code=permission.value, description=permission.value))
            key = {"role_id": rid, "permission_id": pid}
            if await session.get(RolePermission, key) is None:
                session.add(RolePermission(**key))
    await session.flush()

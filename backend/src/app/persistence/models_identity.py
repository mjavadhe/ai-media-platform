"""Identity, RBAC, worker registration and audit models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET, JSONB

# `PgUUID` avoids shadowing `uuid.UUID`; the uppercase import is deliberate.
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import JobType, RoleCode, WorkerStatus
from app.persistence.base import Base, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.persistence.models_job import JobAttempt


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = uuid_pk()
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[UserRole]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id = uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Permission(Base, TimestampMixin):
    __tablename__ = "permissions"

    id = uuid_pk()
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )

    user: Mapped[User] = relationship(back_populates="roles")
    role: Mapped[Role] = relationship(lazy="selectin")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Session(Base, TimestampMixin):
    """Server-side session record so sessions can be revoked (doc 12)."""

    __tablename__ = "sessions"

    id = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(INET)

    user: Mapped[User] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)


class ApiToken(Base, TimestampMixin):
    """Long-lived user token. Only the hash is stored (never the plaintext)."""

    __tablename__ = "api_tokens"

    id = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="selectin")


class Worker(Base, TimestampMixin):
    """Worker identity, registration and access credentials (doc 18)."""

    __tablename__ = "workers"

    id = uuid_pk()
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("gpu_nodes.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(16), default=WorkerStatus.OFFLINE.value, nullable=False
    )
    version: Mapped[str | None] = mapped_column(String(32))
    platform: Mapped[str | None] = mapped_column(String(64))
    capabilities: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

    # one-time registration token (hashed) issued by an admin
    registration_token_hash: Mapped[str | None] = mapped_column(String(128), unique=True)
    registration_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # long-lived access credential (hashed)
    credential_hash: Mapped[str | None] = mapped_column(String(128), unique=True)
    credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credential_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cache_state: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    active_job_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    attempts: Mapped[list[JobAttempt]] = relationship(back_populates="worker")

    __table_args__ = (
        Index("ix_workers_status", "status"),
        Index("ix_workers_node_id", "node_id"),
    )


class AuditEntry(Base):
    """Immutable audit trail for sensitive operations (doc 12)."""

    __tablename__ = "audit_entries"

    id = uuid_pk()
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=None, nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    prev: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    new: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_audit_entries_user_id", "user_id"),
        Index("ix_audit_entries_resource", "resource_type", "resource_id"),
        Index("ix_audit_entries_ts", "ts"),
    )


class LogEntry(Base):
    """Structured log line persisted for the log centre (doc 13)."""

    __tablename__ = "log_entries"

    id = uuid_pk()
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=None, nullable=False
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str] = mapped_column(String(48), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    gpu_device_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    job_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    worker_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    stack_trace: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        Index("ix_log_entries_ts", "ts"),
        Index("ix_log_entries_service", "service"),
        Index("ix_log_entries_job_id", "job_id"),
    )


class Metric(Base):
    """Time-series metric sample (doc 13)."""

    __tablename__ = "metrics"

    id = uuid_pk()
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=None, nullable=False
    )
    service: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float] = mapped_column(nullable=False)
    node_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    gpu_device_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    job_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    labels: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        Index("ix_metrics_name_ts", "name", "ts"),
        Index("ix_metrics_gpu_device_id_ts", "gpu_device_id", "ts"),
    )


class JobEvent(Base):
    """Append-only job timeline used by SSE fan-out and the job detail view.

    SSE is never the source of truth: this table is (doc 29).
    """

    __tablename__ = "job_events"

    id = uuid_pk()
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=None, nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_job_events_job_seq"),
        Index("ix_job_events_job_id_ts", "job_id", "ts"),
    )


__all__ = [
    "ApiToken",
    "AuditEntry",
    "JobEvent",
    "JobType",
    "LogEntry",
    "Metric",
    "Permission",
    "Role",
    "RoleCode",
    "RolePermission",
    "Session",
    "User",
    "UserRole",
    "Worker",
]

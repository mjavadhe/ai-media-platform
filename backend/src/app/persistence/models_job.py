"""Job, attempt, claim and GPU reservation/cluster models.

Two GPU state domains live here and must never be conflated (contract §5.1):

* ``gpu_devices.status`` — physical lifecycle, **Scheduler only** writes it.
* ``gpu_reservations.state`` — logical allocation; the Orchestrator may set
  ``RELEASE_PENDING`` (a request) and the Scheduler completes ``RELEASED``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

# `PgUUID` avoids shadowing `uuid.UUID`; the uppercase import is deliberate.
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    CapabilitySource,
    GpuStatus,
    JobStatus,
    NodeStatus,
    ReservationState,
)
from app.persistence.base import Base, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.persistence.models_identity import Worker


class GpuNode(Base, TimestampMixin):
    __tablename__ = "gpu_nodes"

    id = uuid_pk()
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(16), default=NodeStatus.HEALTHY.value, nullable=False
    )
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    devices: Mapped[list[GpuDevice]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_gpu_nodes_status", "status"),)


class GpuDevice(Base, TimestampMixin):
    """Physical GPU device. ``status`` is written only by the Scheduler."""

    __tablename__ = "gpu_devices"

    id = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("gpu_nodes.id", ondelete="CASCADE"), nullable=False
    )
    uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: opaque, backend-specific handle used for telemetry/probe/execute calls.
    #: Mock: ``mock-node-0-gpu-0``; NVML: the CUDA index as a string. Stored so
    #: telemetry lookups never have to guess from the device name or index.
    backend_device_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(16), default=GpuStatus.AVAILABLE.value, nullable=False
    )
    health: Mapped[str] = mapped_column(String(16), default="OK", nullable=False)

    vram_total_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    vram_free_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    compute_capability: Mapped[str | None] = mapped_column(String(16))
    driver_version: Mapped[str | None] = mapped_column(String(32))
    utilization_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    power_w: Mapped[float | None] = mapped_column(Float)
    memory_used_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: transient pointer used to make GPU release exactly-once; not a domain concept
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    node: Mapped[GpuNode] = relationship(back_populates="devices")
    capabilities: Mapped[list[GpuCapability]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    reservations: Mapped[list[GpuReservation]] = relationship(back_populates="device")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_gpu_devices_uuid"),
        UniqueConstraint("node_id", "index", name="uq_gpu_devices_node_index"),
        UniqueConstraint(
            "node_id", "backend_device_id", name="uq_gpu_devices_node_backend_device"
        ),
        Index("ix_gpu_devices_status", "status"),
        Index("ix_gpu_devices_node_id", "node_id"),
        Index("ix_gpu_devices_reservation_id", "reservation_id"),
    )


class GpuCapability(Base, TimestampMixin):
    """Capability registry/probe entries — data-driven, never GPU-name if-statements."""

    __tablename__ = "gpu_capabilities"

    id = uuid_pk()
    gpu_device_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("gpu_devices.id", ondelete="CASCADE"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(
        String(16), default=CapabilitySource.REGISTRY.value, nullable=False
    )
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    device: Mapped[GpuDevice] = relationship(back_populates="capabilities")

    __table_args__ = (
        UniqueConstraint(
            "gpu_device_id", "capability", name="uq_gpu_capabilities_device_capability"
        ),
    )


class Model(Base, TimestampMixin):
    __tablename__ = "models"

    id = uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    repo: Mapped[str | None] = mapped_column(String(255))
    license: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    versions: Mapped[list[ModelVersion]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("name", "type", name="uq_models_name_type"),
        Index("ix_models_type", "type"),
    )


class ModelVersion(Base, TimestampMixin):
    __tablename__ = "model_versions"

    id = uuid_pk()
    model_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("models.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    vram_requirement_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    precision: Mapped[str] = mapped_column(String(16), default="fp16", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="AVAILABLE", nullable=False)
    tasks: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    engines: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    required_capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))

    model: Mapped[Model] = relationship(back_populates="versions")
    installations: Mapped[list[ModelInstallation]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_model_versions_model_version"),
    )


class ModelInstallation(Base, TimestampMixin):
    __tablename__ = "model_installations"

    id = uuid_pk()
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("model_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("gpu_nodes.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="INSTALLED", nullable=False)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    size_bytes: Mapped[int | None] = mapped_column(Integer)

    version: Mapped[ModelVersion] = relationship(back_populates="installations")

    __table_args__ = (
        UniqueConstraint("model_version_id", "node_id", name="uq_model_installations_version_node"),
        Index("ix_model_installations_node_id", "node_id"),
    )


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"

    id = uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    versions: Mapped[list[WorkflowVersion]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("name", name="uq_workflows_name"),)


class WorkflowVersion(Base, TimestampMixin):
    __tablename__ = "workflow_versions"

    id = uuid_pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    outputs_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    required_models: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    resource_requirements: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    engine_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    workflow: Mapped[Workflow] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_workflow_version"),
    )


class Job(Base, TimestampMixin):
    """Job row. Doubles as the Phase 1-3 queue table (ADR-0002, doc 05)."""

    __tablename__ = "jobs"

    id = uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    sequence_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    shot_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    job_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.CREATED.value, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="NORMAL", nullable=False)
    priority_rank: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # queue mechanics (ordering + visibility)
    visible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dequeued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dequeued_by: Mapped[str | None] = mapped_column(String(64))

    model_version_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    required_vram_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required_capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    preferred_gpu_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    preferred_node_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    policy: Mapped[str | None] = mapped_column(String(32))

    gpu_device_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    node_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    worker_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    progress_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    progress_message: Mapped[str | None] = mapped_column(Text)

    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    prompt: Mapped[str | None] = mapped_column(Text)
    negative_prompt: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    event_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    attempts: Mapped[list[JobAttempt]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobAttempt.attempt_no"
    )
    claims: Mapped[list[JobClaim]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    reservations: Mapped[list[GpuReservation]] = relationship(back_populates="job")

    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','QUEUED','RESERVED','STARTING','RUNNING','PAUSED',"
            "'RETRYING','COMPLETED','FAILED','CANCELLED')",
            name="jobs_status_valid",
        ),
        CheckConstraint("progress_percent >= 0 AND progress_percent <= 100", name="jobs_pct_range"),
        # queue claim index: status + priority + created_at (doc 05)
        Index("ix_jobs_queue_claim", "status", "priority_rank", "created_at"),
        Index("ix_jobs_visible_at", "visible_at"),
        Index("ix_jobs_project_id", "project_id"),
        Index("ix_jobs_gpu_device_id", "gpu_device_id"),
        Index("ix_jobs_worker_id", "worker_id"),
        Index("ix_jobs_created_at", "created_at"),
    )


class JobAttempt(Base, TimestampMixin):
    """One execution window. Historical attempts are never overwritten (doc 21)."""

    __tablename__ = "job_attempts"

    id = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workers.id", ondelete="SET NULL")
    )
    gpu_device_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    node_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure_class: Mapped[str | None] = mapped_column(String(32))
    logs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    runtime_info: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    job: Mapped[Job] = relationship(back_populates="attempts")
    worker: Mapped[Worker | None] = relationship(back_populates="attempts")
    claims: Mapped[list[JobClaim]] = relationship(back_populates="attempt")

    __table_args__ = (
        UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_job_attempt_no"),
        CheckConstraint(
            "status IN ('RUNNING','COMPLETED','FAILED','INTERRUPTED','CANCELLED')",
            name="job_attempts_status_valid",
        ),
        Index("ix_job_attempts_job_id", "job_id"),
        Index("ix_job_attempts_worker_id", "worker_id"),
        # A job can have at most one non-terminal attempt, so a duplicate worker
        # claim can never open a second concurrent attempt.
        Index(
            "uq_job_attempts_open",
            "job_id",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
    )


class JobClaim(Base, TimestampMixin):
    """Lease record. The worker claim is also the start trigger (contract section 10)."""

    __tablename__ = "job_claims"

    id = uuid_pk()
    claim_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    worker_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("job_attempts.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(32))
    renewed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    job: Mapped[Job] = relationship(back_populates="claims")
    attempt: Mapped[JobAttempt] = relationship(back_populates="claims")

    __table_args__ = (
        Index("ix_job_claims_job_id", "job_id"),
        Index("ix_job_claims_worker_id", "worker_id"),
        Index("ix_job_claims_expires_at", "expires_at"),
        # A job can hold at most one live lease, which is what makes the
        # visibility-timeout pattern correct even when two workers race.
        Index(
            "uq_job_claims_active_job",
            "job_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )


class GpuReservation(Base, TimestampMixin):
    """Logical allocation record (contract section 10.1 / 5.1)."""

    __tablename__ = "gpu_reservations"

    id = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("gpu_nodes.id", ondelete="CASCADE"), nullable=False
    )
    gpu_device_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("gpu_devices.id", ondelete="CASCADE"), nullable=False
    )
    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workers.id", ondelete="SET NULL")
    )
    policy: Mapped[str | None] = mapped_column(String(32))
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), default=ReservationState.RESERVED.value, nullable=False
    )
    release_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="reservations")
    device: Mapped[GpuDevice] = relationship(back_populates="reservations")

    __table_args__ = (
        CheckConstraint(
            "state IN ('RESERVED','BUSY','RELEASE_PENDING','RELEASED')",
            name="gpu_reservations_state_valid",
        ),
        Index("ix_gpu_reservations_job_id", "job_id"),
        Index("ix_gpu_reservations_state", "state"),
        Index("ix_gpu_reservations_gpu_device_id", "gpu_device_id"),
        # At most one live reservation per GPU: this is what makes GPU release
        # exactly-once safe, because a late Tx2 cannot free a GPU that has
        # already been re-reserved for a different job.
        Index(
            "uq_gpu_reservations_live_device",
            "gpu_device_id",
            unique=True,
            postgresql_where=text("state IN ('RESERVED','BUSY','RELEASE_PENDING')"),
        ),
    )


__all__ = [
    "GpuCapability",
    "GpuDevice",
    "GpuNode",
    "GpuReservation",
    "Job",
    "JobAttempt",
    "JobClaim",
    "Model",
    "ModelInstallation",
    "ModelVersion",
    "Workflow",
    "WorkflowVersion",
]

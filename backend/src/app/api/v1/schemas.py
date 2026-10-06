"""Pydantic request/response schemas for the v1 API (doc 19)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import JobPriority, JobType


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth ---------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    csrf_token: str
    expires_at: datetime
    user: UserOut


class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    display_name: str | None = None
    email: str | None = None
    is_active: bool
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=8, max_length=256)
    display_name: str | None = None
    email: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["ARTIST"])
    is_active: bool = True


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] = Field(default_factory=list)
    ttl_days: int | None = Field(default=None, ge=1, le=3650)


class ApiTokenOut(BaseModel):
    token: str = Field(description="Shown once; only the hash is stored.")
    prefix: str
    expires_at: datetime | None = None


# --- creative -----------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str | None = Field(default=None, max_length=128, pattern=r"^[a-z0-9-]+$")
    description: str | None = None
    metadata_: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class ProjectOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    status: str
    metadata_: dict[str, Any] = Field(default_factory=dict, alias="metadata")
    created_at: datetime
    updated_at: datetime


class SequenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    position: int = 0


class SequenceOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None = None
    position: int


class ShotCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    code: str | None = Field(default=None, max_length=32)
    position: int = 0
    prompt: str | None = None
    negative_prompt: str | None = None
    camera: dict[str, Any] = Field(default_factory=dict)
    lighting: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class ShotOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    sequence_id: uuid.UUID
    code: str | None = None
    title: str
    status: str
    position: int
    prompt: str | None = None
    negative_prompt: str | None = None


class AssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=24)
    description: str | None = None
    project_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)


class AssetOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID | None = None
    name: str
    type: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


# --- jobs ---------------------------------------------------------------
class JobCreate(BaseModel):
    job_type: JobType = JobType.IMAGE
    project_id: uuid.UUID | None = None
    sequence_id: uuid.UUID | None = None
    shot_id: uuid.UUID | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    model_key: str | None = None
    workflow_key: str | None = None
    model_version_id: uuid.UUID | None = None
    workflow_version_id: uuid.UUID | None = None
    priority: JobPriority = JobPriority.NORMAL
    policy: str | None = None
    required_vram_mb: int | None = Field(default=None, ge=0)
    required_capabilities: list[str] = Field(default_factory=list)
    preferred_gpu_ids: list[str] = Field(default_factory=list)
    preferred_node_ids: list[str] = Field(default_factory=list)
    max_retries: int = Field(default=3, ge=0, le=20)
    queue: bool = True


class JobOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID | None = None
    shot_id: uuid.UUID | None = None
    job_type: str
    status: str
    priority: str
    prompt: str | None = None
    negative_prompt: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    progress_percent: float
    progress_step: int
    progress_total: int
    progress_message: str | None = None
    retry_count: int
    max_retries: int
    error: dict[str, Any] | None = None
    cancel_requested: bool
    gpu_device_id: uuid.UUID | None = None
    node_id: uuid.UUID | None = None
    worker_id: uuid.UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class JobDetailOut(JobOut):
    attempts: list[AttemptOut] = Field(default_factory=list)
    events: list[JobEventOut] = Field(default_factory=list)
    media: list[GeneratedMediaOut] = Field(default_factory=list)


class AttemptOut(ORMModel):
    id: uuid.UUID
    attempt_no: int
    status: str
    worker_id: uuid.UUID | None = None
    gpu_device_id: uuid.UUID | None = None
    failure_class: str | None = None
    error: dict[str, Any] | None = None
    runtime_info: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None


class JobEventOut(ORMModel):
    seq: int
    ts: datetime
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class GeneratedMediaOut(ORMModel):
    id: uuid.UUID
    shot_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    media_type: str
    asset_role: str
    current_version_no: int
    approval_status: str
    prompt: str | None = None
    reproducibility: dict[str, Any] = Field(default_factory=dict)


class MediaVersionOut(BaseModel):
    version_no: int
    storage_key: str
    mime_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None


# --- workers ------------------------------------------------------------
class WorkerRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    version: str | None = None
    platform: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    node_key: str | None = Field(
        default=None, description="Stable backend node id so the Scheduler can match it."
    )


class WorkerRegistrationOut(BaseModel):
    worker_id: uuid.UUID
    credential: str = Field(description="Shown once; only the hash is stored.")
    prefix: str
    expires_at: datetime


class WorkerCreatedOut(BaseModel):
    worker_id: uuid.UUID
    registration_token: str = Field(description="One-time token; shown once.")
    expires_at: datetime


class WorkerOut(ORMModel):
    id: uuid.UUID
    name: str
    node_id: uuid.UUID | None = None
    status: str
    version: str | None = None
    platform: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    active_job_count: int
    last_heartbeat_at: datetime | None = None
    registered_at: datetime | None = None


class WorkerListOut(BaseModel):
    items: list[WorkerOut]
    total: int


class HeartbeatRequest(BaseModel):
    status: str | None = None
    active_job_count: int | None = Field(default=None, ge=0)
    #: Leases this worker still believes it holds. Each one is renewed in the
    #: same transaction; a claim that can no longer be renewed comes back in
    #: ``HeartbeatOut.lost_claim_ids``.
    claim_ids: list[str] = Field(default_factory=list)
    #: Reported GPU telemetry. Informational only: device rows are written by the
    #: Scheduler's discovery pass, never by a worker.
    gpus: list[dict[str, Any]] = Field(default_factory=list)
    cache_state: dict[str, Any] | None = None


class HeartbeatOut(BaseModel):
    worker_id: uuid.UUID
    server_time: datetime
    lease_ttl_seconds: int
    heartbeat_interval_seconds: int
    renewed_claim_ids: list[str] = Field(default_factory=list)
    lost_claim_ids: list[str] = Field(default_factory=list)


class ClaimRequest(BaseModel):
    job_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Optional specific job. When omitted the Scheduler picks the highest "
            "priority RESERVED job on this worker's node."
        ),
    )
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ClaimOut(BaseModel):
    job_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_no: int
    claim_id: str
    lease_expires_at: datetime
    lease_ttl_seconds: int
    heartbeat_interval_seconds: int
    gpu_device_id: uuid.UUID = Field(description="Internal device row id, for traceability.")
    gpu_backend_device_id: str = Field(
        description=(
            "Opaque backend handle the worker passes to its GPU backend "
            "(e.g. 'mock-node-0-gpu-0'). Never a database id."
        )
    )
    node_id: uuid.UUID
    node_key: str | None = None
    backend: str | None = Field(default=None, description="Backend that owns the device.")
    required_vram_mb: int | None = Field(
        default=None, description="Reservation size, so the worker can fail fast on OOM."
    )
    job_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    prompt: str | None = None
    negative_prompt: str | None = None
    model_version_id: uuid.UUID | None = None
    workflow_version_id: uuid.UUID | None = None


class ProgressRequest(BaseModel):
    step: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    percent: float | None = Field(default=None, ge=0, le=100)
    message: str | None = Field(default=None, max_length=2000)


class ProgressOut(BaseModel):
    job_id: uuid.UUID
    status: str
    progress_percent: float
    started: bool = Field(description="True when this report moved STARTING -> RUNNING.")


class OutputDescriptor(BaseModel):
    kind: str = Field(default="file", pattern="^(file|url)$")
    mime_type: str = "application/octet-stream"
    filename: str | None = None
    url: str | None = None
    #: base64 payload for small artifacts, or a plain string for text output
    content_base64: str | None = None
    asset_role: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompleteRequest(BaseModel):
    outputs: list[OutputDescriptor] = Field(default_factory=list)
    runtime_info: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float | None = Field(default=None, ge=0)


class FailRequest(BaseModel):
    failure_class: str = "UNKNOWN"
    message: str | None = None
    error: dict[str, Any] = Field(default_factory=dict)
    retryable: bool | None = None


class TerminalOut(BaseModel):
    job_id: uuid.UUID
    status: str
    attempt_id: uuid.UUID | None = None
    artifacts: int = 0
    release_requested: bool = False
    retry: dict[str, Any] | None = None


class JobPriorityUpdate(BaseModel):
    """Only the queue weight changes; no job state transition is involved."""

    priority: JobPriority


class ShotBatchCreate(BaseModel):
    """Batch shot creation (spec section 31): one prompt template, N shots."""

    shots: list[ShotCreate] = Field(min_length=1, max_length=200)
    #: ``{index}``/``{code}``/``{title}`` are substituted in each shot's prompt.
    prompt_template: str | None = Field(default=None, max_length=4000)
    auto_create_jobs: bool = False
    job_params: dict[str, Any] = Field(default_factory=dict)


class ShotBatchOut(BaseModel):
    shots: list[ShotOut]
    job_ids: list[uuid.UUID] = Field(default_factory=list)


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


# --- gpu / cluster ------------------------------------------------------
class GpuNodeOut(ORMModel):
    id: uuid.UUID
    node_key: str
    name: str
    status: str
    endpoint: str | None = None
    last_seen_at: datetime | None = None
    metadata_: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class GpuDeviceOut(ORMModel):
    id: uuid.UUID
    node_id: uuid.UUID
    uuid: str
    index: int
    name: str
    status: str
    health: str
    vram_total_mb: int
    vram_free_mb: int
    compute_capability: str | None = None
    driver_version: str | None = None
    utilization_percent: float
    temperature_c: float | None = None
    power_w: float | None = None
    memory_used_mb: int
    error_message: str | None = None


class GpuDeviceDetailOut(GpuDeviceOut):
    capabilities: dict[str, Any] = Field(default_factory=dict)


class NodeListOut(BaseModel):
    items: list[GpuNodeOut]


class DeviceListOut(BaseModel):
    items: list[GpuDeviceOut]
    total: int


class ClusterSummaryOut(BaseModel):
    nodes: list[GpuNodeOut]
    devices: list[GpuDeviceDetailOut]
    by_status: dict[str, int]
    queued_jobs: int
    jobs_by_status: dict[str, int]
    scheduler: dict[str, Any] = Field(default_factory=dict)


class DeviceStatusUpdate(BaseModel):
    status: str
    reason: str | None = None


# --- models / workflows -------------------------------------------------
class ModelSpecOut(BaseModel):
    key: str
    name: str
    family: str
    job_type: str
    required_capabilities: list[str]
    min_vram_mb: int
    est_seconds: float
    precision: str
    source: str
    notes: str = ""


class WorkflowSpecOut(BaseModel):
    key: str
    name: str
    version: str
    model_keys: list[str]
    required_capabilities: list[str]
    min_vram_mb: int
    node_count: int
    source: str


class ModelListOut(BaseModel):
    items: list[ModelSpecOut]


class WorkflowListOut(BaseModel):
    items: list[WorkflowSpecOut]


# --- health / metrics ---------------------------------------------------
class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    database: bool
    scheduler: dict[str, Any] = Field(default_factory=dict)
    storage: dict[str, Any] = Field(default_factory=dict)
    gpu_backend: dict[str, Any] = Field(default_factory=dict)
    bus_subscribers: int = 0


class MetricPointOut(BaseModel):
    ts: datetime
    name: str
    value: float
    labels: dict[str, Any] = Field(default_factory=dict)


class LogEntryOut(BaseModel):
    ts: datetime
    level: str
    service: str
    message: str
    job_id: uuid.UUID | None = None
    worker_id: uuid.UUID | None = None
    gpu_device_id: uuid.UUID | None = None
    context: dict[str, Any] = Field(default_factory=dict)


JobDetailOut.model_rebuild()
LoginResponse.model_rebuild()

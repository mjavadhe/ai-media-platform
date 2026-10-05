"""Canonical state enumerations (frozen contract sections 3 and 5).

These are the *only* place job/GPU/reservation states are spelled out. The
centralized transition policy in `app.domain.jobs.state_machine` validates every
mutation against these values, so arbitrary string status writes are impossible.
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    """Job lifecycle states (spec section 13, contract section 3)."""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RESERVED = "RESERVED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)

CLAIMABLE_JOB_STATUS: JobStatus = JobStatus.RESERVED


class JobType(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    OTHER = "OTHER"


class JobPriority(StrEnum):
    """Ordering only — priority never preempts a running job (contract section 6)."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


PRIORITY_RANK: dict[JobPriority, int] = {
    JobPriority.LOW: 0,
    JobPriority.NORMAL: 1,
    JobPriority.HIGH: 2,
    JobPriority.URGENT: 3,
}


class AttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"


TERMINAL_ATTEMPT_STATUSES: frozenset[AttemptStatus] = frozenset(
    {
        AttemptStatus.COMPLETED,
        AttemptStatus.FAILED,
        AttemptStatus.INTERRUPTED,
        AttemptStatus.CANCELLED,
    }
)


class FailureClass(StrEnum):
    """Failure taxonomy driving the retry decision (contract section 12)."""

    RETRYABLE = "RETRYABLE"
    FATAL = "FATAL"

    # retryable
    OOM = "OOM"
    CUDA_ERROR = "CUDA_ERROR"
    WORKER_CRASH = "WORKER_CRASH"
    NETWORK = "NETWORK"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    INFRASTRUCTURE = "INFRASTRUCTURE"

    # fatal
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_MODEL = "INVALID_MODEL"
    INVALID_WORKFLOW = "INVALID_WORKFLOW"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    ENGINE_ERROR = "ENGINE_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


RETRYABLE_FAILURES: frozenset[FailureClass] = frozenset(
    {
        FailureClass.OOM,
        FailureClass.CUDA_ERROR,
        FailureClass.WORKER_CRASH,
        FailureClass.NETWORK,
        FailureClass.LEASE_EXPIRED,
        FailureClass.INFRASTRUCTURE,
    }
)

FATAL_FAILURES: frozenset[FailureClass] = frozenset(
    {
        FailureClass.VALIDATION_ERROR,
        FailureClass.INVALID_MODEL,
        FailureClass.INVALID_WORKFLOW,
        FailureClass.UNSUPPORTED_CAPABILITY,
        FailureClass.ENGINE_ERROR,
        FailureClass.STORAGE_ERROR,
        FailureClass.CANCELLED,
    }
)


def classify_failure(value: str | FailureClass) -> FailureClass:
    """Normalize an arbitrary failure string into the taxonomy.

    Unknown strings map to ``UNKNOWN`` which is treated as retryable-but-bounded
    (the retry counter still caps the loop), so a novel engine error can never
    cause an infinite retry storm.
    """
    if isinstance(value, FailureClass):
        return value
    try:
        return FailureClass(value)
    except ValueError:
        upper = value.strip().upper().replace("-", "_").replace(" ", "_")
        for member in FailureClass:
            if member.value == upper:
                return member
        return FailureClass.UNKNOWN


def is_retryable(value: str | FailureClass) -> bool:
    """Retry decision for a failure class (contract section 12)."""
    failure = classify_failure(value)
    if failure in FATAL_FAILURES:
        return False
    if failure in RETRYABLE_FAILURES:
        return True
    return failure not in {FailureClass.CANCELLED}


class GpuStatus(StrEnum):
    """Physical GPU lifecycle — writable ONLY by the Scheduler (contract section 5.1)."""

    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    BUSY = "BUSY"
    DRAINING = "DRAINING"
    UNHEALTHY = "UNHEALTHY"
    OFFLINE = "OFFLINE"
    MAINTENANCE = "MAINTENANCE"


class ReservationState(StrEnum):
    """Logical allocation state — Orchestrator may request release, Scheduler completes it."""

    RESERVED = "RESERVED"
    BUSY = "BUSY"
    RELEASE_PENDING = "RELEASE_PENDING"
    RELEASED = "RELEASED"


ACTIVE_RESERVATION_STATES: tuple[ReservationState, ...] = (
    ReservationState.RESERVED,
    ReservationState.BUSY,
    ReservationState.RELEASE_PENDING,
)


class NodeStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    UNHEALTHY = "UNHEALTHY"
    MAINTENANCE = "MAINTENANCE"


class GpuHealth(StrEnum):
    OK = "OK"
    ERROR = "ERROR"


class WorkerStatus(StrEnum):
    OFFLINE = "OFFLINE"
    IDLE = "IDLE"
    BUSY = "BUSY"
    DISABLED = "DISABLED"


class CapabilitySource(StrEnum):
    REGISTRY = "REGISTRY"
    PROBE = "PROBE"
    REPORTED = "REPORTED"


class SchedulingPolicy(StrEnum):
    AUTO = "AUTO"
    FASTEST = "FASTEST"
    LOWEST_LOAD = "LOWEST_LOAD"
    LOWEST_RESOURCE_COST = "LOWEST_RESOURCE_COST"
    CAPABILITY_MATCH = "CAPABILITY_MATCH"
    MODEL_CACHE_AWARE = "MODEL_CACHE_AWARE"
    ROUND_ROBIN = "ROUND_ROBIN"
    MANUAL = "MANUAL"


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ShotStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class AssetType(StrEnum):
    CHARACTER = "CHARACTER"
    LOCATION = "LOCATION"
    PROP = "PROP"
    VEHICLE = "VEHICLE"
    WEAPON = "WEAPON"
    ENVIRONMENT = "ENVIRONMENT"
    OTHER = "OTHER"


class MediaType(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ModelStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DOWNLOADING = "DOWNLOADING"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


class InstallationStatus(StrEnum):
    INSTALLED = "INSTALLED"
    INSTALLING = "INSTALLING"
    FAILED = "FAILED"
    REMOVED = "REMOVED"


class StorageOwnerType(StrEnum):
    PROJECT = "PROJECT"
    ASSET = "ASSET"
    SHOT = "SHOT"
    JOB = "JOB"
    MEDIA = "MEDIA"
    SYSTEM = "SYSTEM"


class PermissionCode(StrEnum):
    """Permission codes used by RBAC (doc 12)."""

    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PROJECT_DELETE = "project:delete"
    ASSET_READ = "asset:read"
    ASSET_WRITE = "asset:write"
    ASSET_DELETE = "asset:delete"
    JOB_READ = "job:read"
    JOB_CREATE = "job:create"
    JOB_CANCEL = "job:cancel"
    JOB_RETRY = "job:retry"
    MODEL_READ = "model:read"
    MODEL_WRITE = "model:write"
    MODEL_INSTALL = "model:install"
    WORKFLOW_READ = "workflow:read"
    WORKFLOW_WRITE = "workflow:write"
    STORAGE_READ = "storage:read"
    STORAGE_WRITE = "storage:write"
    LOG_READ = "log:read"
    GPU_READ = "gpu:read"
    GPU_ADMIN = "gpu:admin"
    NODE_READ = "node:read"
    NODE_ADMIN = "node:admin"
    WORKER_READ = "worker:read"
    WORKER_ADMIN = "worker:admin"
    USER_READ = "user:read"
    USER_WRITE = "user:write"
    AUDIT_READ = "audit:read"
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"


class RoleCode(StrEnum):
    """Baseline roles (doc 12). ARTIST is the least privileged."""

    ARTIST = "ARTIST"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"


ROLE_PERMISSIONS: dict[RoleCode, tuple[PermissionCode, ...]] = {
    RoleCode.ARTIST: (
        PermissionCode.PROJECT_READ,
        PermissionCode.PROJECT_WRITE,
        PermissionCode.ASSET_READ,
        PermissionCode.ASSET_WRITE,
        PermissionCode.JOB_READ,
        PermissionCode.JOB_CREATE,
        PermissionCode.JOB_CANCEL,
        PermissionCode.MODEL_READ,
        PermissionCode.WORKFLOW_READ,
        PermissionCode.STORAGE_READ,
        PermissionCode.GPU_READ,
        PermissionCode.NODE_READ,
    ),
    RoleCode.OPERATOR: (
        PermissionCode.PROJECT_READ,
        PermissionCode.PROJECT_WRITE,
        PermissionCode.PROJECT_DELETE,
        PermissionCode.ASSET_READ,
        PermissionCode.ASSET_WRITE,
        PermissionCode.ASSET_DELETE,
        PermissionCode.JOB_READ,
        PermissionCode.JOB_CREATE,
        PermissionCode.JOB_CANCEL,
        PermissionCode.JOB_RETRY,
        PermissionCode.MODEL_READ,
        PermissionCode.MODEL_WRITE,
        PermissionCode.MODEL_INSTALL,
        PermissionCode.WORKFLOW_READ,
        PermissionCode.WORKFLOW_WRITE,
        PermissionCode.STORAGE_READ,
        PermissionCode.STORAGE_WRITE,
        PermissionCode.LOG_READ,
        PermissionCode.GPU_READ,
        PermissionCode.NODE_READ,
        PermissionCode.WORKER_READ,
        PermissionCode.USER_READ,
    ),
    RoleCode.ADMIN: tuple(PermissionCode),
}

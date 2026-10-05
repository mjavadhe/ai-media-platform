"""Job endpoints (``/api/v1/jobs``).

The API may only *create* and *request* things. Every actual job state change is
performed by the Orchestrator (business lifecycle) or the Scheduler
(reservation/start/recovery) — this router never writes ``jobs.status`` directly.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from app.api.deps import (
    AppSettings,
    CsrfChecked,
    DbSession,
    RegistryDep,
    require_permissions,
)
from app.api.v1.schemas import (
    CancelRequest,
    GeneratedMediaOut,
    JobCreate,
    JobDetailOut,
    JobEventOut,
    JobListOut,
    JobOut,
    JobPriorityUpdate,
)
from app.domain.enums import JobPriority, JobStatus, PermissionCode
from app.domain.jobs.state_machine import JobOwner, JobStateMachine
from app.infrastructure.events import Event, get_event_bus
from app.infrastructure.logging import get_logger
from app.orchestrator.service import OrchestratorService, WrongStateError
from app.persistence.models_creative import GeneratedMedia
from app.persistence.repositories.jobs import JobRepository
from app.services.storage import build_storage_backend

logger = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Queue weight per priority; lower sorts first in the scheduling claim.
PRIORITY_RANK = {JobPriority.LOW: 0, JobPriority.NORMAL: 1, JobPriority.HIGH: 2}


def _orchestrator(session, settings) -> OrchestratorService:
    storage = build_storage_backend(
        settings.storage_backend, root=settings.storage_root, bucket=settings.storage_bucket
    )
    return OrchestratorService(session, settings, storage)


@router.get("", response_model=JobListOut)
async def list_jobs(
    session: DbSession,
    principal: require_permissions(PermissionCode.JOB_READ.value),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    project_id: uuid.UUID | None = None,
    shot_id: uuid.UUID | None = None,
    job_type: str | None = None,
    worker_id: uuid.UUID | None = None,
    mine: bool = False,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JobListOut:
    statuses: list[JobStatus] | None = None
    if status_filter:
        try:
            statuses = [JobStatus(value.upper()) for value in status_filter]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    repo = JobRepository(session)
    page = await repo.list(
        status=statuses,
        project_id=project_id,
        shot_id=shot_id,
        job_type=job_type,
        worker_id=worker_id,
        created_by=principal.user.id if mine else None,
        search=search,
        limit=limit,
        offset=offset,
    )
    return JobListOut(
        items=[JobOut.model_validate(job) for job in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreate,
    session: DbSession,
    settings: AppSettings,
    registry: RegistryDep,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.JOB_CREATE.value),
) -> JobOut:
    """Create (and optionally enqueue) a job. ``CREATED -> QUEUED`` is API-owned."""
    repo = JobRepository(session)
    required_vram = payload.required_vram_mb
    required_caps = list(payload.required_capabilities)

    # Model/workflow requirements are *data* from the registry, merged with any
    # explicit request, so adding a model never requires scheduler changes.
    if payload.model_key or payload.workflow_key or not payload.required_capabilities:
        default_model = registry.default_model_for(payload.job_type.value)
        model_key = payload.model_key or (default_model.key if default_model else None)
        workflow = (
            registry.get_workflow(payload.workflow_key)
            if payload.workflow_key
            else registry.select_workflow_for(payload.job_type.value)
        )
        requirements = registry.requirements_for(
            model_key=model_key,
            workflow_key=workflow.key if workflow else None,
        )
        required_vram = max(required_vram or 0, int(requirements["required_vram_mb"]))
        for capability in requirements["required_capabilities"]:
            if capability not in required_caps:
                required_caps.append(capability)
    if payload.model_key and registry.get_model(payload.model_key) is None:
        raise HTTPException(status_code=422, detail=f"unknown model {payload.model_key!r}")
    if payload.workflow_key and registry.get_workflow(payload.workflow_key) is None:
        raise HTTPException(status_code=422, detail=f"unknown workflow {payload.workflow_key!r}")

    job = await repo.create(
        job_type=payload.job_type.value,
        created_by=principal.user.id,
        params=payload.params,
        prompt=payload.prompt,
        negative_prompt=payload.negative_prompt,
        project_id=payload.project_id,
        sequence_id=payload.sequence_id,
        shot_id=payload.shot_id,
        model_version_id=payload.model_version_id,
        workflow_version_id=payload.workflow_version_id,
        required_vram_mb=required_vram or 0,
        required_capabilities=required_caps,
        preferred_gpu_uuids=payload.preferred_gpu_ids,
        preferred_node_ids=[uuid.UUID(value) for value in payload.preferred_node_ids],
        priority=payload.priority,
        policy=payload.policy,
        max_retries=payload.max_retries,
        queue=payload.queue,
    )
    logger.info(
        "job.created",
        job_id=str(job.id),
        job_type=job.job_type,
        required_vram_mb=job.required_vram_mb,
        required_capabilities=list(job.required_capabilities or []),
        actor_id=str(principal.user.id),
    )
    await session.commit()
    await get_event_bus().publish(
        _event(job, "job.created", {"status": job.status})
    )
    return JobOut.model_validate(job)


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.JOB_READ.value),
) -> JobDetailOut:
    repo = JobRepository(session)
    job = await repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    attempts = await repo.list_attempts(job_id)
    events = await repo.list_events(job_id)
    media = list(
        (
            await session.scalars(
                select(GeneratedMedia).where(GeneratedMedia.job_id == job_id).order_by(
                    GeneratedMedia.created_at
                )
            )
        ).all()
    )
    base = JobOut.model_validate(job)
    return JobDetailOut(
        **base.model_dump(),
        attempts=[
            {
                "id": attempt.id,
                "attempt_no": attempt.attempt_no,
                "status": attempt.status,
                "worker_id": attempt.worker_id,
                "gpu_device_id": attempt.gpu_device_id,
                "failure_class": attempt.failure_class,
                "error": attempt.error,
                "runtime_info": attempt.runtime_info or {},
                "started_at": attempt.started_at,
                "finished_at": attempt.finished_at,
                "duration_seconds": attempt.duration_seconds,
            }
            for attempt in attempts
        ],
        events=[
            JobEventOut(
                seq=event.seq, ts=event.ts, event_type=event.event_type, payload=event.payload or {}
            )
            for event in events
        ],
        media=[GeneratedMediaOut.model_validate(item) for item in media],
    )


@router.get("/{job_id}/events", response_model=list[JobEventOut])
async def get_job_events(
    job_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.JOB_READ.value),
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[JobEventOut]:
    """Durable event timeline. This is what an SSE client replays from."""
    events = await JobRepository(session).list_events(job_id, after_seq=after_seq, limit=limit)
    return [
        JobEventOut(
            seq=event.seq, ts=event.ts, event_type=event.event_type, payload=event.payload or {}
        )
        for event in events
    ]


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: uuid.UUID,
    payload: CancelRequest,
    session: DbSession,
    settings: AppSettings,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.JOB_CANCEL.value),
) -> JobOut:
    """Request cancellation. The Orchestrator performs the transition (Tx1)."""
    repo = JobRepository(session)
    job = await repo.get(job_id, lock=True)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    # Mark the request first so a crash between here and the Orchestrator's work
    # still leaves an auditable intent; the Orchestrator sweep can then finish it.
    await repo.request_cancel(job, reason=payload.reason)
    await session.flush()

    orchestrator = _orchestrator(session, settings)
    try:
        result = await orchestrator.cancel(job_id=job_id, reason=payload.reason, actor="api")
    except WrongStateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info(
        "job.cancel_requested",
        job_id=str(job_id),
        status=result.status.value,
        release_requested=result.release_requested,
        actor_id=str(principal.user.id),
    )
    await session.commit()
    await get_event_bus().publish(_event(job, "job.cancelled", {"status": job.status}))
    return JobOut.model_validate(job)


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: uuid.UUID,
    session: DbSession,
    settings: AppSettings,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.JOB_RETRY.value),
) -> JobOut:
    """Requeue a failed/cancelled job with a fresh retry budget."""
    orchestrator = _orchestrator(session, settings)
    try:
        result = await orchestrator.retry(job_id=job_id, actor="api")
    except WrongStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    job = await JobRepository(session).get(job_id)
    if job is None:  # pragma: no cover - orchestrator already loaded it
        raise HTTPException(status_code=404, detail="job not found")
    logger.info("job.retried", job_id=str(job_id), actor_id=str(principal.user.id))
    await session.commit()
    await get_event_bus().publish(_event(job, "job.queued", {"status": result.status.value}))
    return JobOut.model_validate(job)


@router.post("/{job_id}/pause", response_model=JobOut)
async def pause_job(
    job_id: uuid.UUID,
    payload: CancelRequest,
    session: DbSession,
    settings: AppSettings,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.JOB_CANCEL.value),
) -> JobOut:
    """Suspend a running job. The lease and GPU stay held (contract section 3)."""
    orchestrator = _orchestrator(session, settings)
    try:
        job = await orchestrator.pause(job_id=job_id, reason=payload.reason)
    except WrongStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await get_event_bus().publish(_event(job, "job.paused", {"status": job.status}))
    return JobOut.model_validate(job)


@router.post("/{job_id}/resume", response_model=JobOut)
async def resume_job(
    job_id: uuid.UUID,
    session: DbSession,
    settings: AppSettings,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.JOB_CANCEL.value),
) -> JobOut:
    orchestrator = _orchestrator(session, settings)
    try:
        job = await orchestrator.resume(job_id=job_id)
    except WrongStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await get_event_bus().publish(_event(job, "job.queued", {"status": job.status}))
    return JobOut.model_validate(job)


@router.patch("/{job_id}/priority", response_model=JobOut)
async def update_priority(
    job_id: uuid.UUID,
    payload: JobPriorityUpdate,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.JOB_CREATE.value),
) -> JobOut:
    """Reweight an unclaimed job. No state transition is involved."""
    job = await JobRepository(session).get(job_id, lock=True)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    await JobRepository(session).set_priority(
        job, priority=payload.priority.value, priority_rank=PRIORITY_RANK[payload.priority]
    )
    logger.info(
        "job.priority_changed",
        job_id=str(job_id),
        priority=payload.priority.value,
        actor_id=str(principal.user.id),
    )
    await session.commit()
    return JobOut.model_validate(job)


@router.get("/{job_id}/legal-transitions")
async def legal_transitions(
    job_id: uuid.UUID,
    session: DbSession,
    principal: require_permissions(PermissionCode.JOB_READ.value),
) -> dict[str, object]:
    """Which states each owner may move this job to — makes the policy visible."""
    job = await JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    current = JobStatus(job.status)
    return {
        "job_id": str(job_id),
        "current": current.value,
        "legal_targets": {
            owner.value: sorted(
                target.value for target in JobStateMachine.legal_targets(current, owner)
            )
            for owner in JobOwner
        },
        "terminal": current.value in {"COMPLETED", "FAILED", "CANCELLED"},
    }


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: uuid.UUID,
    session: DbSession,
    _csrf: CsrfChecked,
    principal: require_permissions(PermissionCode.PROJECT_DELETE.value),
) -> Response:
    """Delete a *terminal* job. Non-terminal jobs must be cancelled first."""
    repo = JobRepository(session)
    job = await repo.get(job_id, lock=True)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not repo.is_terminal(job):
        raise HTTPException(
            status_code=409,
            detail="only a terminal job can be deleted; cancel it first",
        )
    await session.delete(job)
    await session.commit()
    logger.info("job.deleted", job_id=str(job_id), actor_id=str(principal.user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _event(job, event_type: str, payload: dict) -> Event:
    return Event(event_type=event_type, job_id=job.id, payload=payload)


__all__ = ["router"]

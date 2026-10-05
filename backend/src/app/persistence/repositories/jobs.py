"""Job repository: queue claims, the worker-claim/start path and transitions.

Two claims live here and they are *different operations* (contract §10):

* :meth:`JobRepository.claim_next_queued` — the **scheduling claim** over
  ``QUEUED`` rows, performed by the Scheduler using ``FOR UPDATE SKIP LOCKED``.
* :meth:`JobRepository.start_for_worker` — the **worker claim** over ``RESERVED``
  rows. It is a *trigger*: the Scheduler still performs every write, creating the
  attempt + lease and moving the job to ``STARTING``.

``jobs`` doubles as the Phase 1-3 queue table (ADR-0002), so the queue does not
own ``jobs.status`` — it only claims rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, String, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    PRIORITY_RANK,
    TERMINAL_JOB_STATUSES,
    AttemptStatus,
    JobPriority,
    JobStatus,
)
from app.domain.jobs.state_machine import InvalidTransitionError, JobOwner, JobStateMachine
from app.persistence.models_identity import JobEvent, Worker
from app.persistence.models_job import GpuReservation, Job, JobAttempt, JobClaim
from app.persistence.repositories.common import append_job_event, utcnow


class StartConflictError(Exception):
    """The job was not in a claimable state, or lost the claim race."""


class LeaseExpiredError(Exception):
    """The presented lease is not the live lease for this job."""


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """Result of a successful scheduling claim."""

    job: Job
    attempt_no: int


@dataclass(frozen=True, slots=True)
class StartedJob:
    """Result of a successful worker claim (lease + attempt + STARTING)."""

    job: Job
    attempt: JobAttempt
    claim: JobClaim
    claim_id: str
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class Page:
    """Simple keyset/offset page."""

    items: list[Any]
    total: int
    limit: int
    offset: int


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- reads -----------------------------------------------------------
    async def get(self, job_id: uuid.UUID, *, lock: bool = False) -> Job | None:
        stmt = select(Job).where(Job.id == job_id)
        if lock:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    async def list_events(
        self, job_id: uuid.UUID, *, after_seq: int = 0, limit: int = 500
    ) -> list[JobEvent]:
        rows = await self._session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.seq > after_seq)
            .order_by(JobEvent.seq)
            .limit(limit)
        )
        return list(rows.all())

    def _filtered(
        self,
        *,
        status: list[JobStatus] | None = None,
        project_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
        job_type: str | None = None,
        worker_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        search: str | None = None,
    ) -> Select[tuple[Job]]:
        stmt = select(Job)
        if status:
            stmt = stmt.where(Job.status.in_([s.value for s in status]))
        if project_id:
            stmt = stmt.where(Job.project_id == project_id)
        if shot_id:
            stmt = stmt.where(Job.shot_id == shot_id)
        if job_type:
            stmt = stmt.where(Job.job_type == job_type)
        if worker_id:
            stmt = stmt.where(Job.worker_id == worker_id)
        if created_by:
            stmt = stmt.where(Job.created_by == created_by)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(Job.prompt.ilike(pattern) | cast(Job.id, String).ilike(pattern))
        return stmt

    async def list(
        self,
        *,
        status: list[JobStatus] | None = None,
        project_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
        job_type: str | None = None,
        worker_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page:
        base = self._filtered(
            status=status,
            project_id=project_id,
            shot_id=shot_id,
            job_type=job_type,
            worker_id=worker_id,
            created_by=created_by,
            search=search,
        )
        total = await self._session.scalar(select(func.count()).select_from(base.subquery()))
        rows = await self._session.scalars(
            base.order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset)
        )
        return Page(items=list(rows.all()), total=int(total or 0), limit=limit, offset=offset)

    # --- creation (API) --------------------------------------------------
    async def create(
        self,
        *,
        job_type: str,
        created_by: uuid.UUID | None,
        params: dict[str, Any] | None = None,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        project_id: uuid.UUID | None = None,
        sequence_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
        model_version_id: uuid.UUID | None = None,
        workflow_version_id: uuid.UUID | None = None,
        required_vram_mb: int = 0,
        required_capabilities: list[str] | None = None,
        preferred_gpu_uuids: list[str] | None = None,
        preferred_node_ids: list[uuid.UUID] | None = None,
        priority: JobPriority = JobPriority.NORMAL,
        policy: str | None = None,
        max_retries: int = 3,
        queue: bool = True,
    ) -> Job:
        """Create a job in ``CREATED`` (or ``QUEUED``) — the API is the owner."""
        now = utcnow()
        job = Job(
            job_type=job_type,
            status=JobStatus.CREATED.value,
            priority=priority.value,
            priority_rank=PRIORITY_RANK.get(priority, 1),
            visible_at=now,
            params=params or {},
            prompt=prompt,
            negative_prompt=negative_prompt,
            project_id=project_id,
            sequence_id=sequence_id,
            shot_id=shot_id,
            model_version_id=model_version_id,
            workflow_version_id=workflow_version_id,
            required_vram_mb=required_vram_mb,
            required_capabilities=required_capabilities or [],
            preferred_gpu_ids=preferred_gpu_uuids or [],
            preferred_node_ids=[str(node) for node in (preferred_node_ids or [])],
            policy=policy,
            created_by=created_by,
            max_retries=max_retries,
            event_seq=0,
        )
        self._session.add(job)
        await self._session.flush()
        await append_job_event(
            self._session, job, event_type="job.created", payload={"job_type": job_type}
        )
        if queue:
            await self.enqueue(job, owner=JobOwner.API)
        return job

    # --- queue (API enqueue / Orchestrator requeue) ---------------------
    async def set_priority(self, job: Job, *, priority: str, priority_rank: int) -> Job:
        """Reweight a queued job.

        Only permitted while the job has not been claimed: once a GPU is
        reserved, changing the weight would silently reorder work that is
        already committed to a device.
        """
        if JobStatus(job.status) not in {JobStatus.CREATED, JobStatus.QUEUED, JobStatus.RETRYING}:
            raise InvalidTransitionError(
                JobStatus(job.status), JobStatus(job.status), reason="job is already claimed"
            )
        job.priority = priority
        job.priority_rank = priority_rank
        await self._session.flush()
        await append_job_event(
            self._session, job, event_type="job.priority_changed", payload={"priority": priority}
        )
        return job

    async def pause(self, job: Job, *, reason: str | None = None) -> Job:
        """RUNNING -> PAUSED (Orchestrator). The lease and GPU stay held."""
        JobStateMachine.validate(
            JobStatus(job.status), JobStatus.PAUSED, JobOwner.ORCHESTRATOR
        )
        job.status = JobStatus.PAUSED.value
        job.progress_message = reason or job.progress_message
        await self._session.flush()
        await append_job_event(
            self._session, job, event_type="job.paused", payload={"reason": reason}
        )
        return job

    async def resume(self, job: Job, *, visible_at: datetime | None = None) -> Job:
        """PAUSED -> QUEUED (Orchestrator resume)."""
        JobStateMachine.validate(JobStatus(job.status), JobStatus.QUEUED, JobOwner.ORCHESTRATOR)
        return await self.enqueue(job, owner=JobOwner.ORCHESTRATOR, visible_at=visible_at)

    async def enqueue(
        self,
        job: Job,
        *,
        owner: JobOwner,
        visible_at: datetime | None = None,
        delay_seconds: float | None = None,
    ) -> Job:
        """Move ``CREATED``/``RETRYING``/``PAUSED`` to ``QUEUED``."""
        current = JobStatus(job.status)
        target = JobStatus.QUEUED
        JobStateMachine.validate(current, target, owner)
        when = visible_at or (utcnow() + timedelta(seconds=delay_seconds or 0))
        job.status = target.value
        job.visible_at = when
        job.enqueued_at = utcnow()
        job.dequeued_at = None
        job.dequeued_by = None
        job.cancel_requested = False
        job.finished_at = None
        job.error = None
        await self._session.flush()
        await append_job_event(
            self._session, job, event_type="job.queued", payload={"visible_at": when.isoformat()}
        )
        return job

    # --- scheduling claim (Scheduler) ------------------------------------
    async def claim_next_queued(
        self,
        *,
        required_vram_mb: int | None = None,
        required_capabilities: list[str] | None = None,
        policies: list[str] | None = None,
    ) -> ClaimedJob | None:
        """Claim the highest-priority *visible* ``QUEUED`` job.

        ``FOR UPDATE SKIP LOCKED`` makes this safe with multiple Scheduler
        iterations: a row already being scheduled is invisible to the next scan
        instead of being double-handled.
        """
        stmt = (
            select(Job)
            .where(
                Job.status == JobStatus.QUEUED.value,
                Job.visible_at <= utcnow(),
            )
            .order_by(Job.priority_rank.desc(), Job.created_at.asc(), Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if policies:
            stmt = stmt.where(Job.policy.in_(policies))
        if required_capabilities:
            stmt = stmt.where(Job.required_capabilities.contains(required_capabilities))
        job = await self._session.scalar(stmt)
        if job is None:
            return None
        job.dequeued_at = utcnow()
        job.dequeued_by = "scheduler"
        attempt_no = await self._next_attempt_no(job.id)
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.scheduling_claimed",
            payload={"attempt_no": attempt_no},
        )
        return ClaimedJob(job=job, attempt_no=attempt_no)

    async def _next_attempt_no(self, job_id: uuid.UUID) -> int:
        current = await self._session.scalar(
            select(func.max(JobAttempt.attempt_no)).where(JobAttempt.job_id == job_id)
        )
        return int(current or 0) + 1

    async def count_queued(self) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.status == JobStatus.QUEUED.value, Job.visible_at <= utcnow())
            )
            or 0
        )

    async def count_by_status(self) -> dict[str, int]:
        rows = await self._session.execute(select(Job.status, func.count()).group_by(Job.status))
        return {status: int(count) for status, count in rows.all()}

    # --- reservation/start (Scheduler) ----------------------------------
    async def mark_reserved(self, job: Job, *, reservation: GpuReservation, policy: str) -> Job:
        """``QUEUED -> RESERVED`` — Scheduler owns this transition."""
        current = JobStatus(job.status)
        JobStateMachine.validate(current, JobStatus.RESERVED, JobOwner.SCHEDULER)
        job.status = JobStatus.RESERVED.value
        job.policy = policy
        job.gpu_device_id = reservation.gpu_device_id
        job.node_id = reservation.node_id
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.reserved",
            payload={
                "reservation_id": str(reservation.id),
                "gpu_device_id": str(reservation.gpu_device_id),
                "node_id": str(reservation.node_id),
                "policy": policy,
            },
        )
        return job

    async def mark_unreservable(self, job: Job, *, reason: str) -> Job:
        """Put a claimed-but-unplaceable job back on the queue (Scheduler)."""
        current = JobStatus(job.status)
        JobStateMachine.validate(current, JobStatus.QUEUED, JobOwner.SCHEDULER)
        job.status = JobStatus.QUEUED.value
        job.dequeued_at = None
        job.dequeued_by = None
        job.visible_at = utcnow()
        await self._session.flush()
        await append_job_event(
            self._session, job, event_type="job.unreservable", payload={"reason": reason}
        )
        return job

    async def start_for_worker(
        self,
        job_id: uuid.UUID,
        *,
        worker: Worker,
        reservation: GpuReservation,
        lease_ttl_seconds: int,
        capabilities: dict[str, Any] | None = None,
    ) -> StartedJob:
        """Worker claim = start trigger. All writes performed by the Scheduler.

        Ordering inside one transaction: create the attempt, create the lease,
        flip the reservation to ``BUSY``, then ``RESERVED -> STARTING``. The
        partial unique indexes make a duplicate concurrent claim fail loudly
        instead of producing two running attempts.
        """
        job = await self.get(job_id, lock=True)
        if job is None:
            raise StartConflictError(f"job {job_id} not found")
        current = JobStatus(job.status)
        if current is not JobStatus.RESERVED:
            raise StartConflictError(f"job {job_id} is {current.value}, expected RESERVED")
        if reservation.job_id != job.id:
            raise StartConflictError("reservation does not belong to this job")
        if reservation.state not in {"RESERVED", "BUSY"}:
            raise StartConflictError(f"reservation is {reservation.state}, cannot start")

        attempt_no = await self._next_attempt_no(job.id)
        now = utcnow()
        attempt = JobAttempt(
            job_id=job.id,
            attempt_no=attempt_no,
            status=AttemptStatus.RUNNING.value,
            worker_id=worker.id,
            gpu_device_id=reservation.gpu_device_id,
            node_id=reservation.node_id,
            started_at=now,
            runtime_info={},
        )
        self._session.add(attempt)
        await self._session.flush()

        claim_id = uuid.uuid4().hex
        claim = JobClaim(
            claim_id=claim_id,
            job_id=job.id,
            worker_id=worker.id,
            attempt_id=attempt.id,
            expires_at=now + timedelta(seconds=lease_ttl_seconds),
        )
        self._session.add(claim)
        await self._session.flush()

        reservation.state = "BUSY"
        reservation.worker_id = worker.id

        JobStateMachine.validate(current, JobStatus.STARTING, JobOwner.SCHEDULER)
        job.status = JobStatus.STARTING.value
        job.worker_id = worker.id
        job.gpu_device_id = reservation.gpu_device_id
        job.node_id = reservation.node_id
        job.started_at = job.started_at or now
        if capabilities:
            job.params = {**(job.params or {}), "worker_capabilities": capabilities}
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.starting",
            payload={
                "attempt_id": str(attempt.id),
                "attempt_no": attempt_no,
                "claim_id": claim_id,
                "lease_expires_at": claim.expires_at.isoformat(),
                "gpu_device_id": str(reservation.gpu_device_id),
            },
            attempt_id=attempt.id,
        )
        return StartedJob(
            job=job,
            attempt=attempt,
            claim=claim,
            claim_id=claim_id,
            lease_expires_at=claim.expires_at,
        )

    # --- Orchestrator lifecycle -----------------------------------------
    async def mark_running(
        self, job: Job, *, attempt: JobAttempt, progress_percent: float | None = None
    ) -> Job:
        """``STARTING -> RUNNING`` on the first progress report. Job row only.

        The GPU stays ``BUSY`` and the reservation stays ``BUSY``: running is not
        a GPU state change, and the Scheduler is not involved here (contract §3.2).
        """
        current = JobStatus(job.status)
        JobStateMachine.validate(current, JobStatus.RUNNING, JobOwner.ORCHESTRATOR)
        job.status = JobStatus.RUNNING.value
        if progress_percent is not None:
            job.progress_percent = max(0.0, min(100.0, float(progress_percent)))
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.running",
            payload={"progress_percent": job.progress_percent},
            attempt_id=attempt.id if attempt else None,
        )
        return job

    async def record_progress(
        self,
        job: Job,
        *,
        attempt: JobAttempt | None,
        step: int | None = None,
        total: int | None = None,
        percent: float | None = None,
        message: str | None = None,
    ) -> Job:
        job.progress_step = step if step is not None else job.progress_step
        job.progress_total = total if total is not None else job.progress_total
        job.progress_percent = (
            max(0.0, min(100.0, float(percent))) if percent is not None else job.progress_percent
        )
        job.progress_message = message
        await self._session.flush()
        if attempt is not None:
            await append_job_event(
                self._session,
                job,
                event_type="job.progress",
                payload={
                    "step": job.progress_step,
                    "total": job.progress_total,
                    "percent": round(job.progress_percent, 2),
                    "message": message,
                },
                attempt_id=attempt.id,
            )
        return job

    async def mark_completed(
        self,
        job: Job,
        *,
        attempt: JobAttempt,
        runtime_info: dict[str, Any] | None = None,
        duration_seconds: float | None = None,
    ) -> Job:
        current = JobStatus(job.status)
        JobStateMachine.validate(current, JobStatus.COMPLETED, JobOwner.ORCHESTRATOR)
        now = utcnow()
        job.status = JobStatus.COMPLETED.value
        job.progress_percent = 100.0
        job.progress_step = job.progress_total or job.progress_step
        job.finished_at = now
        job.error = None
        if job.started_at:
            job.duration_seconds = duration_seconds or (now - job.started_at).total_seconds()
        if attempt is not None:
            attempt.status = AttemptStatus.COMPLETED.value
            attempt.finished_at = now
            attempt.duration_seconds = (
                (now - attempt.started_at).total_seconds() if attempt.started_at else None
            )
            attempt.runtime_info = {**(attempt.runtime_info or {}), **(runtime_info or {})}
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.completed",
            payload={"duration_seconds": job.duration_seconds},
            attempt_id=attempt.id if attempt else None,
        )
        return job

    async def mark_retrying(
        self,
        job: Job,
        *,
        attempt: JobAttempt,
        failure_class: str,
        error: dict[str, Any] | None,
        delay_seconds: float,
        retry_count: int,
    ) -> Job:
        current = JobStatus(job.status)
        JobStateMachine.validate(current, JobStatus.RETRYING, JobOwner.ORCHESTRATOR)
        now = utcnow()
        job.status = JobStatus.RETRYING.value
        job.retry_count = retry_count
        job.error = error
        job.worker_id = None
        job.visible_at = now + timedelta(seconds=delay_seconds)
        if attempt is not None:
            attempt.status = AttemptStatus.FAILED.value
            attempt.finished_at = now
            attempt.failure_class = str(failure_class)
            attempt.error = error
            attempt.duration_seconds = (
                (now - attempt.started_at).total_seconds() if attempt.started_at else None
            )
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.retrying",
            payload={
                "failure_class": str(failure_class),
                "retry_count": retry_count,
                "delay_seconds": round(delay_seconds, 3),
                "error": error,
            },
            attempt_id=attempt.id if attempt else None,
        )
        return job

    async def mark_failed(
        self,
        job: Job,
        *,
        attempt: JobAttempt | None,
        failure_class: str,
        error: dict[str, Any] | None,
        reason: str | None = None,
    ) -> Job:
        current = JobStatus(job.status)
        JobStateMachine.validate(current, JobStatus.FAILED, JobOwner.ORCHESTRATOR)
        now = utcnow()
        job.status = JobStatus.FAILED.value
        job.error = {**(error or {}), "failure_class": str(failure_class), "reason": reason}
        job.finished_at = now
        if job.started_at:
            job.duration_seconds = (now - job.started_at).total_seconds()
        if attempt is not None:
            attempt.status = AttemptStatus.FAILED.value
            attempt.finished_at = now
            attempt.failure_class = str(failure_class)
            attempt.error = error
            attempt.duration_seconds = (
                (now - attempt.started_at).total_seconds() if attempt.started_at else None
            )
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.failed",
            payload={"failure_class": str(failure_class), "error": error, "reason": reason},
            attempt_id=attempt.id if attempt else None,
        )
        return job

    async def request_cancel(self, job: Job, *, reason: str | None = None) -> Job:
        """Flag a cancel request (API). The Orchestrator performs the transition."""
        job.cancel_requested = True
        if reason:
            job.error = {**(job.error or {}), "cancel_reason": reason}
        await self._session.flush()
        return job

    async def mark_cancelled(
        self,
        job: Job,
        *,
        attempt: JobAttempt | None,
        reason: str | None = None,
    ) -> Job:
        current = JobStatus(job.status)
        JobStateMachine.validate(current, JobStatus.CANCELLED, JobOwner.ORCHESTRATOR)
        now = utcnow()
        job.status = JobStatus.CANCELLED.value
        job.finished_at = now
        job.worker_id = None
        if job.started_at:
            job.duration_seconds = (now - job.started_at).total_seconds()
        if attempt is not None:
            attempt.status = AttemptStatus.CANCELLED.value
            attempt.finished_at = now
            attempt.duration_seconds = (
                (now - attempt.started_at).total_seconds() if attempt.started_at else None
            )
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.cancelled",
            payload={"reason": reason},
            attempt_id=attempt.id if attempt else None,
        )
        return job

    # --- Scheduler recovery paths ---------------------------------------
    async def requeue_for_recovery(
        self,
        job: Job,
        *,
        attempt: JobAttempt | None,
        reason: str,
        failure_class: str,
        error: dict[str, Any] | None = None,
        visible_delay_seconds: float = 0.0,
        increment_retry: bool = True,
    ) -> Job:
        """Recovery: send a stuck job back to ``QUEUED`` in one transaction.

        Used for expired leases and lost workers. The Scheduler is the actor it
        detected itself, which is why it owns this transition (contract §3.2).
        The GPU side is handled by the caller's release path in the same
        transaction, so no separate reconciliation is required for this case.
        """
        current = JobStatus(job.status)
        JobStateMachine.validate(current, JobStatus.QUEUED, JobOwner.SCHEDULER)
        now = utcnow()
        job.status = JobStatus.QUEUED.value
        job.worker_id = None
        job.gpu_device_id = None
        job.node_id = None
        job.policy = None
        job.visible_at = now + timedelta(seconds=max(0.0, visible_delay_seconds))
        job.dequeued_at = None
        job.dequeued_by = None
        if increment_retry:
            job.retry_count += 1
        job.error = {"failure_class": failure_class, "reason": reason, **(error or {})}
        if attempt is not None:
            attempt.status = AttemptStatus.INTERRUPTED.value
            attempt.finished_at = now
            attempt.failure_class = failure_class
            attempt.duration_seconds = (
                (now - attempt.started_at).total_seconds() if attempt.started_at else None
            )
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.requeued_by_recovery",
            payload={"reason": reason, "failure_class": failure_class},
            attempt_id=attempt.id if attempt else None,
        )
        return job

    async def fail_from_recovery(
        self,
        job: Job,
        *,
        attempt: JobAttempt | None,
        reason: str,
        failure_class: str,
        error: dict[str, Any] | None = None,
    ) -> Job:
        """Recovery: give up on a stuck job (budget exhausted or fatal)."""
        current = JobStatus(job.status)
        JobStateMachine.validate(current, JobStatus.FAILED, JobOwner.SCHEDULER)
        now = utcnow()
        job.status = JobStatus.FAILED.value
        job.error = {"failure_class": failure_class, "reason": reason, **(error or {})}
        job.finished_at = now
        job.worker_id = None
        if attempt is not None:
            attempt.status = AttemptStatus.INTERRUPTED.value
            attempt.finished_at = now
            attempt.failure_class = failure_class
            attempt.duration_seconds = (
                (now - attempt.started_at).total_seconds() if attempt.started_at else None
            )
        await self._session.flush()
        await append_job_event(
            self._session,
            job,
            event_type="job.failed_by_recovery",
            payload={"reason": reason, "failure_class": failure_class},
            attempt_id=attempt.id if attempt else None,
        )
        return job

    # --- leases ----------------------------------------------------------
    async def renew_lease(
        self, job: Job, *, claim_id: str, worker_id: uuid.UUID, lease_ttl_seconds: int
    ) -> JobClaim:
        """Extend the live lease. A stale ``claim_id`` is rejected, not renewed."""
        claim = await self._session.scalar(
            select(JobClaim)
            .where(
                JobClaim.job_id == job.id,
                JobClaim.worker_id == worker_id,
                JobClaim.released_at.is_(None),
            )
            .order_by(JobClaim.created_at.desc())
            .with_for_update()
        )
        if claim is None:
            raise LeaseExpiredError(f"no live claim for job {job.id} / worker {worker_id}")
        if claim.claim_id != claim_id:
            raise LeaseExpiredError("claim_id does not match the live lease")
        if claim.expires_at <= utcnow():
            raise LeaseExpiredError("lease already expired")
        claim.expires_at = utcnow() + timedelta(seconds=lease_ttl_seconds)
        claim.renewed_count += 1
        await self._session.flush()
        return claim

    async def release_claim(
        self, job_id: uuid.UUID, *, reason: str, claim_id: str | None = None
    ) -> bool:
        """Release the live lease(s) for a job. Idempotent."""
        stmt = update(JobClaim).where(
            JobClaim.job_id == job_id,
            JobClaim.released_at.is_(None),
        )
        if claim_id:
            stmt = stmt.where(JobClaim.claim_id == claim_id)
        result = await self._session.execute(
            stmt.values(released_at=utcnow(), release_reason=reason).execution_options(
                synchronize_session=False
            )
        )
        return bool(result.rowcount)

    async def get_by_claim_id(self, claim_id: str) -> Job | None:
        """Resolve a job from an opaque ``claim_id`` (worker-facing lookups)."""
        row = await self._session.scalar(
            select(JobClaim.job_id).where(
                JobClaim.claim_id == claim_id, JobClaim.released_at.is_(None)
            )
        )
        return await self.get(row) if row is not None else None

    async def get_live_claim(self, job_id: uuid.UUID) -> JobClaim | None:
        return await self._session.scalar(
            select(JobClaim)
            .where(JobClaim.job_id == job_id, JobClaim.released_at.is_(None))
            .order_by(JobClaim.created_at.desc())
        )

    async def get_open_attempt(self, job_id: uuid.UUID) -> JobAttempt | None:
        return await self._session.scalar(
            select(JobAttempt)
            .where(
                JobAttempt.job_id == job_id,
                JobAttempt.status == AttemptStatus.RUNNING.value,
            )
            .order_by(JobAttempt.attempt_no.desc())
        )

    async def list_attempts(self, job_id: uuid.UUID) -> list[JobAttempt]:
        rows = await self._session.scalars(
            select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt_no)
        )
        return list(rows.all())

    # --- sweep helpers used by the Scheduler ----------------------------
    async def find_stale_jobs(
        self, *, stuck_statuses: list[JobStatus], cutoff: datetime
    ) -> list[Job]:
        """Jobs in a non-terminal state whose last progress is older than cutoff."""
        rows = await self._session.scalars(
            select(Job)
            .where(
                Job.status.in_([status.value for status in stuck_statuses]),
                Job.updated_at < cutoff,
            )
            .order_by(Job.updated_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        return list(rows.all())

    async def find_expired_leases(self, *, limit: int = 100) -> list[JobClaim]:
        rows = await self._session.scalars(
            select(JobClaim)
            .where(JobClaim.released_at.is_(None), JobClaim.expires_at <= utcnow())
            .order_by(JobClaim.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows.all())

    async def find_due_retries(self, *, limit: int = 100) -> list[Job]:
        """``RETRYING`` jobs whose backoff delay has elapsed (Orchestrator sweep).

        ``skip_locked`` lets several sweeper iterations overlap safely.
        """
        rows = await self._session.scalars(
            select(Job)
            .where(
                Job.status == JobStatus.RETRYING.value,
                Job.visible_at.isnot(None),
                Job.visible_at <= utcnow(),
            )
            .order_by(Job.visible_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows.all())

    async def find_cancel_requested(self, *, limit: int = 100) -> list[Job]:
        rows = await self._session.scalars(
            select(Job)
            .where(
                Job.cancel_requested.is_(True),
                Job.status.notin_([status.value for status in TERMINAL_JOB_STATUSES]),
            )
            .order_by(Job.updated_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows.all())

    def is_terminal(self, job: Job) -> bool:
        return JobStatus(job.status) in TERMINAL_JOB_STATUSES


__all__ = [
    "ClaimedJob",
    "InvalidTransitionError",
    "JobRepository",
    "LeaseExpiredError",
    "Page",
    "StartConflictError",
    "StartedJob",
]

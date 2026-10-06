"""Orchestrator: the business lifecycle of a job.

The Orchestrator owns ``CREATED -> QUEUED``, ``STARTING -> RUNNING``,
``RUNNING -> COMPLETED/RETRYING/FAILED/CANCELLED`` and the retry budget. It does
**not** own GPU state — its only GPU action is requesting a release in Tx1.

The completion/cancellation protocol (contract section 11):

* **Tx1** (here): job -> terminal, attempt -> terminal, media rows written,
  reservation -> ``RELEASE_PENDING``, an event appended. Committed atomically.
* **Tx2** (Scheduler): device -> ``AVAILABLE``, reservation -> ``RELEASED``.

If the process dies between them, the Scheduler's release reconciliation finds
the ``RELEASE_PENDING`` row and finishes Tx2. Because Tx1 already committed, a
crash can never leave a job terminal while its GPU stays reserved forever.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    TERMINAL_JOB_STATUSES,
    FailureClass,
    JobStatus,
)
from app.domain.jobs.state_machine import JobOwner
from app.domain.retry import RetryDecision, decide_retry
from app.infrastructure.config import Settings
from app.infrastructure.logging import get_logger
from app.persistence.models_job import Job
from app.persistence.repositories.common import append_job_event, utcnow
from app.persistence.repositories.gpu import GpuRepository
from app.persistence.repositories.jobs import JobRepository, LeaseExpiredError
from app.services.media import MediaService
from app.services.storage.base import StorageBackend

logger = get_logger(__name__)


class StaleLeaseError(Exception):
    """The caller's claim/lease is no longer the live one for this job."""


class WrongStateError(Exception):
    """The job is not in a state where this operation applies."""


@dataclass(frozen=True, slots=True)
class CompletionResult:
    job_id: uuid.UUID
    status: JobStatus
    attempt_id: uuid.UUID | None
    retry: RetryDecision | None
    artifacts: int
    release_requested: bool


class OrchestratorService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        storage: StorageBackend,
    ) -> None:
        self._session = session
        self._settings = settings
        self._jobs = JobRepository(session)
        self._gpu = GpuRepository(session)
        self._media = MediaService(session, storage)

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------
    async def record_progress(
        self,
        *,
        job_id: uuid.UUID,
        claim_id: str,
        worker_id: uuid.UUID,
        step: int | None = None,
        total: int | None = None,
        percent: float | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        """Handle a progress report.

        The *first* report is what moves ``STARTING -> RUNNING`` (Orchestrator
        owned, Job row only). Later reports just update counters.
        """
        job, attempt = await self._authorise(job_id, claim_id, worker_id)
        current = JobStatus(job.status)
        was_starting = current is JobStatus.STARTING
        if was_starting:
            await self._jobs.mark_running(job, attempt=attempt, progress_percent=percent)
        else:
            await self._jobs.record_progress(
                job, attempt=attempt, step=step, total=total, percent=percent, message=message
            )
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
            attempt_id=attempt.id if attempt else None,
        )
        return {
            "job_id": str(job.id),
            "status": JobStatus(job.status).value,
            "progress_percent": job.progress_percent,
            "started": was_starting,
        }

    # ------------------------------------------------------------------
    # Completion (Tx1)
    # ------------------------------------------------------------------
    async def complete(
        self,
        *,
        job_id: uuid.UUID,
        claim_id: str,
        worker_id: uuid.UUID,
        outputs: list[dict[str, Any]] | None = None,
        runtime_info: dict[str, Any] | None = None,
        duration_seconds: float | None = None,
    ) -> CompletionResult:
        """Job -> COMPLETED, media registered, release requested. All in Tx1."""
        job, attempt = await self._authorise(job_id, claim_id, worker_id)
        if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
            # Idempotent duplicate report: the first one already did the work.
            return CompletionResult(
                job_id=job.id,
                status=JobStatus(job.status),
                attempt_id=attempt.id if attempt else None,
                retry=None,
                artifacts=0,
                release_requested=False,
            )
        if JobStatus(job.status) is not JobStatus.RUNNING:
            raise WrongStateError(f"job {job_id} is {job.status}, expected RUNNING to complete")

        artifacts = await self._media.persist_outputs(
            job=job,
            attempt=attempt,
            outputs=outputs or [],
            prompt=job.prompt,
            negative_prompt=job.negative_prompt,
            runtime_info=runtime_info,
            register_media=True,
        )
        await self._jobs.mark_completed(
            job, attempt=attempt, runtime_info=runtime_info, duration_seconds=duration_seconds
        )
        release_requested = await self._request_release(job)
        await self._jobs.release_claim(job.id, reason="completed", claim_id=claim_id)
        logger.info(
            "job.completed",
            job_id=str(job.id),
            attempt_id=str(attempt.id) if attempt else None,
            artifacts=len(artifacts),
        )
        return CompletionResult(
            job_id=job.id,
            status=JobStatus.COMPLETED,
            attempt_id=attempt.id if attempt else None,
            retry=None,
            artifacts=len(artifacts),
            release_requested=release_requested,
        )

    # ------------------------------------------------------------------
    # Failure (Tx1)
    # ------------------------------------------------------------------
    async def fail(
        self,
        *,
        job_id: uuid.UUID,
        claim_id: str,
        worker_id: uuid.UUID,
        failure_class: str | FailureClass,
        error: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> CompletionResult:
        """Classify the failure, then RETRYING (delayed requeue) or FAILED.

        The Orchestrator decides retry-vs-fail; the Scheduler only re-enters when
        a delayed job becomes visible.
        """
        job, attempt = await self._authorise(job_id, claim_id, worker_id)
        current = JobStatus(job.status)
        if current in TERMINAL_JOB_STATUSES:
            return CompletionResult(
                job_id=job.id,
                status=current,
                attempt_id=attempt.id if attempt else None,
                retry=None,
                artifacts=0,
                release_requested=False,
            )
        if current not in {JobStatus.RUNNING, JobStatus.STARTING}:
            raise WrongStateError(
                f"job {job_id} is {job.status}, expected RUNNING/STARTING to fail"
            )

        decision = decide_retry(
            failure_class,
            attempt_no=(attempt.attempt_no if attempt else 1),
            max_retries=job.max_retries,
            base_seconds=self._settings.job_retry_base_seconds,
            max_seconds=self._settings.job_retry_max_seconds,
        )
        if retryable is not None and retryable != decision.should_retry:
            decision = RetryDecision(
                should_retry=retryable,
                failure_class=decision.failure_class,
                attempt_no=decision.attempt_no,
                retry_count=decision.retry_count,
                max_retries=decision.max_retries,
                delay_seconds=(
                    decide_retry(
                        failure_class,
                        attempt_no=decision.attempt_no,
                        max_retries=job.max_retries,
                        base_seconds=self._settings.job_retry_base_seconds,
                        max_seconds=self._settings.job_retry_max_seconds,
                    ).delay_seconds
                    if retryable
                    else 0.0
                ),
                reason="worker asserted retryability" if retryable else "worker asserted fatal",
            )
        # The retry budget is server policy. A worker reporting its failure as
        # retryable must not be able to extend the budget: otherwise a buggy or
        # malicious agent can keep a job alive forever by always saying
        # "retryable". Re-checked here so the hint above can still *shorten* the
        # budget (retryable=False) but never lengthen it.
        if decision.should_retry and decision.retry_count >= decision.max_retries:
            decision = RetryDecision(
                False,
                decision.failure_class,
                decision.attempt_no,
                decision.retry_count,
                decision.max_retries,
                reason=f"retry budget exhausted ({decision.max_retries} retries)",
            )

        release_requested = await self._request_release(job)
        if decision.should_retry:
            await self._jobs.mark_retrying(
                job,
                attempt=attempt,
                failure_class=decision.failure_class.value,
                error=error,
                delay_seconds=decision.delay_seconds,
                retry_count=decision.retry_count + 1,
            )
            logger.info(
                "job.retry_scheduled",
                job_id=str(job.id),
                failure_class=decision.failure_class.value,
                retry_count=decision.retry_count + 1,
                delay_seconds=round(decision.delay_seconds, 2),
            )
        else:
            await self._jobs.mark_failed(
                job,
                attempt=attempt,
                failure_class=decision.failure_class.value,
                error=error,
                reason=decision.reason,
            )
            # Keep the failed job's artifacts visible in the UI.
            if not error or not error.get("artifacts_recorded"):
                await self._media.persist_outputs(
                    job=job,
                    attempt=attempt,
                    outputs=(error or {}).get("outputs") or [],
                    prompt=job.prompt,
                    negative_prompt=job.negative_prompt,
                    runtime_info=(error or {}).get("runtime_info"),
                    register_media=True,
                )
            logger.warning(
                "job.failed",
                job_id=str(job.id),
                failure_class=decision.failure_class.value,
                reason=decision.reason,
            )
        await self._jobs.release_claim(job.id, reason="failed", claim_id=claim_id)
        return CompletionResult(
            job_id=job.id,
            status=JobStatus(job.status),
            attempt_id=attempt.id if attempt else None,
            retry=decision,
            artifacts=0,
            release_requested=release_requested,
        )

    # ------------------------------------------------------------------
    # Cancellation (Tx1)
    # ------------------------------------------------------------------
    async def cancel(
        self,
        *,
        job_id: uuid.UUID,
        reason: str | None = None,
        actor: str = "api",
    ) -> CompletionResult:
        """Cancel a job. Works from any non-terminal state, in one transaction."""
        job = await self._jobs.get(job_id, lock=True)
        if job is None:
            raise WrongStateError(f"job {job_id} not found")
        current = JobStatus(job.status)
        if current in TERMINAL_JOB_STATUSES:
            return CompletionResult(
                job_id=job.id,
                status=current,
                attempt_id=None,
                retry=None,
                artifacts=0,
                release_requested=False,
            )
        if current is JobStatus.CREATED:
            # Nothing was ever reserved: no GPU side to undo.
            await self._jobs.mark_cancelled(job, attempt=None, reason=reason)
            await append_job_event(
                self._session,
                job,
                event_type="job.cancelled",
                payload={"reason": reason, "actor": actor},
            )
            return CompletionResult(
                job_id=job.id,
                status=JobStatus.CANCELLED,
                attempt_id=None,
                retry=None,
                artifacts=0,
                release_requested=False,
            )
        if current is JobStatus.QUEUED:
            await self._jobs.mark_cancelled(job, attempt=None, reason=reason)
            return CompletionResult(
                job_id=job.id,
                status=JobStatus.CANCELLED,
                attempt_id=None,
                retry=None,
                artifacts=0,
                release_requested=False,
            )

        attempt = await self._jobs.get_open_attempt(job.id)
        release_requested = await self._request_release(job)
        await self._jobs.mark_cancelled(job, attempt=attempt, reason=reason)
        await self._jobs.release_claim(job.id, reason="cancelled")
        logger.info("job.cancelled", job_id=str(job.id), actor=actor, reason=reason)
        return CompletionResult(
            job_id=job.id,
            status=JobStatus.CANCELLED,
            attempt_id=attempt.id if attempt else None,
            retry=None,
            artifacts=0,
            release_requested=release_requested,
        )

    # ------------------------------------------------------------------
    # Retry (user request)
    # ------------------------------------------------------------------
    async def retry(self, *, job_id: uuid.UUID, actor: str = "api") -> CompletionResult:
        """Manually requeue a FAILED job with a fresh retry budget.

        Deliberately does not touch the GPU side: a FAILED job has already been
        through Tx1 *and* Tx2, so its reservation is ``RELEASED``. Performing any
        GPU mutation here would break the Scheduler-only release rule.
        """
        job = await self._jobs.get(job_id, lock=True)
        if job is None:
            raise WrongStateError(f"job {job_id} not found")
        if JobStatus(job.status) is not JobStatus.FAILED:
            raise WrongStateError(
                f"job {job_id} is {job.status}; only FAILED jobs can be retried manually"
            )
        job.retry_count = 0
        await self._jobs.enqueue(job, owner=JobOwner.API, visible_at=utcnow())
        logger.info("job.retried", job_id=str(job.id), actor=actor)
        return CompletionResult(
            job_id=job.id,
            status=JobStatus.QUEUED,
            attempt_id=None,
            retry=None,
            artifacts=0,
            release_requested=False,
        )

    async def pause(self, *, job_id: uuid.UUID, reason: str | None = None) -> Job:
        """RUNNING -> PAUSED. The lease and GPU are *held* while paused."""
        job = await self._jobs.get(job_id, lock=True)
        if job is None:
            raise WrongStateError(f"job {job_id} not found")
        await self._jobs.pause(job, reason=reason)
        logger.info("job.paused", job_id=str(job.id), reason=reason)
        return job

    async def resume(self, *, job_id: uuid.UUID) -> Job:
        """PAUSED -> QUEUED, making the job claimable again."""
        job = await self._jobs.get(job_id, lock=True)
        if job is None:
            raise WrongStateError(f"job {job_id} not found")
        await self._jobs.resume(job, visible_at=utcnow())
        logger.info("job.resumed", job_id=str(job.id))
        return job

    # ------------------------------------------------------------------
    # Periodic sweeps (driven by the orchestrator runner)
    # ------------------------------------------------------------------
    async def sweep_due_retries(self, *, limit: int = 100) -> int:
        """Promote ``RETRYING`` jobs whose backoff has elapsed to ``QUEUED``.

        The Orchestrator owns ``RETRYING -> QUEUED`` (contract section 3.3); the
        decision *to* retry was made when the failure was classified, this only
        makes the row claimable again once ``visible_at`` is in the past.
        """
        promoted = 0
        for job in await self._jobs.find_due_retries(limit=limit):
            await self._jobs.enqueue(job, owner=JobOwner.ORCHESTRATOR, visible_at=utcnow())
            promoted += 1
            logger.info("job.retry_visible", job_id=str(job.id), retry_count=job.retry_count)
        return promoted

    async def sweep_cancel_requests(self, *, limit: int = 100) -> int:
        """Finish cancellation requests that a crashed request handler left open."""
        cancelled = 0
        for job in await self._jobs.find_cancel_requested(limit=limit):
            try:
                await self.cancel(job_id=job.id, reason=job.cancel_reason, actor="sweeper")
            except WrongStateError:
                # Raced with a terminal transition; the flag is cleared below.
                job.cancel_requested = False
            cancelled += 1
        return cancelled

    async def pending_release_count(self) -> int:
        """Read-only view of Tx2 backlog, for health/metrics reporting."""
        return len(await self._gpu.get_pending_releases(older_than_seconds=0.0))

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _authorise(
        self, job_id: uuid.UUID, claim_id: str, worker_id: uuid.UUID
    ) -> tuple[Job, Any]:
        """Load the job and verify the caller's lease is the live one."""
        job = await self._jobs.get(job_id)
        if job is None:
            raise WrongStateError(f"job {job_id} not found")
        if job.worker_id is not None and job.worker_id != worker_id:
            raise StaleLeaseError(f"job {job_id} is owned by another worker")
        try:
            await self._jobs.renew_lease(
                job,
                claim_id=claim_id,
                worker_id=worker_id,
                lease_ttl_seconds=self._settings.lease_ttl_seconds,
            )
        except LeaseExpiredError as exc:
            raise StaleLeaseError(str(exc)) from exc
        attempt = await self._jobs.get_open_attempt(job.id)
        return job, attempt

    async def _request_release(self, job: Job) -> bool:
        """Tx1 GPU action: request, never perform, the release."""
        reservation = await self._gpu.get_reservation_for_job(job.id, lock=True)
        if reservation is None:
            return False
        requested = await self._gpu.request_release(reservation.id)
        if requested:
            logger.info(
                "gpu.release_requested",
                job_id=str(job.id),
                reservation_id=str(reservation.id),
            )
        await self._session.flush()
        return requested


def _orchestrator_owner():
    from app.domain.jobs.state_machine import JobOwner

    return JobOwner.ORCHESTRATOR


__all__ = [
    "CompletionResult",
    "OrchestratorService",
    "StaleLeaseError",
    "WrongStateError",
]

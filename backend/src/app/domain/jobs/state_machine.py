"""Centralized Job state-transition policy (frozen contract section 3).

This module is the **single** place where Job state changes are permitted. Every
component goes through :class:`JobStateMachine`, which enforces:

* the legal transition graph,
* one owner per transition (ownership contract, section 6 of the master prompt),
* timestamp side effects (``started_at`` / ``finished_at``).

The owner check is what makes the ownership contract executable rather than
merely documented: the Orchestrator cannot start a job, the Scheduler cannot
complete one, and the Worker cannot write ``Job.state`` at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.enums import TERMINAL_JOB_STATUSES, JobStatus


class JobOwner(StrEnum):
    """Logical owner of a Job transition (who writes ``jobs.status``)."""

    API = "API"
    SCHEDULER = "SCHEDULER"
    ORCHESTRATOR = "ORCHESTRATOR"


#: The complete legal transition graph. Anything not listed is rejected.
LEGAL_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: frozenset({JobStatus.QUEUED}),
    JobStatus.QUEUED: frozenset({JobStatus.RESERVED, JobStatus.CANCELLED, JobStatus.QUEUED}),
    JobStatus.RESERVED: frozenset({JobStatus.STARTING, JobStatus.CANCELLED, JobStatus.QUEUED}),
    JobStatus.STARTING: frozenset(
        {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED}
    ),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.RETRYING,
            JobStatus.PAUSED,
            JobStatus.CANCELLED,
            JobStatus.QUEUED,
        }
    ),
    JobStatus.PAUSED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.RETRYING: frozenset({JobStatus.QUEUED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.COMPLETED: frozenset(),
    # FAILED is terminal for automatic retries, but the API may re-open it:
    # contract section "API surface" requires POST /jobs/{id}/retry to perform
    # FAILED->QUEUED with a fresh attempt. Nothing else may leave FAILED.
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.CANCELLED: frozenset(),
}

#: Owner per transition. Keys are ``(from, to)``.
#:
#: Scheduler rows are the reservation/start path plus the recovery paths it
#: detects itself (contract section 3.2). Orchestrator rows are the business
#: lifecycle. Cancellation is executed by the Orchestrator; the Scheduler only
#: performs the GPU side (Tx 2), which is not a Job transition at all.
TRANSITION_OWNERS: dict[tuple[JobStatus, JobStatus], frozenset[JobOwner]] = {
    (JobStatus.CREATED, JobStatus.QUEUED): frozenset({JobOwner.API}),
    # scheduler reservation
    (JobStatus.QUEUED, JobStatus.RESERVED): frozenset({JobOwner.SCHEDULER}),
    # scheduler start (worker claim request is the trigger)
    (JobStatus.RESERVED, JobStatus.STARTING): frozenset({JobOwner.SCHEDULER}),
    # orchestrator business lifecycle
    (JobStatus.STARTING, JobStatus.RUNNING): frozenset({JobOwner.ORCHESTRATOR}),
    # A failure/retry may be reported by the Orchestrator (worker reported it) or
    # detected by the Scheduler itself (expired lease, lost worker, dead GPU) —
    # hence the merged owner sets below.
    (JobStatus.STARTING, JobStatus.FAILED): frozenset({JobOwner.ORCHESTRATOR, JobOwner.SCHEDULER}),
    (JobStatus.RUNNING, JobStatus.FAILED): frozenset({JobOwner.ORCHESTRATOR, JobOwner.SCHEDULER}),
    (JobStatus.RUNNING, JobStatus.RETRYING): frozenset({JobOwner.ORCHESTRATOR, JobOwner.SCHEDULER}),
    (JobStatus.RUNNING, JobStatus.COMPLETED): frozenset({JobOwner.ORCHESTRATOR}),
    (JobStatus.RUNNING, JobStatus.PAUSED): frozenset({JobOwner.ORCHESTRATOR}),
    (JobStatus.QUEUED, JobStatus.CANCELLED): frozenset({JobOwner.ORCHESTRATOR}),
    (JobStatus.STARTING, JobStatus.CANCELLED): frozenset({JobOwner.ORCHESTRATOR}),
    (JobStatus.RUNNING, JobStatus.CANCELLED): frozenset({JobOwner.ORCHESTRATOR}),
    (JobStatus.RESERVED, JobStatus.CANCELLED): frozenset({JobOwner.ORCHESTRATOR}),
    (JobStatus.PAUSED, JobStatus.CANCELLED): frozenset({JobOwner.ORCHESTRATOR}),
    (JobStatus.RETRYING, JobStatus.CANCELLED): frozenset({JobOwner.ORCHESTRATOR}),
    (JobStatus.PAUSED, JobStatus.QUEUED): frozenset({JobOwner.ORCHESTRATOR}),
    # orchestrator retry scheduling
    (JobStatus.RETRYING, JobStatus.QUEUED): frozenset({JobOwner.ORCHESTRATOR}),
    (JobStatus.RETRYING, JobStatus.FAILED): frozenset({JobOwner.ORCHESTRATOR}),
    # manual re-queue by a user (POST /jobs/{id}/retry); the Orchestrator
    # executes the retry budget reset, the API is the only permitted actor
    (JobStatus.FAILED, JobStatus.QUEUED): frozenset({JobOwner.API}),
    # scheduler-detected recovery paths
    (JobStatus.RESERVED, JobStatus.QUEUED): frozenset({JobOwner.SCHEDULER}),
    (JobStatus.STARTING, JobStatus.QUEUED): frozenset({JobOwner.SCHEDULER}),
    (JobStatus.RUNNING, JobStatus.QUEUED): frozenset({JobOwner.SCHEDULER}),
}

#: Self-transitions that are legal and idempotent (visible_at pushes, retries).
SELF_TRANSITIONS: frozenset[JobStatus] = frozenset({JobStatus.QUEUED})


class InvalidTransitionError(Exception):
    """Raised when a Job state change is not permitted."""

    def __init__(
        self,
        current: JobStatus,
        target: JobStatus,
        *,
        owner: JobOwner | None = None,
        reason: str | None = None,
    ) -> None:
        self.current = current
        self.target = target
        self.owner = owner
        self.reason = reason
        detail = f"{reason}: " if reason else ""
        if owner is not None:
            detail += f"owner={owner.value} "
        super().__init__(
            f"illegal job transition {current.value} -> {target.value} ({detail}not allowed)"
        )


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of a validated transition, including timestamp side effects."""

    previous: JobStatus
    current: JobStatus
    started_at_set: bool = False
    finished_at_set: bool = False
    extra_fields: dict[str, object] = field(default_factory=dict)

    def as_updates(self) -> dict[str, object]:
        """Columns the caller must write for this transition."""
        updates: dict[str, object] = {"status": self.current.value}
        updates.update(self.extra_fields)
        if self.started_at_set:
            updates["started_at"] = "NOW"
        if self.finished_at_set:
            updates["finished_at"] = "NOW"
        return updates


def is_terminal(status: JobStatus) -> bool:
    return status in TERMINAL_JOB_STATUSES


def can_transition(current: JobStatus, target: JobStatus, owner: JobOwner) -> bool:
    """Whether ``owner`` may move a job from ``current`` to ``target``."""
    if target is current:
        return target in SELF_TRANSITIONS and owner in {JobOwner.API, JobOwner.ORCHESTRATOR}
    if target not in LEGAL_TRANSITIONS.get(current, frozenset()):
        return False
    return owner in TRANSITION_OWNERS.get((current, target), frozenset())


class JobStateMachine:
    """Validates and describes Job transitions. Stateless and side-effect free."""

    @staticmethod
    def validate(current: JobStatus, target: JobStatus, owner: JobOwner) -> None:
        """Raise :class:`InvalidTransitionError` when the change is not permitted."""
        if not can_transition(current, target, owner):
            reason = (
                "terminal state"
                if is_terminal(current)
                else (
                    "owner not permitted"
                    if target in LEGAL_TRANSITIONS.get(current, frozenset())
                    else "transition not in the legal graph"
                )
            )
            raise InvalidTransitionError(current, target, owner=owner, reason=reason)

    @staticmethod
    def plan(current: JobStatus, target: JobStatus, owner: JobOwner) -> TransitionResult:
        """Validate then describe the transition, including timestamp effects."""
        JobStateMachine.validate(current, target, owner)
        started = target is JobStatus.STARTING and current is JobStatus.RESERVED
        finished = target in TERMINAL_JOB_STATUSES
        return TransitionResult(
            previous=current, current=target, started_at_set=started, finished_at_set=finished
        )

    @staticmethod
    def legal_targets(current: JobStatus, owner: JobOwner) -> frozenset[JobStatus]:
        """Every state ``owner`` may legally move this job to."""
        return frozenset(
            target
            for target in LEGAL_TRANSITIONS.get(current, frozenset())
            if owner in TRANSITION_OWNERS.get((current, target), frozenset())
        )

    @staticmethod
    def owner_of(current: JobStatus, target: JobStatus) -> frozenset[JobOwner]:
        """Documented owner(s) of a transition — used by tests and docs."""
        if target is current:
            return frozenset({JobOwner.API, JobOwner.ORCHESTRATOR})
        return TRANSITION_OWNERS.get((current, target), frozenset())

"""partial unique indexes enforcing exactly-once GPU release and single live claim

Revision ID: 7c1d4a9be002
Revises: bfdfa1e17506
Create Date: 2026-10-05

These indexes are the database-level enforcement of the ownership contract
(implementation-contract section 5.1 / 14.6):

* ``uq_gpu_reservations_live_device`` — a GPU can hold at most one *live*
  reservation, so a double release can never free a GPU that has already been
  re-reserved for another job.
* ``uq_job_claims_active_job`` — a job can hold at most one *active* claim
  (lease), which is what makes the visibility-timeout pattern correct even when
  two workers race.
* ``uq_job_attempts_open`` — a job can have at most one attempt in a non-terminal
  state, so a duplicate claim cannot open a second concurrent attempt.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7c1d4a9be002"
down_revision: str | None = "bfdfa1e17506"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPEN_ATTEMPT_STATES = ("RUNNING",)
LIVE_RESERVATION_STATES = ("RESERVED", "BUSY", "RELEASE_PENDING")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"WHERE {column} IN ({rendered})"


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_gpu_reservations_live_device "
        "ON gpu_reservations (gpu_device_id) " + _in_clause("state", LIVE_RESERVATION_STATES)
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_job_claims_active_job "
        "ON job_claims (job_id) WHERE released_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_job_attempts_open "
        "ON job_attempts (job_id) " + _in_clause("status", OPEN_ATTEMPT_STATES)
    )
    # GPU release target guard: the physical device row can only be freed while it
    # still points at the reservation performing the release.
    op.execute("CREATE INDEX ix_gpu_devices_reservation_id ON gpu_devices (reservation_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_gpu_devices_reservation_id")
    op.execute("DROP INDEX IF EXISTS uq_job_attempts_open")
    op.execute("DROP INDEX IF EXISTS uq_job_claims_active_job")
    op.execute("DROP INDEX IF EXISTS uq_gpu_reservations_live_device")

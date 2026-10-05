"""persist the backend device handle used for live telemetry

Revision ID: a3f81c6d92e4
Revises: 7c1d4a9be002
Create Date: 2026-10-05

Why this exists
---------------
Discovery persists GPUs by their *hardware* UUID, but each GPU backend keys its
own telemetry/probe API on an opaque **backend device id** that is backend
specific and not derivable from the UUID:

* ``MockGpuBackend`` -> ``mock-node-<n>-gpu-<m>``
* ``NvmlGpuBackend`` -> the CUDA device index, as a string

Without a persisted handle, the Scheduler cannot refresh live metrics for a busy
device without guessing from the device name or index, which breaks the
"data-driven, never name-based" rule in the hardware constraints. Persisting the
handle at discovery time keeps telemetry lookups exact.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f81c6d92e4"
down_revision: str | None = "7c1d4a9be002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gpu_devices",
        sa.Column("backend_device_id", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_gpu_devices_node_backend_device",
        "gpu_devices",
        ["node_id", "backend_device_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_gpu_devices_node_backend_device", "gpu_devices", type_="unique"
    )
    op.drop_column("gpu_devices", "backend_device_id")

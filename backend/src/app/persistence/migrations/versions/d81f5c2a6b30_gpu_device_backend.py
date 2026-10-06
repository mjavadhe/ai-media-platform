"""Record which GPU backend reported each device.

The claim payload tells the worker which engine owns a device, and placement
shows it in the UI. Without this column the only way to learn it was to infer it
from the node metadata, which breaks as soon as a node is served by more than
one backend.

Revision ID: d81f5c2a6b30
Revises: c6d41a0b7e92
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d81f5c2a6b30"
down_revision = "c6d41a0b7e92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gpu_devices",
        sa.Column("backend", sa.String(length=32), server_default="unknown", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("gpu_devices", "backend")

"""Align the GPU tables with the repository/discovery contract.

Discovery and the GPU repository had been written against a node/device/capability
shape the schema never had: nodes had no stable ``node_key`` (so a display-name
change would fork a node in two, and worker ``node_key`` registration could not be
matched), devices had no ``error_message``/``last_seen_at``, and capabilities had
no ``supported``/``message``/``checked_at``.

Revision ID: c6d41a0b7e92
Revises: a3f81c6d92e4
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c6d41a0b7e92"
down_revision = "a3f81c6d92e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- gpu_nodes -------------------------------------------------------
    op.add_column(
        "gpu_nodes",
        sa.Column("node_key", sa.String(length=128), nullable=True),
    )
    op.add_column("gpu_nodes", sa.Column("endpoint", sa.String(length=256), nullable=True))
    op.add_column("gpu_nodes", sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text())))
    op.add_column("gpu_nodes", sa.Column("last_seen_at", sa.DateTime(timezone=True)))

    # Backfill from the existing unique name so no node is lost or duplicated.
    op.execute("UPDATE gpu_nodes SET node_key = name WHERE node_key IS NULL")
    op.alter_column("gpu_nodes", "node_key", nullable=False)
    op.create_index("uq_gpu_nodes_node_key", "gpu_nodes", ["node_key"], unique=True)
    # Node identity is the backend node key now; a display name must stay free to change.
    op.drop_constraint("uq_gpu_nodes_name", "gpu_nodes", type_="unique")
    op.execute(
        "UPDATE gpu_nodes SET metadata = '{}'::jsonb WHERE metadata IS NULL"
    )
    op.alter_column(
        "gpu_nodes", "metadata", nullable=False, server_default=sa.text("'{}'::jsonb")
    )

    # --- gpu_devices -----------------------------------------------------
    op.add_column("gpu_devices", sa.Column("error_message", sa.String(length=512)))
    op.add_column("gpu_devices", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    op.create_index("ix_gpu_devices_last_seen_at", "gpu_devices", ["last_seen_at"])

    # --- gpu_capabilities ------------------------------------------------
    op.add_column(
        "gpu_capabilities",
        sa.Column("supported", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column("gpu_capabilities", sa.Column("message", sa.String(length=512)))
    op.add_column("gpu_capabilities", sa.Column("checked_at", sa.DateTime(timezone=True)))
    op.drop_column("gpu_capabilities", "probed_at")


def downgrade() -> None:
    op.add_column("gpu_capabilities", sa.Column("probed_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE gpu_capabilities SET probed_at = checked_at")
    op.drop_column("gpu_capabilities", "checked_at")
    op.drop_column("gpu_capabilities", "message")
    op.drop_column("gpu_capabilities", "supported")

    op.drop_index("ix_gpu_devices_last_seen_at", table_name="gpu_devices")
    op.drop_column("gpu_devices", "last_seen_at")
    op.drop_column("gpu_devices", "error_message")

    op.alter_column("gpu_nodes", "metadata", nullable=True, server_default=None)
    # Guarded: an interrupted upgrade may have already dropped it.
    op.execute("ALTER TABLE gpu_nodes DROP CONSTRAINT IF EXISTS uq_gpu_nodes_name")
    op.create_unique_constraint("uq_gpu_nodes_name", "gpu_nodes", ["name"])
    op.drop_index("uq_gpu_nodes_node_key", table_name="gpu_nodes")
    op.drop_column("gpu_nodes", "last_seen_at")
    op.drop_column("gpu_nodes", "metadata")
    op.drop_column("gpu_nodes", "endpoint")
    op.drop_column("gpu_nodes", "node_key")

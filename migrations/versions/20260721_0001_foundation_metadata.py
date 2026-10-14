"""Create the minimal Phase 1 metadata baseline.

Revision ID: 20260721_0001
Revises: None
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable TimescaleDB and create only foundation metadata."""

    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.create_table(
        "system_metadata",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name="pk_system_metadata"),
    )


def downgrade() -> None:
    """Remove Phase 1 metadata but keep the shared extension installed."""

    op.drop_table("system_metadata")

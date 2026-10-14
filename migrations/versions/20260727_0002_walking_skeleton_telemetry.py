"""Create the generic Phase 2 telemetry hypertable.

Revision ID: 20260727_0002
Revises: 20260721_0001
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0002"
down_revision: str | None = "20260721_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one reusable time-series path without retention guesses."""

    op.create_table(
        "telemetry_sample",
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("point_id", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=False),
        sa.Column("receive_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(length=32), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint(
            "source_timestamp",
            "id",
            name="pk_telemetry_sample",
        ),
        sa.UniqueConstraint(
            "point_id",
            "source",
            "source_timestamp",
            name="uq_telemetry_sample_identity",
        ),
    )
    op.create_index(
        "ix_telemetry_sample_point_source_time",
        "telemetry_sample",
        ["point_id", "source", sa.text("source_timestamp DESC")],
        unique=False,
    )
    op.execute(
        "SELECT create_hypertable("
        "'telemetry_sample', "
        "by_range('source_timestamp'), "
        "if_not_exists => TRUE"
        ")"
    )


def downgrade() -> None:
    """Remove only the Phase 2 telemetry path."""

    op.drop_table("telemetry_sample")

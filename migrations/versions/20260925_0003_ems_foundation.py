"""EMS foundation tables: config, audit, events, alarms, users, sessions.

Revision ID: 20260925_0003
Revises: 20260727_0002
Create Date: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260925_0003"
down_revision: str | None = "20260727_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ems_user",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("username", name="uq_ems_user_username"),
    )
    op.create_table(
        "device_config",
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("value_text", sa.String(length=500), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.PrimaryKeyConstraint("group_id", "key", name="pk_device_config"),
    )
    op.create_table(
        "config_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("old_value", sa.String(length=200), nullable=True),
        sa.Column("new_value", sa.String(length=200), nullable=False),
        sa.Column("changed_by", sa.String(length=64), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_config_audit_group_time", "config_audit", ["group_id", "changed_at"])
    op.create_table(
        "ems_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_ems_event_time", "ems_event", ["occurred_at"])
    op.create_table(
        "ems_alarm",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("alarm_id", sa.String(length=128), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("alarm_id", name="uq_ems_alarm_alarm_id"),
    )
    op.create_table(
        "ev_session",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("port_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("energy_kwh", sa.Float(), nullable=False, server_default="0"),
        sa.Column("power_kw", sa.Float(), nullable=False, server_default="0"),
        sa.Column("capped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ev_session")
    op.drop_table("ems_alarm")
    op.drop_index("ix_ems_event_time", table_name="ems_event")
    op.drop_table("ems_event")
    op.drop_index("ix_config_audit_group_time", table_name="config_audit")
    op.drop_table("config_audit")
    op.drop_table("device_config")
    op.drop_table("ems_user")

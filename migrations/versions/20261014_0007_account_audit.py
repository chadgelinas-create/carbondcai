"""Append-only account activity log for admin and user sign-in.

Revision ID: 20261014_0007
Revises: 20261008_0006
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0007"
down_revision: str | None = "20261008_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("gate", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=200), nullable=False, server_default=""),
        sa.CheckConstraint(
            "action IN ('created', 'disabled', 'enabled', 'updated', 'password_reset', "
            "'signed_in', 'sign_in_failed')",
            name="ck_account_audit_account_audit_action",
        ),
        sa.CheckConstraint(
            "gate IN ('user', 'admin', 'directory')",
            name="ck_account_audit_account_audit_gate",
        ),
    )
    op.create_index("ix_account_audit_occurred_at", "account_audit", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_account_audit_occurred_at", table_name="account_audit")
    op.drop_table("account_audit")

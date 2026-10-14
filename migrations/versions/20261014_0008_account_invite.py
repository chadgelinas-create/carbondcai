"""Authorization invites and password-change tokens.

Revision ID: 20261014_0008
Revises: 20261014_0007
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0008"
down_revision: str | None = "20261014_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ems_user",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.drop_constraint("ck_account_audit_account_audit_action", "account_audit", type_="check")
    op.create_check_constraint(
        "ck_account_audit_account_audit_action",
        "account_audit",
        "action IN ('created', 'disabled', 'enabled', 'updated', 'password_reset', "
        "'signed_in', 'sign_in_failed', 'invited')",
    )
    op.create_table(
        "account_invite",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash", name="uq_account_invite_token_hash"),
        sa.CheckConstraint(
            "purpose IN ('invite', 'password_change')",
            name="ck_account_invite_account_invite_purpose",
        ),
    )
    op.create_index("ix_account_invite_username", "account_invite", ["username"])


def downgrade() -> None:
    op.drop_index("ix_account_invite_username", table_name="account_invite")
    op.drop_table("account_invite")
    op.drop_constraint("ck_account_audit_account_audit_action", "account_audit", type_="check")
    op.create_check_constraint(
        "ck_account_audit_account_audit_action",
        "account_audit",
        "action IN ('created', 'disabled', 'enabled', 'updated', 'password_reset', "
        "'signed_in', 'sign_in_failed')",
    )
    op.drop_column("ems_user", "must_change_password")

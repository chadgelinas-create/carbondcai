"""Add company, system, email, and name to EMS users."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0006"
down_revision: str | None = "20261008_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ems_user", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column(
        "ems_user",
        sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
    )
    op.add_column(
        "ems_user",
        sa.Column("company", sa.String(length=120), nullable=False, server_default=""),
    )
    op.add_column(
        "ems_user",
        sa.Column("system_name", sa.String(length=120), nullable=False, server_default=""),
    )
    op.create_index("uq_ems_user_email", "ems_user", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_ems_user_email", table_name="ems_user")
    op.drop_column("ems_user", "system_name")
    op.drop_column("ems_user", "company")
    op.drop_column("ems_user", "display_name")
    op.drop_column("ems_user", "email")

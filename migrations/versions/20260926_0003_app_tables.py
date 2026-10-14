"""Add application settings, accounts, sessions, events, alarms, and audit."""

from __future__ import annotations

from alembic import op
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    String,
    Table,
    text,
)

revision = "20260926_0003"
down_revision = "20260727_0002"
branch_labels = None
depends_on = None

tables = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)
app_user = Table(
    "app_user",
    tables,
    Column("username", String(100), primary_key=True),
    Column("password_hash", String(512), nullable=False),
    Column("role", String(16), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("failed_logins", Integer, nullable=False, server_default=text("0")),
    Column("locked_until", DateTime(timezone=True)),
    CheckConstraint("role IN ('viewer', 'operator', 'admin')", name="user_role"),
)
user_session = Table(
    "user_session",
    tables,
    Column("token_hash", String(64), primary_key=True),
    Column("username", String(100), ForeignKey("app_user.username"), nullable=False),
    Column("csrf_token", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
)
app_config = Table(
    "app_config",
    tables,
    Column("key", String(100), primary_key=True),
    Column("value", JSON, nullable=False),
    Column("revision", Integer, nullable=False),
)
audit = Table(
    "audit",
    tables,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("username", String(100), ForeignKey("app_user.username"), nullable=False),
    Column("action", String(100), nullable=False),
    Column("before", JSON),
    Column("after", JSON),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)
event = Table(
    "event",
    tables,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("device_id", String(100), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("message", String(500), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)
alarm = Table(
    "alarm",
    tables,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("device_id", String(100), nullable=False),
    Column("code", String(100), nullable=False),
    Column("message", String(500), nullable=False),
    Column("raised_at", DateTime(timezone=True), nullable=False),
    Column("cleared_at", DateTime(timezone=True)),
)


def upgrade() -> None:
    tables.create_all(op.get_bind())


def downgrade() -> None:
    tables.drop_all(op.get_bind())

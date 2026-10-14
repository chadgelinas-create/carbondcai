"""Database metadata owned exclusively by reviewed Alembic migrations."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    text,
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

system_metadata = Table(
    "system_metadata",
    metadata,
    Column("key", String(100), primary_key=True),
    Column("value", String(500), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)

telemetry_sample = Table(
    "telemetry_sample",
    metadata,
    Column("source_timestamp", DateTime(timezone=True), nullable=False),
    Column("id", BigInteger, Identity(), nullable=False),
    Column("point_id", String(200), nullable=False),
    Column("label", String(200), nullable=False),
    Column("source", String(200), nullable=False),
    Column("value", Float, nullable=False),
    Column("unit", String(50), nullable=False),
    Column("receive_timestamp", DateTime(timezone=True), nullable=False),
    Column("quality", String(32), nullable=False),
    Column("synthetic", Boolean, nullable=False),
    PrimaryKeyConstraint("source_timestamp", "id", name="pk_telemetry_sample"),
    UniqueConstraint(
        "point_id",
        "source",
        "source_timestamp",
        name="uq_telemetry_sample_identity",
    ),
)

Index(
    "ix_telemetry_sample_point_source_time",
    telemetry_sample.c.point_id,
    telemetry_sample.c.source,
    telemetry_sample.c.source_timestamp.desc(),
)

# `create_hypertable` creates this time index. Declaring it here keeps Alembic
# autogeneration from treating the Timescale-managed index as removable drift.
Index(
    "telemetry_sample_source_timestamp_idx",
    telemetry_sample.c.source_timestamp.desc(),
)

app_user = Table(
    "app_user",
    metadata,
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
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("username", String(100), ForeignKey("app_user.username"), nullable=False),
    Column("csrf_token", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
)
app_config = Table(
    "app_config",
    metadata,
    Column("key", String(100), primary_key=True),
    Column("value", JSON, nullable=False),
    Column("revision", Integer, nullable=False),
)
audit = Table(
    "audit",
    metadata,
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
    metadata,
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
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("device_id", String(100), nullable=False),
    Column("code", String(100), nullable=False),
    Column("message", String(500), nullable=False),
    Column("raised_at", DateTime(timezone=True), nullable=False),
    Column("cleared_at", DateTime(timezone=True)),
)

configuration_revision = Table(
    "configuration_revision",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("scope", String(32), nullable=False),
    Column("name", String(120), nullable=False),
    Column("reason", String(1000), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_by", String(100), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint("scope IN ('monitor', 'simulation')", name="revision_scope"),
)
configuration_approval = Table(
    "configuration_approval",
    metadata,
    Column("revision_id", BigInteger, ForeignKey("configuration_revision.id"), primary_key=True),
    Column("approved_by", String(100), nullable=False),
    Column(
        "approved_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)
configuration_head = Table(
    "configuration_head",
    metadata,
    Column("scope", String(32), primary_key=True),
    Column("revision_id", BigInteger, ForeignKey("configuration_revision.id")),
    Column("generation", Integer, nullable=False, server_default=text("0")),
    Column("pending_action", String(36)),
)
configuration_action = Table(
    "configuration_action",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("scope", String(100), nullable=False),
    Column("actor", String(100), nullable=False),
    Column("kind", String(100), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("before", JSON),
    Column("requested", JSON),
    Column("effective", JSON),
    Column("message", String(1000), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint(
        "outcome IN ('pending', 'accepted', 'applied', 'rejected', 'failed', "
        "'timed_out', 'interrupted')",
        name="action_outcome",
    ),
)
Index("ix_configuration_action_created_at", configuration_action.c.created_at)

ems_user = Table(
    "ems_user",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("password_hash", String(200), nullable=False),
    Column("role", String(32), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("email", String(254)),
    Column("display_name", String(120), nullable=False, server_default=""),
    Column("company", String(120), nullable=False, server_default=""),
    Column("system_name", String(120), nullable=False, server_default=""),
    Column("must_change_password", Boolean, nullable=False, server_default=text("false")),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("email", name="uq_ems_user_email"),
)
account_audit = Table(
    "account_audit",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column(
        "occurred_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("actor", String(64), nullable=False),
    Column("action", String(32), nullable=False),
    Column("subject", String(64), nullable=False),
    Column("gate", String(16), nullable=False),
    Column("detail", String(200), nullable=False, server_default=""),
    CheckConstraint(
        "action IN ('created', 'disabled', 'enabled', 'updated', 'password_reset', "
        "'signed_in', 'sign_in_failed', 'invited')",
        name="account_audit_action",
    ),
    CheckConstraint("gate IN ('user', 'admin', 'directory')", name="account_audit_gate"),
)
Index("ix_account_audit_occurred_at", account_audit.c.occurred_at)

account_invite = Table(
    "account_invite",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("username", String(64), nullable=False),
    Column("email", String(254), nullable=False),
    Column("purpose", String(32), nullable=False),
    Column("created_by", String(64), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True)),
    CheckConstraint("purpose IN ('invite', 'password_change')", name="account_invite_purpose"),
)
Index("ix_account_invite_username", account_invite.c.username)

device_config = Table(
    "device_config",
    metadata,
    Column("group_id", String(64), nullable=False),
    Column("key", String(128), nullable=False),
    Column("value", Float, nullable=True),
    Column("value_text", String(500), nullable=True),
    Column("unit", String(50), nullable=False),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("updated_by", String(64), nullable=False),
    PrimaryKeyConstraint("group_id", "key", name="pk_device_config"),
)

config_audit = Table(
    "config_audit",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("group_id", String(64), nullable=False),
    Column("key", String(128), nullable=False),
    Column("old_value", String(200), nullable=True),
    Column("new_value", String(200), nullable=False),
    Column("changed_by", String(64), nullable=False),
    Column(
        "changed_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)

ems_event = Table(
    "ems_event",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("category", String(64), nullable=False),
    Column("message", String(500), nullable=False),
    Column("device_id", String(64), nullable=True),
    Column(
        "occurred_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("synthetic", Boolean, nullable=False),
)

ems_alarm = Table(
    "ems_alarm",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("alarm_id", String(128), nullable=False, unique=True),
    Column("message", String(500), nullable=False),
    Column("severity", String(32), nullable=False),
    Column("device_id", String(64), nullable=True),
    Column("active", Boolean, nullable=False),
    Column(
        "occurred_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("cleared_at", DateTime(timezone=True), nullable=True),
    Column("synthetic", Boolean, nullable=False),
)

ev_session = Table(
    "ev_session",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("port_id", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("energy_kwh", Float, nullable=False),
    Column("power_kw", Float, nullable=False),
    Column("capped", Boolean, nullable=False),
    Column(
        "started_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column("ended_at", DateTime(timezone=True), nullable=True),
)

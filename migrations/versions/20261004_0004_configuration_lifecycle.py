"""Immutable configuration history, approvals and durable action outcomes."""

from alembic import op
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    text,
)

revision = "20261004_0004"
down_revision = "20260926_0003"
branch_labels = None
depends_on = None
metadata = MetaData()
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


def upgrade() -> None:
    metadata.create_all(op.get_bind())
    # Published contents and approval identity cannot be rewritten or deleted.
    op.execute("""CREATE FUNCTION skid2_immutable_configuration() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Configuration history is immutable'; END; $$""")
    for table in ("configuration_revision", "configuration_approval"):
        op.execute(
            f"CREATE TRIGGER immutable_configuration BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION skid2_immutable_configuration()"
        )
    # Import actual settings, with honest provenance; no invented historical approvals.
    op.execute("""INSERT INTO configuration_revision(scope, name, reason, payload, created_by)
        SELECT CASE key WHEN 'monitor' THEN 'monitor' ELSE 'simulation' END,
        'Imported settings', 'Imported existing settings; earlier approval is not recorded',
        value, 'system:import' FROM app_config WHERE key IN ('monitor', 'simulation.settings')""")
    op.execute("""INSERT INTO configuration_head(scope, revision_id, generation)
        SELECT r.scope, r.id, c.revision FROM configuration_revision r JOIN app_config c
        ON c.key = CASE r.scope WHEN 'monitor' THEN 'monitor' ELSE 'simulation.settings' END""")


def downgrade() -> None:
    metadata.drop_all(op.get_bind())
    op.execute("DROP FUNCTION skid2_immutable_configuration()")

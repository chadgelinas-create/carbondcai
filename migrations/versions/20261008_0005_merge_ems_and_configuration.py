"""Join the EMS foundation branch with the configuration lifecycle branch."""

revision = "20261008_0005"
down_revision = ("20260925_0003", "20261004_0004")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Record that both migration branches are applied."""


def downgrade() -> None:
    """Drop the merge point. The two branches stay in place."""

"""WhatsApp/Slack identities and the one-time tokens that link them to website accounts."""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "channel_identity" not in tables:
        op.create_table(
            "channel_identity",
            sa.Column("identity_id", sa.Integer(), primary_key=True),
            sa.Column("platform", sa.String(20), nullable=False),
            sa.Column("platform_user_id", sa.String(64), nullable=False),
            sa.Column("team_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("reply_to", sa.String(64)),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.user_id")),
            sa.Column("session_id", sa.Integer()),  # no FK: guest conversations are swept
            sa.Column("linked_at", sa.DateTime()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("platform", "team_id", "platform_user_id", name="uq_channel_identity"),
        )
        op.create_index("ix_channel_identity_user_id", "channel_identity", ["user_id"])
    if "link_token" not in tables:
        op.create_table(
            "link_token",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("identity_id", sa.Integer(), sa.ForeignKey("channel_identity.identity_id"), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_link_token_identity_id", "link_token", ["identity_id"])


def downgrade():
    op.drop_table("link_token")
    op.drop_table("channel_identity")

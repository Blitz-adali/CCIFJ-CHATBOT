"""Let staff cancel an event without deleting it (its bookings stay)."""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    existing = {row[1] for row in op.get_bind().execute(sa.text("PRAGMA table_info(event)"))}
    if "cancelled" not in existing:
        op.execute("ALTER TABLE event ADD COLUMN cancelled BOOLEAN NOT NULL DEFAULT 0")


def downgrade():
    op.execute("ALTER TABLE event DROP COLUMN cancelled")

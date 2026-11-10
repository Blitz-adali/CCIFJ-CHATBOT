"""Knowledge documents linked by URL (Calameo, web pages, PDFs) and re-synced on a schedule."""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

COLUMNS = (
    ("sync_url", "VARCHAR(500)"),
    ("download_url", "VARCHAR(500)"),
    ("version", "VARCHAR(300)"),
    ("checked_at", "DATETIME"),
    ("changed_at", "DATETIME"),
    ("sync_error", "VARCHAR(500)"),
)


def upgrade():
    existing = {row[1] for row in op.get_bind().execute(sa.text("PRAGMA table_info(knowledge_document)"))}
    for name, definition in COLUMNS:
        if name not in existing:
            op.execute(f"ALTER TABLE knowledge_document ADD COLUMN {name} {definition}")


def downgrade():
    for name, _ in reversed(COLUMNS):
        op.execute(f"ALTER TABLE knowledge_document DROP COLUMN {name}")

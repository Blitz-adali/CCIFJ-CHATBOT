"""Member contact people, secondary sectors, contact language and directory visibility."""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

MEMBER_COLUMNS = (
    ("secondary_sectors", "VARCHAR(255)"),
    ("language", "VARCHAR(30)"),
    ("directory_visible", "BOOLEAN NOT NULL DEFAULT 1"),
)


def upgrade():
    existing = {row[1] for row in op.get_bind().execute(sa.text("PRAGMA table_info(member)"))}
    for name, definition in MEMBER_COLUMNS:
        if name not in existing:
            op.execute(f"ALTER TABLE member ADD COLUMN {name} {definition}")
    op.execute("""CREATE TABLE IF NOT EXISTS member_contact (
	contact_id INTEGER NOT NULL,
	member_id INTEGER NOT NULL,
	name VARCHAR(150) NOT NULL,
	salutation VARCHAR(20),
	job_title VARCHAR(150),
	email VARCHAR(255),
	language VARCHAR(30),
	directory_visible BOOLEAN NOT NULL DEFAULT 1,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (contact_id),
	FOREIGN KEY(member_id) REFERENCES member (member_id)
)""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_member_contact_member_id ON member_contact (member_id)")


def downgrade():
    op.execute("DROP TABLE member_contact")
    for name, _ in MEMBER_COLUMNS:
        op.execute(f"ALTER TABLE member DROP COLUMN {name}")

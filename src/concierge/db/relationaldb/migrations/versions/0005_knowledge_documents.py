"""Track files staff upload as agent knowledge sources."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS knowledge_document (
	doc_id INTEGER NOT NULL,
	title VARCHAR(255) NOT NULL,
	filename VARCHAR(200) NOT NULL,
	mime_type VARCHAR(100) NOT NULL,
	source_url VARCHAR(500),
	sha256 VARCHAR(64) NOT NULL,
	char_count INTEGER NOT NULL DEFAULT 0,
	chunk_count INTEGER NOT NULL DEFAULT 0,
	uploaded_by INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (doc_id),
	UNIQUE (sha256),
	FOREIGN KEY(uploaded_by) REFERENCES user (user_id)
)""")


def downgrade():
    op.execute("DROP TABLE knowledge_document")

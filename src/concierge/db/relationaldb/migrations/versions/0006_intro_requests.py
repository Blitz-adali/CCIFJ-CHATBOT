"""Store members' introduction requests for staff to handle."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS intro_request (
	intro_id INTEGER NOT NULL,
	reference VARCHAR(32) NOT NULL,
	requester_user_id INTEGER NOT NULL,
	requester_member_id INTEGER,
	target_member_id INTEGER NOT NULL,
	message VARCHAR NOT NULL,
	status VARCHAR(10) NOT NULL DEFAULT 'new',
	staff_note VARCHAR,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (intro_id),
	UNIQUE (reference),
	FOREIGN KEY(requester_user_id) REFERENCES user (user_id),
	FOREIGN KEY(requester_member_id) REFERENCES member (member_id),
	FOREIGN KEY(target_member_id) REFERENCES member (member_id)
)""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_intro_request_requester_user_id ON intro_request (requester_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_intro_request_target_member_id ON intro_request (target_member_id)")


def downgrade():
    op.execute("DROP TABLE intro_request")

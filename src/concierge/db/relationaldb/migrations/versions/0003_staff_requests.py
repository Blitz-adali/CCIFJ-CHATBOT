"""Store staff requests (emails to CCIFJ contacts and tickets for the team)."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS staff_request (
	request_id INTEGER NOT NULL, 
	reference VARCHAR(32) NOT NULL, 
	request_type VARCHAR(11) NOT NULL, 
	category VARCHAR(50) NOT NULL, 
	subject VARCHAR(255) NOT NULL, 
	message VARCHAR NOT NULL, 
	sender_name VARCHAR(100) NOT NULL, 
	sender_email VARCHAR(255) NOT NULL, 
	user_id INTEGER, 
	session_id INTEGER, 
	status VARCHAR(9) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (request_id), 
	UNIQUE (reference), 
	FOREIGN KEY(user_id) REFERENCES user (user_id), 
	FOREIGN KEY(session_id) REFERENCES session (session_id)
)""",
    """CREATE INDEX IF NOT EXISTS ix_staff_request_user_id ON staff_request (user_id)""",
    """CREATE INDEX IF NOT EXISTS ix_staff_request_session_id ON staff_request (session_id)""",
    """CREATE TABLE IF NOT EXISTS staff_request_recipient (
	request_id INTEGER NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	contact_id INTEGER, 
	PRIMARY KEY (request_id, email), 
	FOREIGN KEY(request_id) REFERENCES staff_request (request_id), 
	FOREIGN KEY(contact_id) REFERENCES contact_us (contact_id)
)""",
)


def upgrade():
    for statement in SCHEMA:
        op.execute(statement)


def downgrade():
    op.execute("DROP TABLE staff_request_recipient")
    op.execute("DROP TABLE staff_request")

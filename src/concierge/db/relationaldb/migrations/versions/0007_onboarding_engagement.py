"""Onboarding profiles, activity, engagement rules, automation runs and the outreach outbox."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLES = (
    """CREATE TABLE IF NOT EXISTS member_profile (
	user_id INTEGER NOT NULL,
	joined_at DATETIME,
	goals VARCHAR,
	interests VARCHAR,
	preferred_language VARCHAR(2) NOT NULL DEFAULT 'en',
	preferred_channel VARCHAR(10) NOT NULL DEFAULT 'in_app',
	profile_completed_at DATETIME,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (user_id),
	FOREIGN KEY(user_id) REFERENCES user (user_id)
)""",
    """CREATE TABLE IF NOT EXISTS activity_event (
	activity_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	kind VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (activity_id),
	FOREIGN KEY(user_id) REFERENCES user (user_id)
)""",
    "CREATE INDEX IF NOT EXISTS ix_activity_event_user_id ON activity_event (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_activity_event_created_at ON activity_event (created_at)",
    """CREATE TABLE IF NOT EXISTS automation_rule (
	rule_key VARCHAR(30) NOT NULL,
	enabled BOOLEAN NOT NULL,
	requires_approval BOOLEAN NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (rule_key)
)""",
    """CREATE TABLE IF NOT EXISTS automation_run (
	run_id INTEGER NOT NULL,
	as_of DATETIME NOT NULL,
	triggered_by VARCHAR(20) NOT NULL,
	created_count INTEGER NOT NULL,
	duplicate_count INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (run_id)
)""",
    """CREATE TABLE IF NOT EXISTS outreach (
	outreach_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	rule_key VARCHAR(30) NOT NULL,
	dedupe_key VARCHAR(120) NOT NULL,
	channel VARCHAR(10) NOT NULL,
	subject VARCHAR(255) NOT NULL,
	body VARCHAR NOT NULL,
	reason VARCHAR NOT NULL,
	status VARCHAR(7) NOT NULL,
	run_id INTEGER,
	staff_request_id INTEGER,
	created_at DATETIME NOT NULL,
	sent_at DATETIME,
	read_at DATETIME,
	PRIMARY KEY (outreach_id),
	UNIQUE (dedupe_key),
	FOREIGN KEY(user_id) REFERENCES user (user_id),
	FOREIGN KEY(run_id) REFERENCES automation_run (run_id),
	FOREIGN KEY(staff_request_id) REFERENCES staff_request (request_id)
)""",
    "CREATE INDEX IF NOT EXISTS ix_outreach_user_id ON outreach (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_outreach_rule_key ON outreach (rule_key)",
    "CREATE INDEX IF NOT EXISTS ix_outreach_status ON outreach (status)",
)


def upgrade():
    for statement in TABLES:
        op.execute(statement)


def downgrade():
    for table in ("outreach", "automation_run", "automation_rule", "activity_event", "member_profile"):
        op.execute(f"DROP TABLE {table}")

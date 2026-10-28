"""Baseline existing and new databases without replacing stored records."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS contact_us (
	contact_id INTEGER NOT NULL, 
	name VARCHAR(100), 
	role VARCHAR(50), 
	mobile VARCHAR(30), 
	email VARCHAR(255) NOT NULL, 
	category VARCHAR(100) NOT NULL, 
	sub_category VARCHAR(255) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (contact_id)
)""",
    """CREATE TABLE IF NOT EXISTS event (
	event_id INTEGER NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	description VARCHAR NOT NULL, 
	category VARCHAR(50) NOT NULL, 
	location VARCHAR(255), 
	contact VARCHAR(255), 
	start_at DATETIME, 
	end_at DATETIME, 
	price NUMERIC(10, 2), 
	member_price NUMERIC(10, 2), 
	non_member_price NUMERIC(10, 2), 
	currency VARCHAR(3), 
	reg_deadline DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (event_id)
)""",
    """CREATE INDEX IF NOT EXISTS ix_event_category ON event (category)""",
    """CREATE INDEX IF NOT EXISTS ix_event_start_at ON event (start_at)""",
    """CREATE TABLE IF NOT EXISTS member_category (
	category_id INTEGER NOT NULL, 
	category_name VARCHAR(255) NOT NULL, 
	category_description VARCHAR NOT NULL, 
	annual_price NUMERIC(10, 2) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (category_id), 
	UNIQUE (category_name)
)""",
    """CREATE TABLE IF NOT EXISTS space (
	space_id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	description VARCHAR, 
	capacity INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (space_id), 
	UNIQUE (name)
)""",
    """CREATE TABLE IF NOT EXISTS speaker (
	speaker_id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	role VARCHAR(255), 
	biography VARCHAR, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (speaker_id)
)""",
    """CREATE TABLE IF NOT EXISTS subscription (
	user_email VARCHAR(255) NOT NULL, 
	sub_type VARCHAR(17) NOT NULL, 
	PRIMARY KEY (user_email, sub_type)
)""",
    """CREATE INDEX IF NOT EXISTS ix_subscription_sub_type ON subscription (sub_type)""",
    """CREATE INDEX IF NOT EXISTS ix_subscription_user_email ON subscription (user_email)""",
    """CREATE TABLE IF NOT EXISTS user (
	user_id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	mobile VARCHAR(30), 
	address VARCHAR(255), 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (user_id)
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_user_email ON user (email)""",
    """CREATE TABLE IF NOT EXISTS booking (
	booking_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	event_id INTEGER NOT NULL, 
	status VARCHAR(9) NOT NULL, 
	registered_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (booking_id), 
	FOREIGN KEY(user_id) REFERENCES user (user_id), 
	FOREIGN KEY(event_id) REFERENCES event (event_id)
)""",
    """CREATE INDEX IF NOT EXISTS ix_booking_event_id ON booking (event_id)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_booking_confirmed_user_event ON booking (user_id, event_id) WHERE status = 'confirmed'""",
    """CREATE TABLE IF NOT EXISTS event_language (
	event_id INTEGER NOT NULL, 
	language VARCHAR(30) NOT NULL, 
	PRIMARY KEY (event_id, language), 
	FOREIGN KEY(event_id) REFERENCES event (event_id)
)""",
    """CREATE TABLE IF NOT EXISTS member (
	member_id INTEGER NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	category_id INTEGER, 
	continent VARCHAR(50), 
	country VARCHAR(100), 
	region VARCHAR(255), 
	sector VARCHAR(100), 
	manpower INTEGER, 
	description VARCHAR, 
	contact VARCHAR(255), 
	established_at DATE, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (member_id), 
	FOREIGN KEY(category_id) REFERENCES member_category (category_id)
)""",
    """CREATE INDEX IF NOT EXISTS ix_member_category_id ON member (category_id)""",
    """CREATE INDEX IF NOT EXISTS ix_member_continent ON member (continent)""",
    """CREATE INDEX IF NOT EXISTS ix_member_country ON member (country)""",
    """CREATE INDEX IF NOT EXISTS ix_member_region ON member (region)""",
    """CREATE INDEX IF NOT EXISTS ix_member_sector ON member (sector)""",
    """CREATE TABLE IF NOT EXISTS session (
	session_id INTEGER NOT NULL, 
	user_id INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (session_id), 
	FOREIGN KEY(user_id) REFERENCES user (user_id)
)""",
    """CREATE TABLE IF NOT EXISTS space_reservation (
	reservation_id INTEGER NOT NULL, 
	space_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	event_id INTEGER, 
	title VARCHAR(255) NOT NULL, 
	is_public BOOLEAN NOT NULL, 
	expected_attendees INTEGER, 
	notes VARCHAR, 
	start_at DATETIME NOT NULL, 
	end_at DATETIME NOT NULL, 
	status VARCHAR(9) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (reservation_id), 
	FOREIGN KEY(space_id) REFERENCES space (space_id), 
	FOREIGN KEY(user_id) REFERENCES user (user_id), 
	FOREIGN KEY(event_id) REFERENCES event (event_id)
)""",
    """CREATE INDEX IF NOT EXISTS ix_space_reservation_space_id ON space_reservation (space_id)""",
    """CREATE INDEX IF NOT EXISTS ix_space_reservation_start_at ON space_reservation (start_at)""",
    """CREATE INDEX IF NOT EXISTS ix_space_reservation_user_id ON space_reservation (user_id)""",
    """CREATE TABLE IF NOT EXISTS speaks_at (
	speaker_id INTEGER NOT NULL, 
	event_id INTEGER NOT NULL, 
	PRIMARY KEY (speaker_id, event_id), 
	FOREIGN KEY(speaker_id) REFERENCES speaker (speaker_id), 
	FOREIGN KEY(event_id) REFERENCES event (event_id)
)""",
    """CREATE INDEX IF NOT EXISTS ix_speaks_at_event_id ON speaks_at (event_id)""",
    """CREATE TABLE IF NOT EXISTS user_auth (
	user_id INTEGER NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES user (user_id)
)""",
    """CREATE TABLE IF NOT EXISTS belongs (
	user_id INTEGER NOT NULL, 
	member_id INTEGER NOT NULL, 
	PRIMARY KEY (user_id, member_id), 
	FOREIGN KEY(user_id) REFERENCES user (user_id), 
	FOREIGN KEY(member_id) REFERENCES member (member_id)
)""",
    """CREATE INDEX IF NOT EXISTS ix_belongs_member_id ON belongs (member_id)""",
    """CREATE TABLE IF NOT EXISTS message (
	message_id INTEGER NOT NULL, 
	session_id INTEGER NOT NULL, 
	role VARCHAR(9) NOT NULL, 
	content VARCHAR NOT NULL, 
	tool_calls JSON, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (message_id), 
	FOREIGN KEY(session_id) REFERENCES session (session_id)
)""",
    """CREATE INDEX IF NOT EXISTS ix_message_session_id ON message (session_id)""",
    """CREATE TABLE IF NOT EXISTS ticket (
	ticket_id INTEGER NOT NULL, 
	booking_id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	telephone VARCHAR(30), 
	PRIMARY KEY (ticket_id), 
	FOREIGN KEY(booking_id) REFERENCES booking (booking_id)
)""",
)


def upgrade():
    for statement in SCHEMA:
        op.execute(statement)


def downgrade():
    raise RuntimeError("The baseline cannot be downgraded without deleting application data.")

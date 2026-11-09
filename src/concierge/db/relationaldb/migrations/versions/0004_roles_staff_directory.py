"""Add account roles and turn contact_us into the staff directory the agent routes to.

The contacts that were hard-coded in the agent's `get_ccifj_contact` tool are inserted
here, so staff can edit them from the admin dashboard afterwards.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# The agent lists contacts by contact_id, so the sponsorship head comes before the events officer.
# (name, role, phone, email, department, description, topics)
SEED_CONTACTS = (  # listed in the order the old tool showed them
    ("Marie OZAKI", "Managing Director, Head of Events", "03-4500-6531", "m.ozaki@ccifj.or.jp", "Events",
     "Event sponsorship", "become_sponsor"),
    ("Elena SUZUKI", "Events Officer", "03-4500-6606", "e.suzuki@ccifj.or.jp", "Events",
     "Event questions, registration, speakers and sponsorship", "events,become_speaker,become_sponsor"),
    ("Mayu HANDA", "Director, BONJOUR FRANCE", "03-4500-6530", "m.handa@ccifj.or.jp", "BONJOUR FRANCE",
     "Starting point for companies that want to work in Japan", "bonjour_france"),
    ("Tomoyo FUNABASHI", "Membership Manager", "03-4500-6644", "t.funabashi@ccifj.or.jp", "Membership",
     "Joining, dues and member services", "membership"),
    ("Marianne BARGES", "Membership Associate Manager & Communication Coordinator", "03-4500-6522",
     "m.barges@ccifj.or.jp", "Membership", "Member services and corporate news", "membership,corporate_news"),
    ("Nicolas CHAUVET", "Responsable Recrutement (Recruitment Manager)", "03-4500-6521", "n.chauvet@ccifj.or.jp",
     "Recruitment & Training", "Job postings, recruitment and HR support", "recruitment_and_hr"),
    ("Haruka KOBAYASHI", "Recruitment Officer", "03-4500-6529", "h.kobayashi@ccifj.or.jp",
     "Recruitment & Training", "Job postings, recruitment and HR support", "recruitment_and_hr"),
)
# Department mailboxes that the old hard-coded tool already returned for these topics.
DEPARTMENT_TOPICS = {
    "appui.entreprises@ccifj.or.jp": ("japanese_market_support", "Business Support", "Business Support / Appui aux Entreprises",
                                      "Company creation, market entry, domiciliation and related business support"),
    "communication@ccifj.or.jp": ("corporate_news", "Communication", "Communication Department",
                                  "France Japon Eco magazine and communication inquiries"),
}


def upgrade():
    op.execute("ALTER TABLE \"user\" ADD COLUMN role VARCHAR(5) NOT NULL DEFAULT 'user'")
    op.execute("ALTER TABLE contact_us ADD COLUMN topics VARCHAR(255)")
    op.execute("ALTER TABLE contact_us ADD COLUMN languages VARCHAR(50)")
    op.execute("ALTER TABLE contact_us ADD COLUMN is_triage BOOLEAN NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE contact_us ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1")

    bind = op.get_bind()
    exists = sa.text("SELECT contact_id FROM contact_us WHERE lower(email) = lower(:email)")
    now = sa.text("CURRENT_TIMESTAMP")
    for email, (topics, category, role, description) in DEPARTMENT_TOPICS.items():
        if bind.execute(exists, {"email": email}).first():
            bind.execute(sa.text("UPDATE contact_us SET topics = :topics WHERE lower(email) = lower(:email)"),
                         {"topics": topics, "email": email})
        else:
            bind.execute(sa.text(
                "INSERT INTO contact_us (name, role, email, category, sub_category, topics, created_at, updated_at) "
                f"VALUES (NULL, :role, :email, :category, :description, :topics, {now.text}, {now.text})"),
                {"role": role, "email": email, "category": category, "description": description, "topics": topics})
    for name, role, phone, email, category, description, topics in SEED_CONTACTS:
        if bind.execute(exists, {"email": email}).first():
            continue
        bind.execute(sa.text(
            "INSERT INTO contact_us (name, role, mobile, email, category, sub_category, topics, created_at, updated_at) "
            f"VALUES (:name, :role, :phone, :email, :category, :description, :topics, {now.text}, {now.text})"),
            {"name": name, "role": role, "phone": phone, "email": email, "category": category,
             "description": description, "topics": topics})


def downgrade():
    for column in ("is_active", "is_triage", "languages", "topics"):
        op.execute(f"ALTER TABLE contact_us DROP COLUMN {column}")
    op.execute("ALTER TABLE \"user\" DROP COLUMN role")

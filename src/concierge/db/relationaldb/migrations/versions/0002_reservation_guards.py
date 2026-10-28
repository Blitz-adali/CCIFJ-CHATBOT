"""Enforce reservation validity and prevent overlaps across all writers."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite serializes writes. Checking in BEFORE triggers makes the overlap
    # check and write atomic, including updates and reinstating cancellations.
    for action in ("INSERT", "UPDATE"):
        op.execute(f"""
            CREATE TRIGGER reservation_guard_{action.lower()}
            BEFORE {action} ON space_reservation
            BEGIN
                SELECT RAISE(ABORT, 'space_reservation_invalid_attendees')
                WHERE NEW.expected_attendees IS NOT NULL
                  AND NEW.expected_attendees <= 0;
                SELECT RAISE(ABORT, 'space_reservation_invalid_interval')
                WHERE NEW.end_at <= NEW.start_at;
                SELECT RAISE(ABORT, 'space_reservation_overlap')
                WHERE NEW.status = 'confirmed' AND EXISTS (
                    SELECT 1 FROM space_reservation AS existing
                    WHERE existing.space_id = NEW.space_id
                      AND existing.status = 'confirmed'
                      AND existing.reservation_id != NEW.reservation_id
                      AND existing.start_at < NEW.end_at
                      AND existing.end_at > NEW.start_at
                );
            END
        """)


def downgrade():
    for action in ("insert", "update"):
        op.execute(f"DROP TRIGGER reservation_guard_{action}")

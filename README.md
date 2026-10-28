# CCIFJ Concierge

Requires Python 3.12 or newer. Install dependencies with `uv sync`.

## Database schema updates

Run `uv run concierge-db upgrade` before starting the application, including
after pulling schema changes. This initializes new databases and migrates the
existing database without replacing application records. The existing
`create_db_and_tables()` helper also applies migrations.

After changing SQLModel models, generate a migration with
`uv run concierge-db revision --autogenerate -m "describe the change"`.
Review the generated migration, including data conversions, before running
`uv run concierge-db upgrade`. SQLite table alterations use Alembic batch mode.
Custom triggers and data migrations must be maintained explicitly in revisions;
if rebuilding `space_reservation`, recreate its reservation guards afterward.
Use `uv run concierge-db current` to inspect the installed revision.

Back up the database before deploying schema changes. Apply migrations once
before starting server workers. Existing installations must have the original
baseline schema; incompatible older schemas need an explicit upgrade revision.

## Scheduling times

Event dates, registration deadlines, and room schedules are stored as naive
Japan time (UTC+9). Aware datetime inputs are converted to Japan time before
comparison or storage. Existing scheduling dates are interpreted as Japan time;
creation and update audit timestamps remain UTC. Registration closes at the
explicit deadline, or the event end time when no deadline exists. If both are
unknown, no date-based booking cutoff is applied.

Reservation migrations prevent overlapping confirmed reservations at the
database level, including writes from multiple processes. Existing rows are
preserved; migrations do not automatically repair historic overlaps or counts.

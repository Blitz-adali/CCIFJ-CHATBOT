"""Replace every member company with the rows of a member file (CSV or XLSX).

    uv run python scripts/replace_members.py "path/to/members.csv" [--yes]

Without --yes it only previews: it validates the file and prints what would be created,
without touching the database. With --yes it deletes all member companies, the links
between accounts and companies, and introduction requests (they point at companies),
imports the file, then re-links the demo accounts so personalised answers keep working.
The member matching index rebuilds itself on the next search.

The file may use the CCIFJ directory export layout (Full name, Primary sector, Membership
type, Email, one row per company and one per contact person); see concierge.imports.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Windows consoles default to cp1252; file names and replies may hold accents or Japanese.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import delete  # noqa: E402

from concierge.db.relationaldb.models import Belongs, IntroRequest, Member  # noqa: E402
from concierge.db.relationaldb.seed import DEMO_USERS, _link_member  # noqa: E402
from concierge.db.relationaldb.session import AsyncSessionLocal  # noqa: E402
from concierge.db.relationaldb import crud  # noqa: E402
from concierge.imports import read_table, run_import  # noqa: E402


async def replace(path: Path, commit: bool) -> dict:
    rows = read_table(path.read_bytes(), path.name)
    async with AsyncSessionLocal() as session:
        if commit:
            await session.exec(delete(IntroRequest))
            await session.exec(delete(Belongs))
            await session.exec(delete(Member))
            await session.commit()
        report = await run_import(session, "members", rows, commit)
        if commit:
            for _, email, tier in DEMO_USERS:
                user = await crud.get_user_by_email(session, email)
                if user is not None and tier is not None:
                    await _link_member(session, user, tier)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--yes", action="store_true", help="delete the current members and import the file")
    args = parser.parse_args()
    report = asyncio.run(replace(args.file, args.yes))
    counts = report["counts"]
    for row in report["rows"]:
        if row["errors"]:
            print(f"row {row['row']}: {row['label'] or '(no name)'}: {'; '.join(row['errors'])}")
    print(f"{'Imported' if args.yes else 'Would import'} {counts['create']} companies "
          f"({counts.get('folded', 0)} contact people folded into them, {counts['error']} rows with errors).")
    if not args.yes:
        print("Preview only. Re-run with --yes to replace the current members.")


if __name__ == "__main__":
    main()

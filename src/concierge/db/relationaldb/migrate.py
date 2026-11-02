"""Versioned schema management for new and existing SQLite databases."""

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config


def migration_config(connection=None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def upgrade_connection(connection) -> None:
    """Run inside an async engine's run_sync callback."""
    command.upgrade(migration_config(connection), "head")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("upgrade", "downgrade"):
        p = sub.add_parser(action)
        p.add_argument("revision", nargs="?", default="head" if action == "upgrade" else "-1")
    sub.add_parser("current")
    sub.add_parser("seed-demo", help="Create demo member and non-member accounts.")
    p = sub.add_parser("revision")
    p.add_argument("-m", "--message", required=True)
    p.add_argument("--autogenerate", action="store_true")
    args = parser.parse_args()
    if args.action == "seed-demo":
        from .seed import main as seed_main
        seed_main()
        return
    config = migration_config()
    if args.action == "revision":
        command.revision(config, message=args.message, autogenerate=args.autogenerate)
    elif args.action == "current":
        command.current(config)
    else:
        getattr(command, args.action)(config, args.revision)


if __name__ == "__main__":
    main()

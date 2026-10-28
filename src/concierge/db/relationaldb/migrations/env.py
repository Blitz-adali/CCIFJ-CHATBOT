"""Alembic environment, sharing the application's database configuration."""

import asyncio

from alembic import context
from sqlmodel import SQLModel

from concierge.db.relationaldb.session import engine


def run(connection):
    context.configure(
        connection=connection,
        target_metadata=SQLModel.metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async():
    try:
        async with engine.begin() as connection:
            await connection.run_sync(run)
    finally:
        await engine.dispose()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    asyncio.run(run_async())

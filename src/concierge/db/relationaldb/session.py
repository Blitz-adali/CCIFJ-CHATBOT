import asyncio
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from . import models  # noqa: F401  (import so all tables register on SQLModel.metadata)

DB_PATH = Path(__file__).parent / "system.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # set True to see the SQL being run
)

# expire_on_commit=False keeps objects usable after commit (important in async code)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


# SQLite ignores foreign keys unless you turn this on for each connection.
# Async engines wrap a normal one, so the listener goes on engine.sync_engine.
@event.listens_for(engine.sync_engine, "connect")
def _enable_foreign_keys(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def create_db_and_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: `session: AsyncSession = Depends(get_session)`."""
    async with AsyncSessionLocal() as session:
        yield session


if __name__ == "__main__":
    asyncio.run(create_db_and_tables())
    print(f"Created tables in {DB_PATH}")

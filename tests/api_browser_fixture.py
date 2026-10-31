"""Disposable API for browser smoke tests; no production DB or model calls."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from concierge.api import auth, chat, health, sessions
from concierge.api.agent_backend import response_events, response_locale
from concierge.db.relationaldb.session import get_session

workspace = TemporaryDirectory(prefix="ccifj-browser-db-")
engine = create_async_engine(f"sqlite+aiosqlite:///{Path(workspace.name) / 'browser.db'}")


@asynccontextmanager
async def lifespan(app):
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield
    await engine.dispose()
    workspace.cleanup()


async def database():
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


def approval(name):
    return {"interrupt_id": name, "action_requests": [{"name": "subscribe_newsletter", "args": {"list": name}, "description": "Review newsletter subscription"}]}


class BrowserAgent:
    async def chat(self, history, user_id, thread_id):
        message = history[-1].content
        if message == "subscribe":
            return {"reply": "Review these subscriptions", "pending_approvals": [approval("first"), approval("second")]}
        if message == "cancel":
            await asyncio.sleep(60)
        if message == "slow":
            queue = response_events.get()
            if queue:
                queue.put_nowait({"type": "delta", "text": "Partial answer"})
            await asyncio.sleep(2)
        answer = "Réponse française" if response_locale.get() == "fr" else f"Answer: {message}"
        return {"reply": answer, "suggested_questions": ["What is CCIFJ?"], "sources": [{"title": "Reference", "url": "https://example.com/"}], "actions": [{"type": "external_link", "label": "Reference", "url": "https://example.com/"}]}

    async def resume(self, thread_id, interrupt_id, decision):
        return {"reply": "Decision recorded", "pending_approvals": [approval("second")] if interrupt_id == "first" else []}


backend = BrowserAgent()
app = FastAPI(lifespan=lifespan)
for router in (auth.router, chat.router, health.router, sessions.router):
    app.include_router(router, prefix="/api/v1")
app.dependency_overrides[get_session] = database
app.dependency_overrides[chat.agent_backend] = lambda: backend

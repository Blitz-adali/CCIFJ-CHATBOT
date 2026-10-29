"""API settings loaded from the environment or a local .env file.

Set CCIFJ_AUTH_SECRET to a private, randomly generated value of at least 32
bytes. Run: uv run uvicorn concierge.main:app --app-dir src --reload
"""

from dataclasses import dataclass
from functools import lru_cache
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    auth_secret: str
    access_token_seconds: int = 3600
    session_token_seconds: int = 30 * 24 * 3600
    agent_timeout_seconds: int = 180
    cors_origins: tuple[str, ...] = ()


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    secret = os.getenv("CCIFJ_AUTH_SECRET", "")
    if len(secret.encode()) < 32 or secret == "paste-the-generated-value-here":
        raise RuntimeError("Set CCIFJ_AUTH_SECRET to a private random value of at least 32 bytes.")
    return Settings(
        auth_secret=secret,
        cors_origins=tuple(
            origin.strip() for origin in os.getenv("CCIFJ_CORS_ORIGINS", "").split(",")
            if origin.strip()
        ),
    )

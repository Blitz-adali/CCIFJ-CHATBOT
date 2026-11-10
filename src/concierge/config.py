"""API settings loaded from the environment or a local .env file.

Every variable the application reads is validated here, and all problems are
reported together so a misconfigured .env can be fixed in one pass.

Required:
    CCIFJ_AUTH_SECRET          Private random value of at least 32 bytes.
    model_type                 "qwen" for local Ollama models; anything else uses OpenAI.
    model_name                 Main chat model name.
    router_model_name          Model used for routing.
    OPENAI_API_KEY             Only when model_type is not "qwen".

Optional:
    COHERE_API_KEY                Enables reranking; hybrid retrieval works without it.
    CCIFJ_STAFF_USER_IDS           Existing account IDs allowed to view the staff inbox.
    trim_messages_max_token        Positive integer, default 1000.
    CCIFJ_AGENT_TIMEOUT_SECONDS    Positive integer, default 180.
    CCIFJ_CORS_ORIGINS             Comma-separated http(s) origins.
    LANGSMITH_TRACING              true/false; when true, LANGSMITH_API_KEY is expected.
    CCIFJ_WEB_SEARCH               on/off, default on: OpenAI web search for the events and
                                   general agents (ignored for Ollama models).
    CCIFJ_WEB_SEARCH_DOMAINS       Comma-separated allowed domains (e.g. ccifj.or.jp); default "*" = whole web.
    CCIFJ_PUBLIC_URL               Public address of the website, used in account-linking links sent
                                   to WhatsApp/Slack. Defaults to the first CCIFJ_CORS_ORIGINS entry.
    CCIFJ_LINK_TOKEN_SECONDS       Lifetime of an account-linking link, default 600 (maximum 600).
    WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN
                                   WhatsApp Cloud API; set all four to enable /webhooks/whatsapp.
    SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET
                                   Slack app; set both to enable /webhooks/slack.

Run: uv run uvicorn concierge.main:app --app-dir src --reload
"""

from dataclasses import dataclass
from functools import lru_cache
import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_PLACEHOLDERS = {"", "paste-the-generated-value-here", "meow?", "changeme", "your-key-here"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(RuntimeError):
    """Raised when one or more required settings are missing or invalid."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        details = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"Invalid configuration ({len(problems)} problem(s)). Fix your .env:\n{details}")


@dataclass(frozen=True)
class Settings:
    auth_secret: str
    model_type: str
    model_name: str
    router_model_name: str
    cohere_api_key: str
    trim_messages_max_token: int = 1000
    access_token_seconds: int = 3600
    session_token_seconds: int = 30 * 24 * 3600
    agent_timeout_seconds: int = 180
    cors_origins: tuple[str, ...] = ()
    langsmith_tracing: bool = False
    staff_user_ids: tuple[int, ...] = ()
    public_url: str = ""
    link_token_seconds: int = 600
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    slack_bot_token: str = ""
    slack_signing_secret: str = ""

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_access_token)

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_bot_token)

    @property
    def uses_ollama(self) -> bool:
        return self.model_type.lower() == "qwen"


def _get(name: str) -> str:
    return os.getenv(name, "").strip()


def _is_placeholder(value: str) -> bool:
    return value.lower() in _PLACEHOLDERS or set(value) == {"*"}


def _require(name: str, problems: list[str], hint: str = "") -> str:
    value = _get(name)
    if _is_placeholder(value):
        problems.append(f"{name} is missing or still a placeholder.{' ' + hint if hint else ''}")
    return value


def _positive_int(name: str, default: int, problems: list[str]) -> int:
    raw = _get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        problems.append(f"{name} must be a whole number, got {raw!r}.")
        return default
    if value <= 0:
        problems.append(f"{name} must be greater than 0, got {value}.")
        return default
    return value


def _bool(name: str, problems: list[str]) -> bool:
    raw = _get(name).lower()
    if raw in _TRUE:
        return True
    if raw not in _FALSE:
        problems.append(f"{name} must be true or false, got {raw!r}.")
    return False


def _load_settings() -> Settings:
    problems: list[str] = []

    secret = _require(
        "CCIFJ_AUTH_SECRET", problems,
        'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"',
    )
    if secret and not _is_placeholder(secret) and len(secret.encode()) < 32:
        problems.append("CCIFJ_AUTH_SECRET must be at least 32 bytes long.")

    model_type = _require("model_type", problems, 'Use "qwen" for Ollama, or an OpenAI model family.')
    model_name = _require("model_name", problems)
    router_model_name = _require("router_model_name", problems)
    if model_type and model_type.lower() != "qwen":
        _require("OPENAI_API_KEY", problems, f"Required because model_type={model_type!r} uses OpenAI.")

    # Hybrid retrieval remains available when reranking is not configured.
    cohere_api_key = _get("COHERE_API_KEY")

    staff_ids = []
    for raw_id in _get("CCIFJ_STAFF_USER_IDS").split(","):
        if not raw_id.strip():
            continue
        try:
            staff_id = int(raw_id.strip())
            if staff_id <= 0:
                raise ValueError
            staff_ids.append(staff_id)
        except ValueError:
            problems.append("CCIFJ_STAFF_USER_IDS must contain positive account IDs separated by commas.")

    trim_tokens = _positive_int("trim_messages_max_token", 1000, problems)
    timeout = _positive_int("CCIFJ_AGENT_TIMEOUT_SECONDS", 180, problems)

    cors_origins = tuple(o.strip().rstrip("/") for o in _get("CCIFJ_CORS_ORIGINS").split(",") if o.strip())
    for origin in cors_origins:
        if origin == "*":
            problems.append("CCIFJ_CORS_ORIGINS must list explicit origins, not '*'.")
        elif not origin.startswith(("http://", "https://")):
            problems.append(f"CCIFJ_CORS_ORIGINS entry {origin!r} must start with http:// or https://.")

    tracing = _bool("LANGSMITH_TRACING", problems)
    if tracing and _is_placeholder(_get("LANGSMITH_API_KEY")):
        problems.append("LANGSMITH_API_KEY is missing while LANGSMITH_TRACING=true (set it, or disable tracing).")

    public_url = _get("CCIFJ_PUBLIC_URL").rstrip("/") or (cors_origins[0] if cors_origins else "")
    if public_url and not public_url.startswith(("http://", "https://")):
        problems.append("CCIFJ_PUBLIC_URL must start with http:// or https://.")
    link_seconds = _positive_int("CCIFJ_LINK_TOKEN_SECONDS", 600, problems)
    if link_seconds > 600:
        problems.append("CCIFJ_LINK_TOKEN_SECONDS must be 600 or less: linking links are short-lived.")

    # A channel is either fully configured or off; a half-set one would accept unsigned webhooks.
    whatsapp = {name: _get(name) for name in (
        "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN")}
    slack = {name: _get(name) for name in ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET")}
    for label, values in (("WhatsApp", whatsapp), ("Slack", slack)):
        missing = [name for name, value in values.items() if not value]
        if missing and len(missing) < len(values):
            problems.append(f"{label} is partly configured; also set {', '.join(missing)} (or clear the others).")
        elif not missing and not public_url:
            problems.append(f"Set CCIFJ_PUBLIC_URL so {label} users can be sent an account-linking link.")

    if problems:
        raise ConfigError(problems)

    return Settings(
        auth_secret=secret,
        model_type=model_type,
        model_name=model_name,
        router_model_name=router_model_name,
        cohere_api_key=cohere_api_key,
        trim_messages_max_token=trim_tokens,
        agent_timeout_seconds=timeout,
        cors_origins=cors_origins,
        langsmith_tracing=tracing,
        staff_user_ids=tuple(dict.fromkeys(staff_ids)),
        public_url=public_url,
        link_token_seconds=link_seconds,
        whatsapp_access_token=whatsapp["WHATSAPP_ACCESS_TOKEN"],
        whatsapp_phone_number_id=whatsapp["WHATSAPP_PHONE_NUMBER_ID"],
        whatsapp_app_secret=whatsapp["WHATSAPP_APP_SECRET"],
        whatsapp_verify_token=whatsapp["WHATSAPP_VERIFY_TOKEN"],
        slack_bot_token=slack["SLACK_BOT_TOKEN"],
        slack_signing_secret=slack["SLACK_SIGNING_SECRET"],
    )


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    settings = _load_settings()
    logger.info(
        "Configuration loaded: model_type=%s model=%s router=%s timeout=%ss cors=%s tracing=%s",
        settings.model_type, settings.model_name, settings.router_model_name,
        settings.agent_timeout_seconds, list(settings.cors_origins) or "none", settings.langsmith_tracing,
    )
    return settings


if __name__ == "__main__":
    # Quick check: uv run python -m concierge.config
    try:
        get_settings()
    except ConfigError as error:
        raise SystemExit(str(error))
    print("Configuration OK.")

"""Public JSON request and response shapes. Session IDs are opaque strings."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionResponse(BaseModel):
    session_id: str


class AttachmentUpload(RequestModel):
    """A PDF/PNG/JPEG file sent inline; text is extracted server-side (OCR)."""
    name: str = Field(default="attachment", max_length=200)
    mime_type: str = Field(max_length=100)
    # 5 MB of binary data is at most ~7 MB of base64 text.
    data_base64: str = Field(min_length=1, max_length=7_000_000)


class ChatRequest(RequestModel):
    request_id: str | None = Field(default=None, max_length=100)
    locale: Literal["en", "fr", "ja"] = "en"
    session_id: str | None = Field(default=None, max_length=4096)
    message: str = Field(min_length=1, max_length=16000)
    # Guided conversation paths (see data/conversation_paths.json).
    path_step_id: str | None = Field(default=None, max_length=80)
    path_event_id: str | None = Field(default=None, max_length=40)
    path_context: dict[str, Any] = Field(default_factory=dict)
    attachments: list[AttachmentUpload] = Field(default_factory=list, max_length=4)

    @field_validator("message")
    @classmethod
    def nonempty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank.")
        return value


class ChatResponse(SessionResponse):
    request_id: str | None = None
    mode: str = "agent"
    reply: str
    intent: dict[str, Any] | None = None
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    path_actions: list[dict[str, Any]] = Field(default_factory=list)
    # Agent-proposed buttons: {"id", "label", "prompt", "event_id"?}; sent back as plain messages.
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    active_path_step: str | None = None
    path_context: dict[str, Any] = Field(default_factory=dict)
    attachment_count: int = 0


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    response: ChatResponse | None = None
    message_id: int
    role: Literal["user", "assistant", "tool"]
    content: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        # Audit timestamps in SQLite are naive UTC; make that explicit in JSON.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class HistoryResponse(SessionResponse):
    last_request_id: str | None = None
    turn_status: Literal["ready", "processing", "unknown"] = "ready"
    messages: list[MessageResponse]
    next_after_id: int | None = None
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)


class EmailRequest(RequestModel):
    email: str = Field(min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if value.count("@") != 1 or any(ch.isspace() for ch in value):
            raise ValueError("Invalid email address.")
        local, domain = value.split("@")
        if not local or not domain:
            raise ValueError("Invalid email address.")
        return value


class RegisterRequest(EmailRequest):
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank.")
        return value


class LoginRequest(EmailRequest):
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: int
    name: str
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserResponse


class ReconcileRequest(RequestModel):
    checked_outcome: Literal[True]


class ApprovalRequest(RequestModel):
    request_id: str | None = Field(default=None, max_length=100)
    locale: Literal["en", "fr", "ja"] = "en"
    interrupt_id: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "reject"]

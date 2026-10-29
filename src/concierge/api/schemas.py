"""Public JSON request and response shapes. Session IDs are opaque strings."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionResponse(BaseModel):
    session_id: str


class ChatRequest(RequestModel):
    session_id: str | None = Field(default=None, max_length=4096)
    message: str = Field(min_length=1, max_length=16000)

    @field_validator("message")
    @classmethod
    def nonempty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank.")
        return value


class ChatResponse(SessionResponse):
    reply: str
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
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


class ApprovalRequest(RequestModel):
    interrupt_id: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "reject"]

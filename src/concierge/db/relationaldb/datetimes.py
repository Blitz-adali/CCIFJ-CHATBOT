"""Scheduling uses naive Japan time; audit timestamps remain naive UTC."""

from datetime import datetime, timedelta, timezone

JAPAN_TIME = timezone(timedelta(hours=9), name="JST")


def japan_now() -> datetime:
    return datetime.now(JAPAN_TIME).replace(tzinfo=None)


def japan_naive(value: datetime) -> datetime:
    """Interpret naive schedules as JST and convert aware values to JST."""
    if value.tzinfo is not None:
        value = value.astimezone(JAPAN_TIME)
    return value.replace(tzinfo=None)

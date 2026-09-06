"""Single UTC clock contract for domain and persistence timestamps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class FixedClock:
    current: datetime

    def __post_init__(self) -> None:
        self.current = ensure_utc(self.current)

    def now(self) -> datetime:
        return self.current


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock values must be timezone-aware")
    return value.astimezone(timezone.utc)


SYSTEM_CLOCK: Clock = SystemClock()


def utc_now() -> datetime:
    """SQLAlchemy default callable backed by the canonical system clock."""
    return SYSTEM_CLOCK.now()

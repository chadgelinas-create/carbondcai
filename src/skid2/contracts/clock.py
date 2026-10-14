"""Injected UTC clock boundaries for real runtime and deterministic tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Protocol

from skid2.contracts.domain import normalize_utc


class Clock(Protocol):
    """The only time source consumed by Phase 2 application behavior."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC instant."""


class SystemClock:
    """Production wall-clock adapter."""

    def now(self) -> datetime:
        """Return the host clock normalized to UTC."""

        return datetime.now(UTC)


@dataclass(slots=True)
class ManualClock:
    """Manually advanced deterministic UTC clock."""

    _current: datetime

    def __post_init__(self) -> None:
        self._current = normalize_utc(self._current, "initial_time")

    def now(self) -> datetime:
        """Return the current deterministic instant."""

        return self._current

    def advance(self, duration: timedelta) -> datetime:
        """Advance by a finite positive duration and return the new instant."""

        seconds = duration.total_seconds()
        if not isfinite(seconds) or seconds <= 0:
            raise ValueError("clock advancement must be a finite positive duration")
        self._current += duration
        return self._current

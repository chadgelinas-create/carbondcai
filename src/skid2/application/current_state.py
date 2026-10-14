"""Current-state ordering and freshness policy for the Phase 2 signal."""

from __future__ import annotations

from datetime import timedelta
from threading import RLock

from skid2.contracts import (
    Clock,
    DataQuality,
    Freshness,
    Heartbeat,
    Measurement,
    SystemSnapshot,
)


class ConflictingDuplicateError(ValueError):
    """Raised when one logical sample identity carries different content."""


class CurrentState:
    """Thread-safe in-memory current state with deterministic injected time."""

    def __init__(self, clock: Clock, stale_after: timedelta) -> None:
        if stale_after.total_seconds() <= 0:
            raise ValueError("stale_after must be positive")
        self._clock = clock
        self._stale_after = stale_after
        self._measurement: Measurement | None = None
        self._heartbeat: Heartbeat | None = None
        self._lock = RLock()

    def accept(self, measurement: Measurement) -> bool:
        """Accept a newer sample, ignore exact/older data, and reject conflicts."""

        with self._lock:
            current = self._measurement
            if current is not None:
                if (
                    measurement.point_id == current.point_id
                    and measurement.source == current.source
                    and measurement.source_timestamp == current.source_timestamp
                ):
                    if measurement == current:
                        return False
                    raise ConflictingDuplicateError(
                        "sample identity already exists with different content"
                    )
                if measurement.source_timestamp < current.source_timestamp:
                    return False
            self._measurement = measurement
            return True

    def record_heartbeat(self, heartbeat: Heartbeat) -> None:
        """Record the latest component heartbeat."""

        with self._lock:
            if self._heartbeat is None or heartbeat.observed_at >= self._heartbeat.observed_at:
                self._heartbeat = heartbeat

    def snapshot(self) -> SystemSnapshot:
        """Return current state with freshness derived at read time."""

        now = self._clock.now()
        with self._lock:
            measurement = self._measurement
            heartbeat = self._heartbeat
        if measurement is None or measurement.quality is not DataQuality.GOOD:
            freshness = Freshness.UNAVAILABLE
        elif now - measurement.receive_timestamp > self._stale_after:
            freshness = Freshness.STALE
        else:
            freshness = Freshness.LIVE
        return SystemSnapshot(
            observed_at=now,
            freshness=freshness,
            measurement=measurement,
            heartbeat=heartbeat,
        )

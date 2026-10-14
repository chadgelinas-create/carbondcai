"""Framework-neutral ports required by the walking-skeleton application."""

from __future__ import annotations

from typing import Protocol

from skid2.contracts import Measurement


class MeasurementStoreError(RuntimeError):
    """Bounded persistence failure safe for application-level handling."""


class MeasurementConflictError(MeasurementStoreError):
    """An existing logical sample identity has different content."""


class MeasurementStore(Protocol):
    """Persistence operations required by the walking-skeleton runtime."""

    def append(self, measurement: Measurement) -> bool:
        """Persist one sample idempotently."""

    def latest(self, point_id: str) -> Measurement | None:
        """Return the newest persisted sample for a point."""

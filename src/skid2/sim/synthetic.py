"""Transport-independent deterministic signal used only for the walking skeleton."""

from __future__ import annotations

from enum import StrEnum

from skid2.contracts import Clock, ComponentStatus, DataQuality, Heartbeat, Measurement

SYNTHETIC_POINT_ID = "synthetic.walking_skeleton.test_signal_001"
SYNTHETIC_LABEL = "Synthetic Walking-Skeleton Test Signal"
SYNTHETIC_SOURCE = "synthetic.phase2.generator"
SYNTHETIC_UNIT = "test_unit"


class SyntheticLifecycle(StrEnum):
    """Explicit source lifecycle."""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class SyntheticSignalSource:
    """Emit a deterministic arithmetic sequence using an injected clock."""

    def __init__(self, clock: Clock, max_samples: int = 0) -> None:
        if max_samples < 0:
            raise ValueError("max_samples must be zero (unlimited) or positive")
        self._clock = clock
        self._max_samples = max_samples
        self._sequence = 0
        self._lifecycle = SyntheticLifecycle.STOPPED

    @property
    def lifecycle(self) -> SyntheticLifecycle:
        """Return current lifecycle state."""

        return self._lifecycle

    def start(self) -> None:
        """Start or resume idempotently."""

        self._lifecycle = SyntheticLifecycle.RUNNING

    def pause(self) -> None:
        """Pause without fabricating replacement samples."""

        if self._lifecycle is not SyntheticLifecycle.STOPPED:
            self._lifecycle = SyntheticLifecycle.PAUSED

    def stop(self) -> None:
        """Stop idempotently."""

        self._lifecycle = SyntheticLifecycle.STOPPED

    def emit_next(self) -> Measurement | None:
        """Emit the next deterministic sample only while running."""

        if self._lifecycle is not SyntheticLifecycle.RUNNING:
            return None
        if self._max_samples and self._sequence >= self._max_samples:
            self._lifecycle = SyntheticLifecycle.PAUSED
            return None

        observed_at = self._clock.now()
        measurement = Measurement(
            point_id=SYNTHETIC_POINT_ID,
            label=SYNTHETIC_LABEL,
            source=SYNTHETIC_SOURCE,
            value=10.0 + (self._sequence * 0.5),
            unit=SYNTHETIC_UNIT,
            source_timestamp=observed_at,
            receive_timestamp=observed_at,
            quality=DataQuality.GOOD,
            synthetic=True,
        )
        self._sequence += 1
        if self._max_samples and self._sequence >= self._max_samples:
            self._lifecycle = SyntheticLifecycle.PAUSED
        return measurement

    def heartbeat(self) -> Heartbeat:
        """Report lifecycle health independently of measurement value."""

        status = {
            SyntheticLifecycle.RUNNING: ComponentStatus.HEALTHY,
            SyntheticLifecycle.PAUSED: ComponentStatus.DEGRADED,
            SyntheticLifecycle.STOPPED: ComponentStatus.STOPPED,
        }[self._lifecycle]
        return Heartbeat(
            component=SYNTHETIC_SOURCE,
            observed_at=self._clock.now(),
            status=status,
            synthetic=True,
        )

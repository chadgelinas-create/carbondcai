"""Deterministic synthetic-source tests."""

from datetime import UTC, datetime, timedelta

import pytest

from skid2.contracts import ComponentStatus, ManualClock
from skid2.sim.synthetic import (
    SYNTHETIC_LABEL,
    SYNTHETIC_POINT_ID,
    SYNTHETIC_SOURCE,
    SyntheticLifecycle,
    SyntheticSignalSource,
)

NOW = datetime(2026, 7, 27, 10, tzinfo=UTC)


def test_source_emits_replayable_values_timestamps_and_identity() -> None:
    """The same clock and transitions reproduce the same synthetic trace."""

    def trace() -> list[dict[str, object]]:
        clock = ManualClock(NOW)
        source = SyntheticSignalSource(clock)
        source.start()
        first = source.emit_next()
        clock.advance(timedelta(seconds=1))
        second = source.emit_next()
        assert first is not None and second is not None
        return [dict(first.to_record()), dict(second.to_record())]

    first_trace = trace()

    assert first_trace == trace()
    assert [item["value"] for item in first_trace] == [10.0, 10.5]
    assert first_trace[0]["point_id"] == SYNTHETIC_POINT_ID
    assert "Synthetic" in SYNTHETIC_LABEL
    assert first_trace[0]["source"] == SYNTHETIC_SOURCE
    assert first_trace[0]["synthetic"] is True


def test_lifecycle_and_heartbeat_do_not_fabricate_samples() -> None:
    """Stopped and paused states are explicit and emit no replacement value."""

    clock = ManualClock(NOW)
    source = SyntheticSignalSource(clock)

    assert source.lifecycle is SyntheticLifecycle.STOPPED
    assert source.emit_next() is None
    assert source.heartbeat().status is ComponentStatus.STOPPED
    source.pause()
    assert source.lifecycle is SyntheticLifecycle.STOPPED

    source.start()
    source.start()
    assert source.heartbeat().status is ComponentStatus.HEALTHY
    source.pause()
    assert source.emit_next() is None
    assert source.heartbeat().status is ComponentStatus.DEGRADED
    source.stop()
    source.stop()
    assert source.lifecycle is SyntheticLifecycle.STOPPED


def test_bounded_source_pauses_after_exact_sample_count_and_can_resume() -> None:
    """A bounded source produces exactly its configured evidence sample count."""

    source = SyntheticSignalSource(ManualClock(NOW), max_samples=1)
    source.start()

    assert source.emit_next() is not None
    assert source.lifecycle is SyntheticLifecycle.PAUSED
    assert source.emit_next() is None
    source.start()
    assert source.emit_next() is None
    assert source.lifecycle is SyntheticLifecycle.PAUSED


def test_source_rejects_negative_sample_limit() -> None:
    """Zero means unlimited; negative limits are invalid."""

    with pytest.raises(ValueError, match="zero"):
        SyntheticSignalSource(ManualClock(NOW), max_samples=-1)

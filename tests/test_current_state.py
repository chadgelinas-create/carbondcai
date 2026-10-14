"""Current-state ordering and freshness tests."""

from datetime import UTC, datetime, timedelta

import pytest

from skid2.application.current_state import ConflictingDuplicateError, CurrentState
from skid2.contracts import (
    ComponentStatus,
    DataQuality,
    Freshness,
    Heartbeat,
    ManualClock,
    Measurement,
)

NOW = datetime(2026, 7, 27, 10, tzinfo=UTC)


def measurement(
    *,
    at: datetime = NOW,
    value: float = 10,
    quality: DataQuality = DataQuality.GOOD,
) -> Measurement:
    """Build one valid current-state input."""

    return Measurement(
        point_id="synthetic.test",
        label="Synthetic Test",
        source="synthetic.source",
        value=value,
        unit="test_unit",
        source_timestamp=at,
        receive_timestamp=at,
        quality=quality,
        synthetic=True,
    )


def test_unavailable_boundary_live_stale_and_recovery() -> None:
    """Freshness is derived at read time with exact boundary semantics."""

    clock = ManualClock(NOW)
    state = CurrentState(clock, timedelta(seconds=3))

    assert state.snapshot().freshness is Freshness.UNAVAILABLE
    assert state.accept(measurement())
    assert state.snapshot().freshness is Freshness.LIVE
    clock.advance(timedelta(seconds=3))
    assert state.snapshot().freshness is Freshness.LIVE
    clock.advance(timedelta(microseconds=1))
    assert state.snapshot().freshness is Freshness.STALE

    recovered_at = clock.now()
    assert state.accept(measurement(at=recovered_at, value=11))
    assert state.snapshot().freshness is Freshness.LIVE


def test_non_good_quality_is_unavailable_without_losing_measurement() -> None:
    """Invalid values remain inspectable but cannot be presented as live."""

    state = CurrentState(ManualClock(NOW), timedelta(seconds=3))
    item = measurement(quality=DataQuality.COMM_ERROR)
    state.accept(item)

    snapshot = state.snapshot()
    assert snapshot.freshness is Freshness.UNAVAILABLE
    assert snapshot.measurement == item


def test_duplicate_conflict_and_out_of_order_policy() -> None:
    """Exact duplicates and older samples are ignored; conflicts are rejected."""

    state = CurrentState(ManualClock(NOW), timedelta(seconds=3))
    current = measurement()

    assert state.accept(current)
    assert not state.accept(current)
    assert not state.accept(measurement(at=NOW - timedelta(seconds=1), value=9))
    with pytest.raises(ConflictingDuplicateError, match="different content"):
        state.accept(measurement(value=99))
    assert state.snapshot().measurement == current


def test_heartbeat_ordering_and_stale_threshold_validation() -> None:
    """Older heartbeats cannot replace newer state and threshold must be positive."""

    clock = ManualClock(NOW)
    state = CurrentState(clock, timedelta(seconds=1))
    newer = Heartbeat("source", NOW, ComponentStatus.HEALTHY, True)
    older = Heartbeat(
        "source",
        NOW - timedelta(seconds=1),
        ComponentStatus.DEGRADED,
        True,
    )

    state.record_heartbeat(newer)
    state.record_heartbeat(older)
    assert state.snapshot().heartbeat == newer

    with pytest.raises(ValueError, match="positive"):
        CurrentState(clock, timedelta(0))

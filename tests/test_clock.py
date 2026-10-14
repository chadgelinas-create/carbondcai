"""Injected clock tests."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from skid2.contracts import ManualClock, SystemClock


def test_system_clock_returns_aware_utc() -> None:
    """The real adapter always returns an aware UTC instant."""

    observed = SystemClock().now()

    assert observed.tzinfo is UTC
    assert observed.utcoffset() == timedelta(0)


def test_manual_clock_normalizes_and_advances_exactly() -> None:
    """The deterministic clock is independent of host timezone and speed."""

    clock = ManualClock(datetime(2026, 7, 27, 11, tzinfo=timezone(timedelta(hours=1))))

    assert clock.now() == datetime(2026, 7, 27, 10, tzinfo=UTC)
    assert clock.advance(timedelta(seconds=2.5)) == datetime(
        2026,
        7,
        27,
        10,
        0,
        2,
        500000,
        tzinfo=UTC,
    )


@pytest.mark.parametrize(
    "duration",
    [timedelta(0), timedelta(seconds=-1)],
)
def test_manual_clock_rejects_invalid_advancement(duration: timedelta) -> None:
    """Backward, zero, and non-finite movement fail explicitly."""

    clock = ManualClock(datetime(2026, 7, 27, tzinfo=UTC))

    with pytest.raises(ValueError, match="finite positive"):
        clock.advance(duration)


def test_manual_clock_rejects_naive_initial_time() -> None:
    """The deterministic initial instant must be timezone-aware."""

    with pytest.raises(ValueError, match="timezone-aware"):
        ManualClock(datetime(2026, 7, 27))  # noqa: DTZ001 - intentionally naive.

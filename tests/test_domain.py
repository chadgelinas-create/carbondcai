"""Framework-neutral domain contract tests."""

from datetime import UTC, datetime, timedelta, timezone
from math import inf, nan

import pytest

from skid2.contracts import (
    Alarm,
    AlarmSeverity,
    Command,
    ComponentStatus,
    DataQuality,
    Event,
    Freshness,
    Heartbeat,
    Measurement,
    SystemSnapshot,
)

NOW = datetime(2026, 7, 27, 10, tzinfo=UTC)


def measurement(**overrides: object) -> Measurement:
    """Build one valid contract and allow focused invariant overrides."""

    values: dict[str, object] = {
        "point_id": " synthetic.test ",
        "label": " Synthetic Test Signal ",
        "source": " synthetic.source ",
        "value": 10,
        "unit": " test_unit ",
        "source_timestamp": NOW,
        "receive_timestamp": NOW,
        "quality": DataQuality.GOOD,
        "synthetic": True,
    }
    values.update(overrides)
    return Measurement(**values)  # type: ignore[arg-type]


def test_measurement_normalizes_values_and_serializes_deterministically() -> None:
    """Contracts trim identity, normalize UTC, and serialize primitive values."""

    offset_time = datetime(2026, 7, 27, 11, tzinfo=timezone(timedelta(hours=1)))
    item = measurement(source_timestamp=offset_time, receive_timestamp=offset_time)

    assert item.point_id == "synthetic.test"
    assert item.label == "Synthetic Test Signal"
    assert item.source == "synthetic.source"
    assert item.unit == "test_unit"
    assert item.value == 10.0
    assert item.source_timestamp == NOW
    assert item.to_record() == {
        "point_id": "synthetic.test",
        "label": "Synthetic Test Signal",
        "source": "synthetic.source",
        "value": 10.0,
        "unit": "test_unit",
        "source_timestamp": NOW.isoformat(),
        "receive_timestamp": NOW.isoformat(),
        "quality": "good",
        "synthetic": True,
    }


@pytest.mark.parametrize("field", ["point_id", "label", "source", "unit"])
def test_measurement_rejects_empty_identity_fields(field: str) -> None:
    """Every identity and engineering-unit field is meaningful."""

    with pytest.raises(ValueError, match=field):
        measurement(**{field: "  "})


@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_measurement_rejects_non_finite_values(value: float) -> None:
    """NaN and infinity cannot cross the domain boundary."""

    with pytest.raises(ValueError, match="finite"):
        measurement(value=value)


@pytest.mark.parametrize("value", [True, "10"])
def test_measurement_rejects_non_numeric_values(value: object) -> None:
    """Boolean and string values are not silently coerced into telemetry."""

    with pytest.raises(TypeError, match="numeric"):
        measurement(value=value)


def test_measurement_rejects_naive_or_reversed_timestamps() -> None:
    """Timestamps must be aware and receipt cannot precede source time."""

    with pytest.raises(ValueError, match="timezone-aware"):
        measurement(
            source_timestamp=datetime(2026, 7, 27, 10)  # noqa: DTZ001 - invalid input.
        )
    with pytest.raises(ValueError, match="must not precede"):
        measurement(receive_timestamp=NOW - timedelta(seconds=1))


def test_supporting_contracts_are_minimal_valid_values() -> None:
    """Heartbeat, command, alarm, event, and snapshot carry useful typed facts."""

    item = measurement()
    heartbeat = Heartbeat(" synthetic.source ", NOW, ComponentStatus.HEALTHY, True)
    command = Command(" command-1 ", " synthetic.target ", " observe ", NOW, True)
    alarm = Alarm(
        " alarm-1 ",
        " Synthetic warning ",
        NOW,
        AlarmSeverity.WARNING,
        True,
        True,
    )
    event = Event(" event-1 ", " synthetic ", " Sample emitted ", NOW, True)
    snapshot = SystemSnapshot(NOW, Freshness.LIVE, item, heartbeat)

    assert heartbeat.component == "synthetic.source"
    assert command.target == "synthetic.target"
    assert alarm.message == "Synthetic warning"
    assert event.category == "synthetic"
    assert snapshot.measurement == item


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (lambda: Heartbeat("", NOW, ComponentStatus.HEALTHY, True), "component"),
        (lambda: Command("", "target", "observe", NOW, True), "command_id"),
        (
            lambda: Alarm(
                "alarm",
                "",
                NOW,
                AlarmSeverity.INFO,
                True,
                True,
            ),
            "message",
        ),
        (lambda: Event("event", "", "message", NOW, True), "category"),
    ],
)
def test_supporting_contracts_reject_empty_required_text(
    factory: object,
    match: str,
) -> None:
    """Supporting contracts reject empty identifiers and meanings."""

    with pytest.raises(ValueError, match=match):
        factory()  # type: ignore[operator]


def test_snapshot_requires_unavailable_when_measurement_is_missing() -> None:
    """Missing data can never masquerade as live or stale."""

    with pytest.raises(ValueError, match="must be unavailable"):
        SystemSnapshot(NOW, Freshness.LIVE, None, None)

    assert SystemSnapshot(NOW, Freshness.UNAVAILABLE, None, None).measurement is None

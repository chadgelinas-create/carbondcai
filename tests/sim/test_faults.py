"""Shared controlled fault-test rule and effect tests."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from skid2.contracts import ComponentStatus, DataQuality
from skid2.sim import (
    FAULT_RULES,
    DeviceHealth,
    DeviceLifecycle,
    DeviceStepResult,
    DeviceValue,
    EvidenceClassification,
    FaultRequest,
    FaultTest,
    FaultTestState,
    FaultType,
    apply_faults,
    fault_state,
    find_rule,
    validate_fault,
)

NOW = datetime(2026, 8, 1, 10, 0, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class State:
    lifecycle: DeviceLifecycle = DeviceLifecycle.ACTIVE


def value(logical_id: str, reading: float, quality: DataQuality = DataQuality.GOOD) -> DeviceValue:
    return DeviceValue(
        logical_id,
        reading,
        "kW",
        quality,
        "st-4",
        EvidenceClassification.PROVISIONAL,
    )


def normal_result(*values: DeviceValue) -> DeviceStepResult[State]:
    return DeviceStepResult(
        State(),
        values,
        DeviceHealth(
            DeviceLifecycle.ACTIVE,
            ComponentStatus.HEALTHY,
            DataQuality.GOOD,
            "st-4",
            EvidenceClassification.PROVISIONAL,
        ),
    )


def fault_test(
    fault_type: FaultType,
    *,
    point_id: str | None = None,
    test_value: float | None = None,
    duration_steps: int | None = None,
) -> FaultTest:
    return FaultTest(
        "test-0001",
        "st-4",
        fault_type,
        2,
        NOW,
        point_id,
        test_value,
        duration_steps,
    )


def test_catalog_uses_exact_supported_device_and_fault_pairs() -> None:
    assert len(FAULT_RULES) == 7
    assert find_rule("st-4", FaultType.DERATE).label == "Temperature derating"

    with pytest.raises(ValueError, match="not available"):
        find_rule("dc600", FaultType.DERATE)


def test_empty_fault_path_returns_the_unchanged_normal_result() -> None:
    result = normal_result(value("st-4.actual.power", 80))

    assert apply_faults(result, {}, ()) is result


def test_stuck_invalid_and_out_of_range_effects_are_correlated() -> None:
    point = "st-4.actual.power"
    current = normal_result(value(point, 80))
    previous = {point: value(point, 65)}

    stuck = apply_faults(current, previous, (fault_test(FaultType.STUCK, point_id=point),))
    assert stuck.telemetry[0].value == 65
    assert stuck.telemetry[0].quality is DataQuality.STALE
    assert stuck.telemetry[0].test_id == "test-0001"
    assert stuck.health.test_id == "test-0001"
    assert stuck.faults[0].test_id == "test-0001"
    assert stuck.events[0].test_id == "test-0001"
    assert stuck.events[0].test_started_at == NOW

    invalid = apply_faults(current, previous, (fault_test(FaultType.INVALID, point_id=point),))
    assert invalid.telemetry[0].quality is DataQuality.INVALID

    outside = apply_faults(
        current,
        previous,
        (fault_test(FaultType.RANGE, point_id=point, test_value=9000),),
    )
    assert outside.telemetry[0].value == 9000
    assert outside.telemetry[0].quality is DataQuality.OUT_OF_RANGE


def test_communication_and_derating_apply_only_controlled_changes() -> None:
    power = value("st-4.available.power", 100)
    derated = DeviceValue(
        "st-4.derated",
        False,
        "boolean",
        DataQuality.GOOD,
        "st-4",
        EvidenceClassification.PROVISIONAL,
    )
    current = normal_result(power, derated)

    unavailable = apply_faults(current, {}, (fault_test(FaultType.COMMUNICATION),))
    assert all(item.quality is DataQuality.COMM_ERROR for item in unavailable.telemetry)
    assert unavailable.health.status is ComponentStatus.UNAVAILABLE
    assert unavailable.health.lifecycle is DeviceLifecycle.OFFLINE

    reduced = apply_faults(current, {}, (fault_test(FaultType.DERATE),))
    assert reduced.telemetry[0].value == 50
    assert reduced.telemetry[1].value is True
    assert reduced.health.status is ComponentStatus.DEGRADED


def test_timed_and_latched_fault_states_are_deterministic() -> None:
    timed = fault_test(FaultType.COMMUNICATION, duration_steps=2)
    latched = fault_test(FaultType.DEVICE, duration_steps=1)

    assert fault_state(timed, 1) is FaultTestState.SCHEDULED
    assert fault_state(timed, 2) is FaultTestState.ACTIVE
    assert fault_state(timed, 3) is FaultTestState.ACTIVE
    assert fault_state(timed, 4) is FaultTestState.COMPLETE
    assert fault_state(latched, 20) is FaultTestState.ACTIVE


def test_point_and_test_value_validation_has_no_fallback_matching() -> None:
    values = (value("st-4.actual.power", 10),)
    range_rule = find_rule("st-4", FaultType.RANGE)

    with pytest.raises(ValueError, match="selected value"):
        validate_fault(
            range_rule,
            FaultRequest("st-4", FaultType.RANGE, "actual.power", 20),
            values,
        )
    with pytest.raises(ValueError, match="finite test value"):
        validate_fault(
            range_rule,
            FaultRequest("st-4", FaultType.RANGE, "st-4.actual.power"),
            values,
        )

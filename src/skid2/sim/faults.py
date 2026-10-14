"""Deterministic, simulation-only fault test rules and effects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from math import isfinite

from skid2.contracts import AlarmSeverity, ComponentStatus, DataQuality
from skid2.contracts.domain import normalize_utc
from skid2.sim.device import (
    DeviceEvent,
    DeviceFault,
    DeviceHealth,
    DeviceLifecycle,
    DeviceState,
    DeviceStepResult,
    DeviceValue,
    EvidenceClassification,
)

ALL_SIMULATED_DEVICES = (
    "ba1-1",
    "cnvtr-1",
    "cnvtr-2",
    "dc600",
    "st-4",
    "st-5",
    "st-6",
)
POWER_DEVICES = ("cnvtr-1", "cnvtr-2", "st-4", "st-5", "st-6")


class FaultType(StrEnum):
    DEVICE = "device_fault"
    COMMUNICATION = "communication_loss"
    TIMEOUT = "response_timeout"
    STUCK = "stuck_value"
    INVALID = "invalid_value"
    RANGE = "out_of_range"
    DERATE = "temperature_derate"


class FaultTestState(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    CLEARED = "cleared"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class FaultRule:
    fault_type: FaultType
    label: str
    devices: tuple[str, ...]
    needs_point: bool = False
    needs_value: bool = False
    latched: bool = False
    expected_result: str = ""
    clear_method: str = "Clears after its duration or when cleared manually."
    fidelity: str = "Logical software simulation"
    evidence: str = "Project simulator behaviour"


FAULT_RULES = (
    FaultRule(
        FaultType.DEVICE,
        "Device fault",
        ALL_SIMULATED_DEVICES,
        latched=True,
        expected_result="The device reports a fault and waits for a manual clear.",
        clear_method="Must be cleared manually.",
    ),
    FaultRule(
        FaultType.COMMUNICATION,
        "Communication loss",
        ALL_SIMULATED_DEVICES,
        expected_result=(
            "The device becomes unavailable and its values show a communication problem."
        ),
    ),
    FaultRule(
        FaultType.TIMEOUT,
        "Response timeout",
        ALL_SIMULATED_DEVICES,
        expected_result="The device becomes unavailable because no response is received.",
    ),
    FaultRule(
        FaultType.STUCK,
        "Stuck value",
        ALL_SIMULATED_DEVICES,
        needs_point=True,
        expected_result="The selected value keeps its previous reading.",
    ),
    FaultRule(
        FaultType.INVALID,
        "Invalid value",
        ALL_SIMULATED_DEVICES,
        needs_point=True,
        expected_result="The selected value is marked unavailable.",
    ),
    FaultRule(
        FaultType.RANGE,
        "Out-of-range value",
        ALL_SIMULATED_DEVICES,
        needs_point=True,
        needs_value=True,
        expected_result=(
            "The selected value shows the entered test value and is marked outside its range."
        ),
    ),
    FaultRule(
        FaultType.DERATE,
        "Temperature derating",
        POWER_DEVICES,
        expected_result="Available power is reduced and the device reports derating.",
    ),
)


@dataclass(frozen=True, slots=True)
class FaultRequest:
    device_id: str
    fault_type: FaultType
    point_id: str | None = None
    test_value: float | None = None
    delay_steps: int = 0
    duration_steps: int | None = None

    def __post_init__(self) -> None:
        if self.delay_steps < 0:
            raise ValueError("start delay must not be negative")
        if self.duration_steps is not None and self.duration_steps < 1:
            raise ValueError("duration must be at least one step")


@dataclass(frozen=True, slots=True)
class FaultTest:
    test_id: str
    device_id: str
    fault_type: FaultType
    start_step: int
    started_at: datetime
    point_id: str | None = None
    test_value: float | None = None
    duration_steps: int | None = None
    cleared_at_step: int | None = None
    cleared_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "started_at",
            normalize_utc(self.started_at, "started_at"),
        )
        if self.start_step < 1:
            raise ValueError("start step must be positive")
        if self.duration_steps is not None and self.duration_steps < 1:
            raise ValueError("duration must be at least one step")
        if (self.cleared_at_step is None) != (self.cleared_at is None):
            raise ValueError("clear step and clear time must be provided together")
        if self.cleared_at is not None:
            object.__setattr__(
                self,
                "cleared_at",
                normalize_utc(self.cleared_at, "cleared_at"),
            )


def find_rule(device_id: str, fault_type: FaultType) -> FaultRule:
    for rule in FAULT_RULES:
        if rule.fault_type is fault_type and device_id in rule.devices:
            return rule
    raise ValueError("the selected problem is not available for this device")


def validate_fault(
    rule: FaultRule,
    request: FaultRequest,
    values: tuple[DeviceValue, ...],
) -> None:
    value_by_id = {item.logical_id: item for item in values}
    if rule.needs_point:
        if request.point_id is None:
            raise ValueError("select a value for this test")
        selected = value_by_id.get(request.point_id)
        if selected is None:
            if not values:
                raise ValueError("run one normal step before selecting a value")
            raise ValueError("the selected value is not available for this device")
        if rule.fault_type is FaultType.RANGE and (
            isinstance(selected.value, bool) or not isinstance(selected.value, (int, float))
        ):
            raise ValueError("out-of-range testing requires a numeric value")
    elif request.point_id is not None:
        raise ValueError("this test does not use a selected value")

    if rule.needs_value:
        if request.test_value is None or not isfinite(request.test_value):
            raise ValueError("enter a finite test value")
    elif request.test_value is not None:
        raise ValueError("this test does not use an entered value")


def fault_state(test: FaultTest, sequence: int, rule: FaultRule | None = None) -> FaultTestState:
    selected_rule = find_rule(test.device_id, test.fault_type) if rule is None else rule
    if test.cleared_at_step is not None:
        return FaultTestState.CLEARED
    if sequence < test.start_step:
        return FaultTestState.SCHEDULED
    if selected_rule.latched or test.duration_steps is None:
        return FaultTestState.ACTIVE
    if sequence < test.start_step + test.duration_steps:
        return FaultTestState.ACTIVE
    return FaultTestState.COMPLETE


def apply_faults[StateT: DeviceState](
    result: DeviceStepResult[StateT],
    previous: Mapping[str, DeviceValue],
    tests: tuple[FaultTest, ...],
) -> DeviceStepResult[StateT]:
    """Apply approved active effects after the normal model step."""

    if not tests:
        return result
    changed = result
    for test in sorted(tests, key=lambda item: item.test_id):
        changed = _apply_fault(changed, previous, test)
    return changed


def _apply_fault[StateT: DeviceState](
    result: DeviceStepResult[StateT],
    previous: Mapping[str, DeviceValue],
    test: FaultTest,
) -> DeviceStepResult[StateT]:
    rule = find_rule(test.device_id, test.fault_type)
    telemetry = _fault_values(result.telemetry, previous, test)
    health = _fault_health(result.health, test)
    severity = (
        AlarmSeverity.CRITICAL
        if test.fault_type in {FaultType.DEVICE, FaultType.COMMUNICATION, FaultType.TIMEOUT}
        else AlarmSeverity.WARNING
    )
    fault = DeviceFault(
        fault_id=f"{test.test_id}.{test.fault_type.value}",
        active=True,
        severity=severity,
        source=test.device_id,
        evidence_classification=EvidenceClassification.PROVISIONAL,
        test_id=test.test_id,
        test_started_at=test.started_at,
    )
    event = DeviceEvent(
        event_id=f"{test.test_id}:{test.fault_type.value}",
        category="fault_active",
        message=f"{rule.label} test is active",
        occurred_at=test.started_at,
        source=test.device_id,
        evidence_classification=EvidenceClassification.PROVISIONAL,
        test_id=test.test_id,
        test_started_at=test.started_at,
    )
    return replace(
        result,
        telemetry=telemetry,
        health=health,
        faults=(*result.faults, fault),
        events=(*result.events, event),
    )


def _fault_values(
    values: tuple[DeviceValue, ...],
    previous: Mapping[str, DeviceValue],
    test: FaultTest,
) -> tuple[DeviceValue, ...]:
    if test.fault_type in {FaultType.COMMUNICATION, FaultType.TIMEOUT}:
        return tuple(_tag_value(item, test, quality=DataQuality.COMM_ERROR) for item in values)
    if test.fault_type is FaultType.DEVICE:
        return tuple(_tag_value(item, test, quality=DataQuality.INVALID) for item in values)
    if test.fault_type is FaultType.DERATE:
        return tuple(_derate_value(item, test) for item in values)

    changed: list[DeviceValue] = []
    for item in values:
        if item.logical_id != test.point_id:
            changed.append(item)
            continue
        if test.fault_type is FaultType.STUCK:
            earlier = previous.get(item.logical_id)
            if earlier is None:
                raise ValueError("stuck-value testing requires an earlier reading")
            changed.append(
                replace(
                    item,
                    value=earlier.value,
                    quality=DataQuality.STALE,
                    test_id=test.test_id,
                    test_started_at=test.started_at,
                )
            )
        elif test.fault_type is FaultType.INVALID:
            changed.append(_tag_value(item, test, quality=DataQuality.INVALID))
        elif test.fault_type is FaultType.RANGE:
            if test.test_value is None:
                raise ValueError("out-of-range testing requires an entered value")
            changed.append(
                replace(
                    item,
                    value=test.test_value,
                    quality=DataQuality.OUT_OF_RANGE,
                    test_id=test.test_id,
                    test_started_at=test.started_at,
                )
            )
    return tuple(changed)


def _tag_value(
    value: DeviceValue,
    test: FaultTest,
    *,
    quality: DataQuality,
) -> DeviceValue:
    return replace(
        value,
        quality=quality,
        test_id=test.test_id,
        test_started_at=test.started_at,
    )


def _derate_value(value: DeviceValue, test: FaultTest) -> DeviceValue:
    if value.logical_id.endswith(".derated"):
        return replace(
            value,
            value=True,
            test_id=test.test_id,
            test_started_at=test.started_at,
        )
    power_suffixes = (
        ".available.power",
        ".actual.power",
        ".test.accepted.power",
        ".dc.power.raw",
    )
    if (
        value.logical_id.endswith(power_suffixes)
        and not isinstance(value.value, bool)
        and isinstance(value.value, (int, float))
    ):
        return replace(
            value,
            value=float(value.value) * 0.5,
            test_id=test.test_id,
            test_started_at=test.started_at,
        )
    return value


def _fault_health(health: DeviceHealth, test: FaultTest) -> DeviceHealth:
    if test.fault_type in {FaultType.COMMUNICATION, FaultType.TIMEOUT}:
        lifecycle = DeviceLifecycle.OFFLINE
        status = ComponentStatus.UNAVAILABLE
        quality = DataQuality.COMM_ERROR
    elif test.fault_type is FaultType.DEVICE:
        lifecycle = DeviceLifecycle.FAULTED
        status = ComponentStatus.DEGRADED
        quality = DataQuality.INVALID
    elif test.fault_type is FaultType.STUCK:
        lifecycle = health.lifecycle
        status = ComponentStatus.DEGRADED
        quality = DataQuality.STALE
    elif test.fault_type is FaultType.RANGE:
        lifecycle = health.lifecycle
        status = ComponentStatus.DEGRADED
        quality = DataQuality.OUT_OF_RANGE
    elif test.fault_type is FaultType.INVALID:
        lifecycle = health.lifecycle
        status = ComponentStatus.DEGRADED
        quality = DataQuality.INVALID
    else:
        lifecycle = health.lifecycle
        status = ComponentStatus.DEGRADED
        quality = health.quality
    return replace(
        health,
        lifecycle=lifecycle,
        status=status,
        quality=quality,
        test_id=test.test_id,
        test_started_at=test.started_at,
    )

"""Independent explicit-input Stabiliti logical simulator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from skid2.contracts import AlarmSeverity, ComponentStatus, DataQuality
from skid2.sim.device import (
    CommandDisposition,
    DeviceCapability,
    DeviceCommand,
    DeviceCommandResult,
    DeviceEvent,
    DeviceFault,
    DeviceHealth,
    DeviceIdentity,
    DeviceLifecycle,
    DeviceStep,
    DeviceStepResult,
    DeviceValue,
    EvidenceClassification,
)


@dataclass(frozen=True, slots=True)
class StabilitiInputs:
    enabled: bool = True
    available_power: float = 0.0
    actual_power: float = 0.0
    bus_load: float | None = None
    power_limit: float = 0.0
    maximum_temperature: float = 25.0
    derated: bool = False
    communication_ok: bool = True
    fault_active: bool = False

    def __post_init__(self) -> None:
        if self.bus_load is not None and (
            isinstance(self.bus_load, bool) or not isfinite(self.bus_load) or self.bus_load < 0
        ):
            raise ValueError("bus load must be a finite non-negative value")
        if not isfinite(self.available_power) or self.available_power < 0:
            raise ValueError("available power must be a finite non-negative value")
        if not isfinite(self.actual_power):
            raise ValueError("actual power must be finite")
        if not isfinite(self.power_limit) or self.power_limit < 0:
            raise ValueError("power limit must be a finite non-negative value")
        if not isfinite(self.maximum_temperature):
            raise ValueError("maximum temperature must be finite")


@dataclass(frozen=True, slots=True)
class StabilitiState:
    lifecycle: DeviceLifecycle
    ready: bool
    available: bool
    derated: bool


@dataclass(frozen=True, slots=True)
class StabilitiModel:
    identity: DeviceIdentity
    capabilities: tuple[DeviceCapability, ...]

    def initial_state(self, configuration: None) -> StabilitiState:
        return StabilitiState(DeviceLifecycle.READY, True, True, False)

    def step(
        self,
        request: DeviceStep[None, StabilitiState, StabilitiInputs],
    ) -> DeviceStepResult[StabilitiState]:
        inputs = request.inputs
        lifecycle = _lifecycle(inputs)
        ready = inputs.communication_ok and not inputs.fault_active
        available = inputs.enabled and ready
        thermal = _thermal_derate(inputs.maximum_temperature)
        derated = (inputs.derated or thermal > 0) and available
        served = _served_ac(inputs, available, thermal)
        recovering = request.state.lifecycle in {
            DeviceLifecycle.OFFLINE,
            DeviceLifecycle.FAULTED,
        } and lifecycle not in {DeviceLifecycle.OFFLINE, DeviceLifecycle.FAULTED}
        op_code, _op_name = _operational(lifecycle, available, derated, recovering)
        state = StabilitiState(lifecycle, ready, available, derated)
        quality = DataQuality.GOOD if inputs.communication_ok else DataQuality.COMM_ERROR
        prefix = self.identity.device_id
        voltage = GRID_FORM_VOLTS if available else 0.0
        current = (served * 1000.0 / voltage) if voltage > 0 else 0.0
        values: tuple[DeviceValue, ...] = (
            self._value(f"{prefix}.ready", ready, "boolean", DataQuality.GOOD),
            self._value(f"{prefix}.enabled", inputs.enabled, "boolean", DataQuality.GOOD),
            self._value(f"{prefix}.available", available, "boolean", DataQuality.GOOD),
            self._value(f"{prefix}.derated", derated, "boolean", DataQuality.GOOD),
            self._value(f"{prefix}.operational.state", op_code, "enum", DataQuality.GOOD),
            self._value(
                f"{prefix}.available.power",
                inputs.available_power * (1.0 - thermal) if available else 0.0,
                "kW",
                quality,
            ),
            self._value(f"{prefix}.actual.power", served, "kW", quality),
            self._value(f"{prefix}.ac.port.power", served, "kW", quality),
            self._value(f"{prefix}.ac.port.volts", voltage, "V", quality),
            self._value(f"{prefix}.ac.port.current", current, "A", quality),
            self._value(f"{prefix}.dc.in.power", served, "kW", quality),
            self._value(f"{prefix}.power.limit", inputs.power_limit, "kW", quality),
            self._value(
                f"{prefix}.temperature.maximum",
                inputs.maximum_temperature,
                "degC",
                quality,
            ),
            self._value(
                f"{prefix}.communication.ok",
                inputs.communication_ok,
                "boolean",
                DataQuality.GOOD,
            ),
            self._value(
                f"{prefix}.fault.active",
                inputs.fault_active,
                "boolean",
                DataQuality.GOOD,
            ),
        )
        if inputs.bus_load is not None:
            values += (self._value(f"{prefix}.bus.load", inputs.bus_load, "kW", quality),)
        health = DeviceHealth(
            lifecycle,
            _health(inputs, derated),
            quality,
            prefix,
            EvidenceClassification.PROVISIONAL,
        )
        faults = _faults(prefix, inputs, thermal > 0)
        events = _transition_events(
            self.identity.device_id,
            self.identity.display_name,
            request.state,
            state,
            request.at,
        )
        return DeviceStepResult(
            state,
            values,
            health,
            faults=faults,
            events=events,
            command_results=_unsupported_commands(request.commands, request.at),
        )

    def _value(
        self,
        logical_id: str,
        value: bool | int | float | str,
        unit: str,
        quality: DataQuality,
    ) -> DeviceValue:
        return DeviceValue(
            logical_id,
            value,
            unit,
            quality,
            self.identity.device_id,
            EvidenceClassification.PROVISIONAL,
        )


# 480 V is the confirmed grid-form voltage in the functional spec, not a vendor register.
GRID_FORM_VOLTS = 480.0
THERMAL_DERATE_START_C = 60.0
THERMAL_DERATE_CUTOFF_C = 80.0
OPERATIONAL = ("offline", "ready", "enabled", "derated", "faulted", "recovering")


def _thermal_derate(temperature: float) -> float:
    if temperature >= THERMAL_DERATE_CUTOFF_C:
        return 1.0
    if temperature <= THERMAL_DERATE_START_C:
        return 0.0
    span = THERMAL_DERATE_CUTOFF_C - THERMAL_DERATE_START_C
    return (temperature - THERMAL_DERATE_START_C) / span


def _served_ac(inputs: StabilitiInputs, available: bool, thermal: float) -> float:
    if not available:
        return 0.0
    if inputs.bus_load is None:
        return inputs.actual_power
    cap = min(inputs.available_power, inputs.power_limit) * (1.0 - thermal)
    return min(max(0.0, inputs.bus_load), max(0.0, cap))


def _operational(
    lifecycle: DeviceLifecycle,
    available: bool,
    derated: bool,
    recovering: bool,
) -> tuple[float, str]:
    if recovering:
        name = "recovering"
    elif lifecycle is DeviceLifecycle.OFFLINE:
        name = "offline"
    elif lifecycle is DeviceLifecycle.FAULTED:
        name = "faulted"
    elif derated:
        name = "derated"
    elif available:
        name = "enabled"
    elif lifecycle is DeviceLifecycle.DISABLED:
        name = "ready"
    else:
        name = "ready"
    return float(OPERATIONAL.index(name)), name


def _faults(prefix: str, inputs: StabilitiInputs, thermal: bool) -> tuple[DeviceFault, ...]:
    faults: list[DeviceFault] = []
    if inputs.fault_active:
        faults.append(
            DeviceFault(
                f"{prefix}.test.fault",
                True,
                AlarmSeverity.CRITICAL,
                prefix,
                EvidenceClassification.PROVISIONAL,
            )
        )
    if thermal:
        faults.append(
            DeviceFault(
                f"{prefix}.temperature_derate",
                True,
                AlarmSeverity.WARNING,
                prefix,
                EvidenceClassification.PROVISIONAL,
            )
        )
    return tuple(faults)


def _lifecycle(inputs: StabilitiInputs) -> DeviceLifecycle:
    if not inputs.communication_ok:
        return DeviceLifecycle.OFFLINE
    if inputs.fault_active:
        return DeviceLifecycle.FAULTED
    if inputs.enabled:
        return DeviceLifecycle.ACTIVE
    return DeviceLifecycle.DISABLED


def _health(inputs: StabilitiInputs, derated: bool) -> ComponentStatus:
    if not inputs.communication_ok:
        return ComponentStatus.UNAVAILABLE
    if inputs.fault_active or derated:
        return ComponentStatus.DEGRADED
    return ComponentStatus.HEALTHY


def _transition_events(
    device_id: str,
    display_name: str,
    previous: StabilitiState,
    current: StabilitiState,
    at: datetime,
) -> tuple[DeviceEvent, ...]:
    events: list[DeviceEvent] = []
    changes = (
        ("state", previous.lifecycle.value, current.lifecycle.value),
        ("availability", previous.available, current.available),
        ("derating", previous.derated, current.derated),
    )
    for category, before, after in changes:
        if before == after:
            continue
        readable = str(after).lower() if isinstance(after, bool) else str(after)
        events.append(
            DeviceEvent(
                f"{device_id}:{category}:{readable}",
                "simulation",
                f"{display_name} {category} changed to {readable}",
                at,
                device_id,
                EvidenceClassification.PROVISIONAL,
            )
        )
    return tuple(events)


def _unsupported_commands(
    commands: tuple[DeviceCommand, ...],
    at: datetime,
) -> tuple[DeviceCommandResult, ...]:
    return tuple(
        DeviceCommandResult(
            command.command_id,
            command.capability_id,
            CommandDisposition.REJECTED_UNSUPPORTED,
            "Direct equipment commands are not available in the logical simulator",
            at,
            EvidenceClassification.PROVISIONAL,
        )
        for command in commands
    )

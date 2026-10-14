"""Explicit-input BA1-1 battery simulator without battery physics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from skid2.contracts import AlarmSeverity, ComponentStatus, DataQuality
from skid2.sim.device import (
    DeviceCapability,
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
class BatteryInputs:
    cluster_soc: float = 0.5
    voltage: float = 0.0
    current: float = 0.0
    maximum_temperature: float = 25.0
    charge_power_limit: float = 0.0
    discharge_power_limit: float = 0.0
    main_contactor: bool = False
    precharge_contactor: bool = False
    precharge_state: int = 0
    fire_communication_state: int = 0
    communication_ok: bool = True
    fault_active: bool = False

    def __post_init__(self) -> None:
        _bounded(self.cluster_soc, 0.0, 1.0, "cluster SOC")
        _bounded(self.voltage, 0.0, 6553.5, "battery voltage")
        _bounded(self.current, -3276.8, 3276.7, "battery current")
        _bounded(self.maximum_temperature, -40.0, 125.0, "maximum temperature")
        _bounded(self.charge_power_limit, 0.0, 65535.0, "charge power limit")
        _bounded(self.discharge_power_limit, 0.0, 65535.0, "discharge power limit")
        if not 0 <= self.precharge_state <= 4:
            raise ValueError("precharge state must be in 0..4")
        if not 0 <= self.fire_communication_state <= 2:
            raise ValueError("fire communication state must be in 0..2")


@dataclass(frozen=True, slots=True)
class BatteryState:
    lifecycle: DeviceLifecycle


@dataclass(frozen=True, slots=True)
class BatteryModel:
    identity: DeviceIdentity
    capabilities: tuple[DeviceCapability, ...]

    def initial_state(self, configuration: None) -> BatteryState:
        return BatteryState(DeviceLifecycle.READY)

    def step(
        self,
        request: DeviceStep[None, BatteryState, BatteryInputs],
    ) -> DeviceStepResult[BatteryState]:
        inputs = request.inputs
        lifecycle = _lifecycle(inputs)
        quality = DataQuality.GOOD if inputs.communication_ok else DataQuality.COMM_ERROR
        state = BatteryState(lifecycle)
        values = (
            self._value("bess.cluster.soc", inputs.cluster_soc, "ratio", quality),
            self._value("bess.cluster.voltage", inputs.voltage, "V", quality),
            self._value("bess.cluster.current", inputs.current, "A", quality),
            self._value(
                "bess.cluster.temperature.maximum",
                inputs.maximum_temperature,
                "degC",
                quality,
            ),
            self._value(
                "bess.cluster.limit.charge.power",
                inputs.charge_power_limit,
                "kW",
                quality,
            ),
            self._value(
                "bess.cluster.limit.discharge.power",
                inputs.discharge_power_limit,
                "kW",
                quality,
            ),
            self._value("bess.cluster.contactor.main", inputs.main_contactor, "boolean", quality),
            self._value(
                "bess.cluster.contactor.precharge",
                inputs.precharge_contactor,
                "boolean",
                quality,
            ),
            self._value(
                "bess.cluster.precharge.state", inputs.precharge_state, "state_code", quality
            ),
            self._value(
                "bess.fire.communication.state",
                inputs.fire_communication_state,
                "state_code",
                quality,
            ),
        )
        health = DeviceHealth(
            lifecycle=lifecycle,
            status=_health(inputs),
            quality=quality,
            source=self.identity.device_id,
            evidence_classification=EvidenceClassification.PROVISIONAL,
        )
        faults = (
            (
                DeviceFault(
                    fault_id="ba1-1.test.fault",
                    active=True,
                    severity=AlarmSeverity.CRITICAL,
                    source=self.identity.device_id,
                    evidence_classification=EvidenceClassification.PROVISIONAL,
                ),
            )
            if inputs.fault_active
            else ()
        )
        return DeviceStepResult(
            state,
            values,
            health,
            faults=faults,
            events=_transition_event(self.identity.device_id, request.state, state, request.at),
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


def _lifecycle(inputs: BatteryInputs) -> DeviceLifecycle:
    if not inputs.communication_ok:
        return DeviceLifecycle.OFFLINE
    if inputs.fault_active:
        return DeviceLifecycle.FAULTED
    if inputs.main_contactor:
        return DeviceLifecycle.ACTIVE
    return DeviceLifecycle.READY


def _health(inputs: BatteryInputs) -> ComponentStatus:
    if not inputs.communication_ok:
        return ComponentStatus.UNAVAILABLE
    if inputs.fault_active:
        return ComponentStatus.DEGRADED
    return ComponentStatus.HEALTHY


def _transition_event(
    device_id: str,
    previous: BatteryState,
    current: BatteryState,
    at: datetime,
) -> tuple[DeviceEvent, ...]:
    if previous.lifecycle is current.lifecycle:
        return ()
    return (
        DeviceEvent(
            event_id=f"{device_id}:lifecycle:{current.lifecycle.value}",
            category="simulation",
            message=f"{device_id} changed to {current.lifecycle.value}",
            occurred_at=at,
            source=device_id,
            evidence_classification=EvidenceClassification.PROVISIONAL,
        ),
    )


def _bounded(value: float, minimum: float, maximum: float, label: str) -> None:
    if not isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be in {minimum}..{maximum}")

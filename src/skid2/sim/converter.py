"""Shared explicit-input model for the two converter roles."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import isfinite

from skid2.contracts import AlarmSeverity, ComponentStatus, DataQuality
from skid2.protocol.codecs import vendor_charge_positive_to_internal_discharge_positive
from skid2.sim.device import (
    DeviceCapability,
    DeviceFault,
    DeviceHealth,
    DeviceIdentity,
    DeviceLifecycle,
    DeviceStep,
    DeviceStepResult,
    DeviceValue,
    EvidenceClassification,
)


class ConverterRole(StrEnum):
    BATTERY = "battery"
    SOLAR = "solar"


@dataclass(frozen=True, slots=True)
class ConverterInputs:
    enabled: bool = False
    requested_power: float = 0.0
    available_power: float = 0.0
    charge_power_limit: float = 0.0
    discharge_power_limit: float = 0.0
    dc_voltage: float = 0.0
    dc_current: float = 0.0
    temperature: float = 25.0
    fault_active: bool = False
    communication_ok: bool = True
    pv_available: bool = False
    mppt_applying: bool = False
    test_ramp_per_step: float | None = None
    test_derate_limit: float | None = None

    def __post_init__(self) -> None:
        _bounded(self.requested_power, -3276.8, 3276.7, "requested power")
        _bounded(self.available_power, 0.0, 6553.5, "available power")
        _bounded(self.charge_power_limit, 0.0, 6553.5, "charge power limit")
        _bounded(self.discharge_power_limit, 0.0, 6553.5, "discharge power limit")
        _bounded(self.dc_voltage, 0.0, 6553.5, "DC voltage")
        _bounded(self.dc_current, -3276.8, 3276.7, "DC current")
        _bounded(self.temperature, -32768.0, 32767.0, "converter temperature")
        _optional_non_negative(self.test_ramp_per_step, "test ramp")
        _optional_non_negative(self.test_derate_limit, "test derate limit")


@dataclass(frozen=True, slots=True)
class ConverterState:
    lifecycle: DeviceLifecycle
    accepted_power: float


@dataclass(frozen=True, slots=True)
class ConverterModel:
    identity: DeviceIdentity
    capabilities: tuple[DeviceCapability, ...]
    role: ConverterRole

    def initial_state(self, configuration: None) -> ConverterState:
        return ConverterState(DeviceLifecycle.READY, 0.0)

    def step(
        self,
        request: DeviceStep[None, ConverterState, ConverterInputs],
    ) -> DeviceStepResult[ConverterState]:
        inputs = request.inputs
        lifecycle = _lifecycle(inputs)
        accepted = (
            _ramp(
                request.state.accepted_power,
                self._target(inputs),
                inputs.test_ramp_per_step,
            )
            if lifecycle is DeviceLifecycle.ACTIVE
            else 0.0
        )
        state = ConverterState(lifecycle, accepted)
        quality = DataQuality.GOOD if inputs.communication_ok else DataQuality.COMM_ERROR
        values = self._values(inputs, accepted, quality)
        health = DeviceHealth(
            lifecycle,
            _health(inputs),
            quality,
            self.identity.device_id,
            EvidenceClassification.PROVISIONAL,
        )
        faults = (
            (
                DeviceFault(
                    f"{self.identity.device_id}.test.fault",
                    True,
                    AlarmSeverity.CRITICAL,
                    self.identity.device_id,
                    EvidenceClassification.PROVISIONAL,
                ),
            )
            if inputs.fault_active
            else ()
        )
        return DeviceStepResult(state, values, health, faults=faults)

    def _target(self, inputs: ConverterInputs) -> float:
        available = inputs.available_power
        if inputs.test_derate_limit is not None:
            available = min(available, inputs.test_derate_limit)
        if self.role is ConverterRole.SOLAR:
            return min(max(inputs.requested_power, 0.0), available)
        maximum = min(available, inputs.discharge_power_limit)
        minimum = -min(available, inputs.charge_power_limit)
        return min(max(inputs.requested_power, minimum), maximum)

    def _values(
        self,
        inputs: ConverterInputs,
        accepted: float,
        quality: DataQuality,
    ) -> tuple[DeviceValue, ...]:
        prefix = "cnvtr1" if self.role is ConverterRole.BATTERY else "cnvtr2"
        raw_power = (
            float(vendor_charge_positive_to_internal_discharge_positive(Decimal(str(accepted))))
            if self.role is ConverterRole.BATTERY
            else accepted
        )
        common = (
            self._value(f"{prefix}.dc.power.raw", raw_power, "kW", quality),
            self._value(f"{prefix}.dc.bus.voltage", inputs.dc_voltage, "V", quality),
            self._value(f"{prefix}.temperature.module", inputs.temperature, "degC", quality),
            self._value(f"{prefix}.test.requested.power", inputs.requested_power, "kW", quality),
            self._value(f"{prefix}.test.accepted.power", accepted, "kW", quality),
        )
        if self.role is ConverterRole.BATTERY:
            return (
                *common,
                self._value("cnvtr1.available.power", inputs.available_power, "kW", quality),
                self._value("cnvtr1.test.charge.limit", inputs.charge_power_limit, "kW", quality),
                self._value(
                    "cnvtr1.test.discharge.limit", inputs.discharge_power_limit, "kW", quality
                ),
                self._value(
                    "cnvtr1.test.power.limit",
                    min(inputs.available_power, inputs.test_derate_limit)
                    if inputs.test_derate_limit is not None
                    else inputs.available_power,
                    "kW",
                    quality,
                ),
                self._value("cnvtr1.dc.current.raw", inputs.dc_current, "A", quality),
                self._value("cnvtr1.status.fault", inputs.fault_active, "boolean", quality),
            )
        online = (
            inputs.enabled
            and inputs.pv_available
            and inputs.communication_ok
            and not inputs.fault_active
        )
        return (
            *common,
            self._value(
                "cnvtr2.available.power",
                min(inputs.available_power, inputs.test_derate_limit)
                if inputs.test_derate_limit is not None
                else inputs.available_power,
                "kW",
                quality,
            ),
            self._value("cnvtr2.pv.fault", inputs.fault_active, "boolean", quality),
            self._value("cnvtr2.pv.online", online, "boolean", quality),
            self._value(
                "cnvtr2.pv.mppt.applying",
                inputs.mppt_applying and online,
                "boolean",
                quality,
            ),
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


def _lifecycle(inputs: ConverterInputs) -> DeviceLifecycle:
    if not inputs.communication_ok:
        return DeviceLifecycle.OFFLINE
    if inputs.fault_active:
        return DeviceLifecycle.FAULTED
    if inputs.enabled:
        return DeviceLifecycle.ACTIVE
    return DeviceLifecycle.READY


def _health(inputs: ConverterInputs) -> ComponentStatus:
    if not inputs.communication_ok:
        return ComponentStatus.UNAVAILABLE
    if inputs.fault_active:
        return ComponentStatus.DEGRADED
    return ComponentStatus.HEALTHY


def _ramp(current: float, target: float, limit: float | None) -> float:
    if limit is None:
        return target
    return min(max(target, current - limit), current + limit)


def _bounded(value: float, minimum: float, maximum: float, label: str) -> None:
    if not isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be in {minimum}..{maximum}")


def _optional_non_negative(value: float | None, label: str) -> None:
    if value is not None and (not isfinite(value) or value < 0):
        raise ValueError(f"{label} must be a finite non-negative value")

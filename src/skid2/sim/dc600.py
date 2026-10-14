"""Explicit logical DC600 demand and availability handshake."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite

from skid2.contracts import AlarmSeverity, ComponentStatus, DataQuality
from skid2.contracts.domain import normalize_utc
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
class Dc600Inputs:
    demand_power: float | None = None
    available_power: float | None = None
    actual_power: float | None = None
    demand_fresh: bool = True
    available_fresh: bool = True
    session_active: bool = False
    communication_ok: bool = True
    fault_active: bool = False
    demand_updated_at: datetime | None = field(default=None, repr=False)
    available_updated_at: datetime | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _optional_non_negative(self.demand_power, "demand power")
        _optional_non_negative(self.available_power, "available power")
        _optional_non_negative(self.actual_power, "actual power")
        if self.demand_updated_at is not None:
            object.__setattr__(
                self,
                "demand_updated_at",
                normalize_utc(self.demand_updated_at, "demand_updated_at"),
            )
        if self.available_updated_at is not None:
            object.__setattr__(
                self,
                "available_updated_at",
                normalize_utc(self.available_updated_at, "available_updated_at"),
            )


@dataclass(frozen=True, slots=True)
class Dc600State:
    lifecycle: DeviceLifecycle
    live: bool
    accepted_power: float | None
    limited: bool
    status: str
    session_active: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Dc600Model:
    identity: DeviceIdentity
    capabilities: tuple[DeviceCapability, ...]

    def initial_state(self, configuration: None) -> Dc600State:
        return Dc600State(
            DeviceLifecycle.READY,
            False,
            None,
            False,
            "Waiting",
            False,
            "Demand or available power is not current",
        )

    def step(
        self,
        request: DeviceStep[None, Dc600State, Dc600Inputs],
    ) -> DeviceStepResult[Dc600State]:
        inputs = request.inputs
        live = _live(inputs)
        accepted = (
            min(inputs.demand_power, inputs.available_power)
            if live and inputs.demand_power is not None and inputs.available_power is not None
            else None
        )
        limited = (
            accepted is not None
            and inputs.demand_power is not None
            and accepted < inputs.demand_power
        )
        reason = _reason(inputs, live, limited)
        status = _status(inputs, live, limited, accepted, request.state.status)
        lifecycle = _lifecycle(inputs, live, accepted)
        state = Dc600State(
            lifecycle,
            live,
            accepted,
            limited,
            status,
            inputs.session_active,
            reason,
        )
        quality = _quality(inputs)
        values = list(self._base_values(inputs, live, limited, status, reason, quality))
        if accepted is not None:
            values.append(self._value("dc600.accepted.power", accepted, "kW", quality))
        if inputs.actual_power is not None:
            values.append(self._value("dc600.actual.power", inputs.actual_power, "kW", quality))
        health = DeviceHealth(
            lifecycle,
            _health(inputs, live),
            quality,
            self.identity.device_id,
            EvidenceClassification.PROVISIONAL,
        )
        faults = (
            (
                DeviceFault(
                    "dc600.test.fault",
                    True,
                    AlarmSeverity.CRITICAL,
                    self.identity.device_id,
                    EvidenceClassification.PROVISIONAL,
                ),
            )
            if inputs.fault_active
            else ()
        )
        return DeviceStepResult(
            state,
            tuple(values),
            health,
            faults=faults,
            events=_transition_events(request.state, state, request.at),
            command_results=_unsupported_commands(request.commands, request.at),
        )

    def _base_values(
        self,
        inputs: Dc600Inputs,
        live: bool,
        limited: bool,
        status: str,
        reason: str,
        quality: DataQuality,
    ) -> tuple[DeviceValue, ...]:
        values = [
            self._value("dc600.handshake.live", live, "boolean", DataQuality.GOOD),
            self._value("dc600.session.active", inputs.session_active, "boolean", DataQuality.GOOD),
            self._value("dc600.status", status, "text", DataQuality.GOOD),
            self._value("dc600.handshake.phase", status, "text", DataQuality.GOOD),
            self._value("dc600.request.limited", limited, "boolean", DataQuality.GOOD),
            self._value("dc600.derate.reason", reason, "text", DataQuality.GOOD),
            self._value(
                "dc600.communication.ok",
                inputs.communication_ok,
                "boolean",
                DataQuality.GOOD,
            ),
            self._value(
                "dc600.fault.active",
                inputs.fault_active,
                "boolean",
                DataQuality.GOOD,
            ),
        ]
        values.extend(self._appendix_a_values(inputs, quality))
        if inputs.demand_power is not None:
            values.append(self._value("dc600.demand.power", inputs.demand_power, "kW", quality))
            if inputs.demand_updated_at is not None:
                values.append(
                    self._value(
                        "dc600.demand.updated.at",
                        inputs.demand_updated_at.isoformat(),
                        "timestamp",
                        quality,
                    )
                )
        if inputs.available_power is not None:
            values.append(
                self._value("dc600.available.power", inputs.available_power, "kW", quality)
            )
            if inputs.available_updated_at is not None:
                values.append(
                    self._value(
                        "dc600.available.updated.at",
                        inputs.available_updated_at.isoformat(),
                        "timestamp",
                        quality,
                    )
                )
        return tuple(values)

    def _appendix_a_values(
        self,
        inputs: Dc600Inputs,
        quality: DataQuality,
    ) -> list[DeviceValue]:
        demand_kw = 0.0 if inputs.demand_power is None else inputs.demand_power
        available_kw = 0.0 if inputs.available_power is None else inputs.available_power
        occupied = inputs.session_active or demand_kw > 0
        if inputs.fault_active or not inputs.communication_ok:
            status = 2
        elif occupied and demand_kw > 0:
            status = 1
        else:
            status = 0
        demand_w = int(round(max(0.0, demand_kw) * 1000.0))
        available_w = int(round(max(0.0, available_kw) * 1000.0))
        return [
            self._value("dc600.evcs.demand.power", demand_w, "W", quality),
            self._value("dc600.evcs.status", status, "enum", DataQuality.GOOD),
            self._value(
                "dc600.evcs.vehicles",
                1 if inputs.session_active else 0,
                "count",
                DataQuality.GOOD,
            ),
            self._value("dc600.evcs.bus.voltage", 750.0, "V", quality),
            self._value("dc600.pms.available.power", available_w, "W", quality),
            self._value(
                "dc600.pms.system.status",
                5 if inputs.fault_active or not inputs.communication_ok else 0,
                "enum",
                DataQuality.GOOD,
            ),
            self._value(
                "dc600.pms.time.remaining",
                999 if available_kw > 0 else 0,
                "min",
                DataQuality.GOOD,
            ),
            self._value("dc600.pms.pv.power", 0, "W", DataQuality.GOOD),
            self._value("dc600.pms.bess.soc", 0.0, "percent", DataQuality.GOOD),
        ]

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


def _live(inputs: Dc600Inputs) -> bool:
    return (
        inputs.communication_ok
        and not inputs.fault_active
        and inputs.demand_fresh
        and inputs.available_fresh
        and inputs.demand_power is not None
        and inputs.available_power is not None
    )


def _quality(inputs: Dc600Inputs) -> DataQuality:
    if not inputs.communication_ok:
        return DataQuality.COMM_ERROR
    if not inputs.demand_fresh or not inputs.available_fresh:
        return DataQuality.STALE
    return DataQuality.GOOD


def _lifecycle(
    inputs: Dc600Inputs,
    live: bool,
    accepted: float | None,
) -> DeviceLifecycle:
    if not inputs.communication_ok:
        return DeviceLifecycle.OFFLINE
    if inputs.fault_active:
        return DeviceLifecycle.FAULTED
    if live and accepted is not None and accepted > 0:
        return DeviceLifecycle.ACTIVE
    return DeviceLifecycle.READY


def _health(inputs: Dc600Inputs, live: bool) -> ComponentStatus:
    if not inputs.communication_ok:
        return ComponentStatus.UNAVAILABLE
    if inputs.fault_active or not live:
        return ComponentStatus.DEGRADED
    return ComponentStatus.HEALTHY


def _reason(inputs: Dc600Inputs, live: bool, limited: bool) -> str:
    if not inputs.communication_ok:
        return "Communication lost"
    if inputs.fault_active:
        return "DC600 test fault is active"
    if inputs.demand_power is None and inputs.available_power is None:
        return "Demand and available power are missing"
    if inputs.demand_power is None:
        return "Demand power is missing"
    if inputs.available_power is None:
        return "Available power is missing"
    if not inputs.demand_fresh and not inputs.available_fresh:
        return "Demand and available power are stale"
    if not inputs.demand_fresh:
        return "Demand power is stale"
    if not inputs.available_fresh:
        return "Available power is stale"
    if not live:
        return "Demand or available power is not current"
    if limited:
        return "Demand exceeds available power"
    return "Demand is within available power"


def _status(
    inputs: Dc600Inputs,
    live: bool,
    limited: bool,
    accepted: float | None,
    previous: str,
) -> str:
    if not inputs.communication_ok:
        return "Offline"
    if inputs.fault_active:
        return "Faulted"
    live_charge = (
        "Charging",
        "Limited",
        "Acknowledged",
        "Requested",
        "Recovering",
    )
    if not live:
        if previous in live_charge:
            return "Timeout"
        return "Waiting for current values"
    if previous == "Timeout":
        return "Recovering"
    if previous == "Offline":
        return "Recovering"
    if limited:
        return "Limited"
    if inputs.session_active and accepted is not None and accepted > 0:
        if previous in {
            "Waiting",
            "Waiting for current values",
            "Ready",
            "Recovering",
            "Timeout",
        }:
            return "Requested"
        if previous == "Requested":
            return "Acknowledged"
        return "Charging"
    return "Ready"


def _transition_events(
    previous: Dc600State,
    current: Dc600State,
    at: datetime,
) -> tuple[DeviceEvent, ...]:
    events: list[DeviceEvent] = []
    changes = (
        ("status", previous.status, current.status),
        ("session", previous.session_active, current.session_active),
        ("limiting", previous.limited, current.limited),
    )
    for category, before, after in changes:
        if before == after:
            continue
        readable = str(after).lower() if isinstance(after, bool) else str(after)
        events.append(
            DeviceEvent(
                f"dc600:{category}:{readable}",
                "simulation",
                f"DC600 {category} changed to {readable}",
                at,
                "dc600",
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


def _optional_non_negative(value: float | None, label: str) -> None:
    if value is not None and (not isfinite(value) or value < 0):
        raise ValueError(f"{label} must be a finite non-negative value")

"""Typed plant state and a pure fixed-step boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from math import fsum, isclose, isfinite, ulp

from skid2.contracts import ComponentStatus, DataQuality
from skid2.contracts.domain import normalize_utc
from skid2.plant.battery import BatteryConfig, battery_limits, battery_power
from skid2.plant.bus import BusConfig, step_bus
from skid2.plant.pv import PROFILE_IDS, PV_IDS, PvConfig, pv_available, pv_limit, pv_power
from skid2.sim.device import (
    DeviceCapability,
    DeviceEvent,
    DeviceLifecycle,
    DeviceState,
    DeviceStepResult,
    EvidenceClassification,
    ExecutionProfile,
)

DEVICE_IDS = ("ba1-1", "cnvtr-1", "cnvtr-2", "dc600", "st-4", "st-5", "st-6")
MODEL_VERSION = "5"

BATTERY_FIELDS = (
    ("Battery energy", "kWh"),
    ("Battery SOC", "ratio"),
    ("Requested power", "kW"),
    ("Accepted power", "kW"),
    ("Charge limit", "kW"),
    ("Discharge limit", "kW"),
    ("Offered power", "kW"),
    ("Actual power", "kW"),
    ("Energy loss rate", "kW"),
    ("Energy change", "kWh"),
    ("Request not met", "kW"),
    ("Balance error", "kW"),
)
BATTERY_LEDGER_FIELDS = (
    ("Battery energy change", "kWh"),
    ("Battery loss", "kW"),
    ("Battery balance error", "kW"),
)

PV_FIELDS = (
    ("Available solar", "kW"),
    ("Requested power", "kW"),
    ("Accepted power", "kW"),
    ("Output limit", "kW"),
    ("Offered power", "kW"),
    ("Power delivered", "kW"),
    ("Conversion loss", "kW"),
    ("Unused solar", "kW"),
)

BUS_FIELDS = (
    ("Bus voltage", "VDC"),
    ("Bus energy", "kWh"),
    ("Power difference", "kW"),
    ("Steps without calculation", "count"),
)
LEDGER_FIELDS = (
    ("Offered supply", "kW"),
    ("Requested load", "kW"),
    ("Accepted load", "kW"),
    ("Power supplied", "kW"),
    ("Power used", "kW"),
    ("Modeled loss", "kW"),
    ("Stored power", "kW"),
    ("Energy change", "kWh"),
    ("Unused supply", "kW"),
    ("Power not supplied", "kW"),
    ("Balance error", "kW"),
    ("Excluded inputs", "count"),
)
PORT_FIELDS = (
    ("Requested power", "kW"),
    ("Accepted power", "kW"),
    ("Power supplied", "kW"),
    ("Power used", "kW"),
    ("Modeled loss", "kW"),
    ("Unused supply", "kW"),
    ("Power not supplied", "kW"),
    ("Energy transfer", "kWh"),
)


class ValueStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    BLOCKED = "blocked"
    NOT_IMPLEMENTED = "not_implemented"


@dataclass(frozen=True, slots=True)
class PlantValue:
    name: str
    value: float | None
    unit: str
    status: ValueStatus
    quality: DataQuality | None
    at: datetime | None
    reason: str
    capability_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, ValueStatus) or (
            self.quality is not None and not isinstance(self.quality, DataQuality)
        ):
            raise ValueError("plant value status or quality is invalid")
        if self.value is not None and (isinstance(self.value, bool) or not isfinite(self.value)):
            raise ValueError("plant value must be finite")
        if self.status is ValueStatus.AVAILABLE:
            if self.value is None or self.quality is None or self.at is None:
                raise ValueError("available plant value requires a sample and quality")
        elif self.value is not None:
            raise ValueError("unavailable plant value cannot contain a number")
        elif not self.reason:
            raise ValueError("unavailable plant value requires a reason")
        if not self.name or not self.unit or not self.capability_id:
            raise ValueError("plant value requires identity and units")
        if self.at is not None:
            object.__setattr__(self, "at", normalize_utc(self.at, "plant value time"))


@dataclass(frozen=True, slots=True)
class PlantConfig:
    profile_id: str
    revision: str
    seed: int
    step_seconds: float
    initial_at: datetime
    capabilities: tuple[DeviceCapability, ...]
    limitations: tuple[str, ...]
    review_owner: str
    reviewers: tuple[str, ...]
    bus: BusConfig
    pv: tuple[PvConfig, ...] = ()
    battery: BatteryConfig | None = None
    model_version: str = MODEL_VERSION

    def __post_init__(self) -> None:
        if self.profile_id not in PROFILE_IDS or self.model_version != MODEL_VERSION:
            raise ValueError("unsupported plant profile or model version")
        expected = () if self.profile_id == "stage0" else PV_IDS
        if not isinstance(self.pv, tuple) or tuple(item.device_id for item in self.pv) != expected:
            raise ValueError("solar configuration does not match the profile")
        if type(self.seed) is not int or not 0 <= self.seed <= 2**31 - 1:
            raise ValueError("plant seed must be an integer in 0..2147483647")
        if not isfinite(self.step_seconds) or not 0.001 <= self.step_seconds <= 60:
            raise ValueError("plant step must be between 0.001 and 60 seconds")
        if timedelta(seconds=self.step_seconds).total_seconds() != self.step_seconds:
            raise ValueError("plant step must use whole microseconds")
        if not self.revision or not self.capabilities or not self.limitations:
            raise ValueError("plant profile requires revision, capabilities and limitations")
        ids = tuple(item.capability_id for item in self.capabilities)
        if len(ids) != len(set(ids)) or any(
            item.device_id != "plant" for item in self.capabilities
        ):
            raise ValueError("plant capability identity is invalid")
        battery_capability = next(
            item for item in self.capabilities if item.capability_id == "plant.bess"
        )
        if battery_capability.available != (self.battery is not None):
            raise ValueError("battery configuration does not match its capability")
        if self.battery is not None and (
            battery_capability.evidence_classification is not EvidenceClassification.PROVISIONAL
            or battery_capability.enabled_profiles != frozenset({ExecutionProfile.STAGE_0})
        ):
            raise ValueError("battery assumptions require the simulation profile")
        object.__setattr__(self, "initial_at", normalize_utc(self.initial_at, "plant initial time"))

    @property
    def step(self) -> timedelta:
        return timedelta(seconds=self.step_seconds)


@dataclass(frozen=True, slots=True)
class PlantPort:
    device_id: str
    at: datetime
    sequence: int
    health: ComponentStatus
    readings: tuple[PlantValue, ...]
    outputs: tuple[PlantValue, ...]
    ledger: tuple[PlantValue, ...] = ()


@dataclass(frozen=True, slots=True)
class PlantState:
    configuration: PlantConfig
    at: datetime
    sequence: int
    bus: tuple[PlantValue, ...]
    pv: tuple[PlantValue, ...]
    bess: tuple[PlantValue, ...]
    load: tuple[PlantValue, ...]
    converter: tuple[PlantValue, ...]
    thermal: tuple[PlantValue, ...]
    ledger: tuple[PlantValue, ...]
    balance: ValueStatus
    ports: tuple[PlantPort, ...]
    pv_paths: tuple[PvPath, ...] = ()


@dataclass(frozen=True, slots=True)
class PvPath:
    device_id: str
    destination: str
    health: ComponentStatus
    reason: str
    values: tuple[PlantValue, ...]


PlantInputs = tuple[tuple[str, DeviceStepResult[DeviceState] | None], ...]


@dataclass(frozen=True, slots=True)
class PlantStepResult:
    state: PlantState
    events: tuple[DeviceEvent, ...]


@dataclass(frozen=True, slots=True)
class PlantModel:
    configuration: PlantConfig

    def initial_state(self) -> PlantState:
        """Start from the declared bus and battery energy."""

        at = self.configuration.initial_at
        bus = (
            _value("Bus voltage", self.configuration.bus.nominal_voltage_v, "VDC", at),
            _value("Bus energy", self.configuration.bus.initial_energy_kwh, "kWh", at),
            _missing("Power difference", "kW", "Waiting for power readings"),
            _value("Steps without calculation", 0.0, "count", at),
        )
        paths = tuple(_initial_pv(item, at) for item in self.configuration.pv)
        pv = _pv_total(paths) if paths else _summary("pv", (("CNVTR-2 PV power", "kW"),))
        bess = (
            _initial_battery(self.configuration.battery, at)
            if self.configuration.battery is not None
            else _summary("bess", (("Battery energy", "kWh"), ("Battery SOC", "ratio")))
        )
        load = _summary("load", (("Served EV load", "kW"), ("Auxiliary load", "kW")))
        converter = _summary("converter", (("Converter loss", "kW"),))
        thermal = _summary("thermal", (("Plant temperature", "degC"),))
        ledger = _missing_ledger("Waiting for power readings", 6, at)
        if self.configuration.battery is not None:
            ledger += tuple(
                _battery_value(name, None, unit, at, "Waiting for power readings")
                for name, unit in BATTERY_LEDGER_FIELDS
            )
        outputs = bus + pv + bess + load + converter + thermal
        return PlantState(
            self.configuration,
            self.configuration.initial_at,
            0,
            bus,
            pv,
            bess,
            load,
            converter,
            thermal,
            ledger,
            ValueStatus.MISSING,
            tuple(
                PlantPort(
                    device_id,
                    self.configuration.initial_at,
                    0,
                    ComponentStatus.STOPPED,
                    (),
                    outputs + _st_output(None) if device_id.startswith("st-") else outputs,
                    _empty_port(device_id),
                )
                for device_id in DEVICE_IDS
            ),
            paths,
        )

    def step(
        self,
        *,
        state: PlantState,
        inputs: PlantInputs,
        configuration: PlantConfig,
        at: datetime,
        dt: timedelta,
    ) -> PlantStepResult:
        if configuration != self.configuration or state.configuration != configuration:
            raise ValueError("plant configuration does not match the run")
        if dt != configuration.step:
            raise ValueError("plant step does not match the profile")
        if at != state.at + dt:
            raise ValueError("plant time does not match the next step")
        if tuple(device_id for device_id, _ in inputs) != DEVICE_IDS:
            raise ValueError("plant inputs do not match the device inventory")
        ports = []
        events = []
        for previous, (device_id, result) in zip(state.ports, inputs, strict=True):
            health = ComponentStatus.UNAVAILABLE if result is None else result.health.status
            readings = (
                ()
                if result is None
                else tuple(
                    PlantValue(
                        value.logical_id,
                        float(value.value),
                        value.unit,
                        ValueStatus.AVAILABLE,
                        value.quality,
                        at,
                        "Reported device test input",
                        "plant.runtime",
                    )
                    for value in result.telemetry
                    if isinstance(value.value, (int, float)) and not isinstance(value.value, bool)
                )
            )
            port = PlantPort(device_id, at, state.sequence + 1, health, readings, previous.outputs)
            port = replace(port, ledger=_requests(port))
            ports.append(_st_port(port, result))
            if health != previous.health:
                events.append(
                    DeviceEvent(
                        f"plant.{device_id}.health",
                        "plant_port",
                        f"{device_id}: {health.value}",
                        at,
                        "plant",
                        EvidenceClassification.PROVISIONAL,
                    )
                )
        paths = tuple(
            _prepare_pv(item, dict(inputs)[item.device_id], at, configuration.initial_at)
            for item in configuration.pv
        )
        if paths:
            ports = [_pv_port(port, paths) for port in ports]
        current = replace(
            state, at=at, sequence=state.sequence + 1, ports=tuple(ports), pv_paths=paths
        )
        if configuration.battery is not None:
            current = _prepare_battery(current, inputs)
        current = _balance(current)
        if configuration.battery is not None:
            current = _finish_battery(current)
        if paths:
            current = _finish_pv(current)
            events.extend(_pv_events(state, current))
        events.extend(_bus_events(state, current))
        outputs = (
            current.bus
            + current.pv
            + current.bess
            + current.load
            + current.converter
            + current.thermal
        )
        current = replace(
            current,
            ports=tuple(
                replace(port, outputs=outputs + _st_output(port)) for port in current.ports
            ),
        )
        return PlantStepResult(current, tuple(events))

    def validate(
        self, state: PlantState, at: datetime, sequence: int, *, previous: PlantState | None = None
    ) -> None:
        if (
            state.configuration != self.configuration
            or state.at != at
            or state.sequence != sequence
        ):
            raise ValueError("plant snapshot does not match the frame")
        if tuple(port.device_id for port in state.ports) != DEVICE_IDS:
            raise ValueError("plant snapshot inventory is invalid")
        if any(port.at != at or port.sequence != sequence for port in state.ports):
            raise ValueError("plant port time does not match the frame")
        groups: tuple[tuple[PlantValue, ...], ...] = (
            state.bus,
            state.pv,
            state.bess,
            state.load,
            state.converter,
            state.thermal,
            state.ledger,
        )
        if any(not group for group in groups) or not isinstance(state.ports, tuple):
            raise ValueError("plant summaries or ports are incomplete")
        capabilities = {item.capability_id for item in self.configuration.capabilities}
        for port in state.ports:
            if not isinstance(port.health, ComponentStatus):
                raise ValueError("plant port health is invalid")
            groups += (port.readings, port.outputs, port.ledger)
        for group in groups:
            if not isinstance(group, tuple) or any(
                not isinstance(value, PlantValue) or value.capability_id not in capabilities
                for value in group
            ):
                raise ValueError("plant values do not match the contract")
        for port in state.ports:
            if port.device_id.startswith("st-"):
                actual = tuple(value for value in port.outputs if value.name == "AC power supplied")
                if actual != _st_output(port):
                    raise ValueError("Stabiliti AC output does not match bus use")
        _validate_balance(state)
        _validate_pv(state)
        _validate_battery(state, previous)


def _summary(capability: str, fields: tuple[tuple[str, str], ...]) -> tuple[PlantValue, ...]:
    return tuple(
        PlantValue(
            name,
            None,
            unit,
            ValueStatus.NOT_IMPLEMENTED,
            None,
            None,
            "This physical calculation is not available yet",
            f"plant.{capability}",
        )
        for name, unit in fields
    )


def _value(name: str, value: float, unit: str, at: datetime) -> PlantValue:
    capability = "plant.bus" if name in {field[0] for field in BUS_FIELDS} else "plant.balance"
    return PlantValue(
        name,
        value,
        unit,
        ValueStatus.AVAILABLE,
        DataQuality.GOOD,
        at,
        "Calculated using simulation assumptions",
        capability,
    )


def _missing(name: str, unit: str, reason: str, quality: DataQuality | None = None) -> PlantValue:
    capability = "plant.bus" if name in {field[0] for field in BUS_FIELDS} else "plant.balance"
    return PlantValue(name, None, unit, ValueStatus.MISSING, quality, None, reason, capability)


def _number(values: tuple[PlantValue, ...], name: str) -> float:
    matches = tuple(value for value in values if value.name == name)
    if len(matches) != 1 or matches[0].value is None:
        raise ValueError(f"plant reading is missing or repeated: {name}")
    return matches[0].value


def _empty_port(device_id: str) -> tuple[PlantValue, ...]:
    reason = (
        "Battery transfer is counted at the battery converter"
        if device_id == "ba1-1"
        else "Waiting for power readings"
    )
    return tuple(_missing(name, unit, reason) for name, unit in PORT_FIELDS)


def _input(port: PlantPort, point: str, name: str, sign: float) -> PlantValue:
    matches = tuple(value for value in port.readings if value.name == point)
    if len(matches) > 1:
        raise ValueError(f"power reading is repeated: {point}")
    if not matches:
        return _missing(name, "kW", "No power reading reported")
    reading = matches[0]
    if reading.unit != "kW":
        raise ValueError(f"power reading must use kW: {point}")
    if port.health is ComponentStatus.UNAVAILABLE:
        return _missing(name, "kW", "Device is unavailable", DataQuality.COMM_ERROR)
    if reading.status is not ValueStatus.AVAILABLE or reading.quality is not DataQuality.GOOD:
        return _missing(
            name, "kW", "Power reading excluded because its quality is not good", reading.quality
        )
    if reading.value is None:
        raise ValueError("available power reading has no value")
    if port.device_id != "cnvtr-1" and reading.value < 0:
        return _missing(
            name, "kW", "Power direction is outside this simulation", DataQuality.INVALID
        )
    return _value(name, reading.value * sign, "kW", port.at)


def _requests(port: PlantPort) -> tuple[PlantValue, ...]:
    if port.device_id == "ba1-1":
        return _empty_port(port.device_id)
    points = {
        "cnvtr-1": ("cnvtr1.test.requested.power", "cnvtr1.test.accepted.power", 1.0),
        "cnvtr-2": ("cnvtr2.test.requested.power", "cnvtr2.test.accepted.power", 1.0),
        "dc600": ("dc600.demand.power", "dc600.accepted.power", -1.0),
        "st-4": ("st-4.bus.load", "st-4.bus.load", -1.0),
        "st-5": ("st-5.bus.load", "st-5.bus.load", -1.0),
        "st-6": ("st-6.bus.load", "st-6.bus.load", -1.0),
    }
    requested, accepted, sign = points[port.device_id]
    return (
        _input(port, requested, "Requested power", sign),
        _input(port, accepted, "Accepted power", sign),
        *(
            _missing(name, unit, "Waiting for a complete power balance")
            for name, unit in PORT_FIELDS[2:]
        ),
    )


def _st_port(port: PlantPort, result: DeviceStepResult[DeviceState] | None) -> PlantPort:
    if not port.device_id.startswith("st-") or result is None:
        return port
    readings = tuple(
        _device_read(result, f"{port.device_id}.{point}", unit, port.at)
        for point, unit in (
            ("bus.load", "kW"),
            ("available.power", "kW"),
            ("power.limit", "kW"),
            ("available", "boolean"),
        )
    )
    bad = next((item for item in readings if item.value is None), None)
    if bad is not None:
        accepted = _missing("Accepted power", "kW", bad.reason, bad.quality)
    else:
        values = tuple(float(item.value) for item in readings if item.value is not None)
        online = values[3] == 1 and result.health.lifecycle is DeviceLifecycle.ACTIVE
        demand = min(values[:3]) if online else 0.0
        accepted = _value("Accepted power", -demand, "kW", port.at)
    return replace(port, ledger=(port.ledger[0], accepted, *port.ledger[2:]))


def _st_output(port: PlantPort | None) -> tuple[PlantValue, ...]:
    if port is None:
        return (_missing("AC power supplied", "kW", "Waiting for power readings"),)
    if not port.device_id.startswith("st-"):
        return ()
    used = next(value for value in port.ledger if value.name == "Power used")
    return (
        replace(
            used,
            name="AC power supplied",
            reason="Ideal conversion from DC bus power used"
            if used.value is not None
            else used.reason,
        ),
    )


def _missing_ledger(reason: str, excluded: int, at: datetime) -> tuple[PlantValue, ...]:
    return tuple(
        _value(name, float(excluded), unit, at)
        if name == "Excluded inputs"
        else _missing(name, unit, reason)
        for name, unit in LEDGER_FIELDS
    )


def _balance(state: PlantState) -> PlantState:
    active = tuple(port for port in state.ports if port.device_id != "ba1-1")
    excluded = sum(any(value.value is None for value in port.ledger[:2]) for port in active)
    if excluded:
        reason = "Bus calculation paused while power readings are missing or excluded"
        bus = tuple(
            _value(name, _number(state.bus, name) + 1, unit, state.at)
            if name == "Steps without calculation"
            else _missing(name, unit, reason)
            if name == "Power difference"
            else replace(value, quality=DataQuality.STALE, reason=reason)
            for (name, unit), value in zip(BUS_FIELDS, state.bus, strict=True)
        )
        return replace(
            state,
            bus=bus,
            balance=ValueStatus.MISSING,
            ledger=_missing_ledger(reason, excluded, state.at),
        )

    accepted = tuple(_bus_power(state, port) for port in active)
    supply = fsum(max(value, 0.0) for value in accepted)
    demand = fsum(max(-value, 0.0) for value in accepted)
    config = state.configuration
    result = step_bus(
        energy_kwh=_number(state.bus, "Bus energy"),
        supply_kw=supply,
        demand_kw=demand,
        seconds=config.step_seconds,
        config=config.bus,
    )
    supply_ratio = result.supplied_kw / supply if supply > 0 else 0.0
    load_ratio = result.served_kw / demand if demand > 0 else 0.0
    ports = []
    for port in state.ports:
        if port.device_id == "ba1-1":
            ports.append(port)
            continue
        request = _number(port.ledger, "Requested power")
        power = _number(port.ledger, "Accepted power")
        physical = _bus_power(state, port)
        supplied = max(physical, 0.0) * supply_ratio
        served = max(-physical, 0.0) * load_ratio
        port_amounts = (
            request,
            power,
            supplied,
            served,
            supplied * config.bus.loss_ratio,
            max(physical, 0.0) - supplied,
            max(-request - served, 0.0),
            (supplied - served) * config.step_seconds / 3600.0,
        )
        ports.append(
            replace(
                port,
                ledger=tuple(
                    _value(name, value, unit, state.at)
                    for (name, unit), value in zip(PORT_FIELDS, port_amounts, strict=True)
                ),
            )
        )
    current = tuple(port for port in ports if port.device_id != "ba1-1")
    requested_load = fsum(max(-_number(port.ledger, "Requested power"), 0.0) for port in current)
    unmet = fsum(_number(port.ledger, "Power not supplied") for port in current)
    amounts = (
        supply,
        requested_load,
        demand,
        result.supplied_kw,
        result.served_kw,
        result.loss_kw,
        result.stored_kw,
        result.stored_kw * config.step_seconds / 3600.0,
        result.unused_kw,
        unmet,
        result.residual_kw,
        0.0,
    )
    bus_amounts = (
        result.voltage_v,
        result.energy_kwh,
        result.net_kw,
        _number(state.bus, "Steps without calculation"),
    )
    return replace(
        state,
        ports=tuple(ports),
        balance=ValueStatus.AVAILABLE,
        bus=tuple(
            _value(name, value, unit, state.at)
            for (name, unit), value in zip(BUS_FIELDS, bus_amounts, strict=True)
        ),
        ledger=tuple(
            _value(name, value, unit, state.at)
            for (name, unit), value in zip(LEDGER_FIELDS, amounts, strict=True)
        ),
    )


def _bus_events(previous: PlantState, current: PlantState) -> tuple[DeviceEvent, ...]:
    messages = []
    if previous.balance != current.balance:
        messages.append(
            (
                "balance",
                "Bus calculation resumed"
                if current.balance is ValueStatus.AVAILABLE
                else "Bus calculation paused: power readings are missing or excluded",
            )
        )
    if current.balance is ValueStatus.AVAILABLE:
        for name, key, message in (
            ("Unused supply", "unused", "Simulation limit reached: some supply is unused"),
            ("Power not supplied", "unmet", "Some requested power could not be supplied"),
        ):
            before = (
                previous.balance is ValueStatus.AVAILABLE
                and _number(previous.ledger, name) > current.configuration.bus.tolerance_kw
            )
            after = _number(current.ledger, name) > current.configuration.bus.tolerance_kw
            if before != after:
                messages.append((key, message if after else "Power limit condition cleared"))
    return tuple(
        DeviceEvent(
            f"plant.bus.{key}",
            "plant_bus",
            message,
            current.at,
            "plant",
            EvidenceClassification.PROVISIONAL,
        )
        for key, message in messages
    )


def _validate_balance(state: PlantState) -> None:
    if state.configuration.battery is not None:
        state = replace(state, ledger=state.ledger[: len(LEDGER_FIELDS)])
    for fields, values in ((BUS_FIELDS, state.bus), (LEDGER_FIELDS, state.ledger)):
        if tuple((value.name, value.unit) for value in values) != fields:
            raise ValueError("bus fields or units do not match the model")
    config = state.configuration.bus
    energy = _number(state.bus, "Bus energy")
    voltage = _number(state.bus, "Bus voltage")
    if not 0 <= energy <= config.max_energy_kwh or not 0 <= voltage <= config.max_voltage_v:
        raise ValueError("bus state is outside the model range")
    if not isclose(
        energy, config.capacitance_f * voltage**2 / 7_200_000.0, rel_tol=1e-12, abs_tol=1e-12
    ):
        raise ValueError("bus voltage does not match stored energy")
    skipped = _number(state.bus, "Steps without calculation")
    if skipped != int(skipped) or not 0 <= skipped <= state.sequence:
        raise ValueError("bus skipped-step count is invalid")
    for port in state.ports:
        if tuple((value.name, value.unit) for value in port.ledger) != PORT_FIELDS:
            raise ValueError("device power ledger is incomplete")
        if port.device_id.startswith("st-") and any(
            value.value is not None and value.value > 0 for value in port.ledger[:3]
        ):
            raise ValueError("Stabiliti power must flow from the bus to AC loads")
        if port.device_id == "ba1-1" and any(value.value is not None for value in port.ledger):
            raise ValueError("battery transfer must only be counted at its converter")
    if any(value.at is not None and value.at > state.at for value in state.bus):
        raise ValueError("bus reading is ahead of the frame")
    if state.balance is ValueStatus.MISSING:
        if any(value.value is not None for value in state.ledger[:-1]):
            raise ValueError("incomplete balance cannot contain calculated totals")
        excluded = sum(
            any(value.value is None for value in port.ledger[:2])
            for port in state.ports
            if port.device_id != "ba1-1"
        )
        if excluded == 0 or _number(state.ledger, "Excluded inputs") != excluded:
            raise ValueError("excluded input count does not match the device ports")
        if state.sequence > 0 and any(
            value.quality is not DataQuality.STALE for value in state.bus[:2]
        ):
            raise ValueError("paused bus values must be marked stale")
        return
    if state.balance is not ValueStatus.AVAILABLE:
        raise ValueError("bus balance status is invalid")
    if any(
        value.quality is not DataQuality.GOOD or value.at != state.at
        for value in state.bus + state.ledger
    ):
        raise ValueError("calculated bus values must belong to the current frame")
    ports = tuple(port for port in state.ports if port.device_id != "ba1-1")
    if _number(state.ledger, "Excluded inputs") != 0 or any(
        value.quality is not DataQuality.GOOD or value.at != state.at
        for port in ports
        for value in port.ledger
    ):
        raise ValueError("complete balance requires current device power")
    for name in (
        "Power supplied",
        "Power used",
        "Modeled loss",
        "Unused supply",
        "Power not supplied",
    ):
        total = fsum(_number(port.ledger, name) for port in ports)
        if abs(total - _number(state.ledger, name)) > config.tolerance_kw:
            raise ValueError("device power does not match the bus total")
    supplied = _number(state.ledger, "Power supplied")
    served = _number(state.ledger, "Power used")
    loss = _number(state.ledger, "Modeled loss")
    stored = _number(state.ledger, "Stored power")
    if (
        abs(
            _number(state.ledger, "Requested load")
            - served
            - _number(state.ledger, "Power not supplied")
        )
        > config.tolerance_kw
    ):
        raise ValueError("unmet demand is missing from the balance")
    residual = supplied - served - loss - stored
    if (
        abs(residual) > config.tolerance_kw
        or abs(residual - _number(state.ledger, "Balance error")) > config.tolerance_kw
    ):
        raise ValueError("bus balance exceeds the numerical tolerance")
    if (
        abs(
            _number(state.ledger, "Offered supply")
            - supplied
            - _number(state.ledger, "Unused supply")
        )
        > config.tolerance_kw
    ):
        raise ValueError("unused supply is missing from the balance")
    if not isclose(
        _number(state.ledger, "Energy change"),
        stored * state.configuration.step_seconds / 3600.0,
        abs_tol=1e-12,
    ):
        raise ValueError("bus energy change does not match stored power")


def _solar(
    name: str, value: float | None, at: datetime, reason: str, quality: DataQuality | None = None
) -> PlantValue:
    if value is None:
        return replace(_missing(name, "kW", reason, quality), capability_id="plant.pv")
    return replace(_value(name, value, "kW", at), capability_id="plant.pv", reason=reason)


def _initial_pv(config: PvConfig, at: datetime) -> PvPath:
    reason = "Waiting for device readings"
    return PvPath(
        config.device_id,
        config.destination,
        ComponentStatus.STOPPED,
        reason,
        tuple(
            _solar(name, pv_available(config, 0) if name == "Available solar" else None, at, reason)
            for name, _ in PV_FIELDS
        ),
    )


def _device_read(
    result: DeviceStepResult[DeviceState], point: str, unit: str, at: datetime
) -> PlantValue:
    matches = tuple(item for item in result.telemetry if item.logical_id == point)
    if len(matches) != 1:
        return _solar(point, None, at, "Required device reading is missing or repeated")
    item = matches[0]
    if item.quality is not DataQuality.GOOD:
        return _solar(point, None, at, "Device reading is not usable", item.quality)
    valid = (
        type(item.value) is bool
        if unit == "boolean"
        else (isinstance(item.value, (int, float)) and not isinstance(item.value, bool))
    )
    if item.unit != unit or not valid:
        return _solar(
            point, None, at, "Device reading has the wrong type or unit", DataQuality.INVALID
        )
    value = float(item.value)
    if not isfinite(value) or value < 0:
        return _solar(
            point, None, at, "Device power is outside this simulation", DataQuality.INVALID
        )
    return _solar(point, value, at, "Current device reading")


def _prepare_pv(
    config: PvConfig, result: DeviceStepResult[DeviceState] | None, at: datetime, start: datetime
) -> PvPath:
    available = pv_available(config, (at - start).total_seconds())
    initial = _initial_pv(config, at)
    values = list(initial.values)
    values[0] = _solar("Available solar", available, at, "Saved sunlight profile")
    if result is None:
        return replace(initial, health=ComponentStatus.UNAVAILABLE, values=tuple(values))
    points = (
        ("cnvtr2.test.requested.power", "kW"),
        ("cnvtr2.test.accepted.power", "kW"),
        ("cnvtr2.pv.online", "boolean"),
        ("cnvtr2.available.power", "kW"),
    )
    readings = tuple(_device_read(result, point, unit, at) for point, unit in points)
    bad = tuple(item for item in readings if item.value is None)
    if bad:
        reason = bad[0].reason
        values[1:] = [_solar(name, None, at, reason, bad[0].quality) for name, _ in PV_FIELDS[1:]]
        return replace(initial, health=result.health.status, reason=reason, values=tuple(values))
    numbers = tuple(float(item.value) for item in readings if item.value is not None)
    online = numbers[2] == 1 and result.health.lifecycle is DeviceLifecycle.ACTIVE
    request, accepted = numbers[:2]
    limit = min(config.limit_kw, numbers[3]) if online else 0.0
    offered = pv_limit(
        available_kw=available, accepted_kw=accepted, limit_kw=limit, efficiency=config.efficiency
    )
    reason = (
        "Device is disabled or solar is unavailable"
        if not online
        else "No sunlight"
        if available == 0
        else "Output is limited by the device or accepted request"
        if offered < available * config.efficiency
        else "Available solar is being used"
    )
    amounts = (available, request, accepted, limit, offered, None, None, None)
    return PvPath(
        config.device_id,
        config.destination,
        result.health.status,
        reason,
        tuple(
            _solar(name, value, at, reason)
            for (name, _), value in zip(PV_FIELDS, amounts, strict=True)
        ),
    )


def _pv_port(port: PlantPort, paths: tuple[PvPath, ...]) -> PlantPort:
    if port.device_id not in PV_IDS:
        return port
    path = next(item for item in paths if item.device_id == port.device_id)
    offered = path.values[4]
    if offered.value is None:
        accepted = replace(_missing("Accepted power", "kW", path.reason, offered.quality), at=None)
    else:
        return port
    return replace(port, ledger=(port.ledger[0], accepted, *port.ledger[2:]))


def _bus_power(state: PlantState, port: PlantPort) -> float:
    if state.configuration.battery is not None and port.device_id == "cnvtr-1":
        return _number(state.bess, "Offered power")
    if state.pv_paths and port.device_id == "cnvtr-2":
        path = next(item for item in state.pv_paths if item.device_id == port.device_id)
        return _number(path.values, "Offered power")
    return _number(port.ledger, "Accepted power")


def _finish_pv(state: PlantState) -> PlantState:
    paths = []
    for path, config in zip(state.pv_paths, state.configuration.pv, strict=True):
        if path.values[4].value is None:
            paths.append(path)
            continue
        offered = _number(path.values, "Offered power")
        if state.balance is not ValueStatus.AVAILABLE:
            reason = "Waiting for a complete DC bus balance"
            values = path.values[:5] + tuple(
                _solar(name, None, state.at, reason) for name, _ in PV_FIELDS[5:]
            )
            paths.append(replace(path, reason=reason, values=values))
            continue
        port = next(item for item in state.ports if item.device_id == path.device_id)
        actual = min(offered, _number(port.ledger, "Power supplied"))
        power = pv_power(
            available_kw=_number(path.values, "Available solar"),
            actual_kw=actual,
            efficiency=config.efficiency,
        )
        reason = "DC bus cannot accept all offered solar" if actual < offered else path.reason
        values = path.values[:5] + tuple(
            _solar(name, value, state.at, reason)
            for (name, _), value in zip(
                PV_FIELDS[5:], (power.actual_kw, power.loss_kw, power.curtailed_kw), strict=True
            )
        )
        paths.append(replace(path, reason=reason, values=values))
    result = tuple(paths)
    return replace(state, pv_paths=result, pv=_pv_total(result))


def _pv_total(paths: tuple[PvPath, ...]) -> tuple[PlantValue, ...]:
    return (*paths[0].values, replace(paths[0].values[5], name="Delivered to DC bus"))


def _pv_events(previous: PlantState, current: PlantState) -> tuple[DeviceEvent, ...]:
    return tuple(
        DeviceEvent(
            f"plant.pv.{path.device_id}",
            "plant_pv",
            path.reason,
            current.at,
            path.device_id,
            EvidenceClassification.PROVISIONAL,
        )
        for before, path in zip(previous.pv_paths, current.pv_paths, strict=True)
        if before.reason != path.reason
    )


def _validate_pv(state: PlantState) -> None:
    configs = state.configuration.pv
    if tuple(path.device_id for path in state.pv_paths) != tuple(
        item.device_id for item in configs
    ):
        raise ValueError("solar paths do not match the configuration")
    tolerance = state.configuration.bus.tolerance_kw
    for path, config in zip(state.pv_paths, configs, strict=True):
        if (
            path.destination != config.destination
            or not isinstance(path.health, ComponentStatus)
            or not path.reason
        ):
            raise ValueError("solar path identity or status is invalid")
        if tuple((item.name, item.unit) for item in path.values) != PV_FIELDS:
            raise ValueError("solar fields do not match the model")
        for item in path.values:
            if item.capability_id != "plant.pv" or (
                item.value is not None
                and (item.value < 0 or item.quality is not DataQuality.GOOD or item.at != state.at)
            ):
                raise ValueError("solar values must be non-negative current calculations")
        available = _number(path.values, "Available solar")
        if available != pv_available(
            config, (state.at - state.configuration.initial_at).total_seconds()
        ):
            raise ValueError("solar resource does not match the saved profile")
        if path.values[4].value is None:
            if any(item.value is not None for item in path.values[5:]):
                raise ValueError("missing solar limits cannot contain delivered power")
            continue
        offered = _number(path.values, "Offered power")
        expected = pv_limit(
            available_kw=available,
            accepted_kw=_number(path.values, "Accepted power"),
            limit_kw=_number(path.values, "Output limit"),
            efficiency=config.efficiency,
        )
        if (
            abs(offered - expected) > tolerance
            or _number(path.values, "Output limit") > config.limit_kw
        ):
            raise ValueError("solar output exceeds the active limit")
        if path.values[5].value is None:
            if path.destination != "dc" or state.balance is ValueStatus.AVAILABLE:
                raise ValueError("solar output is unexpectedly missing")
            continue
        actual = _number(path.values, "Power delivered")
        if actual > offered + tolerance:
            raise ValueError("solar output exceeds its offer")
        power = pv_power(available_kw=available, actual_kw=actual, efficiency=config.efficiency)
        if (
            abs(power.loss_kw - _number(path.values, "Conversion loss")) > tolerance
            or abs(power.curtailed_kw - _number(path.values, "Unused solar")) > tolerance
        ):
            raise ValueError("solar conversion balance is invalid")
        if path.destination == "dc":
            port = next(item for item in state.ports if item.device_id == path.device_id)
            if abs(actual - _number(port.ledger, "Power supplied")) > tolerance:
                raise ValueError("solar output does not match the DC bus")
    if configs and state.pv != _pv_total(state.pv_paths):
        raise ValueError("solar totals do not match the individual paths")


def _battery_value(
    name: str,
    value: float | None,
    unit: str,
    at: datetime,
    reason: str,
    quality: DataQuality | None = None,
) -> PlantValue:
    result = (
        _missing(name, unit, reason, quality) if value is None else _value(name, value, unit, at)
    )
    return replace(result, capability_id="plant.bess", reason=reason)


def _initial_battery(config: BatteryConfig, at: datetime) -> tuple[PlantValue, ...]:
    return tuple(
        _battery_value(
            name,
            config.initial_energy_kwh
            if name == "Battery energy"
            else config.initial_energy_kwh / config.capacity_kwh
            if name == "Battery SOC"
            else None,
            unit,
            at,
            "Selected battery setup"
            if name in {"Battery energy", "Battery SOC"}
            else "Waiting for device readings",
        )
        for name, unit in BATTERY_FIELDS
    )


def _battery_limit(
    state: PlantState,
    battery: PlantPort,
    converter: PlantPort,
    *,
    charge: bool,
) -> PlantValue:
    config = state.configuration.battery
    if config is None:
        raise ValueError("battery setup is missing")
    direction = "charge" if charge else "discharge"
    name = "Charge limit" if charge else "Discharge limit"
    readings = tuple(
        _input(port, point, name, 1.0)
        for port, point in (
            (battery, f"bess.cluster.limit.{direction}.power"),
            (converter, f"cnvtr1.test.{direction}.limit"),
            (converter, "cnvtr1.available.power"),
            (converter, "cnvtr1.test.power.limit"),
        )
    )
    for reading in readings:
        if reading.value is None or reading.value < 0:
            return _battery_value(
                name,
                None,
                "kW",
                state.at,
                f"{direction.capitalize()} limit is unavailable: {reading.reason}"
                if reading.value is None
                else "Power limit cannot be negative",
                reading.quality if reading.value is None else DataQuality.INVALID,
            )
    charge_kw, discharge_kw = battery_limits(
        config, _number(state.bess, "Battery energy"), state.configuration.step_seconds
    )
    bounds = (
        (_number(readings[:1], name), "Battery limit"),
        (_number(readings[1:2], name), "Converter limit"),
        (_number(readings[2:3], name), "Converter available power"),
        (_number(readings[3:], name), "Converter power cap"),
        (config.charge_limit_kw if charge else config.discharge_limit_kw, "Selected power limit"),
        (charge_kw if charge else discharge_kw, "Battery full" if charge else "Battery empty"),
    )
    limit, reason = min(bounds, key=lambda item: item[0])
    return _battery_value(name, limit, "kW", state.at, reason)


def _prepare_battery(state: PlantState, inputs: PlantInputs) -> PlantState:
    battery = next(port for port in state.ports if port.device_id == "ba1-1")
    converter = next(port for port in state.ports if port.device_id == "cnvtr-1")
    request, accepted = converter.ledger[:2]
    charge = _battery_limit(state, battery, converter, charge=True)
    discharge = _battery_limit(state, battery, converter, charge=False)
    results = dict(inputs)
    devices = (results["ba1-1"], results["cnvtr-1"])
    bad = next((value for value in (request, accepted) if value.value is None), None)
    unavailable = any(
        result is None
        or result.health.lifecycle in {DeviceLifecycle.OFFLINE, DeviceLifecycle.FAULTED}
        for result in devices
    )
    inactive = any(
        result is not None and result.health.lifecycle is not DeviceLifecycle.ACTIVE
        for result in devices
    )
    reason = "Requested power is available"
    power: float | None = None
    quality = None
    if unavailable:
        reason = "Battery or converter is unavailable"
        quality = (
            DataQuality.COMM_ERROR
            if any(
                result is None or result.health.quality is DataQuality.COMM_ERROR
                for result in devices
            )
            else DataQuality.INVALID
        )
    elif bad is not None:
        reason, quality = bad.reason, bad.quality
    elif inactive:
        power, reason = 0.0, "Battery or converter is inactive"
        charge = replace(
            charge,
            value=0.0,
            status=ValueStatus.AVAILABLE,
            quality=DataQuality.GOOD,
            at=state.at,
            reason=reason,
        )
        discharge = replace(
            discharge,
            value=0.0,
            status=ValueStatus.AVAILABLE,
            quality=DataQuality.GOOD,
            at=state.at,
            reason=reason,
        )
    else:
        target = _number((accepted,), "Accepted power")
        limit = discharge if target > 0 else charge if target < 0 else None
        if limit is not None and limit.value is None:
            reason, quality = limit.reason, limit.quality
        else:
            power = (
                0.0
                if limit is None
                else min(abs(target), _number((limit,), limit.name)) * (1 if target > 0 else -1)
            )
            if power != target and limit is not None:
                reason = limit.reason
            elif target != _number((request,), "Requested power"):
                reason = "Converter request is limited"
            elif power == 0:
                reason = "Battery is idle"
            if any(
                result is not None
                and any("temperature_derate" in fault.fault_id for fault in result.faults)
                for result in devices
            ):
                reason = "Temperature limit" if power == target else reason
    offered = _battery_value("Offered power", power, "kW", state.at, reason, quality)
    values = (
        *state.bess[:2],
        replace(request, capability_id="plant.bess"),
        replace(accepted, capability_id="plant.bess"),
        charge,
        discharge,
        offered,
        *(
            _battery_value(name, None, unit, state.at, reason, quality)
            for name, unit in BATTERY_FIELDS[7:]
        ),
    )
    if power is None:
        # Keep reported readings; exclude only the calculated contribution.
        converter = replace(
            converter,
            ledger=(
                request,
                _missing("Accepted power", "kW", reason, quality),
                *converter.ledger[2:],
            ),
        )
    return replace(
        state,
        bess=values,
        ports=tuple(converter if port.device_id == "cnvtr-1" else port for port in state.ports),
    )


def _finish_battery(state: PlantState) -> PlantState:
    config = state.configuration.battery
    if config is None:
        raise ValueError("battery setup is missing")
    offered = state.bess[6]
    if state.balance is not ValueStatus.AVAILABLE:
        reason = (
            offered.reason if offered.value is None else "Waiting for a complete DC bus balance"
        )
        held = tuple(
            replace(value, quality=DataQuality.STALE, reason=reason) for value in state.bess[:2]
        )
        unknown = tuple(
            _battery_value(name, None, unit, state.at, reason, offered.quality)
            for name, unit in BATTERY_FIELDS[7:]
        )
        ledger = tuple(
            _battery_value(name, None, unit, state.at, reason, offered.quality)
            for name, unit in BATTERY_LEDGER_FIELDS
        )
        return replace(
            state, bess=(*held, *state.bess[2:7], *unknown), ledger=(*state.ledger, *ledger)
        )
    port = next(item for item in state.ports if item.device_id == "cnvtr-1")
    actual = _number(port.ledger, "Power supplied") - _number(port.ledger, "Power used")
    before = _number(state.bess, "Battery energy")
    result = battery_power(config, before, actual, state.configuration.step_seconds)
    change = result.energy_kwh - before
    residual = actual + result.loss_kw + change / (state.configuration.step_seconds / 3600)
    request = _number(state.bess, "Requested power")
    served = max(actual if request > 0 else -actual, 0.0) if request != 0 else 0.0
    unmet = max(abs(request) - served, 0.0)
    reason = "DC bus limits battery transfer" if actual != offered.value else offered.reason
    amounts = (result.energy_kwh, result.energy_kwh / config.capacity_kwh)
    energy = tuple(
        _battery_value(name, value, unit, state.at, reason)
        for (name, unit), value in zip(BATTERY_FIELDS[:2], amounts, strict=True)
    )
    values = (actual, result.loss_kw, change, unmet, residual)
    finished = tuple(
        _battery_value(name, value, unit, state.at, reason)
        for (name, unit), value in zip(BATTERY_FIELDS[7:], values, strict=True)
    )
    ledger = tuple(
        _battery_value(name, value, unit, state.at, reason)
        for (name, unit), value in zip(
            BATTERY_LEDGER_FIELDS, (change, result.loss_kw, residual), strict=True
        )
    )
    return replace(
        state, bess=(*energy, *state.bess[2:7], *finished), ledger=(*state.ledger, *ledger)
    )


def _validate_battery(state: PlantState, previous: PlantState | None) -> None:
    config = state.configuration.battery
    if config is None:
        return
    if tuple((value.name, value.unit) for value in state.bess) != BATTERY_FIELDS:
        raise ValueError("battery fields or units do not match the model")
    ledger = state.ledger[len(LEDGER_FIELDS) :]
    if tuple((value.name, value.unit) for value in ledger) != BATTERY_LEDGER_FIELDS:
        raise ValueError("battery ledger is incomplete")
    energy = _number(state.bess, "Battery energy")
    soc = _number(state.bess, "Battery SOC")
    if not 0 <= energy <= config.capacity_kwh or soc != energy / config.capacity_kwh:
        raise ValueError("battery energy or level is inconsistent")
    if state.sequence == 0 and energy != config.initial_energy_kwh:
        raise ValueError("battery initial energy does not match its setup")
    for value in (*state.bess, *ledger):
        if value.capability_id != "plant.bess" or (value.at is not None and value.at > state.at):
            raise ValueError("battery reading identity or time is invalid")
    if state.balance is not ValueStatus.AVAILABLE:
        if any(value.value is not None for value in (*state.bess[7:], *ledger)):
            raise ValueError("paused battery cannot contain calculated transfer")
        if state.sequence > 0 and any(
            value.quality is not DataQuality.STALE for value in state.bess[:2]
        ):
            raise ValueError("held battery energy must be stale")
        if previous is not None and energy != _number(previous.bess, "Battery energy"):
            raise ValueError("paused battery energy changed")
        return
    for value in (*state.bess[:4], *state.bess[6:], *ledger):
        if value.value is None or value.quality is not DataQuality.GOOD or value.at != state.at:
            raise ValueError("calculated battery values must belong to the current frame")
    actual = _number(state.bess, "Actual power")
    offered = _number(state.bess, "Offered power")
    accepted = _number(state.bess, "Accepted power")
    if (
        abs(actual) > abs(offered) + 1e-12
        or actual * offered < 0
        or abs(offered) > abs(accepted) + 1e-12
        or offered * accepted < 0
    ):
        raise ValueError("battery transfer exceeds offered or accepted power")
    if offered != 0:
        limit = _number(state.bess, "Discharge limit" if offered > 0 else "Charge limit")
        if abs(offered) > limit:
            raise ValueError("battery power exceeds its current limit")
    port = next(item for item in state.ports if item.device_id == "cnvtr-1")
    if actual != _number(port.ledger, "Power supplied") - _number(port.ledger, "Power used"):
        raise ValueError("battery transfer differs from the converter")
    change = _number(state.bess, "Energy change")
    loss = _number(state.bess, "Energy loss rate")
    hours = state.configuration.step_seconds / 3600
    tolerance = state.configuration.bus.tolerance_kw + 4 * ulp(config.capacity_kwh) / hours
    residual = actual + loss + change / hours
    if loss < 0 or abs(residual) > tolerance or _number(state.bess, "Balance error") != residual:
        raise ValueError("battery energy balance is invalid")
    if tuple(value.value for value in ledger) != (change, loss, residual):
        raise ValueError("battery loss or energy is missing from the shared ledger")
    if previous is not None:
        if (
            previous.configuration != state.configuration
            or state.sequence != previous.sequence + 1
            or state.at != previous.at + state.configuration.step
        ):
            raise ValueError("battery step does not follow the previous frame")
        before = _number(previous.bess, "Battery energy")
        expected = battery_power(config, before, actual, state.configuration.step_seconds)
        if energy != expected.energy_kwh or loss != expected.loss_kw or change != energy - before:
            raise ValueError("battery energy change does not match actual transfer")

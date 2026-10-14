"""Versioned JSON checkpoints with explicit decoding of each device model."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from skid2.plant.model import PlantConfig, PlantState, PlantStepResult, PlantValue
from skid2.sim.battery import BatteryInputs, BatteryState
from skid2.sim.converter import ConverterInputs, ConverterState
from skid2.sim.dc600 import Dc600Inputs, Dc600State
from skid2.sim.device import (
    DeviceCommand,
    DeviceCommandResult,
    DeviceEvent,
    DeviceFault,
    DeviceHealth,
    DeviceState,
    DeviceStepResult,
    DeviceValue,
)
from skid2.sim.faults import FaultTest
from skid2.sim.runtime import (
    DeviceRuntimeSnapshot,
    DeviceUpdate,
    FrameFailure,
    RuntimeDeviceHealth,
    SimulationSnapshot,
    SimulationTraceEntry,
)
from skid2.sim.stabiliti import StabilitiInputs, StabilitiState

SimulationInputs = BatteryInputs | ConverterInputs | StabilitiInputs | Dc600Inputs
SimulationDeviceState = BatteryState | ConverterState | StabilitiState | Dc600State

DEVICE_TYPES: dict[str, tuple[type[SimulationDeviceState], type[SimulationInputs]]] = {
    "ba1-1": (BatteryState, BatteryInputs),
    "cnvtr-1": (ConverterState, ConverterInputs),
    "cnvtr-2": (ConverterState, ConverterInputs),
    "st-4": (StabilitiState, StabilitiInputs),
    "st-5": (StabilitiState, StabilitiInputs),
    "st-6": (StabilitiState, StabilitiInputs),
    "dc600": (Dc600State, Dc600Inputs),
}


@dataclass(frozen=True, slots=True)
class SimulationActivity:
    sequence: int
    at: datetime
    device_id: str
    category: str
    message: str
    test_id: str | None = None
    test_started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SavedRunInfo:
    saved_at: datetime
    simulation_at: datetime
    sequence: int


@dataclass(frozen=True, slots=True)
class SavedSimulation:
    runtime: SimulationSnapshot
    configuration: PlantConfig
    pending: tuple[tuple[str, SimulationInputs], ...]
    frames: tuple[tuple[DeviceUpdate, ...], ...]
    fault_tests: tuple[FaultTest, ...]
    fault_activity: tuple[SimulationActivity, ...]
    recovery_steps: tuple[tuple[str, int], ...]
    next_test_number: int
    accepted_inputs: tuple[tuple[str, SimulationInputs], ...]
    failure: FrameFailure | None


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _Device(_Document):
    device_id: str
    state: dict[str, Any]
    inputs: dict[str, Any]
    health: RuntimeDeviceHealth
    telemetry: tuple[DeviceValue, ...]
    faults: tuple[DeviceFault, ...]
    events: tuple[DeviceEvent, ...]
    plant_inputs: tuple[PlantValue, ...]

    def decode(self) -> DeviceRuntimeSnapshot:
        return DeviceRuntimeSnapshot(
            self.device_id,
            _typed(DEVICE_TYPES[self.device_id][0], self.state),
            decode_inputs(self.device_id, self.inputs),
            self.health,
            self.telemetry,
            self.faults,
            self.events,
            self.plant_inputs,
        )


class _Result(_Document):
    state: dict[str, Any]
    telemetry: tuple[DeviceValue, ...]
    health: DeviceHealth
    faults: tuple[DeviceFault, ...]
    events: tuple[DeviceEvent, ...]
    command_results: tuple[DeviceCommandResult, ...]

    def decode(self, device_id: str) -> DeviceStepResult[DeviceState]:
        return DeviceStepResult(
            _typed(DEVICE_TYPES[device_id][0], self.state),
            self.telemetry,
            self.health,
            self.faults,
            self.events,
            self.command_results,
        )


class _Trace(_Document):
    sequence: int = Field(ge=1)
    at: AwareDatetime
    dt_seconds: float = Field(gt=0)
    device_id: str
    result: _Result | None
    failure_code: str | None

    def decode(self) -> SimulationTraceEntry:
        return SimulationTraceEntry(
            self.sequence,
            self.at,
            self.dt_seconds,
            self.device_id,
            None if self.result is None else self.result.decode(self.device_id),
            self.failure_code,
        )


class _Update(_Document):
    device_id: str
    inputs: dict[str, Any] | None
    commands: tuple[DeviceCommand, ...]
    fault_tests: tuple[FaultTest, ...]

    def decode(self) -> DeviceUpdate:
        if self.device_id not in DEVICE_TYPES:
            raise ValueError("unknown checkpoint device")
        return DeviceUpdate(
            self.device_id,
            None if self.inputs is None else decode_inputs(self.device_id, self.inputs),
            self.commands,
            self.fault_tests,
        )


class _Runtime(_Document):
    at: AwareDatetime
    sequence: int = Field(ge=0)
    devices: tuple[_Device, ...]
    trace: tuple[_Trace, ...]
    plant: PlantState
    plant_trace: tuple[PlantStepResult, ...]

    def decode(self) -> SimulationSnapshot:
        return SimulationSnapshot(
            self.at,
            self.sequence,
            tuple(item.decode() for item in self.devices),
            tuple(item.decode() for item in self.trace),
            self.plant,
            self.plant_trace,
        )


class _Simulation(_Document):
    runtime: _Runtime
    configuration: PlantConfig
    pending: tuple[tuple[str, dict[str, Any]], ...]
    frames: tuple[tuple[_Update, ...], ...]
    fault_tests: tuple[FaultTest, ...]
    fault_activity: tuple[SimulationActivity, ...]
    recovery_steps: tuple[tuple[str, int], ...]
    next_test_number: int = Field(ge=1)
    accepted_inputs: tuple[tuple[str, dict[str, Any]], ...]
    failure: FrameFailure | None


class _Checkpoint(_Document):
    schema_version: Literal[1]
    compatibility: str
    saved_at: AwareDatetime
    simulation: _Simulation


class _Setup(_Document):
    schema_version: Literal[1]
    compatibility: str
    configuration: PlantConfig
    inputs: dict[str, dict[str, Any]]


def _preserve_numbers[T](value: T, raw: object) -> T:
    """Keep valid JSON number representations used by the existing trace hashes."""
    if type(value) in {int, float} and type(raw) in {int, float}:
        return cast(T, raw)
    if is_dataclass(value) and not isinstance(value, type) and isinstance(raw, dict):
        if not {field.name for field in fields(value)} <= raw.keys():
            raise ValueError("saved document is missing required state fields")
        return replace(
            value,
            **{
                field.name: _preserve_numbers(getattr(value, field.name), raw[field.name])
                for field in fields(value)
            },
        )
    if isinstance(value, tuple) and isinstance(raw, list):
        return cast(
            T,
            tuple(
                _preserve_numbers(item, original) for item, original in zip(value, raw, strict=True)
            ),
        )
    return value


def _typed[T: SimulationInputs | SimulationDeviceState](kind: type[T], values: dict[str, Any]) -> T:
    if set(values) != {field.name for field in fields(kind)}:
        raise ValueError("checkpoint device fields do not match its model")
    return _preserve_numbers(
        TypeAdapter(kind).validate_json(json.dumps(values, allow_nan=False), strict=True), values
    )


def decode_inputs(device_id: str, values: dict[str, Any]) -> SimulationInputs:
    if device_id not in DEVICE_TYPES:
        raise ValueError("unknown checkpoint device")
    return _typed(DEVICE_TYPES[device_id][1], values)


def _pairs(
    values: tuple[tuple[str, dict[str, Any]], ...],
) -> tuple[tuple[str, SimulationInputs], ...]:
    if len(dict(values)) != len(values):
        raise ValueError("duplicate checkpoint inputs")
    return tuple((key, decode_inputs(key, value)) for key, value in values)


def _json_default(value: object) -> str | list[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, frozenset):
        return sorted(str(item) for item in value)
    raise TypeError(f"unsupported checkpoint value: {type(value).__name__}")


def _json_document(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(value, default=_json_default, allow_nan=False))
    return result


def encode_checkpoint(
    saved: SavedSimulation, saved_at: datetime, compatibility: str
) -> dict[str, Any]:
    return _json_document(
        {
            "schema_version": 1,
            "compatibility": compatibility,
            "saved_at": saved_at,
            "simulation": asdict(saved),
        }
    )


def decode_checkpoint(
    value: dict[str, Any], compatibility: str
) -> tuple[SavedSimulation, SavedRunInfo]:
    document = _Checkpoint.model_validate_json(json.dumps(value, allow_nan=False))
    if document.compatibility != compatibility:
        raise ValueError("saved run uses incompatible simulation models or protocols")
    data = document.simulation
    try:
        saved = SavedSimulation(
            data.runtime.decode(),
            data.configuration,
            _pairs(data.pending),
            tuple(tuple(item.decode() for item in frame) for frame in data.frames),
            data.fault_tests,
            data.fault_activity,
            data.recovery_steps,
            data.next_test_number,
            _pairs(data.accepted_inputs),
            data.failure,
        )
    except KeyError as error:
        raise ValueError("unknown checkpoint device") from error
    saved = _preserve_numbers(saved, value["simulation"])
    return saved, SavedRunInfo(document.saved_at, saved.runtime.at, saved.runtime.sequence)


def encode_setup(
    configuration: PlantConfig, inputs: dict[str, SimulationInputs], compatibility: str
) -> dict[str, Any]:
    return _json_document(
        {
            "schema_version": 1,
            "compatibility": compatibility,
            "configuration": asdict(configuration),
            "inputs": {key: asdict(value) for key, value in inputs.items()},
        }
    )


def decode_setup(
    value: dict[str, Any], compatibility: str
) -> tuple[PlantConfig, dict[str, SimulationInputs]]:
    data = _Setup.model_validate_json(json.dumps(value, allow_nan=False))
    if data.compatibility != compatibility:
        raise ValueError("saved settings use incompatible simulation models or protocols")
    return _preserve_numbers(data.configuration, value["configuration"]), {
        key: decode_inputs(key, value) for key, value in data.inputs.items()
    }

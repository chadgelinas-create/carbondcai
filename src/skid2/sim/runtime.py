"""Deterministic in-process orchestration for transport-independent device models."""

from __future__ import annotations

import json
from copy import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING, Protocol, cast

from skid2.contracts import Clock, ComponentStatus
from skid2.sim.device import (
    DeviceCapability,
    DeviceCommand,
    DeviceEvent,
    DeviceFault,
    DeviceHealth,
    DeviceModel,
    DeviceState,
    DeviceStep,
    DeviceStepResult,
    DeviceValue,
)
from skid2.sim.faults import FaultTest, apply_faults

if TYPE_CHECKING:
    from skid2.plant.model import PlantModel, PlantState, PlantStepResult, PlantValue


@dataclass(frozen=True, slots=True)
class FrameFailure:
    sequence: int
    at: datetime
    code: str


class FrameError(RuntimeError):
    def __init__(self, failure: FrameFailure) -> None:
        self.failure = failure
        super().__init__(failure.code)


class RuntimeLifecycle(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    SHUTDOWN = "shutdown"


class RuntimeTransitionError(RuntimeError):
    """The requested runtime lifecycle transition is invalid."""


class DeviceRegistrationError(ValueError):
    """A device registration or frame update is invalid."""


@dataclass(frozen=True, slots=True)
class DeviceRegistration[ConfigurationT, StateT: DeviceState, InputsT]:
    model: DeviceModel[ConfigurationT, StateT, InputsT]
    configuration: ConfigurationT
    initial_inputs: InputsT


@dataclass(frozen=True, slots=True)
class DeviceUpdate:
    device_id: str
    inputs: object | None = None
    commands: tuple[DeviceCommand, ...] = ()
    fault_tests: tuple[FaultTest, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeDeviceHealth:
    device_id: str
    lifecycle: str
    status: ComponentStatus
    model_health: DeviceHealth | None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class SimulationTraceEntry:
    sequence: int
    at: datetime
    dt_seconds: float
    device_id: str
    result: DeviceStepResult[DeviceState] | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class DeviceRuntimeSnapshot:
    device_id: str
    state: DeviceState
    inputs: object
    health: RuntimeDeviceHealth
    telemetry: tuple[DeviceValue, ...]
    faults: tuple[DeviceFault, ...]
    events: tuple[DeviceEvent, ...]
    plant_inputs: tuple[PlantValue, ...] = ()


@dataclass(frozen=True, slots=True)
class SimulationSnapshot:
    at: datetime
    sequence: int
    devices: tuple[DeviceRuntimeSnapshot, ...]
    trace: tuple[SimulationTraceEntry, ...]
    plant: PlantState | None = None
    plant_trace: tuple[PlantStepResult, ...] = ()


class _RuntimeDevice(Protocol):
    @property
    def device_id(self) -> str: ...

    @property
    def capabilities(self) -> tuple[DeviceCapability, ...]: ...

    def reset(self) -> None: ...

    def snapshot(self) -> DeviceRuntimeSnapshot: ...

    def restore(self, snapshot: DeviceRuntimeSnapshot) -> None: ...

    def mark_failed(self) -> None: ...

    def advance(
        self,
        *,
        inputs: object | None,
        commands: tuple[DeviceCommand, ...],
        fault_tests: tuple[FaultTest, ...],
        at: datetime,
        dt: timedelta,
        plant_inputs: tuple[PlantValue, ...] = (),
    ) -> DeviceStepResult[DeviceState]: ...


class _DeviceSlot[ConfigurationT, StateT: DeviceState, InputsT]:
    def __init__(
        self,
        registration: DeviceRegistration[ConfigurationT, StateT, InputsT],
    ) -> None:
        self._model = registration.model
        self._configuration = registration.configuration
        self._initial_inputs = registration.initial_inputs
        self._inputs = registration.initial_inputs
        self._initial_state = registration.model.initial_state(registration.configuration)
        self._state = self._initial_state
        self._health = self._stopped_health()
        self._telemetry: tuple[DeviceValue, ...] = ()
        self._faults: tuple[DeviceFault, ...] = ()
        self._events: tuple[DeviceEvent, ...] = ()
        self._plant_inputs: tuple[PlantValue, ...] = ()

    @property
    def device_id(self) -> str:
        return self._model.identity.device_id

    @property
    def capabilities(self) -> tuple[DeviceCapability, ...]:
        return self._model.capabilities

    def _stopped_health(self) -> RuntimeDeviceHealth:
        return RuntimeDeviceHealth(
            device_id=self.device_id,
            lifecycle=self._state.lifecycle.value,
            status=ComponentStatus.STOPPED,
            model_health=None,
        )

    def reset(self) -> None:
        self._state = self._initial_state
        self._inputs = self._initial_inputs
        self._health = self._stopped_health()
        self._telemetry = ()
        self._faults = ()
        self._events = ()
        self._plant_inputs = ()

    def snapshot(self) -> DeviceRuntimeSnapshot:
        return DeviceRuntimeSnapshot(
            self.device_id,
            self._state,
            self._inputs,
            self._health,
            self._telemetry,
            self._faults,
            self._events,
            self._plant_inputs,
        )

    def restore(self, snapshot: DeviceRuntimeSnapshot) -> None:
        self._state = cast(StateT, snapshot.state)
        self._inputs = cast(InputsT, snapshot.inputs)
        self._health = snapshot.health
        self._telemetry = snapshot.telemetry
        self._faults = snapshot.faults
        self._events = snapshot.events
        self._plant_inputs = snapshot.plant_inputs

    def mark_failed(self) -> None:
        self._health = RuntimeDeviceHealth(
            self.device_id,
            self._state.lifecycle.value,
            ComponentStatus.UNAVAILABLE,
            self._health.model_health,
            "device_step_failed",
        )

    def advance(
        self,
        *,
        inputs: object | None,
        commands: tuple[DeviceCommand, ...],
        fault_tests: tuple[FaultTest, ...],
        at: datetime,
        dt: timedelta,
        plant_inputs: tuple[PlantValue, ...] = (),
    ) -> DeviceStepResult[DeviceState]:
        if inputs is not None:
            self._inputs = cast(InputsT, inputs)
        self._plant_inputs = plant_inputs
        normal_result = self._model.step(
            DeviceStep(
                state=self._state,
                inputs=self._inputs,
                commands=commands,
                configuration=self._configuration,
                at=at,
                dt=dt,
                plant=plant_inputs,
            )
        )
        typed_result = apply_faults(
            normal_result,
            {item.logical_id: item for item in self._telemetry},
            fault_tests,
        )
        self._state = typed_result.state
        self._telemetry = typed_result.telemetry
        self._faults = typed_result.faults
        self._events = typed_result.events
        self._health = RuntimeDeviceHealth(
            self.device_id,
            typed_result.health.lifecycle.value,
            typed_result.health.status,
            typed_result.health,
        )
        return cast(DeviceStepResult[DeviceState], typed_result)


class SimulatorRuntime:
    def __init__(
        self, clock: Clock, fixed_step: timedelta, *, plant: PlantModel | None = None
    ) -> None:
        self._fixed_step = _validate_step(fixed_step)
        self._initial_at = clock.now()
        self._at = self._initial_at
        self._sequence = 0
        self._lifecycle = RuntimeLifecycle.STOPPED
        self._devices: dict[str, _RuntimeDevice] = {}
        self._trace: list[SimulationTraceEntry] = []
        self._plant = plant
        self._plant_state = None if plant is None else plant.initial_state()
        self._plant_trace: list[PlantStepResult] = []
        if plant is not None and (
            plant.configuration.initial_at != self._at or plant.configuration.step != fixed_step
        ):
            raise ValueError("plant and runtime configuration must use the same clock and step")

    @property
    def lifecycle(self) -> RuntimeLifecycle:
        return self._lifecycle

    @property
    def at(self) -> datetime:
        return self._at

    @property
    def sequence(self) -> int:
        return self._sequence

    def register[ConfigurationT, StateT: DeviceState, InputsT](
        self,
        registration: DeviceRegistration[ConfigurationT, StateT, InputsT],
    ) -> None:
        if self._lifecycle is not RuntimeLifecycle.STOPPED:
            raise RuntimeTransitionError("devices can be registered only while stopped")
        device_id = registration.model.identity.device_id
        if device_id in self._devices:
            raise DeviceRegistrationError(f"duplicate device identity: {device_id}")
        if any(capability.device_id != device_id for capability in registration.model.capabilities):
            raise DeviceRegistrationError("capability device identity does not match model")
        self._devices[device_id] = _DeviceSlot(registration)

    def start(self) -> None:
        if self._lifecycle is RuntimeLifecycle.SHUTDOWN:
            raise RuntimeTransitionError("shutdown runtime cannot be restarted")
        if not self._devices:
            raise RuntimeTransitionError("runtime requires at least one registered device")
        self._lifecycle = RuntimeLifecycle.RUNNING

    def stop(self) -> None:
        if self._lifecycle is not RuntimeLifecycle.SHUTDOWN:
            self._lifecycle = RuntimeLifecycle.STOPPED

    def shutdown(self) -> None:
        self._lifecycle = RuntimeLifecycle.SHUTDOWN

    def advance(
        self,
        updates: tuple[DeviceUpdate, ...] = (),
        *,
        dt: timedelta | None = None,
    ) -> tuple[SimulationTraceEntry, ...]:
        if self._lifecycle is not RuntimeLifecycle.RUNNING:
            raise RuntimeTransitionError("runtime must be running before advance")
        duration = self._fixed_step if dt is None else _validate_step(dt)
        updates_by_id = _validate_updates(updates, self._devices)
        next_at = self._at + duration
        sequence = self._sequence + 1
        frame: list[SimulationTraceEntry] = []
        devices = {device_id: copy(device) for device_id, device in self._devices.items()}
        outputs = (
            {}
            if self._plant_state is None
            else {port.device_id: port.outputs for port in self._plant_state.ports}
        )

        for device_id in sorted(self._devices):
            device = devices[device_id]
            update = updates_by_id.get(device_id)
            commands = (
                ()
                if update is None
                else tuple(
                    sorted(
                        update.commands,
                        key=lambda command: (command.issued_at, command.command_id),
                    )
                )
            )
            try:
                result = device.advance(
                    inputs=None if update is None else update.inputs,
                    commands=commands,
                    fault_tests=() if update is None else update.fault_tests,
                    at=next_at,
                    dt=duration,
                    plant_inputs=() if self._plant is None else outputs[device_id],
                )
                entry = SimulationTraceEntry(
                    sequence,
                    next_at,
                    duration.total_seconds(),
                    device_id,
                    result,
                    None,
                )
            except Exception:
                device.mark_failed()
                entry = SimulationTraceEntry(
                    sequence,
                    next_at,
                    duration.total_seconds(),
                    device_id,
                    None,
                    "device_step_failed",
                )
            frame.append(entry)

        plant_result = self._step_plant(frame, next_at, duration)

        # Publish only after the whole frame is valid.
        self._devices = devices
        if plant_result is not None:
            self._plant_state = plant_result.state
            self._plant_trace.append(plant_result)
        self._sequence = sequence
        self._at = next_at
        self._trace.extend(frame)
        return tuple(frame)

    def _step_plant(
        self, frame: list[SimulationTraceEntry], at: datetime, dt: timedelta
    ) -> PlantStepResult | None:
        if self._plant is None:
            return None
        try:
            if self._plant_state is None:
                raise ValueError("plant state is missing")
            result = self._plant.step(
                state=self._plant_state,
                inputs=tuple((entry.device_id, entry.result) for entry in frame),
                configuration=self._plant.configuration,
                at=at,
                dt=dt,
            )
            self._plant.validate(result.state, at, self._sequence + 1, previous=self._plant_state)
            if any(event.occurred_at != at for event in result.events):
                raise ValueError("plant event time does not match the frame")
            json.dumps(asdict(result), default=_json_default, allow_nan=False)
            return result
        except ValueError:
            raise FrameError(FrameFailure(self._sequence + 1, at, "invalid_plant_step")) from None
        except Exception:
            raise FrameError(FrameFailure(self._sequence + 1, at, "plant_step_failed")) from None

    def fork(self) -> SimulatorRuntime:
        """Copy state containers before replay or restore."""

        result = copy(self)
        result._devices = {key: copy(device) for key, device in self._devices.items()}
        result._trace = list(self._trace)
        result._plant_trace = list(self._plant_trace)
        return result

    def snapshot(self) -> SimulationSnapshot:
        return SimulationSnapshot(
            at=self._at,
            sequence=self._sequence,
            devices=tuple(
                self._devices[device_id].snapshot() for device_id in sorted(self._devices)
            ),
            trace=tuple(self._trace),
            plant=self._plant_state,
            plant_trace=tuple(self._plant_trace),
        )

    def reset(self) -> None:
        if self._lifecycle is not RuntimeLifecycle.STOPPED:
            raise RuntimeTransitionError("runtime must be stopped before reset")
        for device in self._devices.values():
            device.reset()
        self._at = self._initial_at
        self._sequence = 0
        self._trace.clear()
        self._plant_trace.clear()
        self._plant_state = None if self._plant is None else self._plant.initial_state()

    def restore(self, snapshot: SimulationSnapshot) -> None:
        if self._lifecycle is not RuntimeLifecycle.STOPPED:
            raise RuntimeTransitionError("runtime must be stopped before restore")
        current_ids = tuple(sorted(self._devices))
        snapshot_ids = tuple(item.device_id for item in snapshot.devices)
        if snapshot_ids != current_ids:
            raise DeviceRegistrationError("snapshot device inventory does not match runtime")
        if (snapshot.plant is None) != (self._plant is None):
            raise DeviceRegistrationError("snapshot plant does not match runtime")
        if self._plant is not None and snapshot.plant is not None:
            self._plant.validate(snapshot.plant, snapshot.at, snapshot.sequence)
            if snapshot.at != self._initial_at + self._fixed_step * snapshot.sequence:
                raise DeviceRegistrationError("snapshot time does not match the fixed step")
            if len(snapshot.plant_trace) != snapshot.sequence:
                raise DeviceRegistrationError("snapshot plant trace is incomplete")
            previous = self._plant.initial_state()
            for number, result in enumerate(snapshot.plant_trace, 1):
                self._plant.validate(
                    result.state,
                    self._initial_at + self._fixed_step * number,
                    number,
                    previous=previous,
                )
                previous = result.state
            if snapshot.plant_trace and snapshot.plant_trace[-1].state != snapshot.plant:
                raise DeviceRegistrationError("snapshot plant trace does not match state")
        devices = {key: copy(device) for key, device in self._devices.items()}
        for device_snapshot in snapshot.devices:
            current = devices[device_snapshot.device_id].snapshot()
            if type(device_snapshot.state) is not type(current.state) or type(
                device_snapshot.inputs
            ) is not type(current.inputs):
                raise DeviceRegistrationError("snapshot device types do not match runtime")
            devices[device_snapshot.device_id].restore(device_snapshot)
        self._devices = devices
        self._plant_state = snapshot.plant
        self._plant_trace = list(snapshot.plant_trace)
        self._at = snapshot.at
        self._sequence = snapshot.sequence
        self._trace = list(snapshot.trace)

    def capabilities(self) -> tuple[DeviceCapability, ...]:
        return tuple(
            capability
            for device_id in sorted(self._devices)
            for capability in sorted(
                self._devices[device_id].capabilities,
                key=lambda item: item.capability_id,
            )
        )

    def health(self) -> tuple[RuntimeDeviceHealth, ...]:
        return tuple(
            self._devices[device_id].snapshot().health for device_id in sorted(self._devices)
        )

    def plant_trace_bytes(self) -> bytes:
        return json.dumps(
            [asdict(item) for item in self._plant_trace],
            default=_json_default,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def trace_bytes(self) -> bytes:
        return json.dumps(
            [asdict(item) for item in self._trace],
            default=_json_default,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


def _validate_step(value: timedelta) -> timedelta:
    seconds = value.total_seconds()
    if not isfinite(seconds) or seconds <= 0:
        raise ValueError("simulation step must be a finite positive duration")
    return value


def _validate_updates(
    updates: tuple[DeviceUpdate, ...],
    devices: dict[str, _RuntimeDevice],
) -> dict[str, DeviceUpdate]:
    result: dict[str, DeviceUpdate] = {}
    for update in updates:
        if update.device_id not in devices:
            raise DeviceRegistrationError(f"unknown device update: {update.device_id}")
        if update.device_id in result:
            raise DeviceRegistrationError(f"duplicate device update: {update.device_id}")
        result[update.device_id] = update
    return result


def _json_default(value: object) -> str | list[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, frozenset):
        return sorted(str(item) for item in value)
    raise TypeError(f"unsupported trace value: {type(value).__name__}")

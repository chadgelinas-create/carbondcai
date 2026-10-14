"""Own the deterministic Simulator Lab device models and test inputs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, cast

from skid2.application.simulation_checkpoint import (
    SavedRunInfo,
    SavedSimulation,
    decode_checkpoint,
    decode_setup,
    encode_checkpoint,
    encode_setup,
)
from skid2.application.simulation_checkpoint import (
    SimulationActivity as SimulationActivity,
)
from skid2.application.simulation_checkpoint import (
    SimulationDeviceState as SimulationDeviceState,
)
from skid2.application.simulation_checkpoint import (
    SimulationInputs as SimulationInputs,
)
from skid2.contracts import Clock, ComponentStatus, DataQuality, ManualClock
from skid2.persistence.simulation import SimulationStore
from skid2.plant.battery import BatteryConfig
from skid2.plant.bus import BusConfig
from skid2.plant.model import MODEL_VERSION, PlantConfig, PlantModel, PlantState, PlantValue, PvPath
from skid2.plant.profiles import plant_profile
from skid2.protocol.loader import parse_document
from skid2.protocol.models import ProtocolDocument
from skid2.sim.battery import BatteryInputs, BatteryModel
from skid2.sim.converter import (
    ConverterInputs,
    ConverterModel,
    ConverterRole,
)
from skid2.sim.dc600 import Dc600Inputs, Dc600Model
from skid2.sim.device import (
    CapabilityKind,
    DeviceCapability,
    DeviceEvent,
    DeviceFault,
    DeviceIdentity,
    DeviceValue,
    EvidenceClassification,
    ExecutionProfile,
    FidelityLevel,
)
from skid2.sim.faults import (
    FAULT_RULES,
    FaultRequest,
    FaultRule,
    FaultTest,
    FaultTestState,
    fault_state,
    find_rule,
    validate_fault,
)
from skid2.sim.runtime import (
    DeviceRegistration,
    DeviceUpdate,
    FrameError,
    FrameFailure,
    RuntimeLifecycle,
    SimulatorRuntime,
)
from skid2.sim.stabiliti import StabilitiInputs, StabilitiModel

DEVICE_FILES = (
    ("ba1-1", Path("devices/ba1-bess-v1.18.candidate.yaml")),
    ("cnvtr-1", Path("devices/cnvtr1-sinexcel-v126.candidate.yaml")),
    ("cnvtr-2", Path("devices/cnvtr2-sinexcel-v126.candidate.yaml")),
    ("st-4", Path("blocked/st4.blocked.yaml")),
    ("st-5", Path("blocked/st5.blocked.yaml")),
    ("st-6", Path("blocked/st6.blocked.yaml")),
    ("dc600", Path("devices/dc600-appendix-a.candidate.yaml")),
)

DEVICE_DISPLAY_NAMES = {
    "st-4": "Stabiliti 01",
    "st-5": "Stabiliti 02",
    "st-6": "Stabiliti 03",
}

InputType = type[BatteryInputs] | type[ConverterInputs] | type[StabilitiInputs] | type[Dc600Inputs]


@dataclass(frozen=True, slots=True)
class DeviceSummary:
    """One simulated device with current values and connection boundaries."""

    device_id: str
    display_name: str
    role: str
    lifecycle: str
    health: ComponentStatus
    test_id: str | None
    test_started_at: datetime | None
    implementation_status: str
    protocol_status: str
    protocol: str
    endpoint_status: str
    model_status: str
    firmware_status: str
    blocker_reason: str | None
    capabilities: tuple[DeviceCapability, ...]
    values: tuple[DeviceValue, ...]
    faults: tuple[DeviceFault, ...]
    inputs: SimulationInputs
    applied_inputs: SimulationInputs
    inputs_pending: bool
    status_text: str
    testing_method: str
    direct_connection: str
    test_notice: str | None


@dataclass(frozen=True, slots=True)
class StabilitiFleetSummary:
    ready: int
    available: int
    derated: int
    faulted: int
    total: int
    available_power: float


@dataclass(frozen=True, slots=True)
class BusSample:
    at: datetime
    sequence: int
    values: tuple[PlantValue, ...]


@dataclass(frozen=True, slots=True)
class PvSample:
    at: datetime
    sequence: int
    values: tuple[PlantValue, ...]
    paths: tuple[PvPath, ...]


@dataclass(frozen=True, slots=True)
class SimulationState:
    """Current runtime, device, activity, and fleet state."""

    lifecycle: RuntimeLifecycle
    at: datetime
    sequence: int
    trace_entries: int
    trace_hash: str
    devices: tuple[DeviceSummary, ...]
    activity: tuple[SimulationActivity, ...]
    stabiliti_fleet: StabilitiFleetSummary
    fault_rules: tuple[FaultRule, ...]
    fault_tests: tuple[FaultTest, ...]
    plant: PlantState
    battery_history: tuple[BusSample, ...]
    bus_history: tuple[BusSample, ...]
    pv_history: tuple[PvSample, ...]
    plant_trace_hash: str
    failure: FrameFailure | None
    snapshot_saved: bool
    saved_run: SavedRunInfo | None
    restored_run: SavedRunInfo | None


class SimulationService:
    """Own typed device inputs and exact deterministic replay."""

    def __init__(
        self,
        clock: Clock,
        step: timedelta,
        protocol_root: Path,
        *,
        seed: int,
        profile_id: str = "stage0",
        repository: SimulationStore | None = None,
    ) -> None:
        self._lock = RLock()
        self._repository = repository
        self._saved_run: SavedRunInfo | None = None
        self._restored_run: SavedRunInfo | None = None
        self._accepted_inputs: dict[str, SimulationInputs] = {}
        self._step = step
        self._config = replace(
            plant_profile(profile_id, seed=seed, step_seconds=step.total_seconds()),
            initial_at=clock.now(),
        )
        self._runtime = SimulatorRuntime(clock, step, plant=PlantModel(self._config))
        self._failure: FrameFailure | None = None
        self._saved: SavedSimulation | None = None
        self._documents: dict[str, ProtocolDocument] = {}
        self._input_types: dict[str, InputType] = {}
        self._pending: dict[str, SimulationInputs] = {}
        self._frames: list[tuple[DeviceUpdate, ...]] = []
        self._fault_tests: list[FaultTest] = []
        self._fault_activity: list[SimulationActivity] = []
        self._recovery_steps: dict[str, int] = {}
        self._next_test_number = 1
        for expected_id, relative_path in DEVICE_FILES:
            document = parse_document(protocol_root / relative_path)
            if document.device.device_id != expected_id:
                raise ValueError(f"unexpected device identity: {document.device.device_id}")
            self._documents[expected_id] = document
            self._register(document)
        # Bump engine_version when device evolution or fault semantics change.
        self._compatibility = sha256(
            json.dumps(
                {
                    "engine_version": 1,
                    "plant_model": MODEL_VERSION,
                    "protocols": {
                        key: sha256((protocol_root / path).read_bytes()).hexdigest()
                        for key, path in DEVICE_FILES
                    },
                },
                sort_keys=True,
                default=_json_default,
            ).encode()
        ).hexdigest()
        self._latest_setup = encode_setup(self._config, {}, self._compatibility)

    @property
    def step_seconds(self) -> float:
        with self._lock:
            return self._step.total_seconds()

    def _new_runtime(self, configuration: PlantConfig) -> SimulatorRuntime:
        candidate = SimulatorRuntime(
            ManualClock(configuration.initial_at),
            configuration.step,
            plant=PlantModel(configuration),
        )
        for document in self._documents.values():
            self._register(document, candidate, configuration)
        return candidate

    def _save_setup(self, configuration: PlantConfig, inputs: dict[str, SimulationInputs]) -> None:
        document = encode_setup(configuration, inputs, self._compatibility)
        if self._repository is not None:
            self._repository.save("simulation.settings", document)
        self._latest_setup = document

    def configuration_document(self) -> dict[str, Any]:
        """Describe the installed setup, independently of evolving run progress."""
        with self._lock:
            return encode_setup(
                self._config,
                self._configuration_inputs(self._config, self._accepted_inputs),
                self._compatibility,
            )

    def propose_profile(
        self,
        profile_id: str,
        seed: int,
        *,
        battery_profile: str | None = None,
        battery: BatteryConfig | None = None,
        bus: BusConfig | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            configuration = plant_profile(
                profile_id,
                seed=seed,
                step_seconds=self._step.total_seconds(),
                battery_profile=battery_profile,
                battery=battery,
                bus=bus,
            )
            self._new_runtime(configuration)
            # A scenario change has always started with its default device inputs.
            # Carrying overrides across scenarios can silently disable new equipment.
            return encode_setup(configuration, {}, self._compatibility)

    def propose_inputs(self, device_id: str, inputs: SimulationInputs) -> dict[str, Any]:
        with self._lock:
            expected = self._input_types.get(device_id)
            if expected is None or not isinstance(inputs, expected):
                raise ValueError("Choose valid inputs for this device.")
            _validate_normal_inputs(inputs)
            return encode_setup(
                self._config,
                self._configuration_inputs(
                    self._config, {**self._accepted_inputs, device_id: inputs}
                ),
                self._compatibility,
            )

    @staticmethod
    def _configuration_inputs(
        configuration: PlantConfig,
        inputs: dict[str, SimulationInputs],
    ) -> dict[str, SimulationInputs]:
        # Freshness timestamps belong to run execution. Normalize them to the start
        # of the proposed run when comparing or serializing configuration intent.
        return {
            key: replace(
                value,
                demand_updated_at=configuration.initial_at
                if value.demand_power is not None
                else None,
                available_updated_at=configuration.initial_at
                if value.available_power is not None
                else None,
            )
            if isinstance(value, Dc600Inputs)
            else value
            for key, value in inputs.items()
        }

    def configuration_matches(self, document: dict[str, Any]) -> bool:
        with self._lock:
            configuration, inputs = decode_setup(document, self._compatibility)
            expected = encode_setup(
                configuration,
                self._configuration_inputs(configuration, inputs),
                self._compatibility,
            )
            return self.configuration_document() == expected

    def validate_configuration(self, document: dict[str, Any]) -> None:
        with self._lock:
            configuration, inputs = decode_setup(document, self._compatibility)
            for device_id, value in inputs.items():
                expected = self._input_types.get(device_id)
                if expected is None or not isinstance(value, expected):
                    raise ValueError("Settings contain an unknown device or incompatible inputs.")
                _validate_normal_inputs(value)
            self._new_runtime(configuration)

    def install_configuration(self, document: dict[str, Any]) -> None:
        """Install a validated setup as a new paused run; retain the manual checkpoint."""
        with self._lock:
            if self._runtime.lifecycle is RuntimeLifecycle.RUNNING:
                raise ValueError("Pause the simulation before applying settings.")
            self.validate_configuration(document)
            configuration, inputs = decode_setup(document, self._compatibility)
            candidate = self._new_runtime(configuration)
            self._runtime = candidate
            self._config = configuration
            self._step = configuration.step
            self._accepted_inputs = inputs
            self._latest_setup = document
            self.reset()

    def load_saved_state(self) -> None:
        """Load one durable experiment without starting it or rewriting settings."""
        with self._lock:
            if self._repository is None:
                return
            document = self._repository.load("simulation.checkpoint")
            if document is not None:
                saved, info = decode_checkpoint(document, self._compatibility)
                self._install_checkpoint(saved, info)
            else:
                self.new_run()

    def new_run(self) -> SimulationState:
        with self._lock:
            if self._runtime.lifecycle is RuntimeLifecycle.RUNNING:
                raise ValueError("pause the simulation before starting a new run")
            document = (
                self._repository.load("simulation.settings")
                if self._repository is not None
                else self._latest_setup
            )
            configuration, inputs = decode_setup(
                self._latest_setup if document is None else document, self._compatibility
            )
            for value in inputs.values():
                _validate_normal_inputs(value)
            candidate = self._new_runtime(configuration)
            self._runtime = candidate
            self._config = configuration
            self._step = configuration.step
            self._accepted_inputs = inputs
            return self.reset()

    def delete_saved_run(self) -> SimulationState:
        with self._lock:
            if self._repository is not None:
                self._repository.delete_checkpoint()
            self._saved = None
            self._saved_run = None
            self._restored_run = None
            return self.state()

    def start(self) -> SimulationState:
        with self._lock:
            self._runtime.start()
            self._restored_run = None
            return self.state()

    def pause(self) -> SimulationState:
        with self._lock:
            self._runtime.stop()
            return self.state()

    def set_inputs(self, device_id: str, inputs: SimulationInputs) -> SimulationState:
        with self._lock:
            expected = self._input_types.get(device_id)
            if expected is None:
                raise ValueError(f"unknown simulation device: {device_id}")
            if not isinstance(inputs, expected):
                raise ValueError(f"wrong inputs for simulation device: {device_id}")
            _validate_normal_inputs(inputs)
            if isinstance(inputs, Dc600Inputs):
                inputs = replace(
                    inputs,
                    demand_updated_at=(
                        self._runtime.at if inputs.demand_power is not None else None
                    ),
                    available_updated_at=(
                        self._runtime.at if inputs.available_power is not None else None
                    ),
                )
            accepted = {**self._accepted_inputs, device_id: inputs}
            self._save_setup(self._config, accepted)
            self._accepted_inputs = accepted
            self._restored_run = None
            self._pending[device_id] = inputs
            return self.state()

    def start_fault(self, request: FaultRequest) -> SimulationState:
        with self._lock:
            if request.device_id not in self._documents:
                raise ValueError(f"unknown simulation device: {request.device_id}")
            rule = find_rule(request.device_id, request.fault_type)
            current_sequence = self._runtime.sequence
            if any(
                test.device_id == request.device_id
                and fault_state(test, current_sequence)
                in {FaultTestState.SCHEDULED, FaultTestState.ACTIVE}
                for test in self._fault_tests
            ):
                raise ValueError("clear the current test for this device before starting another")
            validate_fault(rule, request, self.values(request.device_id))
            start_step = current_sequence + request.delay_steps + 1
            test = FaultTest(
                test_id=f"test-{self._next_test_number:04d}",
                device_id=request.device_id,
                fault_type=request.fault_type,
                point_id=request.point_id,
                test_value=request.test_value,
                start_step=start_step,
                duration_steps=request.duration_steps,
                started_at=self._runtime.at + self._step * (request.delay_steps + 1),
            )
            self._restored_run = None
            self._fault_tests.append(test)
            self._next_test_number += 1
            self._fault_activity.append(
                SimulationActivity(
                    current_sequence,
                    self._runtime.at,
                    request.device_id,
                    "fault_scheduled",
                    f"{rule.label} scheduled for step {start_step}",
                    test.test_id,
                    test.started_at,
                )
            )
            return self.state()

    def clear_fault(self, test_id: str) -> SimulationState:
        with self._lock:
            for index, test in enumerate(self._fault_tests):
                if test.test_id != test_id:
                    continue
                state = fault_state(test, self._runtime.sequence)
                if state not in {FaultTestState.SCHEDULED, FaultTestState.ACTIVE}:
                    raise ValueError("the selected test is already finished")
                cleared = replace(
                    test,
                    cleared_at_step=self._runtime.sequence,
                    cleared_at=self._runtime.at,
                )
                self._restored_run = None
                self._fault_tests[index] = cleared
                rule = find_rule(test.device_id, test.fault_type)
                self._fault_activity.append(
                    SimulationActivity(
                        self._runtime.sequence,
                        self._runtime.at,
                        test.device_id,
                        "fault_cleared",
                        (
                            f"{rule.label} cleared; run the next step to confirm recovery"
                            if state is FaultTestState.ACTIVE
                            else f"{rule.label} cancelled before it started"
                        ),
                        test.test_id,
                        test.started_at,
                    )
                )
                if state is FaultTestState.ACTIVE:
                    self._recovery_steps[test.test_id] = self._runtime.sequence + 1
                return self.state()
            raise ValueError(f"unknown fault test: {test_id}")

    def fault_rules(self) -> tuple[FaultRule, ...]:
        with self._lock:
            return FAULT_RULES

    def report_bytes(self) -> bytes:
        with self._lock:
            state = self.state()
            tests = []
            for test in state.fault_tests:
                rule = find_rule(test.device_id, test.fault_type)
                item = asdict(test)
                item.update(
                    {
                        "label": rule.label,
                        "state": fault_state(test, state.sequence).value,
                        "expected_result": rule.expected_result,
                        "clear_method": rule.clear_method,
                        "latched": rule.latched,
                    }
                )
                tests.append(item)
            payload = {
                "schema_version": 2,
                "plant": asdict(state.plant),
                "plant_trace_hash": state.plant_trace_hash,
                "plant_trace": json.loads(self._runtime.plant_trace_bytes()),
                "failure": None if self._failure is None else asdict(self._failure),
                "generated_at": state.at,
                "sequence": state.sequence,
                "trace_hash": state.trace_hash,
                "tests": tests,
                "activity": [asdict(item) for item in state.activity],
                "trace": json.loads(self._runtime.trace_bytes()),
                "limitations": [
                    *self._config.limitations,
                    (
                        "DC600 and Stabiliti tests are internal software simulations. "
                        "Vendor-compatible network behaviour is waiting for vendor protocol details."
                    ),
                ],
            }
            return json.dumps(
                payload,
                default=_json_default,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")

    def step(self) -> SimulationState:
        with self._lock:
            self._restored_run = None
            was_running = self._runtime.lifecycle is RuntimeLifecycle.RUNNING
            if not was_running:
                self._runtime.start()
            previous_sequence = self._runtime.sequence
            next_sequence = previous_sequence + 1
            active_by_device: dict[str, list[FaultTest]] = {}
            for test in self._fault_tests:
                if fault_state(test, next_sequence) is FaultTestState.ACTIVE:
                    active_by_device.setdefault(test.device_id, []).append(test)
            updated_devices = sorted(set(self._pending) | set(active_by_device))
            updates = tuple(
                DeviceUpdate(
                    device_id,
                    self._pending.get(device_id),
                    fault_tests=tuple(active_by_device.get(device_id, ())),
                )
                for device_id in updated_devices
            )
            try:
                self._runtime.advance(updates)
            except FrameError as error:
                self._runtime.stop()
                self._failure = error.failure
                return self.state()
            self._failure = None
            self._frames.append(updates)
            self._pending.clear()
            self._record_recovery(previous_sequence, next_sequence)
            if not was_running:
                self._runtime.stop()
            return self.state()

    def reset(self) -> SimulationState:
        with self._lock:
            self._runtime.stop()
            self._runtime.reset()
            self._failure = None
            self._accepted_inputs = {
                key: replace(
                    value,
                    demand_updated_at=self._runtime.at if value.demand_power is not None else None,
                    available_updated_at=self._runtime.at
                    if value.available_power is not None
                    else None,
                )
                if isinstance(value, Dc600Inputs)
                else value
                for key, value in self._accepted_inputs.items()
            }
            self._pending = dict(self._accepted_inputs)
            self._restored_run = None
            self._frames.clear()
            self._fault_tests.clear()
            self._fault_activity.clear()
            self._recovery_steps.clear()
            self._next_test_number = 1
            return self.state()

    def tick(self) -> None:
        """Advance only while the shared runtime is running."""

        with self._lock:
            if self._runtime.lifecycle is RuntimeLifecycle.RUNNING:
                self.step()

    def save_snapshot(self) -> SimulationState:
        """Commit one complete checkpoint before reporting success."""

        with self._lock:
            saved = SavedSimulation(
                self._runtime.snapshot(),
                self._config,
                tuple(sorted(self._pending.items())),
                tuple(self._frames),
                tuple(self._fault_tests),
                tuple(self._fault_activity),
                tuple(sorted(self._recovery_steps.items())),
                self._next_test_number,
                tuple(sorted(self._accepted_inputs.items())),
                self._failure,
            )
            saved_at = datetime.now(UTC)
            if self._repository is not None:
                self._repository.save(
                    "simulation.checkpoint", encode_checkpoint(saved, saved_at, self._compatibility)
                )
            self._saved = saved
            self._saved_run = SavedRunInfo(saved_at, saved.runtime.at, saved.runtime.sequence)
            return self.state()

    def restore(self) -> SimulationState:
        """Restore a checked checkpoint while paused."""

        with self._lock:
            if self._runtime.lifecycle is RuntimeLifecycle.RUNNING:
                raise ValueError("pause the simulation before restoring")
            saved = self._saved
            if saved is None:
                raise ValueError("no simulation snapshot is saved")
            self._install_checkpoint(saved, self._saved_run)
            return self.state()

    def _install_checkpoint(self, saved: SavedSimulation, info: SavedRunInfo | None) -> None:
        """Validate a complete candidate, including replay, before publishing it."""
        if len(saved.frames) != saved.runtime.sequence:
            raise ValueError("saved run replay history is incomplete")
        for _, inputs in (*saved.accepted_inputs, *saved.pending):
            _validate_normal_inputs(inputs)
        test_ids = {test.test_id for test in saved.fault_tests}
        if len(test_ids) != len(saved.fault_tests) or saved.next_test_number != len(test_ids) + 1:
            raise ValueError("saved run fault numbering is invalid")
        for number, test in enumerate(saved.fault_tests, 1):
            find_rule(test.device_id, test.fault_type)
            if test.test_id != f"test-{number:04d}" or test.started_at != (
                saved.configuration.initial_at + saved.configuration.step * test.start_step
            ):
                raise ValueError("saved run fault schedule is invalid")
        if len(dict(saved.recovery_steps)) != len(saved.recovery_steps) or any(
            key not in test_ids or step != saved.runtime.sequence + 1
            for key, step in saved.recovery_steps
        ):
            raise ValueError("saved run fault recovery is invalid")
        candidate = self._new_runtime(saved.configuration)
        candidate.restore(saved.runtime)
        replayed = self._new_runtime(saved.configuration)
        replayed.start()
        try:
            for updates in saved.frames:
                replayed.advance(updates)
        except FrameError as error:
            raise ValueError("saved run replay failed validation") from error
        if replayed.snapshot() != candidate.snapshot():
            raise ValueError("saved run state does not match its recorded history")
        self._runtime = candidate
        self._config = saved.configuration
        self._step = saved.configuration.step
        self._pending = dict(saved.pending)
        self._accepted_inputs = dict(saved.accepted_inputs)
        self._frames = list(saved.frames)
        self._fault_tests = list(saved.fault_tests)
        self._fault_activity = list(saved.fault_activity)
        self._recovery_steps = dict(saved.recovery_steps)
        self._next_test_number = saved.next_test_number
        self._failure = saved.failure
        self._saved = saved
        self._saved_run = info
        self._restored_run = info

    def set_profile(
        self,
        profile_id: str,
        seed: int,
        *,
        battery_profile: str | None = None,
        battery: BatteryConfig | None = None,
        bus: BusConfig | None = None,
    ) -> SimulationState:
        with self._lock:
            if self._runtime.lifecycle is RuntimeLifecycle.RUNNING:
                raise ValueError("pause the simulation before changing the profile")
            configuration = plant_profile(
                profile_id,
                seed=seed,
                step_seconds=self._step.total_seconds(),
                battery_profile=battery_profile,
                battery=battery,
                bus=bus,
            )
            # Build the replacement before changing the active run.
            candidate = self._new_runtime(configuration)
            self._save_setup(configuration, {})
            self._runtime = candidate
            self._config = configuration
            self._accepted_inputs = {}
            return self.reset()

    def replay(self) -> SimulationState:
        """Compare both traces before replacing the active runtime."""

        with self._lock:
            candidate = self._runtime.fork()
            candidate.stop()
            candidate.reset()
            candidate.start()
            try:
                for updates in self._frames:
                    candidate.advance(updates)
            except FrameError as error:
                self._failure = error.failure
                self._runtime.stop()
                return self.state()
            if (
                candidate.trace_bytes() != self._runtime.trace_bytes()
                or candidate.plant_trace_bytes() != self._runtime.plant_trace_bytes()
                or candidate.snapshot().devices != self._runtime.snapshot().devices
                or candidate.snapshot().plant != self._runtime.snapshot().plant
            ):
                self._failure = FrameFailure(
                    self._runtime.sequence, self._runtime.at, "replay_mismatch"
                )
                self._runtime.stop()
                return self.state()
            if self._runtime.lifecycle is not RuntimeLifecycle.RUNNING:
                candidate.stop()
            self._runtime = candidate
            self._failure = None
            return self.state()

    def state(self) -> SimulationState:
        with self._lock:
            snapshot = self._runtime.snapshot()
            if snapshot.plant is None:
                raise RuntimeError("shared plant is missing")
            capabilities = self._runtime.capabilities()
            devices = tuple(
                self._device_summary(
                    item.device_id,
                    item.health.lifecycle,
                    item.health.status,
                    None if item.health.model_health is None else item.health.model_health.test_id,
                    (
                        None
                        if item.health.model_health is None
                        else item.health.model_health.test_started_at
                    ),
                    tuple(
                        capability
                        for capability in capabilities
                        if capability.device_id == item.device_id
                    ),
                    item.telemetry,
                    item.faults,
                    cast(SimulationInputs, self._pending.get(item.device_id, item.inputs)),
                    cast(SimulationInputs, item.inputs),
                )
                for item in snapshot.devices
            )
            trace = self._runtime.trace_bytes()
            return SimulationState(
                lifecycle=self._runtime.lifecycle,
                at=snapshot.at,
                sequence=snapshot.sequence,
                trace_entries=len(snapshot.trace),
                trace_hash=sha256(trace).hexdigest(),
                devices=devices,
                activity=self._activity(),
                stabiliti_fleet=_fleet(devices),
                fault_rules=FAULT_RULES,
                fault_tests=tuple(self._fault_tests),
                plant=snapshot.plant,
                battery_history=tuple(
                    BusSample(entry.state.at, entry.state.sequence, entry.state.bess)
                    for entry in snapshot.plant_trace[-120:]
                ),
                bus_history=tuple(
                    BusSample(entry.state.at, entry.state.sequence, entry.state.bus)
                    for entry in snapshot.plant_trace[-120:]
                ),
                pv_history=tuple(
                    PvSample(
                        entry.state.at, entry.state.sequence, entry.state.pv, entry.state.pv_paths
                    )
                    for entry in snapshot.plant_trace[-120:]
                ),
                plant_trace_hash=sha256(self._runtime.plant_trace_bytes()).hexdigest(),
                failure=self._failure,
                snapshot_saved=self._saved is not None,
                saved_run=self._saved_run,
                restored_run=self._restored_run,
            )

    def values(self, device_id: str) -> tuple[DeviceValue, ...]:
        """Return the latest logical values emitted by one simulated device."""

        with self._lock:
            for device in self._runtime.snapshot().devices:
                if device.device_id == device_id:
                    return device.telemetry
            raise ValueError(f"unknown simulation device: {device_id}")

    def _register(
        self,
        document: ProtocolDocument,
        runtime: SimulatorRuntime | None = None,
        configuration: PlantConfig | None = None,
    ) -> None:
        target = self._runtime if runtime is None else runtime
        device_id = document.device.device_id
        identity = DeviceIdentity(
            device_id,
            DEVICE_DISPLAY_NAMES.get(device_id, document.device.display_name),
        )
        capabilities = _capabilities(document)
        config = self._config if configuration is None else configuration
        solar = bool(config.pv)
        battery = config.battery
        if device_id == "ba1-1":
            target.register(
                DeviceRegistration(
                    BatteryModel(identity, capabilities),
                    None,
                    BatteryInputs(
                        main_contactor=True,
                        charge_power_limit=battery.charge_limit_kw,
                        discharge_power_limit=battery.discharge_limit_kw,
                    )
                    if battery is not None
                    else BatteryInputs(),
                )
            )
            self._input_types[device_id] = BatteryInputs
        elif device_id == "cnvtr-1":
            target.register(
                DeviceRegistration(
                    ConverterModel(identity, capabilities, ConverterRole.BATTERY),
                    None,
                    ConverterInputs(
                        enabled=True,
                        available_power=max(battery.charge_limit_kw, battery.discharge_limit_kw),
                        charge_power_limit=battery.charge_limit_kw,
                        discharge_power_limit=battery.discharge_limit_kw,
                    )
                    if battery is not None
                    else ConverterInputs(),
                )
            )
            self._input_types[device_id] = ConverterInputs
        elif device_id == "cnvtr-2":
            target.register(
                DeviceRegistration(
                    ConverterModel(identity, capabilities, ConverterRole.SOLAR),
                    None,
                    ConverterInputs(
                        enabled=True, pv_available=True, requested_power=100, available_power=100
                    )
                    if solar
                    else ConverterInputs(),
                )
            )
            self._input_types[device_id] = ConverterInputs
        elif device_id in {"st-4", "st-5", "st-6"}:
            target.register(
                DeviceRegistration(
                    StabilitiModel(identity, capabilities),
                    None,
                    StabilitiInputs(bus_load=0, available_power=40, power_limit=40)
                    if solar or battery is not None
                    else StabilitiInputs(),
                )
            )
            self._input_types[device_id] = StabilitiInputs
        elif device_id == "dc600":
            target.register(
                DeviceRegistration(
                    Dc600Model(identity, capabilities),
                    None,
                    Dc600Inputs(demand_power=60 if solar else 0, available_power=100)
                    if solar or battery is not None
                    else Dc600Inputs(),
                )
            )
            self._input_types[device_id] = Dc600Inputs
        else:  # pragma: no cover - DEVICE_FILES is a fixed internal inventory.
            raise ValueError(f"unsupported simulation device: {device_id}")

    def _device_summary(
        self,
        device_id: str,
        lifecycle: str,
        health: ComponentStatus,
        test_id: str | None,
        test_started_at: datetime | None,
        capabilities: tuple[DeviceCapability, ...],
        values: tuple[DeviceValue, ...],
        faults: tuple[DeviceFault, ...],
        inputs: SimulationInputs,
        applied_inputs: SimulationInputs,
    ) -> DeviceSummary:
        document = self._documents[device_id]
        device = document.device
        blocker_reason = "; ".join(blocker.reason for blocker in document.blockers) or None
        mapped = device_id in {"ba1-1", "cnvtr-1", "cnvtr-2"}
        return DeviceSummary(
            device_id=device.device_id,
            display_name=DEVICE_DISPLAY_NAMES.get(device_id, device.display_name),
            role=device.role,
            lifecycle=lifecycle,
            health=health,
            test_id=test_id,
            test_started_at=test_started_at,
            implementation_status="simulated",
            protocol_status=document.lifecycle,
            protocol=device.protocol,
            endpoint_status=device.endpoint_status,
            model_status=device.model.status,
            firmware_status=device.firmware.status,
            blocker_reason=blocker_reason,
            capabilities=capabilities,
            values=values,
            faults=faults,
            inputs=inputs,
            applied_inputs=applied_inputs,
            inputs_pending=device_id in self._pending,
            status_text=_status_text(health, faults, values),
            testing_method="Simulator Lab",
            direct_connection="Modbus TCP simulator" if mapped else "Built-in simulator",
            test_notice=_test_notice(device_id),
        )

    def _activity(self) -> tuple[SimulationActivity, ...]:
        result: list[SimulationActivity] = list(self._fault_activity)
        result.extend(
            SimulationActivity(
                entry.state.sequence, event.occurred_at, event.source, event.category, event.message
            )
            for entry in self._runtime.snapshot().plant_trace
            for event in entry.events
            if event.category in {"plant_bus", "plant_pv"}
        )
        for entry in self._runtime.snapshot().trace:
            if entry.result is None:
                result.append(
                    SimulationActivity(
                        entry.sequence,
                        entry.at,
                        entry.device_id,
                        "failure",
                        f"{entry.device_id} step failed",
                    )
                )
                continue
            events: tuple[DeviceEvent, ...] = entry.result.events
            if not events:
                result.append(
                    SimulationActivity(
                        entry.sequence,
                        entry.at,
                        entry.device_id,
                        "step",
                        f"{entry.device_id} step complete",
                    )
                )
            result.extend(
                SimulationActivity(
                    entry.sequence,
                    event.occurred_at,
                    entry.device_id,
                    event.category,
                    event.message,
                    event.test_id,
                    event.test_started_at,
                )
                for event in events
            )
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    item.sequence,
                    item.at,
                    item.device_id,
                    _activity_order(item.category),
                    item.message,
                ),
            )
        )

    def _record_recovery(self, previous_sequence: int, current_sequence: int) -> None:
        for test in self._fault_tests:
            before = fault_state(test, previous_sequence)
            after = fault_state(test, current_sequence)
            rule = find_rule(test.device_id, test.fault_type)
            if before is FaultTestState.ACTIVE and after is FaultTestState.COMPLETE:
                self._fault_activity.append(
                    SimulationActivity(
                        current_sequence,
                        self._runtime.at,
                        test.device_id,
                        "recovery",
                        f"{rule.label} duration ended and normal simulation resumed",
                        test.test_id,
                        test.started_at,
                    )
                )
            if self._recovery_steps.get(test.test_id) == current_sequence:
                self._fault_activity.append(
                    SimulationActivity(
                        current_sequence,
                        self._runtime.at,
                        test.device_id,
                        "recovery",
                        f"{rule.label} cleared and normal simulation resumed",
                        test.test_id,
                        test.started_at,
                    )
                )
                del self._recovery_steps[test.test_id]


def _capabilities(document: ProtocolDocument) -> tuple[DeviceCapability, ...]:
    device_id = document.device.device_id
    source_ids = tuple(source.source_id for source in document.sources)
    decision_ids = tuple(blocker.decision_id for blocker in document.blockers)
    transport_evidence = (
        EvidenceClassification.BLOCKED
        if document.lifecycle == "blocked"
        else EvidenceClassification.PROVISIONAL
    )
    return (
        DeviceCapability(
            f"{device_id}.simulation",
            device_id,
            CapabilityKind.BEHAVIOR,
            EvidenceClassification.PROVISIONAL,
            FidelityLevel.LEVEL_1,
            document.lifecycle,
            available=True,
            enabled_profiles=frozenset({ExecutionProfile.STAGE_0}),
            source_ids=source_ids,
            assumptions=("Explicit Simulator Lab test inputs; no plant physics",),
        ),
        DeviceCapability(
            f"{device_id}.faults",
            device_id,
            CapabilityKind.FAULT,
            EvidenceClassification.PROVISIONAL,
            FidelityLevel.LEVEL_1,
            document.lifecycle,
            available=True,
            enabled_profiles=frozenset({ExecutionProfile.STAGE_0}),
            source_ids=source_ids,
        ),
        DeviceCapability(
            f"{device_id}.transport",
            device_id,
            CapabilityKind.TRANSPORT,
            transport_evidence,
            FidelityLevel.CONTRACT,
            document.lifecycle,
            source_ids=source_ids,
            decision_ids=decision_ids,
        ),
    )


def _fleet(devices: tuple[DeviceSummary, ...]) -> StabilitiFleetSummary:
    units = tuple(device for device in devices if device.device_id.startswith("st-"))
    ready = 0
    available = 0
    derated = 0
    faulted = 0
    power = 0.0
    for unit in units:
        values = {value.logical_id: value for value in unit.values}
        readiness = values.get(f"{unit.device_id}.ready")
        if readiness is not None and readiness.value is True:
            ready += 1
        availability = values.get(f"{unit.device_id}.available")
        if availability is not None and availability.value is True:
            available += 1
            available_power = values.get(f"{unit.device_id}.available.power")
            if available_power is not None and isinstance(available_power.value, (int, float)):
                power += float(available_power.value)
        unit_derated = values.get(f"{unit.device_id}.derated")
        if unit_derated is not None and unit_derated.value is True:
            derated += 1
        unit_fault = values.get(f"{unit.device_id}.fault.active")
        if unit_fault is not None and unit_fault.value is True:
            faulted += 1
    return StabilitiFleetSummary(ready, available, derated, faulted, len(units), power)


def _status_text(
    health: ComponentStatus,
    faults: tuple[DeviceFault, ...],
    values: tuple[DeviceValue, ...],
) -> str:
    if health is ComponentStatus.STOPPED:
        return "Ready"
    if any(fault.fault_id.endswith(".response_timeout") for fault in faults):
        return "Response timeout"
    if health is ComponentStatus.UNAVAILABLE:
        return "Communication lost"
    if any(fault.fault_id.endswith(".temperature_derate") for fault in faults):
        return "Derated"
    if faults:
        return "Fault active"
    dc600_status = next(
        (value.value for value in values if value.logical_id == "dc600.status"),
        None,
    )
    if isinstance(dc600_status, str):
        return dc600_status
    if any(value.quality is DataQuality.STALE for value in values):
        return "Information is stale"
    if any(value.logical_id.endswith(".derated") and value.value is True for value in values):
        return "Derated"
    if any(value.logical_id.endswith(".enabled") and value.value is False for value in values):
        return "Disabled"
    if health is ComponentStatus.DEGRADED:
        return "Waiting for current test values"
    return "Simulation ready"


def _activity_order(category: str) -> int:
    return {
        "fault_active": 0,
        "step": 1,
        "fault_scheduled": 2,
        "fault_cleared": 3,
        "recovery": 4,
    }.get(category, 5)


def _test_notice(device_id: str) -> str | None:
    if device_id.startswith("cnvtr-"):
        return "This rule is for software testing. The real equipment setting is not confirmed."
    if device_id.startswith("st-"):
        return (
            "Use the controls below to test availability, derating, communication loss, faults, "
            "and recovery. No physical equipment is connected during this test."
        )
    if device_id == "dc600":
        return (
            "Use the controls below to test demand, available power, limiting, stale data, faults, "
            "and recovery. No physical equipment is connected during this test."
        )
    return None


def _validate_normal_inputs(inputs: SimulationInputs) -> None:
    if not inputs.communication_ok:
        raise ValueError("use Fault testing to simulate a communication problem")
    if inputs.fault_active:
        raise ValueError("use Fault testing to simulate a device fault")
    if isinstance(inputs, StabilitiInputs) and inputs.derated:
        raise ValueError("use Fault testing to simulate derating")
    if isinstance(inputs, Dc600Inputs) and (not inputs.demand_fresh or not inputs.available_fresh):
        raise ValueError("use Fault testing to simulate a response timeout")
    if isinstance(inputs, ConverterInputs) and inputs.test_derate_limit is not None:
        raise ValueError("use Fault testing to simulate derating")


def _json_default(value: object) -> str | list[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, frozenset):
        return sorted(str(item) for item in value)
    raise TypeError(f"unsupported report value: {type(value).__name__}")

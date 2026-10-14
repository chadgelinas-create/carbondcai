"""Deterministic in-process multi-device simulator runtime tests."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from skid2.contracts import ComponentStatus, DataQuality, ManualClock
from skid2.sim import (
    CapabilityKind,
    DeviceCapability,
    DeviceCommand,
    DeviceHealth,
    DeviceIdentity,
    DeviceLifecycle,
    DeviceRegistration,
    DeviceRegistrationError,
    DeviceStep,
    DeviceStepResult,
    DeviceUpdate,
    DeviceValue,
    EvidenceClassification,
    FidelityLevel,
    RuntimeLifecycle,
    RuntimeTransitionError,
    SimulatorRuntime,
)

NOW = datetime(2026, 8, 1, 10, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Configuration:
    increment: int = 1


@dataclass(frozen=True, slots=True)
class State:
    lifecycle: DeviceLifecycle
    count: int


@dataclass(frozen=True, slots=True)
class Inputs:
    enabled: bool
    fail: bool = False


class CountingModel:
    def __init__(self, device_id: str) -> None:
        self.identity = DeviceIdentity(device_id, device_id)
        self.capabilities = (
            DeviceCapability(
                f"{device_id}.behavior.count",
                device_id,
                CapabilityKind.BEHAVIOR,
                EvidenceClassification.SOURCED,
                FidelityLevel.LEVEL_1,
                "approved",
                available=True,
            ),
        )

    def initial_state(self, configuration: Configuration) -> State:
        return State(DeviceLifecycle.READY, 0)

    def step(
        self,
        request: DeviceStep[Configuration, State, Inputs],
    ) -> DeviceStepResult[State]:
        if request.inputs.fail:
            raise RuntimeError("deliberate failure with unstable detail")
        count = request.state.count
        if request.inputs.enabled:
            count += request.configuration.increment
        state = State(DeviceLifecycle.ACTIVE, count)
        ordered_commands = ",".join(command.command_id for command in request.commands)
        telemetry = (
            DeviceValue(
                f"{self.identity.device_id}.count",
                count,
                "count",
                DataQuality.GOOD,
                self.identity.device_id,
                EvidenceClassification.SOURCED,
            ),
            DeviceValue(
                f"{self.identity.device_id}.commands",
                ordered_commands,
                "command_ids",
                DataQuality.GOOD,
                self.identity.device_id,
                EvidenceClassification.SOURCED,
            ),
        )
        health = DeviceHealth(
            state.lifecycle,
            ComponentStatus.HEALTHY,
            DataQuality.GOOD,
            self.identity.device_id,
            EvidenceClassification.SOURCED,
        )
        return DeviceStepResult(state, telemetry, health)


def registration(device_id: str) -> DeviceRegistration[Configuration, State, Inputs]:
    return DeviceRegistration(CountingModel(device_id), Configuration(), Inputs(True))


def runtime(*device_ids: str) -> SimulatorRuntime:
    result = SimulatorRuntime(ManualClock(NOW), timedelta(seconds=1))
    for device_id in device_ids:
        result.register(registration(device_id))
    return result


def test_multiple_devices_have_stable_order_and_canonical_trace_bytes() -> None:
    first = runtime("device-b", "device-a")
    second = runtime("device-a", "device-b")
    first.start()
    second.start()

    first_frame = first.advance()
    second_frame = second.advance()

    assert tuple(item.device_id for item in first_frame) == ("device-a", "device-b")
    assert first_frame == second_frame
    assert first.trace_bytes() == second.trace_bytes()
    assert first.at == NOW + timedelta(seconds=1)
    assert first.sequence == 1


def test_commands_and_capabilities_have_deterministic_order() -> None:
    simulator = runtime("device-b", "device-a")
    simulator.start()
    commands = (
        DeviceCommand("later-id", "missing", NOW),
        DeviceCommand("earlier-id", "missing", NOW - timedelta(seconds=1)),
        DeviceCommand("first-id", "missing", NOW),
    )

    frame = simulator.advance((DeviceUpdate("device-a", Inputs(True), commands),))

    assert frame[0].result is not None
    assert frame[0].result.telemetry[1].value == "earlier-id,first-id,later-id"
    assert tuple(item.device_id for item in simulator.capabilities()) == (
        "device-a",
        "device-b",
    )


def test_failure_retains_state_and_does_not_stop_other_devices() -> None:
    simulator = runtime("device-a", "device-b")
    simulator.start()
    first = simulator.advance()
    state_before_failure = first[0].result
    assert state_before_failure is not None

    frame = simulator.advance((DeviceUpdate("device-a", Inputs(True, fail=True)),))

    assert frame[0].failure_code == "device_step_failed"
    assert frame[0].result is None
    assert frame[1].result is not None
    snapshot = simulator.snapshot()
    assert snapshot.devices[0].state == state_before_failure.state
    assert snapshot.devices[0].health.status is ComponentStatus.UNAVAILABLE
    assert snapshot.devices[0].health.failure_code == "device_step_failed"
    assert snapshot.devices[1].state == State(DeviceLifecycle.ACTIVE, 2)


def test_unknown_or_duplicate_update_rejects_entire_frame() -> None:
    simulator = runtime("device-a")
    simulator.start()

    with pytest.raises(DeviceRegistrationError, match="unknown"):
        simulator.advance((DeviceUpdate("missing", Inputs(True)),))
    assert simulator.sequence == 0

    update = DeviceUpdate("device-a", Inputs(True))
    with pytest.raises(DeviceRegistrationError, match="duplicate"):
        simulator.advance((update, update))
    assert simulator.sequence == 0


def test_snapshot_restore_reset_and_requested_step_replay() -> None:
    simulator = runtime("device-a")
    initial = simulator.snapshot()
    simulator.start()
    simulator.advance(dt=timedelta(seconds=2.5))
    captured = simulator.snapshot()
    assert captured.devices[0].telemetry[0].value == 1
    expected_bytes = simulator.trace_bytes()
    simulator.stop()

    simulator.reset()
    assert simulator.snapshot() == initial
    assert simulator.snapshot().devices[0].telemetry == ()
    simulator.restore(captured)
    assert simulator.snapshot() == captured
    assert simulator.trace_bytes() == expected_bytes


def test_lifecycle_registration_and_inventory_guards() -> None:
    simulator = SimulatorRuntime(ManualClock(NOW), timedelta(seconds=1))
    with pytest.raises(RuntimeTransitionError, match="at least one"):
        simulator.start()
    simulator.register(registration("device-a"))
    with pytest.raises(DeviceRegistrationError, match="duplicate"):
        simulator.register(registration("device-a"))
    simulator.start()
    simulator.start()
    assert simulator.lifecycle.value == RuntimeLifecycle.RUNNING.value
    with pytest.raises(RuntimeTransitionError, match="only while stopped"):
        simulator.register(registration("device-b"))
    with pytest.raises(RuntimeTransitionError, match="stopped"):
        simulator.reset()
    simulator.stop()
    simulator.stop()
    simulator.shutdown()
    simulator.stop()
    assert simulator.lifecycle.value == RuntimeLifecycle.SHUTDOWN.value
    with pytest.raises(RuntimeTransitionError, match="cannot be restarted"):
        simulator.start()


def test_restore_rejects_different_inventory() -> None:
    source = runtime("device-a")
    target = runtime("device-b")

    with pytest.raises(DeviceRegistrationError, match="inventory"):
        target.restore(source.snapshot())


@pytest.mark.parametrize("duration", [timedelta(0), timedelta(seconds=-1)])
def test_invalid_step_is_rejected(duration: timedelta) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        SimulatorRuntime(ManualClock(NOW), duration)

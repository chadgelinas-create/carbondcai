"""Transport-independent simulator DeviceModel contract tests."""

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta

import pytest

from skid2.contracts import ComponentStatus, DataQuality
from skid2.sim import (
    CapabilityKind,
    CommandDisposition,
    DeviceCapability,
    DeviceCommand,
    DeviceCommandResult,
    DeviceHealth,
    DeviceIdentity,
    DeviceLifecycle,
    DeviceModel,
    DeviceStep,
    DeviceStepResult,
    DeviceValue,
    EvidenceClassification,
    ExecutionProfile,
    FidelityLevel,
)

NOW = datetime(2026, 7, 31, 10, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FakeConfiguration:
    increment_kw_per_second: float


@dataclass(frozen=True, slots=True)
class FakeState:
    lifecycle: DeviceLifecycle
    power_kw: float


@dataclass(frozen=True, slots=True)
class FakeInputs:
    enabled: bool


class FakeDeviceModel:
    identity = DeviceIdentity("fake-1", "Minimal fake device")
    capabilities = (
        DeviceCapability(
            capability_id="fake.command.enable",
            device_id="fake-1",
            kind=CapabilityKind.COMMAND,
            evidence_classification=EvidenceClassification.PROVISIONAL,
            fidelity_level=FidelityLevel.CONTRACT,
            review_status="approved_for_stage0_test",
            available=True,
            enabled_profiles=frozenset({ExecutionProfile.STAGE_0}),
            assumptions=("Contract-test behavior only",),
        ),
    )

    def initial_state(self, configuration: FakeConfiguration) -> FakeState:
        return FakeState(DeviceLifecycle.INITIALIZING, 0.0)

    def step(
        self,
        request: DeviceStep[FakeConfiguration, FakeState, FakeInputs],
    ) -> DeviceStepResult[FakeState]:
        supported = {item.capability_id for item in self.capabilities if item.available}
        command_results = tuple(
            DeviceCommandResult(
                command.command_id,
                command.capability_id,
                (
                    CommandDisposition.ACCEPTED
                    if command.capability_id in supported
                    else CommandDisposition.REJECTED_UNSUPPORTED
                ),
                (
                    "capability available"
                    if command.capability_id in supported
                    else "capability unavailable or unsupported"
                ),
                request.at,
                EvidenceClassification.PROVISIONAL,
            )
            for command in request.commands
        )
        lifecycle = DeviceLifecycle.ACTIVE if request.inputs.enabled else DeviceLifecycle.IDLE
        power_kw = request.state.power_kw
        if lifecycle is DeviceLifecycle.ACTIVE:
            power_kw += request.configuration.increment_kw_per_second * request.dt.total_seconds()
        state = FakeState(lifecycle, power_kw)
        telemetry = DeviceValue(
            "fake.power",
            power_kw,
            "kW",
            DataQuality.GOOD,
            "fake-1",
            EvidenceClassification.PROVISIONAL,
        )
        health = DeviceHealth(
            lifecycle,
            ComponentStatus.HEALTHY,
            DataQuality.GOOD,
            "fake-1",
            EvidenceClassification.PROVISIONAL,
        )
        return DeviceStepResult(state, (telemetry,), health, command_results=command_results)


def test_fake_model_structurally_satisfies_contract_and_replays() -> None:
    model: DeviceModel[FakeConfiguration, FakeState, FakeInputs] = FakeDeviceModel()
    configuration = FakeConfiguration(5.0)
    initial = model.initial_state(configuration)
    request = DeviceStep(
        initial,
        FakeInputs(enabled=True),
        (),
        configuration,
        NOW,
        timedelta(seconds=2),
    )

    first = model.step(request)
    second = model.step(request)

    assert first == second
    assert first.state == FakeState(DeviceLifecycle.ACTIVE, 10.0)
    assert initial == FakeState(DeviceLifecycle.INITIALIZING, 0.0)
    assert model.initial_state(configuration) == initial
    assert first.telemetry[0].unit == "kW"
    assert first.telemetry[0].quality is DataQuality.GOOD
    assert first.telemetry[0].provisional is True


def test_unsupported_capability_returns_typed_rejection() -> None:
    model = FakeDeviceModel()
    request = DeviceStep(
        model.initial_state(FakeConfiguration(1.0)),
        FakeInputs(enabled=False),
        (DeviceCommand("command-1", "fake.command.missing", NOW),),
        FakeConfiguration(1.0),
        NOW,
        timedelta(seconds=1),
    )

    result = model.step(request)

    assert result.command_results[0].disposition is CommandDisposition.REJECTED_UNSUPPORTED


def test_step_rejects_invalid_time_and_dt() -> None:
    with pytest.raises(ValueError, match="finite positive"):
        DeviceStep(
            FakeState(DeviceLifecycle.IDLE, 0.0),
            FakeInputs(False),
            (),
            FakeConfiguration(1.0),
            NOW,
            timedelta(0),
        )


def test_blocked_capability_cannot_be_available() -> None:
    with pytest.raises(ValueError, match="cannot be available"):
        DeviceCapability(
            "fake.command.blocked",
            "fake-1",
            CapabilityKind.COMMAND,
            EvidenceClassification.BLOCKED,
            FidelityLevel.CONTRACT,
            "pending",
            available=True,
        )


def test_initial_state_is_immutable() -> None:
    state = FakeDeviceModel().initial_state(FakeConfiguration(1.0))

    with pytest.raises(FrozenInstanceError):
        state.power_kw = 20.0  # type: ignore[misc]
